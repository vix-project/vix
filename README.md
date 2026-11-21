Vix is a very secure Linux distribution which uses Void rootfs and Nix package manager.

## How do I install?
1. Clone the repo
2. Type `just metal`, you will get `vix.iso` shortly.
3. Flash the ISO to your memory stick
4. Boot it up
5. Login as root:vix, partition your drive.
6. Mount your root to `/mnt` and ESP (for EFI) to `/mnt/boot`
7. Run `vix-install /mnt [optionally blockdev for BIOS legacy installation]`.
   The installer creates `/mnt/etc/vix/flake.nix` with the target root UUID,
   builds it, and seeds the first declarative generation. An existing machine
   flake on the target is reused.
8. Optionally edit the machine flake before rebooting. After boot, use `vix switch`
   for later changes.
9. Reboot and profit

## Just
Available recipes:
- clean -- clean workdir from build artifacts
- getroot -- download Void rootfs
- merge -- merge Void rootfs and `over` overlay to `build` directory
- install-nix -- install Nix PM under chroot to `build`
- base -- install needed XBPS packages under chroot to `build`
- remove-xbps -- Remove XBPS PM. Optional one, never called automatically
- iso -- make an ISO
- metal -- make an ISO with some firmware stuff baked

## Downsides of Vix
- xbps is used for PAM stack and some other system-deep things (PM can be removed, but not the packages)
- Nix profiles are Nix profiles. Sometimes you need tinkering
- Nixpkgs provide no runit service definitions; Vix services are declared with [its own modules](docs/runit.md).

## Configure a running Vix machine

Install the helper through Nix, then create the configuration on the Vix machine:

```sh
nix profile add github:vix-project/vix#vix
vix init
# Edit /etc/vix/flake.nix to declare services and optional kernel settings.
vix switch
vix status
```

`vix init` creates `/etc/vix/flake.nix` with a `vixConfigurations.default`
configuration built by `vix.lib.vixSystem`. `vix switch /etc/vix#default` builds
that flake and starts, updates or removes its declared runit services on the
already booted machine. Rebuilding an ISO is not required.

System packages are declared in the same flake and installed into a managed
profile:

```nix
environment.systemPackages = with pkgs; [ git htop ripgrep ];
```

After `vix switch`, open a new login shell to use them. The package environment
is part of the generation, so `vix rollback` restores it together with services.

Kernels are optional and declarative: set `boot.kernel.enable = true`, select
`boot.kernelPackages`, and declare `boot.extraModulePackages` and
`hardware.firmware`. The matching initramfs and GRUB entries are generated
together; the selected kernel takes effect at reboot. Existing kernels remain
available for rollback. The old imperative `kernel`, `kmod` and `firmware`
commands are replaced by these settings.

`vix generations` lists saved configurations; `vix rollback` restores the
previous one. Existing unmanaged Void services remain independent.

The project uses flake-parts, with locked nixpkgs inputs. See
[the configuration guide](docs/runit.md) for the machine flake format, options,
activation, kernel migration and tests.
