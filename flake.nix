{
  description = "App launcher over installed apps and all of nixpkgs, with a confirm-before-fetch flow (terminal + rofi)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = {
    self,
    nixpkgs,
  }: let
    systems = ["x86_64-linux" "aarch64-linux"];
    forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
  in {
    packages = forAllSystems (pkgs: {
      default = pkgs.callPackage ./nix/package.nix {};
      # Boot image for the microvm backend (only built if something uses it).
      microvm-guest = pkgs.callPackage ./nix/microvm-guest.nix {};
    });

    overlays.default = final: _prev: {
      nix-launch = final.callPackage ./nix/package.nix {};
    };

    homeManagerModules.default = import ./nix/hm-module.nix self;
    homeModules.default = self.homeManagerModules.default;

    lib.mkOnDemandApp = import ./nix/on-demand-app.nix;

    checks = forAllSystems (pkgs: {
      package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      on-demand-app = self.lib.mkOnDemandApp pkgs {attr = "hello";};
    });

    formatter = forAllSystems (pkgs: pkgs.alejandra);
  };
}
