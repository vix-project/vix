{ pkgs }:

pkgs.runCommand "vix-activation-code" { } ''
  mkdir -p "$out"
  install -m644 ${./activate.py} "$out/activate.py"
  install -m644 ${./boot.py} "$out/boot.py"
''
