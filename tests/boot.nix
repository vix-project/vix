{
  pkgs ? import <nixpkgs> { },
}:

let
  code = import ../nix/activation-library.nix { inherit pkgs; };
in
pkgs.runCommand "vix-boot-tests"
  {
    nativeBuildInputs = [
      pkgs.python3Minimal
      pkgs.grub2
    ];
    PYTHONPATH = code;
  }
  ''
    python3 ${./boot.py}
    mkdir -p "$out"
    printf 'passed\n' > "$out/result"
  ''
