{ pkgs }:

let
  host = (import ../lib/vix-system.nix { nixpkgs = pkgs.path; }) {
    inherit pkgs;
    modules = [
      ({ config, ... }: {
        boot.kernel.enable = true;
        boot.kernelPackages = pkgs.linuxPackages_6_12;
        boot.rootDevice = "/dev/vda";
        boot.extraModulePackages = [ config.boot.kernelPackages.v4l2loopback ];
        boot.initrd.kernelModules = [ "v4l2loopback" ];
        hardware.firmware = [ pkgs.wireless-regdb ];
      })
    ];
  };
in
pkgs.runCommand "vix-kernel-tests"
  {
    nativeBuildInputs = [
      pkgs.python3Minimal
      pkgs.kmod
      pkgs.gzip
      pkgs.cpio
    ];
    VIX_BOOT = host.config.system.build.boot;
  }
  ''
    python3 ${./kernel.py}
    mkdir -p "$out"
    printf 'passed\n' > "$out/result"
  ''
