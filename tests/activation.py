"""Integration coverage against private runsvdir instances, without root."""

import importlib.util
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import json
import tempfile
import time
import unittest


sys.path.insert(0, str(Path(os.environ["VIX_ACTIVATION_SOURCE"]).parent))
spec = importlib.util.spec_from_file_location(
    "vix_activate", os.environ["VIX_ACTIVATION_SOURCE"]
)
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


def fixture(name):
    return Path(os.environ[f"VIX_{name}"])


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.previous_test_dir = os.environ.get("VIX_TEST_DIR")
        os.environ["VIX_TEST_DIR"] = str(self.root)
        self.scan = self.root / "scan"
        self.scan.mkdir()
        self.output = (self.root / "supervisor.log").open("w")
        self.manager = activation.Manager(
            self.root / "state", self.scan, self.root / "gcroots", timeout=8
        )
        self.supervisor = subprocess.Popen(
            ["runsvdir", str(self.scan)],
            stdout=self.output,
            stderr=self.output,
            env=os.environ | {"VIX_TEST_DIR": str(self.root)},
            start_new_session=True,
        )

    def tearDown(self):
        try:
            with self.manager.locked():
                self.manager.stop([p.name for p in self.scan.iterdir()])
        finally:
            if self.supervisor.poll() is None:
                os.killpg(self.supervisor.pid, signal.SIGTERM)
                self.supervisor.wait(timeout=10)
            self.output.close()
            self.temporary.cleanup()
            if self.previous_test_dir is None:
                os.environ.pop("VIX_TEST_DIR", None)
            else:
                os.environ["VIX_TEST_DIR"] = self.previous_test_dir

    def apply(self, name):
        with self.manager.locked():
            return self.manager.apply(fixture(name))

    def package_system(self, name, fixture_name, packages=None):
        system = self.root / name
        system.mkdir()
        (system / "services").symlink_to(fixture(fixture_name) / "services")
        if packages is not None:
            (system / "packages").symlink_to(Path(os.environ[packages]))
        (system / "vix-system.json").write_text('{"format":1}')
        return system

    def pid(self, name):
        output = subprocess.check_output(
            ["sv", "status", str(self.manager.runtime / name)], text=True
        )
        return int(re.search(r"\(pid ([0-9]+)\)", output).group(1))

    def value(self, name):
        return (self.root / f"{name}.value").read_text().strip()

    def assert_clean(self):
        self.assertFalse(self.manager.journal.exists())
        self.assertFalse(activation.exists(self.manager.state / "pending"))
        self.assertFalse(activation.exists(self.manager.roots / "pending"))

    def test_update_remove_rollback_and_logger_toggle(self):
        self.assertEqual(self.apply("FIRST"), 1)
        original = self.pid("worker")
        stable = self.pid("stable")
        self.assertEqual(self.value("worker"), "one")
        self.assertTrue((self.manager.roots / "1").samefile(fixture("FIRST")))
        # Reapplying a generation must not restart healthy daemons.
        self.assertEqual(self.apply("FIRST"), 1)
        self.assertEqual(self.pid("worker"), original)
        self.assertEqual(self.apply("SECOND"), 2)
        self.assertEqual(self.pid("stable"), stable)
        self.assertNotEqual(self.pid("worker"), original)
        self.assertEqual(self.value("worker"), "two")
        self.assertEqual(self.value("added"), "new")
        self.assertEqual(self.apply("NO_LOGGER"), 3)
        self.assertEqual(self.pid("stable"), stable)
        self.assertEqual(self.value("worker"), "three")
        self.assertFalse(activation.exists(self.scan / "added"))
        self.assertFalse(self.manager.supervised(self.manager.runtime / "added"))
        self.assertFalse((self.manager.runtime / "worker/log").exists())
        self.assertTrue((self.manager.runtime / "worker/.vix-log-disabled").is_dir())
        with self.manager.locked():
            self.assertEqual(self.manager.rollback(), 2)
        self.assertEqual(self.value("worker"), "two")
        self.assertTrue((self.manager.runtime / "worker/log/run").is_symlink())
        self.assertEqual(self.pid("stable"), stable)
        with self.manager.locked():
            self.assertEqual(self.manager.rollback(1), 1)
        self.assertEqual(self.value("worker"), "one")
        self.assertEqual(self.manager.generation_ids(), [1, 2, 3])
        self.assert_clean()

    def test_failed_readiness_restores_previous_generation(self):
        self.apply("FIRST")
        stable = self.pid("stable")
        with self.assertRaisesRegex(
            activation.ActivationError, "previous configuration restored"
        ):
            self.apply("BAD")
        self.assertEqual(self.manager.current_id(), 1)
        self.assertEqual(self.manager.generation_ids(), [1])
        self.assertEqual(self.value("worker"), "one")
        self.assertEqual(self.pid("stable"), stable)
        self.assertFalse(activation.exists(self.scan / "added"))
        self.assert_clean()

    def test_failed_first_generation_leaves_no_active_services(self):
        with self.assertRaisesRegex(
            activation.ActivationError, "previous configuration restored"
        ):
            self.apply("BAD")
        self.assertIsNone(self.manager.current_id())
        self.assertEqual(list(self.scan.iterdir()), [])
        self.assertEqual(self.manager.generation_ids(), [])
        self.assert_clean()

    def test_unmanaged_collision_fails_before_modifying_services(self):
        self.apply("FIRST")
        original = self.pid("worker")
        foreign = self.scan / "foreign"
        foreign.symlink_to(self.root / "not-a-vix-service")
        try:
            with self.assertRaisesRegex(
                activation.ActivationError, "unmanaged service"
            ):
                self.apply("COLLISION")
            self.assertEqual(os.readlink(foreign), str(self.root / "not-a-vix-service"))
            self.assertEqual(self.pid("worker"), original)
            self.assertEqual(self.manager.current_id(), 1)
            self.assert_clean()
        finally:
            foreign.unlink()

    def test_concurrent_activation_is_rejected(self):
        with self.manager.locked():
            other = activation.Manager(
                self.manager.state, self.scan, self.manager.roots
            )
            with self.assertRaisesRegex(
                activation.ActivationError, "another Vix activation"
            ):
                with other.locked():
                    self.fail("second activation acquired the lock")

    def test_unmanaged_runtime_directory_is_not_adopted(self):
        with self.manager.locked():
            (self.manager.runtime / "worker").mkdir()
        with self.assertRaisesRegex(activation.ActivationError, "unmanaged directory"):
            self.apply("FIRST")
        self.assertIsNone(self.manager.current_id())
        self.assertEqual(list(self.scan.iterdir()), [])
        self.assert_clean()

    def test_no_previous_generation_does_not_change_services(self):
        self.apply("FIRST")
        original = self.pid("worker")
        with self.manager.locked():
            with self.assertRaisesRegex(
                activation.ActivationError, "no previous generation"
            ):
                self.manager.rollback()
        self.assertEqual(self.pid("worker"), original)
        self.assertEqual(self.manager.current_id(), 1)

    def test_helper_status_reads_declared_services_without_restarting_them(self):
        self.apply("FIRST")
        original = self.pid("worker")
        with self.manager.locked():
            code, output = self.manager.status()
        self.assertEqual(code, 0)
        self.assertIn("worker", output)
        self.assertIn("stable", output)
        self.assertEqual(self.pid("worker"), original)

    def test_runtime_activation_installs_a_boot_hook_without_an_image_rebuild(self):
        self.manager.boot_hook = self.root / "core-services/99-vix.sh"
        self.manager.boot_hook.parent.mkdir()
        self.apply("FIRST")
        subprocess.run(["sh", "-n", str(self.manager.boot_hook)], check=True)
        self.assertTrue(
            self.manager.boot_hook.read_text().startswith("# Managed by Vix:")
        )

    def test_foreign_boot_hook_is_not_replaced(self):
        self.manager.boot_hook = self.root / "core-services/99-vix.sh"
        self.manager.boot_hook.parent.mkdir()
        self.manager.boot_hook.write_text("# my own boot hook\n")
        with self.assertRaisesRegex(activation.ActivationError, "unmanaged boot hook"):
            self.apply("FIRST")
        self.assertEqual(self.manager.boot_hook.read_text(), "# my own boot hook\n")
        self.assertIsNone(self.manager.current_id())
        self.assertEqual(list(self.scan.iterdir()), [])

    def test_failed_boot_update_restores_services_and_the_grub_configuration(self):
        class PrivateBoot(activation.BootManager):
            fail = False

            def filesystem(self):
                return "TEST-UUID", Path("/")

            def activate(self, *args):
                super().activate(*args)
                if self.fail:
                    raise activation.BootError("injected failure after boot selection")

        def boot_system(name, fixture_name):
            system = self.root / name
            asset = self.root / f"asset-{name}"
            runtime = asset / "runtime"
            (runtime / "modules/6.test").mkdir(parents=True)
            (asset / "kernel").write_bytes(b"test kernel")
            (asset / "initrd").write_bytes(b"test initrd")
            (asset / "boot.json").write_text(
                json.dumps(
                    {
                        "format": 1,
                        "kernel_version": "6.test",
                        "runtime": str(runtime),
                        "kernel": str(asset / "kernel"),
                        "initrd": str(asset / "initrd"),
                        "root_device": "/dev/test",
                        "kernel_params": [],
                    }
                )
            )
            system.mkdir()
            (system / "boot").symlink_to(asset)
            (system / "services").symlink_to(fixture(fixture_name) / "services")
            (system / "vix-system.json").write_text('{"format":1}')
            return system

        first, second = (
            boot_system("kernel-one", "FIRST"),
            boot_system("kernel-two", "SECOND"),
        )
        boot = PrivateBoot(self.root / "boot")
        boot.grub.parent.mkdir(parents=True)
        boot.grub.write_text("menuentry 'Legacy' { true; }\n")
        self.manager.boot = boot
        with self.manager.locked():
            self.manager.apply(first)
        previous = boot.grub.read_bytes()
        stable = self.pid("stable")
        boot.fail = True
        with self.manager.locked():
            with self.assertRaisesRegex(
                activation.ActivationError, "previous configuration restored"
            ):
                self.manager.apply(second)
        self.assertEqual(boot.grub.read_bytes(), previous)
        self.assertEqual(self.manager.current_id(), 1)
        self.assertEqual(self.value("worker"), "one")
        self.assertEqual(self.pid("stable"), stable)
        self.assert_clean()

    def test_committed_journal_is_cleaned_without_rolling_back(self):
        self.apply("FIRST")
        self.apply("SECOND")
        original = self.pid("worker")
        activation.atomic_link(fixture("SECOND"), self.manager.roots / "pending")
        activation.atomic_link(fixture("SECOND"), self.manager.state / "pending")
        activation.atomic_json(
            {"format": 1, "phase": "committed"}, self.manager.journal
        )
        with self.manager.locked():
            self.manager.recover()
        self.assertEqual(self.manager.current_id(), 2)
        self.assertEqual(self.pid("worker"), original)
        self.assert_clean()

    def crash_during_update(self):
        child = os.fork()
        if child == 0:
            try:
                self.apply("BAD")
            finally:
                os._exit(99)
        try:
            target = fixture("BAD") / "services/worker/run"
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                run = self.manager.runtime / "worker/run"
                if run.is_symlink() and run.resolve() == target.resolve():
                    break
                time.sleep(0.05)
            else:
                self.fail("activation did not reach script replacement")
            os.kill(child, signal.SIGKILL)
        finally:
            _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertTrue(self.manager.journal.exists())

    def test_sigkill_is_recovered_on_next_operation(self):
        self.apply("FIRST")
        stable = self.pid("stable")
        self.crash_during_update()
        self.assertEqual(self.apply("SECOND"), 2)
        self.assertEqual(self.manager.current_id(), 2)
        self.assertEqual(self.value("worker"), "two")
        self.assertEqual(self.pid("stable"), stable)
        self.assert_clean()

    def test_boot_recovery_does_not_wait_for_stage_two(self):
        self.apply("FIRST")
        self.crash_during_update()
        with self.manager.locked():
            self.manager.stop([p.name for p in self.scan.iterdir()])
        os.killpg(self.supervisor.pid, signal.SIGTERM)
        self.supervisor.wait(timeout=10)
        with self.manager.locked():
            self.manager.recover(boot=True)
        self.assertEqual(self.manager.current_id(), 1)
        self.assertTrue((self.scan / "worker").is_symlink())
        self.assertFalse(activation.exists(self.scan / "added"))
        self.assertFalse(self.manager.supervised(self.manager.runtime / "worker"))
        self.assert_clean()

    def test_boot_menu_selection_restores_the_selected_service_declarations(self):
        self.apply("FIRST")
        self.apply("SECOND")
        with self.manager.locked():
            self.manager.stop([p.name for p in self.scan.iterdir()])
        os.killpg(self.supervisor.pid, signal.SIGTERM)
        self.supervisor.wait(timeout=10)
        with self.manager.locked():
            self.manager.boot_generation(1)
        self.assertEqual(self.manager.current_id(), 1)
        self.assertFalse(activation.exists(self.scan / "added"))
        self.supervisor = subprocess.Popen(
            ["runsvdir", str(self.scan)],
            stdout=self.output,
            stderr=self.output,
            env=os.environ | {"VIX_TEST_DIR": str(self.root)},
            start_new_session=True,
        )
        with self.manager.locked():
            self.manager.start(["stable", "worker"])
        self.assertEqual(self.value("worker"), "one")
        self.assert_clean()

    def test_legacy_boot_clears_stale_booted_markers(self):
        self.apply("FIRST")
        original = self.pid("worker")
        runtime = self.root / "previous-kernel-runtime"
        (runtime / "modules/not-the-running-kernel").mkdir(parents=True)
        marker = self.manager.roots.parent / "vix-booted"
        system_marker = self.manager.roots.parent / "vix-booted-system"
        marker.symlink_to(runtime)
        system_marker.symlink_to(fixture("FIRST"))
        with self.manager.locked():
            self.manager.boot_generation()
        self.assertFalse(activation.exists(marker))
        self.assertFalse(activation.exists(system_marker))
        self.assertEqual(self.manager.current_id(), 1)
        self.assertEqual(self.pid("worker"), original)

    def test_declared_packages_follow_generations_and_rollback(self):
        self.manager.profile = self.root / "profile"
        self.manager.profile_hook = self.root / "profile.d/vix-system.sh"
        first = self.package_system("packages-one", "FIRST", "VIX_PACKAGES_ONE")
        second = self.package_system("packages-two", "SECOND", "VIX_PACKAGES_TWO")
        empty = self.package_system("packages-empty", "EMPTY")

        with self.manager.locked():
            self.manager.apply(first)
        self.assertTrue((self.manager.profile / "bin/hello").is_file())
        hook = self.manager.profile_hook.read_text()
        self.assertTrue(hook.startswith("# Managed by Vix:"))
        self.assertIn(str(self.manager.profile), hook)

        with self.manager.locked():
            self.manager.apply(second)
        self.assertTrue((self.manager.profile / "bin/rg").is_file())
        self.assertFalse(activation.exists(self.manager.profile / "bin/hello"))

        with self.manager.locked():
            self.manager.rollback(1)
        self.assertTrue((self.manager.profile / "bin/hello").is_file())
        self.assertFalse(activation.exists(self.manager.profile / "bin/rg"))

        with self.manager.locked():
            self.manager.apply(empty)
        self.assertFalse(activation.exists(self.manager.profile))
        self.assertFalse(activation.exists(self.manager.profile_hook))
        self.assert_clean()

    def test_offline_install_seeds_a_generation_without_starting_services(self):
        target = self.root / "target"
        scan = target / "etc/runit/runsvdir/default"
        scan.mkdir(parents=True)
        (target / "etc/runit/core-services").mkdir(parents=True)
        (target / "etc/profile.d").mkdir(parents=True)
        (target / "nix/var/nix/profiles").mkdir(parents=True)
        manager = activation.Manager(
            target / "var/lib/vix",
            scan,
            target / "nix/var/nix/gcroots/vix",
            timeout=8,
            boot_hook=target / "etc/runit/core-services/99-vix.sh",
            profile=target / "nix/var/nix/profiles/vix-system",
            profile_hook=target / "etc/profile.d/vix-system.sh",
        )
        system = self.package_system("install-system", "FIRST", "VIX_PACKAGES_ONE")
        foreign = scan / "agetty-tty1"
        foreign.symlink_to("/etc/sv/agetty-tty1")

        with manager.locked():
            self.assertEqual(manager.install_generation(system), 1)
        self.assertEqual(manager.current_id(), 1)
        self.assertEqual((manager.generations / "1").resolve(), system.resolve())
        self.assertTrue((scan / "worker").is_symlink())
        self.assertEqual(
            os.readlink(scan / "worker"), str(manager.runtime / "worker")
        )
        self.assertEqual(os.readlink(foreign), "/etc/sv/agetty-tty1")
        self.assertFalse(manager.supervised(manager.runtime / "worker"))
        self.assertTrue((manager.profile / "bin/hello").is_file())
        self.assertTrue(manager.boot_hook.read_text().startswith("# Managed by Vix:"))
        self.assertTrue(
            manager.profile_hook.read_text().startswith("# Managed by Vix:")
        )
        self.assertEqual(manager.generation_ids(), [1])

    def test_foreign_profile_hook_is_not_replaced(self):
        self.manager.profile = self.root / "profile"
        self.manager.profile_hook = self.root / "profile.d/vix-system.sh"
        self.manager.profile_hook.parent.mkdir()
        self.manager.profile_hook.write_text("# my own PATH hook\n")
        system = self.package_system("packages-foreign", "FIRST", "VIX_PACKAGES_ONE")
        with self.assertRaisesRegex(
            activation.ActivationError, "unmanaged profile hook"
        ):
            with self.manager.locked():
                self.manager.apply(system)
        self.assertEqual(
            self.manager.profile_hook.read_text(), "# my own PATH hook\n"
        )
        self.assertFalse(activation.exists(self.manager.profile))
        self.assertIsNone(self.manager.current_id())
        self.assertEqual(list(self.scan.iterdir()), [])

    def test_empty_generation_stops_only_managed_services(self):
        self.apply("FIRST")
        self.assertEqual(self.apply("EMPTY"), 2)
        self.assertEqual(list(self.scan.iterdir()), [])
        self.assertEqual(self.manager.current_id(), 2)
        self.assertFalse(self.manager.supervised(self.manager.runtime / "worker"))
        self.assert_clean()


if __name__ == "__main__":
    unittest.main(verbosity=2)
