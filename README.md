# nix-launch

An app launcher for NixOS / home-manager that knows about **everything in
nixpkgs**, not just what's installed. Installed apps come first; anything
else runs through `nix shell` only after a screen that shows what the package
actually is (description, version, homepage, license, which programs it
ships, whether it's already in the store). Nothing is ever installed.

One tool, two front ends that behave the same:

- **rofi**: `rofi -show apps`, a script mode with icons, two-line rows and a
  confirm screen.
- **terminal**: `launch` (fzf picker with a preview pane), `launch run CMD`,
  and a zsh/bash command-not-found hook that shows the same information
  before asking `[y/N]`.

## What's in the list

In this order:

| | | badge |
|---|---|---|
| 1 | Installed desktop apps (`.desktop` files), most-used first | none |
| 2 | Pinned on-demand apps (see `mkOnDemandApp`) | `󰐃 pinned` |
| 3 | Installed command-line tools, one row per package | `󰆍 installed` |
| 4 | nixpkgs packages that ship a desktop file | `󰇚 nixpkgs` |
| 5 | Everything else in nixpkgs with a binary | `󰇚 nixpkgs` (dim) |

From rofi, graphical packages launch detached (with a "Fetching…"
notification if they need downloading), and command-line packages open a
terminal with the package on `PATH`. Typing something that matches nothing
and pressing Enter runs it as a command, or offers the nixpkgs packages that
provide it.

## Install

```nix
# flake.nix
inputs.nix-launch.url = "github:<you>/nix-launch";
inputs.nix-launch.inputs.nixpkgs.follows = "nixpkgs";

# Prebuilt database for nix-locate (strongly recommended).
inputs.nix-index-database.url = "github:nix-community/nix-index-database";
inputs.nix-index-database.inputs.nixpkgs.follows = "nixpkgs";
```

```nix
# home-manager
imports = [
  inputs.nix-launch.homeManagerModules.default
  inputs.nix-index-database.homeModules.nix-index
];
programs.launch.enable = true;
```

That gives you the `launch` command, the shell hooks, a login service that
builds the catalog, and the rofi mode if `programs.rofi.enable` is set.
The package alone is `inputs.nix-launch.packages.${system}.default` (or
`pkgs.nix-launch` via `overlays.default`).

See [`examples/`](examples) for a full home-manager setup, the NixOS
registry pin, the rofi theme, and Hyprland / Sway bindings and blur rules.
Compositor config is deliberately not managed by the module.

### Options (`programs.launch.*`)

| option | default | |
|---|---|---|
| `enable` | `false` | |
| `nixpkgs` | `"nixpkgs"` | Flake uninstalled packages come from. Pin the registry entry (`examples/nixos.nix`) so it matches your system. |
| `terminal` | `null` | Terminal for CLI tools started from rofi; `null` uses `$TERMINAL`, then `xterm`. |
| `iconThemes` | Papirus-Dark, Papirus, Adwaita, breeze, hicolor | Searched for icons of packages that aren't installed. |
| `colors` | `{}` | Overrides for the Catppuccin Mocha palette, e.g. `{ accent = "#89b4fa"; }`. |
| `enableZshIntegration` / `enableBashIntegration` | home-manager's default | command-not-found hook. Turns nix-index's own hook off. |
| `indexService.enable` | `true` | Build the catalog at login when it's stale. |
| `rofi.enable` | `programs.rofi.enable` | Add the rofi script mode. |
| `rofi.modeName` | `"apps"` | |
| `nixIndexPackage` | `programs.nix-index.package` | Provides `nix-locate`. |

Each one maps to an environment variable read by `launch`
(`LAUNCH_NIXPKGS`, `LAUNCH_TERMINAL`, `LAUNCH_ICON_THEMES`, `LAUNCH_COLORS`
as JSON), so the bare package can be configured without the module too.

### On-demand apps

```nix
let onDemand = inputs.nix-launch.lib.mkOnDemandApp pkgs; in
home.packages = [
  (onDemand { attr = "kicad"; })
  (onDemand { attr = "jetbrains.idea"; bin = "idea"; })
];
```

Each puts a command on `PATH` that runs `nix shell nixpkgs#<attr>`, and
appears in the launcher as pinned (no confirm screen). Anything can show up
there by shipping `share/launch/pinned/<name>.json` with
`{"name", "attr", "bin", "description"}`.

## CLI

```
launch                  fuzzy picker (fzf + preview)
launch run CMD [ARGS]   run CMD, offering nixpkgs packages if it's missing
launch info NAME        details for an app, command or package
launch search TERM      matching entries
launch refresh [--force]
```

## How it works

- **Catalog**: `nix-locate` lists every package with a `bin/*` or a
  `share/applications/*.desktop` (~19k packages, after skipping language
  library sets). One `nix eval` reads their `meta` (description, version,
  homepage, license, mainProgram) from the same flake `nix shell` will use.
  It takes about 20s and is cached in `~/.cache/launch/catalog.json`, keyed on
  the nixpkgs revision and the nix-index database, so it only rebuilds when
  one of them changes.
- **Installed detection** compares the store paths of binaries on `PATH`
  and of installed desktop files against each package's `pname`.
- **History** (`~/.local/state/launch/history.json`) orders each tier by
  how often and how recently you've launched things.
- **rofi** talks to the same script through the
  [script mode protocol](https://davatorium.github.io/rofi/current/rofi-script.5/);
  the confirm screen is a second page using rofi's message box.

Requires `nix` with flakes enabled, Python 3, and (for the picker) fzf; the
package brings its own `nix-locate`, fzf and `notify-send`.

## License

MIT
