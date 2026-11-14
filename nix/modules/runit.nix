{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkEnableOption mkOption types;

  serviceModule = { name, ... }: {
    options = {
      enable = mkEnableOption "the ${name} runit service";

      run = mkOption {
        type = types.lines;
        description = "Service script body. Use exec to run the foreground daemon.";
      };

      finish = mkOption {
        type = types.nullOr types.lines;
        default = null;
        description = "Optional finish script body; runit passes the exit code and signal as arguments.";
      };

      check = mkOption {
        type = types.nullOr types.lines;
        default = null;
        description = "Optional readiness check script body, invoked by sv.";
      };

      environment = mkOption {
        type = types.attrsOf types.str;
        default = { };
        apply =
          environment:
          assert lib.assertMsg (lib.all validVariable (
            builtins.attrNames environment
          )) "services.runit.${name}.environment: names must be valid shell variable names";
          environment;
        description = "Environment variables for run, finish and check (not the logger).";
      };

      log = {
        enable = mkEnableOption "logging for the ${name} service";

        run = mkOption {
          type = types.lines;
          default = ''
            ${pkgs.coreutils}/bin/mkdir -p ${lib.escapeShellArg "/var/log/${name}"}
            exec ${pkgs.runit}/bin/svlogd -tt ${lib.escapeShellArg "/var/log/${name}"}
          '';
          description = "Logger script body. The default writes to /var/log/<service>.";
        };
      };
    };
  };

  validName = name: builtins.match "[a-zA-Z0-9_][a-zA-Z0-9_.-]*" name != null;
  validVariable = name: builtins.match "[a-zA-Z_][a-zA-Z0-9_]*" name != null;

  enabled = lib.filterAttrs (_: service: service.enable) config.services.runit;

  makeScript =
    name: environment: body:
    pkgs.writeShellScript name ''
      ${lib.concatStringsSep "\n" (
        lib.mapAttrsToList (key: value: "export ${key}=${lib.escapeShellArg value}") environment
      )}
      ${body}
    '';

  installScript = name: environment: script: body: ''
    install -m755 ${makeScript "${name}-${script}" environment body} "$out/${name}/${script}"
  '';
in
{
  options = {
    services.runit = mkOption {
      type = types.attrsOf (types.submodule serviceModule);
      default = { };
      apply =
        services:
        assert lib.assertMsg (lib.all validName (builtins.attrNames services))
          "services.runit: invalid service name (use letters, digits, underscores, dots and hyphens; start with a letter, digit or underscore)";
        services;
      description = ''
        Runit services. Names must start with a letter, digit or underscore and
        contain only letters, digits, underscores, dots and hyphens.
        Only enabled services are included in the generated directory.
      '';
    };

    system.build.services = mkOption {
      type = types.package;
      readOnly = true;
      description = "Immutable runit service templates; copy to a writable directory before running.";
    };
  };

  config.system.build.services = pkgs.runCommand "vix-runit-services" { } ''
    mkdir -p "$out"
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (name: service: ''
        mkdir -p "$out/${name}"
        ${installScript name service.environment "run" (
          lib.optionalString service.log.enable "exec 2>&1\n" + service.run
        )}
        ${lib.optionalString (service.finish != null) (
          installScript name service.environment "finish" service.finish
        )}
        ${lib.optionalString (service.check != null) (
          installScript name service.environment "check" service.check
        )}
        ${lib.optionalString service.log.enable ''
          mkdir -p "$out/${name}/log"
          ${installScript name { } "log/run" service.log.run}
        ''}
      '') enabled
    )}
  '';
}
