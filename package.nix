{ pkgs }:

pkgs.writeShellApplication {
  name = "vix";
  runtimeInputs = [
    pkgs.nix
    pkgs.coreutils
    pkgs.gnugrep
    pkgs.runit
    pkgs.util-linux
  ];
  text = ''
    ${builtins.readFile ./over/usr/local/bin/vix}
  '';
}
