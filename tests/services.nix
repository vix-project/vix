{
  pkgs ? import <nixpkgs> { },
}:

let
  inherit (pkgs) lib;
  evaluate =
    modules:
    (import ../lib/vix-system.nix { nixpkgs = pkgs.path; }) {
      inherit pkgs modules;
    };
  build = modules: (evaluate modules).config.system.build.services;
  succeeds = modules: (builtins.tryEval (build modules).drvPath).success;

  service = {
    services.runit.worker = {
      enable = true;
      environment.MESSAGE = "hello 'quoted' $world `literal`\nsecond line";
      run = ''
        printf '%s\n' "$MESSAGE"
        printf '%s\n' 'stderr captured' >&2
        exec ${pkgs.coreutils}/bin/sleep 60
      '';
      check = ''
        ${pkgs.gnugrep}/bin/grep -q 'stderr captured' "$VIX_TEST_LOG"
      '';
      finish = ''
        printf '%s %s\n' "$1" "$2" > "$VIX_TEST_FINISH"
      '';
      log = {
        enable = true;
        run = ''
          exec ${pkgs.coreutils}/bin/cat > "$VIX_TEST_LOG"
        '';
      };
    };
    # Disabled services do not need a run script and must not be emitted.
    services.runit.disabled.enable = false;
  };

  generated = build [ service ];
  expectedOutput = pkgs.writeText "worker-output" (
    service.services.runit.worker.environment.MESSAGE + "\nstderr captured\n"
  );
  minimal = build [
    {
      services.runit.minimal = {
        enable = true;
        run = "exit 0";
      };
    }
  ];
  empty = build [ ];
  defaultLogger = build [
    {
      services.runit.logged = {
        enable = true;
        run = "exit 0";
        log.enable = true;
      };
    }
  ];

  merged = evaluate [
    {
      services.runit.merged = {
        enable = lib.mkDefault false;
        run = lib.mkBefore "echo before";
      };
    }
    {
      services.runit.merged = {
        enable = true;
        run = "exec sleep 1";
      };
    }
  ];
in
assert merged.config.services.runit.merged.run == "echo before\nexec sleep 1";
assert merged.config.services.runit.merged.enable;
assert
  !(succeeds [
    {
      services.runit.bad = {
        enable = true;
        run = 42;
      };
    }
  ]);
assert !(succeeds [ { services.runit.bad.enable = true; } ]);
assert
  !(succeeds [
    {
      services.runit."../escape" = {
        enable = true;
        run = "exit 0";
      };
    }
  ]);
assert
  !(succeeds [
    {
      services.runit."bad\nname" = {
        enable = true;
        run = "exit 0";
      };
    }
  ]);
assert
  !(succeeds [
    {
      services.runit.bad = {
        enable = true;
        run = "exit 0";
        environment."INVALID-NAME" = "x";
      };
    }
  ]);
assert
  !(succeeds [
    {
      services.runit.bad = {
        enable = true;
        run = "exit 0";
        typo = true;
      };
    }
  ]);
pkgs.runCommand "vix-runit-tests"
  {
    nativeBuildInputs = [
      pkgs.runit
      pkgs.gnugrep
      pkgs.diffutils
    ];
  }
  ''
    set -eu
    test -z "$(ls -A ${empty})"
    test ! -e ${generated}/disabled
    test -x ${minimal}/minimal/run
    test ! -e ${minimal}/minimal/finish
    test ! -e ${minimal}/minimal/check
    test ! -e ${minimal}/minimal/log
    test -x ${defaultLogger}/logged/log/run
    grep -F '${pkgs.runit}/bin/svlogd' ${defaultLogger}/logged/log/run

    # Runit writes supervise state beside each script, so use a writable copy.
    cp -R ${generated}/worker "$TMPDIR/worker"
    chmod -R u+w "$TMPDIR/worker"
    export VIX_TEST_LOG="$TMPDIR/service.log"
    export VIX_TEST_FINISH="$TMPDIR/finish.status"
    runsv "$TMPDIR/worker" > "$TMPDIR/runsv.log" 2>&1 &
    supervisor=$!
    trap 'kill "$supervisor" 2>/dev/null || true' EXIT

    # Wait for runsv to publish its control FIFO before invoking sv.
    for attempt in $(seq 1 100); do
      test ! -p "$TMPDIR/worker/supervise/control" || break
      sleep 0.1
    done
    sv -w 10 up "$TMPDIR/worker"
    diff -u ${expectedOutput} "$VIX_TEST_LOG"

    sv -w 10 down "$TMPDIR/worker"
    grep -Fx -- '-1 15' "$VIX_TEST_FINISH"
    sv -w 10 exit "$TMPDIR/worker"
    wait "$supervisor"
    trap - EXIT

    mkdir -p "$out"
    cp "$VIX_TEST_LOG" "$VIX_TEST_FINISH" "$out/"
  ''
