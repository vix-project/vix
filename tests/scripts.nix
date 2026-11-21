{
  pkgs ? import <nixpkgs> { },
}:

pkgs.runCommand "vix-script-tests"
  {
    nativeBuildInputs = [ pkgs.dash ];
  }
  ''
    dash -n ${../over/usr/local/bin/vix}
    dash -n ${../over/usr/local/bin/vix-install}
    mkdir -p "$out"
    printf 'passed\n' > "$out/result"
  ''
