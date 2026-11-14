{
  pkgs ? import <nixpkgs> { },
}:

let
  service = name: value: {
    enable = true;
    run = ''
      printf '%s\n' ${pkgs.lib.escapeShellArg value} > "$VIX_TEST_DIR/${name}.value"
      printf '%s\n' ${pkgs.lib.escapeShellArg value}
      exec ${pkgs.coreutils}/bin/sleep 3600
    '';
    check = ''
      test "$(${pkgs.coreutils}/bin/cat "$VIX_TEST_DIR/${name}.value")" = ${pkgs.lib.escapeShellArg value}
    '';
  };
  logged =
    value:
    (service "worker" value)
    // {
      log.enable = true;
      log.run = ''
        exec ${pkgs.coreutils}/bin/cat >> "$VIX_TEST_DIR/worker.log"
      '';
    };
  build =
    services:
    ((import ../lib/vix-system.nix { nixpkgs = pkgs.path; }) {
      inherit pkgs;
      modules = [ { services.runit = services; } ];
    }).config.system.build.toplevel;
  stable = service "stable" "unchanged";
  first = build {
    worker = logged "one";
    inherit stable;
  };
  second = build {
    worker = logged "two";
    inherit stable;
    added = service "added" "new";
  };
  noLogger = build {
    worker = service "worker" "three";
    inherit stable;
  };
  bad = build {
    worker = (logged "bad") // {
      check = "exit 1";
    };
    inherit stable;
    added = service "added" "new";
  };
  collision = build { foreign = service "foreign" "must not start"; };
  empty = build { };
in
pkgs.runCommand "vix-activation-tests"
  {
    nativeBuildInputs = [
      pkgs.python3Minimal
      pkgs.runit
    ];
    VIX_ACTIVATION_SOURCE = "${import ../nix/activation-library.nix { inherit pkgs; }}/activate.py";
    VIX_FIRST = first;
    VIX_SECOND = second;
    VIX_NO_LOGGER = noLogger;
    VIX_BAD = bad;
    VIX_COLLISION = collision;
    VIX_EMPTY = empty;
  }
  ''
    python3 ${./activation.py}
    mkdir -p "$out"
    printf 'passed\n' > "$out/result"
  ''
