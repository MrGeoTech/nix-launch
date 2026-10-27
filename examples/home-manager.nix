# A home-manager setup using most of what nix-launch offers. Everything under
# programs.launch is optional; defining any `apps` (or `enable = true`) gives
# you the `launch` command, the zsh/bash command-not-found hook, the login
# indexing service, and the rofi mode if programs.rofi is enabled.
#
# flake.nix:
#   inputs.nix-launch.url = "github:<you>/nix-launch";
#   inputs.nix-launch.inputs.nixpkgs.follows = "nixpkgs";
#   inputs.nix-index-database.url = "github:nix-community/nix-index-database";
#   inputs.nix-index-database.inputs.nixpkgs.follows = "nixpkgs";
{inputs, ...}: {
  imports = [
    inputs.nix-launch.homeManagerModules.default
    # Prebuilt nix-locate database. Without it, run `nix-index` once yourself.
    inputs.nix-index-database.homeModules.nix-index
  ];

  programs.launch = {
    terminal = "ghostty";
    # colors.accent = "#89b4fa";
    # nixpkgs = "github:NixOS/nixpkgs/nixos-unstable";

    # A built-in profile plus the folders work lives in.
    profiles.files = {
      inherits = ["default"];
      paths = {
        "~/Projects" = "rw";
        "~/Documents" = "rw";
      };
    };

    # Commands that are always on PATH but only fetched on first use. They
    # show up in the launcher as "pinned" and run without a confirm screen.
    apps = {
      vlc.access.args = "ro"; # only the file it was asked to play
      kicad.profile = "files";
      libreoffice = {
        attr = "libreoffice-fresh";
        profile = "files";
      };
      # Screen capture needs portals, which bwrap can't host.
      obs = {
        attr = "obs-studio";
        backend = "none";
      };
      ncdu.backend = "none"; # needs to see the real disk
    };
  };

  programs.rofi = {
    enable = true;
    # The theme in ./rofi; copy it next to your config.
    theme = "~/.config/rofi/launcher.rasi";
  };
  xdg.configFile."rofi/launcher.rasi".source = ./rofi/launcher.rasi;
  xdg.configFile."rofi/colors.rasi".source = ./rofi/colors.rasi;
}
