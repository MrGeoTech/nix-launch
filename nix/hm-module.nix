self: {
  config,
  options,
  lib,
  pkgs,
  ...
}: let
  cfg = config.programs.launch;
  inherit (lib) mkOption mkIf mkDefault types;

  presets = builtins.fromJSON (builtins.readFile ../src/presets.json);

  launch = cfg.package.override {
    nix-index = cfg.nixIndexPackage;
    settings = {
      inherit (cfg) nixpkgs iconThemes colors;
      terminal = cfg.terminal;
    };
  };
  launchExe = lib.getExe launch;

  # ---------------------------------------------------------------------
  # Types
  # ---------------------------------------------------------------------

  shareMode = types.nullOr (types.enum ["none" "ro" "rw"]);
  flag = desc:
    mkOption {
      type = types.nullOr types.bool;
      default = null;
      description = "${desc} null inherits.";
    };

  # What a sandboxed app may reach. Every field is optional: null means
  # "take it from `inherits`", and anything still unset is off.
  profileType = types.submodule {
    options = {
      inherits = mkOption {
        type = types.listOf types.str;
        default = [];
        example = ["gui" "net"];
        description = "Profiles applied first, in order; this profile's own settings win.";
      };
      network = mkOption {
        type = types.nullOr (types.enum ["none" "full"]);
        default = null;
        description = "`none`: no network at all. `full`: the host's network. null inherits.";
      };
      home = mkOption {
        type = types.nullOr (types.enum ["none" "private" "full"]);
        default = null;
        description = ''
          `none`: an empty, throwaway home. `private`: a persistent home of
          its own (~/.local/share/launch/homes/<app>). `full`: your real home.
          null inherits.
        '';
      };
      args = mkOption {
        type = shareMode;
        default = null;
        description = ''
          Files given on the command line (e.g. a file manager opening one):
          `ro` shares the file read-only, `rw` shares its directory
          read-write (so saving via a temp file works). null inherits.
        '';
      };
      cwd = mkOption {
        type = shareMode;
        default = null;
        description = "Share the directory it's started from. Never applies to $HOME or /. null inherits.";
      };
      wayland = flag "Wayland socket.";
      x11 = flag "X11 (Xwayland) socket and Xauthority.";
      audio = flag "PipeWire and PulseAudio sockets.";
      gpu = flag "/dev/dri and /sys, for hardware rendering.";
      dbus = flag "The whole session bus. Lets the app talk to most of your desktop.";
      theming = flag "Read-only GTK/Qt/font/icon/cursor config from your home, so apps look right.";
      paths = mkOption {
        type = types.attrsOf (types.enum ["ro" "rw"]);
        default = {};
        example = {"~/Projects/pcb" = "rw";};
        description = "Extra paths to share. Merged with inherited profiles.";
      };
      env = mkOption {
        type = types.attrsOf types.str;
        default = {};
        description = "Extra environment variables. Merged with inherited profiles.";
      };
      devices = mkOption {
        type = types.listOf types.str;
        default = [];
        example = ["/dev/ttyUSB0"];
        description = "Extra device nodes. Merged with inherited profiles.";
      };
    };
  };

  backendType = types.submodule {
    options = {
      type = mkOption {
        type = types.enum ["none" "bwrap" "microvm"];
        description = ''
          `none`: run directly (only fetched on demand, no isolation).
          `bwrap`: bubblewrap namespaces, near-zero overhead.
          `microvm`: a throwaway VM (not implemented yet; refuses to run).
        '';
      };
      extraArgs = mkOption {
        type = types.listOf types.str;
        default = [];
        description = "Extra arguments for the sandbox tool (bwrap only for now).";
      };
      memory = mkOption {
        type = types.int;
        default = 2048;
        description = "MiB of RAM (microvm).";
      };
      vcpus = mkOption {
        type = types.int;
        default = 2;
        description = "Virtual CPUs (microvm).";
      };
    };
  };

  appType = types.submodule ({name, ...}: {
    options = {
      attr = mkOption {
        type = types.str;
        default = name;
        defaultText = lib.literalExpression "<name>";
        example = "jetbrains.idea";
        description = "nixpkgs attribute path.";
      };
      bin = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Program inside the package. Defaults to meta.mainProgram, then the attr's last segment.";
      };
      description = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Shown in the launcher. Defaults to the package's meta.description.";
      };
      backend = mkOption {
        type = types.str;
        default = "default";
        description = "Name of an entry in `backends`.";
      };
      profile = mkOption {
        type = types.str;
        default = "default";
        description = "Name of an entry in `profiles`.";
      };
      access = mkOption {
        type = profileType;
        default = {};
        example = {paths."~/Projects/pcb" = "rw";};
        description = "Additions/overrides on top of `profile`, just for this app.";
      };
    };
  });

  # ---------------------------------------------------------------------
  # Rendering
  # ---------------------------------------------------------------------

  pkgFor = attr:
    lib.attrByPath (lib.splitString "." attr)
    (throw "programs.launch.apps: unknown nixpkgs attribute `${attr}`")
    pkgs;

  appJson = name: app: let
    pkg = pkgFor app.attr;
    licenses = lib.toList (pkg.meta.license or []);
  in {
    inherit (app) attr backend profile;
    bin =
      if app.bin != null
      then app.bin
      else pkg.meta.mainProgram or (lib.last (lib.splitString "." app.attr));
    description =
      if app.description != null
      then app.description
      else pkg.meta.description or "";
    free = lib.all (l: !(builtins.isAttrs l) || (l.free or true)) licenses;
    access = profileJson app.access;
  };

  profileJson = p:
    lib.filterAttrs (_: v: v != null && v != [] && v != {}) {
      inherit (p) inherits network home args cwd wayland x11 audio gpu dbus theming paths env devices;
    };

  configJson = {
    backends = lib.mapAttrs (_: b: {inherit (b) type extraArgs memory vcpus;}) cfg.backends;
    profiles = lib.mapAttrs (_: profileJson) cfg.profiles;
    apps = lib.mapAttrs appJson cfg.apps;
  };

  # One command per app; `launch exec` does the fetching and sandboxing.
  wrapper = name: _:
    pkgs.writeShellScriptBin name ''
      exec ${launchExe} exec ${lib.escapeShellArg name} -- "$@"
    '';

  # Unknown command -> `launch run`, which shows what would run and asks
  # first. The command only travels through argv, and non-interactive
  # shells (scripts) keep failing normally.
  handler = fn: ''
    ${fn}() {
      if [ -n "''${1:-}" ] && [ -t 0 ]; then
        ${launchExe} run -- "$@"
        return $?
      fi
      echo "''${1:-command}: command not found" >&2
      return 127
    }
  '';

  # Built-in entries, each field at mkDefault so `profiles.gui.audio = false`
  # changes one setting instead of replacing the preset.
  presetDefaults = lib.mapAttrs (_: lib.mapAttrs (_: mkDefault));
in {
  options.programs.launch = {
    enable = mkOption {
      type = types.bool;
      default = cfg.apps != {};
      defaultText = lib.literalExpression "config.programs.launch.apps != { }";
      description = "Whether to enable launch. On automatically once any `apps` are defined.";
    };

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

    apps = mkOption {
      type = types.attrsOf appType;
      default = {};
      example = lib.literalExpression ''
        {
          vlc = { };
          obs = { attr = "obs-studio"; backend = "none"; };
          kicad.access.paths."~/Projects/pcb" = "rw";
          sketchy-tool = { backend = "microvm"; profile = "isolated"; };
        }
      '';
      description = ''
        On-demand apps. Each name becomes a command on PATH (and a pinned
        entry in the launcher) that fetches the package on first use and
        runs it in its backend with its profile's access. No prompt: listing
        it here is the approval.
      '';
    };

    backends = mkOption {
      type = types.attrsOf backendType;
      default = {};
      description = ''
        Named ways to run apps. Built in: `none`, `bwrap`, `microvm`, and
        `default` (bwrap), which is what apps and uninstalled packages run
        in unless they say otherwise.
      '';
    };

    profiles = mkOption {
      type = types.attrsOf profileType;
      default = {};
      description = ''
        Named access sets. Built in: `isolated`, `shell`, `net`, `gui`,
        `gui-net`, `full`, and `default` (gui-net), see presets.json. Change
        a field of a built-in (`profiles.default.network = "none"`) or define
        your own with `inherits`.
      '';
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
        Flake that packages come from at run time (`<this>#attr`). The
        default registry entry is best when your system pins it to the
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
    programs.launch.backends = presetDefaults presets.backends;
    programs.launch.profiles = presetDefaults presets.profiles;

    assertions = let
      known = set: name: builtins.hasAttr name set;
    in
      lib.flatten [
        (lib.mapAttrsToList (n: a: [
            {
              assertion = known cfg.backends a.backend;
              message = "programs.launch.apps.${n}.backend: no backend named `${a.backend}`.";
            }
            {
              assertion = known cfg.profiles a.profile;
              message = "programs.launch.apps.${n}.profile: no profile named `${a.profile}`.";
            }
          ])
          cfg.apps)
        (lib.mapAttrsToList (n: p:
          map (i: {
            assertion = known cfg.profiles i;
            message = "programs.launch.profiles.${n}.inherits: no profile named `${i}`.";
          })
          p.inherits)
        cfg.profiles)
      ];

    warnings = lib.optional (lib.any (a: (cfg.backends.${a.backend}.type or null) == "microvm") (lib.attrValues cfg.apps))
      "programs.launch: the microvm backend isn't implemented yet; apps using it will refuse to start.";

    home.packages = [launch] ++ lib.mapAttrsToList wrapper cfg.apps;

    xdg.configFile."launch/config.json".text = builtins.toJSON configJson;

    # Ours replaces nix-index's own "here's how to install it" handler.
    programs.nix-index.enableZshIntegration = mkIf cfg.enableZshIntegration (mkDefault false);
    programs.nix-index.enableBashIntegration = mkIf cfg.enableBashIntegration (mkDefault false);

    programs.zsh.initContent = mkIf cfg.enableZshIntegration (handler "command_not_found_handler");
    programs.bash.initExtra = mkIf cfg.enableBashIntegration (handler "command_not_found_handle");

    # home-manager moved rofi's options under `settings` (Rasi names,
    # "name:path" for script modes); older releases only have `modes`.
    programs.rofi =
      mkIf cfg.rofi.enable
      (
        if options.programs.rofi ? settings
        then {settings.modes = ["${cfg.rofi.modeName}:${launchExe}"];}
        else {
          modes = [
            {
              name = cfg.rofi.modeName;
              path = launchExe;
            }
          ];
        }
      );

    systemd.user.services.launch-index = mkIf (cfg.indexService.enable && pkgs.stdenv.hostPlatform.isLinux) {
      Unit.Description = "Index nixpkgs for the launch app launcher";
      Service = {
        Type = "oneshot";
        ExecStart = "${launchExe} refresh --quiet";
        Nice = 19;
        IOSchedulingClass = "idle";
      };
      Install.WantedBy = ["default.target"];
    };
  };
}
