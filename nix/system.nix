{
  pkgs,
  services,
  packages ? null,
  boot ? null,
}:

pkgs.runCommand "vix-system" { } ''
  mkdir -p "$out/bin"
  ln -s ${services} "$out/services"
  ${pkgs.lib.optionalString (packages != null) ''ln -s ${packages} "$out/packages"''}
  ${pkgs.lib.optionalString (boot != null) ''ln -s ${boot} "$out/boot"''}
  printf '{"format":1}\n' > "$out/vix-system.json"
  cat > "$out/bin/vix-activate" <<'EOF'
  #!${pkgs.runtimeShell}
  exec ${pkgs.python3Minimal}/bin/python3 ${
    import ./activation-library.nix { inherit pkgs; }
  }/activate.py "$@"
  EOF
  chmod +x "$out/bin/vix-activate"
''
