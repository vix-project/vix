{
  description = "Vix: declarative services and kernels on a running Void-based system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
  };

  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" ];

      perSystem = { pkgs, ... }: {
        packages.vix = import ./package.nix { inherit pkgs; };
        packages.default = import ./package.nix { inherit pkgs; };
        checks.services = import ./tests/services.nix { inherit pkgs; };
        checks.packages = import ./tests/packages.nix { inherit pkgs; };
        checks.scripts = import ./tests/scripts.nix { inherit pkgs; };
        checks.activation = import ./tests/activation.nix { inherit pkgs; };
        checks.boot = import ./tests/boot.nix { inherit pkgs; };
        checks.kernel = import ./tests/kernel.nix { inherit pkgs; };
        formatter = pkgs.nixfmt;
      };

      flake.lib.vixSystem = import ./lib/vix-system.nix {
        inherit (inputs) nixpkgs;
      };
    };
}
