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

  host = evaluate [
    {
      environment.systemPackages = [
        pkgs.hello
        pkgs.ripgrep
      ];
    }
  ];
  merged = evaluate [
    { environment.systemPackages = [ pkgs.hello ]; }
    { environment.systemPackages = [ pkgs.jq ]; }
  ];
  empty = evaluate [ ];
  invalid = builtins.tryEval (
    (evaluate [ { environment.systemPackages = [ "not-a-package" ]; } ])
    .config.system.build.packages.drvPath
  );
in
assert
  host.config.environment.systemPackages == [
    pkgs.hello
    pkgs.ripgrep
  ];
assert lib.elem pkgs.hello merged.config.environment.systemPackages;
assert lib.elem pkgs.jq merged.config.environment.systemPackages;
assert lib.length merged.config.environment.systemPackages == 2;
assert !invalid.success;
pkgs.runCommand "vix-packages-tests"
  {
    nativeBuildInputs = [
      pkgs.coreutils
      pkgs.gnugrep
    ];
  }
  ''
    set -eu
    mkdir -p "$out"

    test -x ${host.config.system.build.packages}/bin/hello
    test -x ${host.config.system.build.packages}/bin/rg
    ${host.config.system.build.packages}/bin/hello > "$out/hello.txt"
    grep -F 'Hello, world!' "$out/hello.txt"

    # The toplevel exposes the environment for activation.
    test "$(readlink -f ${host.config.system.build.toplevel}/packages)" = \
      "$(readlink -f ${host.config.system.build.packages})"

    # Multiple modules merge; an empty declaration builds an empty profile.
    test -x ${merged.config.system.build.packages}/bin/hello
    test -x ${merged.config.system.build.packages}/bin/jq
    test ! -e ${empty.config.system.build.packages}/bin/hello
    test ! -e ${empty.config.system.build.packages}/bin/jq

    printf 'passed\n' > "$out/result"
  ''
