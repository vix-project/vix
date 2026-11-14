{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkEnableOption mkOption types;
in
{
  options = {
    boot.kernel.enable = mkEnableOption "declarative kernel, initramfs and GRUB generations";
    boot.kernelPackages = mkOption {
      type = types.raw;
      default = pkgs.linuxPackages;
      description = "Kernel package set; use the same set for extra module packages.";
    };
    boot.extraModulePackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = "Additional modules built for boot.kernelPackages.kernel.";
    };
    boot.rootDevice = mkOption {
      type = types.nullOr (types.strMatching "[a-zA-Z0-9_./=:+-]+");
      default = null;
      description = "Root device, for example UUID=...; required when kernel management is enabled.";
    };
    boot.kernelParams = mkOption {
      type = types.listOf (types.strMatching "[^\n\r]+");
      default = [ "quiet" ];
      apply =
        parameters:
        assert lib.assertMsg (lib.all (
          parameter: !(lib.hasPrefix "root=" parameter || lib.hasPrefix "vix.generation=" parameter)
        ) parameters) "root= and vix.generation= are reserved kernel parameters";
        parameters;
      description = "Additional command-line parameters for the declared kernel.";
    };
    boot.initrd.kernelModules = mkOption {
      type = types.listOf (types.strMatching "[a-zA-Z0-9_-]+");
      default = [ ];
      description = "Modules included in and loaded by the initramfs, with their dependencies.";
    };
    hardware.firmware = mkOption {
      type = types.nullOr (types.listOf types.package);
      default = null;
      description = "Firmware packages; null preserves the existing firmware directory.";
    };
    system.build.boot = mkOption {
      type = types.nullOr types.package;
      readOnly = true;
      description = "Declared kernel, module tree, firmware and matching initramfs.";
    };
  };

  config.boot.initrd.kernelModules = lib.mkBefore [
    "ext4"
    "btrfs"
    "xfs"
    "vfat"
    "ahci"
    "sd_mod"
    "nvme"
    "virtio_pci"
    "virtio_blk"
    "virtio_scsi"
    "usb_storage"
    "xhci_pci"
  ];

  config.system.build.boot =
    assert lib.assertMsg (
      config.boot.kernel.enable
      || (config.boot.extraModulePackages == [ ] && config.hardware.firmware == null)
    ) "boot.extraModulePackages and hardware.firmware require boot.kernel.enable";
    if config.boot.kernel.enable then
      assert lib.assertMsg (
        config.boot.rootDevice != null
      ) "boot.rootDevice must be declared when boot.kernel.enable is true";
      import ../kernel.nix { inherit config lib pkgs; }
    else
      null;
}
