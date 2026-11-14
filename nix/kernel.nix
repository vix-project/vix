{
  config,
  lib,
  pkgs,
}:

let
  kernel = config.boot.kernelPackages.kernel;
  version = kernel.modDirVersion;
  modules = pkgs.aggregateModules ([ kernel.modules ] ++ config.boot.extraModulePackages);
  firmware =
    if config.hardware.firmware == null || config.hardware.firmware == [ ] then
      pkgs.runCommand "vix-empty-firmware" { } ''mkdir -p "$out/lib/firmware"''
    else
      pkgs.buildEnv {
        name = "vix-firmware";
        paths = config.hardware.firmware;
        pathsToLink = [ "/lib/firmware" ];
      };

  runtime = pkgs.runCommand "vix-boot-runtime-${version}" { } ''
    mkdir -p "$out"
    ln -s ${kernel}/bzImage "$out/kernel"
    ln -s ${modules}/lib/modules "$out/modules"
    ln -s ${firmware}/lib/firmware "$out/firmware"
  '';

  closure = pkgs.makeModulesClosure {
    kernel = modules;
    inherit firmware;
    rootModules = config.boot.initrd.kernelModules;
    allowMissing = false;
  };
  initrdModules =
    pkgs.runCommand "vix-initrd-modules-${version}"
      {
        nativeBuildInputs = [
          pkgs.kmod
          pkgs.xz
          pkgs.gzip
          pkgs.zstd
        ];
      }
      ''
        mkdir -p "$out/lib/modules" "$out/lib/firmware"
        cp -r ${closure}/. "$out/"
        chmod -R u+w "$out"
        cp ${modules}/lib/modules/${version}/modules.builtin* "$out/lib/modules/${version}/"
        cp ${modules}/lib/modules/${version}/modules.order "$out/lib/modules/${version}/"
        find "$out/lib/modules" -name '*.xz' -exec xz -d '{}' \;
        find "$out/lib/modules" -name '*.gz' -exec gzip -d '{}' \;
        find "$out/lib/modules" -name '*.zst' -exec zstd -d --rm '{}' \;
        depmod -b "$out" ${lib.escapeShellArg version}
      '';

  # Runtime modules stay on the root filesystem. Keeping their references out
  # of this script's registered closure avoids copying the full tree into the
  # initramfs; the boot bundle explicitly retains runtime below.
  init =
    (pkgs.writeTextFile {
      name = "vix-init";
      executable = true;
      text = ''
        #!/bin/sh
        export PATH=/bin
        mkdir -p /proc /sys /dev /mnt/root
        mount -t proc proc /proc
        mount -t sysfs sysfs /sys
        mount -t devtmpfs devtmpfs /dev
        exec </dev/console >/dev/console 2>&1
        rescue() { echo "vix: $*"; exec cttyhack sh; }
        for module in ${lib.escapeShellArgs config.boot.initrd.kernelModules}; do
          /kmod/bin/modprobe --config=/dev/null "$module" || rescue "cannot load $module"
        done
        root=
        for argument in $(cat /proc/cmdline); do
          case "$argument" in root=*) root="''${argument#root=}" ;; esac
        done
        [ -n "$root" ] || rescue 'missing root= kernel parameter'
        device=
        for attempt in $(seq 1 100); do
          case "$root" in
            UUID=*|PARTUUID=*|LABEL=*|PARTLABEL=*) device="$(findfs "$root" 2>/dev/null)" ;;
            *) device="$root" ;;
          esac
          [ ! -b "$device" ] || break
          sleep 0.1
        done
        [ -b "$device" ] || rescue "root device not found: $root"
        mount -o rw "$device" /mnt/root || rescue "cannot mount $device"
        chroot /mnt/root /bin/sh -eu -c '
          export PATH=/usr/bin:/usr/sbin:/bin:/sbin
          runtime=$1
          version=$2
          [ -d "$runtime/modules/$version" ] || mount /nix
          mkdir -p "/lib/modules/$version"
          mount --bind "$runtime/modules/$version" "/lib/modules/$version"
          mount -o remount,bind,ro "/lib/modules/$version"
          if [ "$3" = yes ]; then
            mkdir -p /lib/firmware
            mount --bind "$runtime/firmware" /lib/firmware
            mount -o remount,bind,ro /lib/firmware
          fi
          mkdir -p /nix/var/nix/gcroots
          ln -sfn "$runtime" /nix/var/nix/gcroots/.vix-booted-new
          mv -Tf /nix/var/nix/gcroots/.vix-booted-new /nix/var/nix/gcroots/vix-booted
        ' sh ${
          lib.escapeShellArgs [
            (toString runtime)
            version
            (if config.hardware.firmware == null then "no" else "yes")
          ]
        } || rescue 'cannot prepare kernel modules'
        mount --move /proc /mnt/root/proc
        mount --move /sys /mnt/root/sys
        mount --move /dev /mnt/root/dev
        exec switch_root /mnt/root /sbin/init
      '';
    }).overrideAttrs
      { unsafeDiscardReferences.out = true; };

  initrd = pkgs.makeInitrd {
    name = "vix-initrd-${version}";
    contents = [
      {
        object = pkgs.kmod;
        symlink = "/kmod";
      }
      {
        object = "${pkgs.pkgsStatic.busybox}/bin";
        symlink = "/bin";
      }
      {
        object = init;
        symlink = "/init";
      }
      {
        object = "${initrdModules}/lib/modules";
        symlink = "/lib/modules";
      }
      {
        object = "${initrdModules}/lib/firmware";
        symlink = "/lib/firmware";
      }
    ];
  };
  descriptor = pkgs.writeText "vix-boot.json" (
    builtins.toJSON {
      format = 1;
      kernel_version = version;
      inherit runtime;
      kernel = "${runtime}/kernel";
      initrd = "${initrd}/initrd";
      root_device = config.boot.rootDevice;
      kernel_params = config.boot.kernelParams;
    }
  );
in
pkgs.runCommand "vix-boot-${version}" { } ''
  mkdir -p "$out"
  ln -s ${runtime} "$out/runtime"
  ln -s ${runtime}/kernel "$out/kernel"
  ln -s ${initrd}/initrd "$out/initrd"
  ln -s ${descriptor} "$out/boot.json"
''
