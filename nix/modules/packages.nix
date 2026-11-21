{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;
in
{
  options = {
    environment.systemPackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = ''
        Packages installed in the system profile. `vix switch` atomically
        exposes them through <nix/var/nix/profiles/vix-system>; `vix rollback`
        restores the packages of an earlier generation.
      '';
    };

    system.build.packages = mkOption {
      type = types.package;
      readOnly = true;
      description = "Combined package environment for the active generation.";
    };
  };

  config.system.build.packages = pkgs.buildEnv {
    name = "vix-system-packages";
    paths = config.environment.systemPackages;
    pathsToLink = [
      "/bin"
      "/include"
      "/lib"
      "/libexec"
      "/sbin"
      "/share"
    ];
    extraOutputsToInstall = [
      "out"
      "man"
      "dev"
      "info"
    ];
    # Overrides are common in a system profile (for example busybox or a
    # custom coreutils); later packages take precedence deterministically.
    ignoreCollisions = true;
  };
}
