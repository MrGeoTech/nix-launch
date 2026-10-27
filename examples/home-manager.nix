# A home-manager setup using everything nix-launch offers. Everything below
# programs.launch is optional; `programs.launch.enable = true` alone gives
# you the `launch` command, the zsh/bash command-not-found hook, the login
# indexing service, and the rofi mode if programs.rofi is enabled.
#
# flake.nix:
#   inputs.nix-launch.url = "github:<you>/nix-launch";
#   inputs.nix-launch.inputs.nixpkgs.follows = "nixpkgs";
#   inputs.nix-index-database.url = "github:nix-community/nix-index-database";
#   inputs.nix-index-database.inputs.nixpkgs.follows = "nixpkgs";
{
  inputs,
  pkgs,
  ...
}: let
  onDemand = inputs.nix-launch.lib.mkOnDemandApp pkgs;
in {
  imports = [
    inputs.nix-launch.homeManagerModules.default
    # Prebuilt nix-locate database. Without it, run `nix-index` once yourself.
    inputs.nix-index-database.homeModules.nix-index
  ];

  programs.launch = {
    enable = true;
    terminal = "ghostty";
    # colors.accent = "#89b4fa";
    # nixpkgs = "github:NixOS/nixpkgs/nixos-unstable";
  };

  # Commands that are always on PATH but only fetched on first use. They
  # show up in the launcher as "pinned" and run without a confirm screen.
  home.packages = [
    (onDemand {attr = "kicad";})
    (onDemand {attr = "obs-studio"; bin = "obs";})
    (onDemand {attr = "libreoffice-fresh"; bin = "libreoffice";})
  ];

  programs.rofi = {
    enable = true;
    # The theme in ./rofi; copy it next to your config.
    theme = "~/.config/rofi/launcher.rasi";
  };
  xdg.configFile."rofi/launcher.rasi".source = ./rofi/launcher.rasi;
  xdg.configFile."rofi/colors.rasi".source = ./rofi/colors.rasi;
}
