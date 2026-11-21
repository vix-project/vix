# Configure a running Vix machine

Service declarations live on the Vix machine, in `/etc/vix/flake.nix`.
The `vix` helper builds that flake and manages its services through the running
runit instance. Kernel, module and firmware selections use the same declaration
and generation history. The project is built with flake-parts.

## Install and initialize

`vix-install` initializes `/etc/vix/flake.nix` on a new target with the target
root UUID, builds it, and seeds the first declarative generation before the
first boot. If the target already has a machine flake, the installer reuses it
instead of overwriting it. After boot, later changes use `vix switch`. The
first build resolves the flake inputs, so network access or a cached
`VIX_FLAKE_URL` is needed; for an unmerged branch set `VIX_FLAKE_URL` as
described below.

On an already booted Vix system that was installed without a machine flake:

```sh
nix profile add github:vix-project/vix#vix
vix init
```

Open a new login shell if the Nix profile's `bin` directory is not on `PATH`.
`vix init` creates a machine flake and records the current root filesystem UUID.
It refuses to overwrite an existing flake. It does not enable any service or
replace the current kernel. For another configuration directory use
`vix init /path/to/config`. `VIX_ROOT_DEVICE` overrides the detected UUID; the
installer uses this to generate the target's flake.

Edit the generated flake on the Vix machine, for example:

```nix
{
  inputs.vix.url = "github:vix-project/vix";

  outputs = { vix, ... }: {
    vixConfigurations.default = vix.lib.vixSystem {
      system = "x86_64-linux";
      modules = [ ({ pkgs, ... }: {
        services.runit.hello = {
          enable = true;
          run = ''
            exec ${pkgs.python3}/bin/python -m http.server 8080 \
              --bind 127.0.0.1 --directory /srv/public
          '';
          log.enable = true;
        };
      }) ];
    };
  };
}
```

Prepare any runtime data such as `/srv/public`, then apply:

```sh
vix switch
vix status
```

The first build writes `flake.lock`; retain it with the machine configuration.
To update its inputs explicitly, use `nix flake update --flake /etc/vix`, then
`vix switch`. Package and kernel choices come from the flake's locked nixpkgs,
independent of the host's channels or registry.

Rebuilding or reinstalling an ISO is not required. First activation registers
`/etc/runit/core-services/99-vix.sh` on the running machine for boot selection
and recovery. It refuses to replace an unrelated file at that location.

## Commands and flake API

```sh
vix build                            # Build /etc/vix#default as ./result.
vix switch                           # Build and apply /etc/vix#default.
vix switch /etc/vix#laptop            # Select a named configuration.
vix switch github:you/machine#laptop  # Use another machine flake.
vix status                           # Service status and pending kernel changes.
vix generations                      # Retained generations; * marks the current one.
vix rollback                         # Activate the nearest lower numbered generation.
vix rollback 3                       # Activate a particular retained generation.
vix recover                          # Restore an interrupted activation.
```

The helper resolves `FLAKE#HOST` to
`vixConfigurations.HOST.config.system.build.toplevel`. Host names contain
letters, digits, underscores and hyphens. It accepts a flake reference rather
than an individual `configuration.nix` file. Local modules can still be split
into files and listed in the machine flake's `modules`.

`vix.lib.vixSystem` accepts `system`, `modules`, optional `pkgs`, and
`specialArgs`. It returns `config`, `options` and `pkgs`, using `lib.evalModules`
without NixOS or systemd modules. Modules receive `pkgs`, `lib` and `config`
and support `imports`, `mkDefault`, `mkForce`, `mkBefore` and `mkAfter`.

Activation requires root on an already booted Vix machine in runit's `default`
runlevel. `vix build` can run without root and does not activate anything.

## Install packages

Declare system packages in a machine module:

```nix
{ pkgs, ... }: {
  environment.systemPackages = with pkgs; [
    git
    htop
    ripgrep
  ];
}
```

Then apply the machine flake:

```sh
vix switch
# Open a new login shell to pick up the updated PATH.
```

`vix switch` builds a combined package environment and atomically points
`/nix/var/nix/profiles/vix-system` at it. It also maintains
`/etc/profile.d/vix-system.sh`, which prepends the profile's `bin` and `sbin`
directories to `PATH`. Existing login shells keep their old `PATH`; open a new
shell or source the profile hook.

Packages are part of the generation. `vix rollback` restores both the services
and the package environment of the selected generation, and the generation's
Nix closure stays rooted. Removing a package from the list and switching
removes it from the profile. Package collisions are resolved deterministically
by the build environment, so a later package can override an earlier one.

The profile is independent of Nix's per-user profiles and of the
`vix-network` profile used by the base image. Use `environment.systemPackages`
for packages that should be available system-wide; per-user `nix profile add`
remains available for personal tools. Declared services receive the same
package environment on `PATH`; set `services.runit.<name>.environment.PATH` to
override it.

## Runit options

For each `services.runit.<name>`:

| Option | Default | Meaning |
| --- | --- | --- |
| `enable` | `false` | Include and activate the service. |
| `run` | Required when enabled | Foreground service script body; use `exec`. |
| `finish` | `null` | Script run after exit; receives runit's exit code and signal as `$1` and `$2`. |
| `check` | `null` | Readiness check used by `sv`; exit zero when ready. |
| `environment` | `{}` | String values exported for `run`, `finish` and `check`. |
| `log.enable` | `false` | Generate a logger and redirect service stderr to stdout. |
| `log.run` | `svlogd -tt /var/log/<name>` | Logger script body; the default creates the log directory. |

Script bodies use the nixpkgs runtime shell; omit a shebang. Use store paths
such as `${pkgs.python3}/bin/python` to retain package dependencies. Environment
values are shell-escaped. Keys must be shell variable names. The logger
inherits its supervisor's environment without the service's overrides.

Names start with a letter, digit or underscore and contain only letters,
digits, underscores, dots and hyphens. Disabled services need no `run` script.
Services run as the supervisor's user, from their writable service directory
(or `log` for the logger). Accounts, privilege dropping, daemon configuration
files and service dependency ordering remain the administrator's responsibility.

Vix refuses to replace unmanaged runit entries, including existing Void
services. Managed directories live under `/var/lib/vix/services`, with links
from `/etc/runit/runsvdir/default`; `/etc/sv` is unchanged. Removed services'
runtime directories are retained. Keep daemon data in a dedicated data
directory rather than beside generated scripts.

New services start, removed services stop, changed services and their loggers
restart, and unchanged services keep their processes. Every enabled service
must be running and pass its optional `check` before the generation commits.
Without `check`, this confirms only that its process is running.
`VIX_SWITCH_TIMEOUT` sets the per-service timeout (default 30 seconds, range
1–3600). `vix status` reads runit status without restarting services.

## Declare a kernel

Kernel management is opt-in. In a machine module:

```nix
{ pkgs, config, ... }: {
  boot.kernel.enable = true;
  boot.rootDevice = "UUID=your-root-filesystem-uuid";
  boot.kernelPackages = pkgs.linuxPackages;
  boot.extraModulePackages = [];
  boot.kernelParams = [ "quiet" ];
  # Optional: select an extra driver from this same kernel package set.
  # boot.extraModulePackages = [ config.boot.kernelPackages.v4l2loopback ];
  # Optional: declare the firmware needed by this machine.
  # hardware.firmware = [ pkgs.linux-firmware pkgs.wireless-regdb ];
}
```

Use the root device recorded by `vix init`. `boot.kernelPackages` chooses the
kernel and the matching package set for extra modules. Inconsistent module
versions fail during the Nix build. `boot.initrd.kernelModules` adds modules to
the default storage and filesystem drivers; dependencies and required declared
firmware are included in the matching initramfs. Use `lib.mkForce` only when
intentionally replacing that default list.

`hardware.firmware = null` preserves the existing firmware directory. A list
selects its complete firmware tree; an empty list selects an empty tree.
Firmware and extra modules require `boot.kernel.enable`. If a boot device needs
firmware, declare its package so it can be included before the root filesystem
is mounted. Encrypted and network root setup is outside this module's scope.

Kernel management uses the existing GRUB installation at
`/boot/grub/grub.cfg`. It does not install GRUB or partition disks. The first
adoption saves the original configuration as `grub.cfg.vix-original` and keeps
a menu entry for it. Complete kernel/initramfs pairs are copied to
`/boot/vix/kernels`; one atomic GRUB configuration replacement selects them.
Both separate `/boot` filesystems and `/boot` on the root filesystem are supported.

New kernels take effect at reboot. The initramfs exposes the selected kernel's
module tree and optional firmware as read-only bind mounts before normal init.
The currently running kernel's modules are not replaced during `vix switch`.
GC roots protect the actually booted runtime as well as retained generations.
Selecting a retained GRUB generation selects its saved service declarations
before runsvdir starts. `vix rollback` also restores the boot selection; reboot
to use its kernel. Returning to a generation without kernel management restores
the original GRUB configuration.

The imperative `vix kernel`, `vix kmod` and `vix firmware` commands have been
replaced by these flake options.

## Failure recovery and development

Builds and collision checks complete before changing services. Activations use
a process-held lock. A failed startup, readiness check or boot configuration
update restores the previous service configuration and boot selection. This
cannot undo changes a daemon made to external data.

Generation closures are rooted in `/nix/var/nix/gcroots/vix`. A durable journal
retains pending dependencies if interrupted. The next switch recovers it first;
the runtime-installed boot hook also recovers before runsvdir starts. If stopping
a service or recovery fails, Vix preserves the journal and reports the error.
At boot the hook opens a repair shell and retries after it exits.

Build and check the project with:

```sh
nix build .#vix
nix flake check
```

Checks use private runit supervisors for updates, readiness failures, rollback,
concurrency, interruption recovery and boot selection. Boot configuration tests
also run `grub-script-check`. The kernel check builds a Linux 6.12 fixture with
`v4l2loopback` to verify module compatibility and initramfs contents; neither
is a default system requirement. Checks do not modify host services or boot files.

When testing an unmerged branch on a running Vix machine, install with
`nix profile add .#vix` and set `VIX_FLAKE_URL` to that branch's flake reference
when running `vix init`.
