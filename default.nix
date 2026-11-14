{
  pkgs ? import <nixpkgs> { },
  modules ? [ ],
}:

let
  evaluated = pkgs.lib.evalModules {
    specialArgs = { inherit pkgs; };
    modules = [ ./nix/modules/runit.nix ] ++ modules;
  };
in
{
  inherit (evaluated) config options;
  services = evaluated.config.system.build.services;
}
