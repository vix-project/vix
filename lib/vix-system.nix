{ nixpkgs }:
{
  system ? "x86_64-linux",
  pkgs ? import nixpkgs { inherit system; },
  modules ? [ ],
  specialArgs ? { },
}:

(pkgs.lib.evalModules {
  specialArgs = specialArgs // {
    inherit pkgs;
  };
  modules = [
    ../nix/modules/packages.nix
    ../nix/modules/runit.nix
    ../nix/modules/kernel.nix

  ]
  ++ modules;
})
// {
  inherit pkgs;
}
