{
  pkgs ? import <nixpkgs> { },
}:

pkgs.linkFarm "vix-tests" [
  {
    name = "boot";
    path = import ./boot.nix { inherit pkgs; };
  }
  {
    name = "services";
    path = import ./services.nix { inherit pkgs; };
  }
  {
    name = "activation";
    path = import ./activation.nix { inherit pkgs; };
  }
]
