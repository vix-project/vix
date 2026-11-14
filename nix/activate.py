"""Apply immutable service templates to writable runit directories.

The command entry point is Vix-only. Manager is also used by the unprivileged
integration tests with private directories and their own runsvdir process.
"""

import argparse
import contextlib
import datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

from boot import BootError, BootManager, atomic_file


NAME = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*\Z")
SCRIPTS = ("run", "finish", "check", "log/run")
BOOT_HOOK = """# Managed by Vix: recover and select declarations before runsvdir starts.
while :; do
    _vix_driver=
    if [ -f /var/lib/vix/transaction.json ]; then
        _vix_driver=/var/lib/vix/pending/bin/vix-activate
    elif [ -x /var/lib/vix/current/bin/vix-activate ]; then
        _vix_driver=/var/lib/vix/current/bin/vix-activate
    else
        break
    fi
    if [ -x "$_vix_driver" ] && "$_vix_driver" boot; then break; fi
    printf '%s\\n' 'Vix boot recovery failed; repair the state, then exit to retry.' >&2
    /sbin/sulogin
done
unset _vix_driver
"""


class ActivationError(Exception):
    pass


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_link(target, path):
    temporary = path.with_name(path.name + ".new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, path)
    fsync_dir(path.parent)


def atomic_json(value, path):
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fsync_dir(path.parent)


def exists(path):
    return os.path.lexists(path)


class Manager:
    def __init__(
        self, state, scan, roots, sv="sv", timeout=30, boot=None, boot_hook=None
    ):
        self.state = Path(state)
        self.scan = Path(scan)
        self.roots = Path(roots)
        self.runtime = self.state / "services"
        self.generations = self.state / "generations"
        self.metadata = self.state / "metadata"
        self.journal = self.state / "transaction.json"
        self.sv = str(sv)
        self.timeout = timeout
        self.boot = boot or BootManager()
        self.boot_hook = Path(boot_hook) if boot_hook is not None else None

    @contextlib.contextmanager
    def locked(self):
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ActivationError("another Vix activation is running") from error
            for directory in (
                self.runtime,
                self.generations,
                self.metadata,
                self.roots,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            if not self.scan.is_dir():
                raise ActivationError(f"runit scan directory is missing: {self.scan}")
            yield

    def current_id(self):
        current = self.state / "current"
        if not exists(current):
            return None
        if not current.is_symlink():
            raise ActivationError(f"expected a managed symlink: {current}")
        match = re.fullmatch(r"generations/([1-9][0-9]*)", os.readlink(current))
        if match is None or not current.is_dir():
            raise ActivationError(f"invalid current generation: {current}")
        return int(match.group(1))

    def generation_ids(self):
        return sorted(
            int(p.name)
            for p in self.generations.iterdir()
            if re.fullmatch(r"[1-9][0-9]*", p.name) and p.is_symlink()
        )

    def generation_system(self, number):
        path = self.generations / str(number)
        if not path.is_symlink() or not path.is_dir():
            raise ActivationError(f"generation {number} does not exist")
        return path.resolve()

    def load(self, system):
        if system is None:
            return {}
        system = Path(system).resolve(strict=True)
        try:
            marker = json.loads((system / "vix-system.json").read_text())
        except (OSError, ValueError) as error:
            raise ActivationError(f"not a Vix system: {system}") from error
        if marker != {"format": 1}:
            raise ActivationError(f"unsupported Vix system format: {system}")
        services = system / "services"
        if not services.is_dir():
            raise ActivationError(f"service templates are missing: {services}")
        result = {}
        for service in services.iterdir():
            if not NAME.fullmatch(service.name) or not service.is_dir():
                raise ActivationError(f"invalid service template: {service}")
            expected = {"run", "finish", "check", "log"}
            if any(p.name not in expected for p in service.iterdir()):
                raise ActivationError(
                    f"unexpected files in service template: {service}"
                )
            if not (service / "run").is_file() or not os.access(
                service / "run", os.X_OK
            ):
                raise ActivationError(
                    f"service run script is not executable: {service}"
                )
            if exists(service / "log") and (
                not (service / "log").is_dir()
                or {p.name for p in (service / "log").iterdir()} != {"run"}
            ):
                raise ActivationError(f"invalid logger template: {service}")
            digest = hashlib.sha256()
            for filename in SCRIPTS:
                script = service / filename
                digest.update(filename.encode() + b"\0")
                if exists(script):
                    if not script.is_file() or not os.access(script, os.X_OK):
                        raise ActivationError(f"script is not executable: {script}")
                    digest.update(b"present\0" + script.read_bytes())
                digest.update(b"\0")
            result[service.name] = (service, digest.hexdigest())
        return result

    def preflight(self, names):
        for name in names:
            runtime = self.runtime / name
            scanned = self.scan / name
            if exists(scanned) and (
                not scanned.is_symlink() or os.readlink(scanned) != str(runtime)
            ):
                raise ActivationError(
                    f"refusing to replace unmanaged service: {scanned}"
                )
            if exists(runtime):
                marker = runtime / ".vix-owned"
                if runtime.is_symlink() or not runtime.is_dir() or not marker.is_file():
                    raise ActivationError(
                        f"refusing to replace unmanaged directory: {runtime}"
                    )
                if marker.read_text() != "1\n":
                    raise ActivationError(f"invalid ownership marker: {runtime}")
                for filename in SCRIPTS:
                    path = runtime / filename
                    if exists(path) and not path.is_symlink():
                        raise ActivationError(f"managed script was replaced: {path}")
                log = runtime / "log"
                if exists(log) and (log.is_symlink() or not log.is_dir()):
                    raise ActivationError(
                        f"managed logger directory was replaced: {log}"
                    )
                parked = runtime / ".vix-log-disabled"
                if exists(parked) and (parked.is_symlink() or not parked.is_dir()):
                    raise ActivationError(
                        f"managed logger directory was replaced: {parked}"
                    )
                if exists(log) and exists(parked):
                    raise ActivationError(
                        f"both active and parked logger directories exist: {runtime}"
                    )

    @staticmethod
    def supervised(runtime):
        try:
            fd = os.open(runtime / "supervise/ok", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ENXIO):
                return False
            raise
        else:
            os.close(fd)
            return True

    def control(self, action, runtime):
        process = subprocess.Popen(
            [self.sv, "-w", str(self.timeout), action, str(runtime)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=self.timeout + 2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ActivationError(f"timed out waiting for {runtime.name} to {action}")
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if process.returncode:
            raise ActivationError(output.strip() or f"{action} failed: {runtime}")

    def stop(self, names):
        # Remove scan links first: runsvdir must not respawn a supervisor while
        # we stop it and replace its scripts (including the logger).
        for name in names:
            (self.scan / name).unlink(missing_ok=True)
        fsync_dir(self.scan)
        for name in names:
            runtime = self.runtime / name
            if self.supervised(runtime):
                self.control("exit", runtime)
                deadline = time.monotonic() + self.timeout
                while self.supervised(runtime):
                    if time.monotonic() >= deadline:
                        raise ActivationError(f"supervisor did not exit: {name}")
                    time.sleep(0.05)

    def install(self, desired):
        for name, (template, _) in desired.items():
            runtime = self.runtime / name
            if not runtime.exists():
                staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=self.runtime))
                marker = staging / ".vix-owned"
                with marker.open("w") as stream:
                    stream.write("1\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                fsync_dir(staging)
                os.rename(staging, runtime)
                fsync_dir(self.runtime)
            log = runtime / "log"
            parked = runtime / ".vix-log-disabled"
            if (template / "log").exists() and not log.exists():
                if parked.exists():
                    os.rename(parked, log)
                else:
                    log.mkdir()
            elif not (template / "log").exists() and log.exists():
                os.rename(log, parked)
            fsync_dir(runtime)
            for filename in SCRIPTS:
                target = runtime / filename
                if (template / filename).exists():
                    atomic_link(template / filename, target)
                else:
                    target.unlink(missing_ok=True)

    def start(self, names):
        for name in names:
            atomic_link(self.runtime / name, self.scan / name)
        for name in names:
            runtime = self.runtime / name
            deadline = time.monotonic() + self.timeout
            while not self.supervised(runtime):
                if time.monotonic() >= deadline:
                    raise ActivationError(f"runit did not discover service: {name}")
                time.sleep(0.05)
            self.control("up", runtime)

    def finish_transaction(self):
        # Once the journal is absent, the current generation is authoritative.
        # Keep the pending driver rooted until that deletion is durable.
        self.journal.unlink(missing_ok=True)
        fsync_dir(self.state)
        (self.state / "pending").unlink(missing_ok=True)
        (self.roots / "pending").unlink(missing_ok=True)
        fsync_dir(self.state)
        fsync_dir(self.roots)

    def recover(self, boot=False):
        if not self.journal.exists():
            self.finish_transaction()
            return
        transaction = json.loads(self.journal.read_text())
        if transaction.get("format") != 1:
            raise ActivationError(
                "unsupported activation journal; manual recovery required"
            )
        if transaction["phase"] == "committed":
            self.finish_transaction()
            return
        old = self.load(transaction["old_system"])
        touched = transaction["touched"]
        self.preflight(set(old) | set(touched))
        self.boot.restore(transaction.get("boot"))
        self.stop(touched)
        self.install(old)
        if boot:
            for name in old:
                atomic_link(self.runtime / name, self.scan / name)
        else:
            self.start(old.keys())
        old_id = transaction["old_id"]
        if old_id is None:
            (self.state / "current").unlink(missing_ok=True)
        else:
            atomic_link(f"generations/{old_id}", self.state / "current")
        if transaction["new_record"]:
            number = str(transaction["target_id"])
            (self.generations / number).unlink(missing_ok=True)
            (self.metadata / f"{number}.json").unlink(missing_ok=True)
            (self.roots / number).unlink(missing_ok=True)
        self.finish_transaction()
        print("vix: restored the previous service configuration", flush=True)

    def apply(self, system, target_id=None):
        self.recover()
        system = Path(system).resolve(strict=True)
        desired = self.load(system)
        old_id = self.current_id()
        old_system = self.generation_system(old_id) if old_id is not None else None
        old = self.load(old_system)
        self.preflight(set(old) | set(desired))
        boot_snapshot = self.boot.snapshot(old_system, system)
        if self.boot_hook is not None:
            if not self.boot_hook.parent.is_dir():
                raise ActivationError("Void runit core-services directory is missing")
            if exists(self.boot_hook) and (
                self.boot_hook.is_symlink()
                or not self.boot_hook.read_text().startswith("# Managed by Vix:")
            ):
                raise ActivationError(
                    f"refusing to replace unmanaged boot hook: {self.boot_hook}"
                )
        if old_system == system:
            self.install(desired)
            self.start(desired.keys())
            print(f"vix: generation {old_id} is already current", flush=True)
            return old_id
        unchanged = {
            name
            for name in old.keys() & desired.keys()
            if old[name][1] == desired[name][1]
        }
        touched = sorted((old.keys() | desired.keys()) - unchanged)
        new_record = target_id is None
        if new_record:
            target_id = max(self.generation_ids(), default=0) + 1
        atomic_link(system, self.roots / "pending")
        atomic_link(system, self.state / "pending")
        transaction = {
            "format": 1,
            "phase": "activating",
            "old_id": old_id,
            "old_system": str(old_system) if old_system else None,
            "target_id": target_id,
            "new_record": new_record,
            "new_system": str(system),
            "touched": touched,
            "boot": boot_snapshot,
        }
        atomic_json(transaction, self.journal)
        try:
            if self.boot_hook is not None:
                atomic_file(self.boot_hook, BOOT_HOOK.encode())
            self.stop(touched)
            self.install(desired)
            self.start(desired.keys())
            if new_record:
                atomic_link(system, self.roots / str(target_id))
                atomic_link(system, self.generations / str(target_id))
                atomic_json(
                    {
                        "created": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat()
                    },
                    self.metadata / f"{target_id}.json",
                )
            if boot_snapshot is not None:
                generations = {
                    n: self.generation_system(n) for n in self.generation_ids()
                }
                self.boot.activate(system, target_id, generations)
            atomic_link(f"generations/{target_id}", self.state / "current")
            transaction["phase"] = "committed"
            atomic_json(transaction, self.journal)
        except (Exception, KeyboardInterrupt) as error:
            print(
                f"vix: activation failed: {error}; recovering the transaction",
                file=sys.stderr,
            )
            try:
                self.recover()
            except (Exception, KeyboardInterrupt) as recovery_error:
                raise ActivationError(
                    f"recovery is incomplete: {recovery_error}; run vix recover before switching again"
                ) from error
            if self.current_id() != old_id:
                raise ActivationError(
                    f"generation {self.current_id()} was committed, but activation ended with an error: {error}"
                ) from error
            raise ActivationError(
                "activation failed; previous configuration restored"
            ) from error
        self.finish_transaction()
        print(f"vix: switched to generation {target_id}", flush=True)
        return target_id

    def rollback(self, target=None):
        self.recover()
        current = self.current_id()
        candidates = [
            number for number in self.generation_ids() if number < (current or 0)
        ]
        if target is None:
            if not candidates:
                raise ActivationError("no previous generation")
            target = candidates[-1]
        return self.apply(self.generation_system(target), target_id=target)

    def list_generations(self):
        current = self.current_id()
        for number in self.generation_ids():
            metadata = self.metadata / f"{number}.json"
            created = json.loads(metadata.read_text())["created"]
            print(
                f"{'*' if number == current else ' '} {number} {created} {self.generation_system(number)}"
            )
        if self.journal.exists():
            print("vix: an interrupted activation needs recovery", file=sys.stderr)

    def boot_generation(self, selected=None):
        interrupted = self.journal.exists()
        if selected is None:
            runtime_root = self.roots.parent / "vix-booted"
            try:
                mounted = runtime_root.exists() and os.path.samefile(
                    runtime_root / "modules" / os.uname().release,
                    Path("/lib/modules") / os.uname().release,
                )
            except OSError:
                mounted = False
            if not mounted:
                # A legacy boot must not inherit the previous boot's marker.
                runtime_root.unlink(missing_ok=True)
                (self.roots.parent / "vix-booted-system").unlink(missing_ok=True)
                fsync_dir(self.roots.parent)
        if selected is not None and (self.generations / str(selected)).is_dir():
            booted = self.generation_system(selected)
            descriptor = self.boot.load(booted)
            runtime_root = self.roots.parent / "vix-booted"
            if (
                descriptor is not None
                and runtime_root.exists()
                and (
                    runtime_root.resolve() == Path(descriptor["runtime"]).resolve()
                    and os.uname().release == descriptor["kernel_version"]
                )
            ):
                # A crash may roll the desired generation back while this
                # newly booted kernel still needs its original module tree.
                atomic_link(booted, self.roots.parent / "vix-booted-system")
        self.recover(boot=True)
        if interrupted or selected is None:
            return
        system = self.generation_system(selected)
        desired = self.load(system)
        current = self.current_id()
        old_system = self.generation_system(current) if current is not None else None
        old = self.load(old_system)
        names = set(old) | set(desired)
        self.preflight(names)
        if any(self.supervised(self.runtime / name) for name in names):
            raise ActivationError("boot generation selection must run before runsvdir")
        for name in names:
            (self.scan / name).unlink(missing_ok=True)
        self.install(desired)
        for name in desired:
            atomic_link(self.runtime / name, self.scan / name)
        if self.boot.load(old_system) is not None or self.boot.load(system) is not None:
            self.boot.activate(
                system,
                selected,
                {n: self.generation_system(n) for n in self.generation_ids()},
            )
        atomic_link(f"generations/{selected}", self.state / "current")

    def status(self):
        current = self.current_id()
        services = (
            self.load(self.generation_system(current)) if current is not None else {}
        )
        kernel = (
            self.boot.status(self.generation_system(current))
            if current is not None
            else ""
        )
        if not services:
            return 0, "vix: no active declared services\n" + kernel
        result = subprocess.run(
            [
                self.sv,
                "status",
                *(str(self.runtime / name) for name in sorted(services)),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=self.timeout,
        )
        output = result.stdout + kernel
        if self.journal.exists():
            output += "vix: an interrupted activation needs vix recover\n"
        return result.returncode, output


def main():
    parser = argparse.ArgumentParser(description="Vix generation activation")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("switch").add_argument("system", type=Path)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("generation", type=int, nargs="?")
    commands.add_parser("generations")
    commands.add_parser("status")
    boot = commands.add_parser("boot")
    boot.add_argument("--generation", type=int)
    recover = commands.add_parser("recover")
    recover.add_argument("--boot", action="store_true")
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise ActivationError("must be root")
    release = Path("/etc/os-release").read_text().splitlines()
    if not any(line in ("ID=vix", 'ID="vix"') for line in release):
        raise ActivationError("activation is supported only on Vix")
    timeout = int(os.environ.get("VIX_SWITCH_TIMEOUT", "30"))
    if not 1 <= timeout <= 3600:
        raise ActivationError("VIX_SWITCH_TIMEOUT must be between 1 and 3600 seconds")
    manager = Manager(
        "/var/lib/vix",
        "/etc/runit/runsvdir/default",
        "/nix/var/nix/gcroots/vix",
        timeout=timeout,
        boot_hook="/etc/runit/core-services/99-vix.sh",
    )
    offline_boot = arguments.command == "boot" or (
        arguments.command == "recover" and arguments.boot
    )
    if (
        not offline_boot
        and Path("/etc/runit/runsvdir/current").resolve() != manager.scan.resolve()
    ):
        raise ActivationError(
            "switch to runit's default runlevel before activating services"
        )
    if Path("/proc/1/comm").read_text().strip() != "runit":
        raise ActivationError("runit must be running as PID 1")
    if arguments.command == "switch" and arguments.system.resolve().parent != Path(
        "/nix/store"
    ):
        raise ActivationError(
            "the system must be a build output directly in /nix/store"
        )
    with manager.locked():
        if arguments.command == "switch":
            manager.apply(arguments.system)
        elif arguments.command == "rollback":
            manager.rollback(arguments.generation)
        elif arguments.command == "generations":
            manager.list_generations()
        elif arguments.command == "status":
            code, output = manager.status()
            print(output, end="")
            sys.exit(code)
        elif arguments.command == "boot":
            selected = arguments.generation
            if selected is None:
                for argument in Path("/proc/cmdline").read_text().split():
                    if argument.startswith("vix.generation="):
                        selected = int(argument.split("=", 1)[1])
            manager.boot_generation(selected)
        elif arguments.command == "recover":
            manager.recover(boot=arguments.boot)


if __name__ == "__main__":

    def interrupted(signum, _frame):
        raise InterruptedError(f"interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except (ActivationError, BootError, OSError, ValueError) as error:
        print(f"vix: {error}", file=sys.stderr)
        sys.exit(1)
