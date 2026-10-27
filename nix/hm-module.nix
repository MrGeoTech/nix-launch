self: {
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.programs.launch;
  inherit (lib) mkOption mkEnableOption mkIf types;

  launch = cfg.package.override {
    nix-index = cfg.nixIndexPackage;
    settings = {
      inherit (cfg) nixpkgs iconThemes colors;
      terminal = cfg.terminal;
    };
  };

  # Hand an unknown command to `launch run`, which shows what would run and
  # asks before fetching anything. The command only travels through argv,
  # and non-interactive shells (scripts) keep failing normally.
  handler = fn: ''
    ${fn}() {
      if [ -n "''${1:-}" ] && [ -t 0 ]; then
        ${lib.getExe launch} run -- "$@"
        return $?
      fi
      echo "''${1:-command}: command not found" >&2
      return 127
    }
  '';
in {
  options.programs.launch = {
    enable = mkEnableOption "launch, an app launcher over installed apps and all of nixpkgs";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "nix-launch.packages.\${system}.default";
      description = "The launch package (before the options below are applied).";
    };

    finalPackage = mkOption {
      type = types.package;
      readOnly = true;
      default = launch;
      description = "The launch package with this module's settings applied.";
    };

    nixIndexPackage = mkOption {
      type = types.package;
      default = config.programs.nix-index.package;
      defaultText = lib.literalExpression "config.programs.nix-index.package";
      description = ''
        Provides `nix-locate`, which needs a database. Plain nix-index only
        works after you've run `nix-index` yourself; nix-index-database's
        `nix-index-with-db` (set automatically by its home-manager module)
        ships one.
      '';
    };

    nixpkgs = mkOption {
      type = types.str;
      default = "nixpkgs";
      example = "github:NixOS/nixpkgs/nixos-unstable";
      description = ''
        Flake that uninstalled packages come from (`nix shell <this>#attr`).
        The default registry entry is best when your system pins it to the
        nixpkgs it was built from (`nix.registry.nixpkgs.flake = inputs.nixpkgs`).
      '';
    };

    terminal = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "ghostty";
      description = "Terminal for command-line tools started from rofi. null uses $TERMINAL, then xterm.";
    };

    iconThemes = mkOption {
      type = types.listOf types.str;
      default = ["Papirus-Dark" "Papirus" "Adwaita" "breeze" "hicolor"];
      description = "Icon themes searched (in order) for icons of packages that aren't installed.";
    };

    colors = mkOption {
      type = types.attrsOf types.str;
      default = {};
      example = {accent = "#89b4fa";};
      description = ''
        Overrides for the Catppuccin Mocha palette used in rofi rows and the
        terminal UI. Keys: text subtext1 subtext0 overlay1 overlay0 surface0
        surface1 accent blue green peach yellow red.
      '';
    };

    enableZshIntegration = lib.hm.shell.mkZshIntegrationOption {inherit config;};
    enableBashIntegration = lib.hm.shell.mkBashIntegrationOption {inherit config;};

    indexService.enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Build the nixpkgs catalog at login (about 20s, low priority, skipped
        when nixpkgs and the nix-index database haven't changed). Without it
        the catalog is built in the background the first time it's needed.
      '';
    };

    rofi = {
      enable = mkOption {
        type = types.bool;
        default = config.programs.rofi.enable;
        defaultText = lib.literalExpression "config.programs.rofi.enable";
        description = "Add launch to rofi as a script mode (`rofi -show <modeName>`).";
      };
      modeName = mkOption {
        type = types.str;
        default = "apps";
        description = "Name of the rofi mode.";
      };
    };
  };

  config = mkIf cfg.enable {
    home.packages = [launch];

    # Ours replaces nix-index's own "here's how to install it" handler.
    programs.nix-index.enableZshIntegration = mkIf cfg.enableZshIntegration (lib.mkDefault false);
    programs.nix-index.enableBashIntegration = mkIf cfg.enableBashIntegration (lib.mkDefault false);

    programs.zsh.initContent = mkIf cfg.enableZshIntegration (handler "command_not_found_handler");
    programs.bash.initExtra = mkIf cfg.enableBashIntegration (handler "command_not_found_handle");

    programs.rofi.modes = mkIf cfg.rofi.enable [
      {
        name = cfg.rofi.modeName;
        path = lib.getExe launch;
      }
    ];

    systemd.user.services.launch-index = mkIf (cfg.indexService.enable && pkgs.stdenv.hostPlatform.isLinux) {
      Unit.Description = "Index nixpkgs for the launch app launcher";
      Service = {
        Type = "oneshot";
        ExecStart = "${lib.getExe launch} refresh --quiet";
        Nice = 19;
        IOSchedulingClass = "idle";
      };
      Install.WantedBy = ["default.target"];
    };
  };
}
