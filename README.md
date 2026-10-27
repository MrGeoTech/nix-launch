# nix-launch

An app launcher for NixOS / home-manager that knows about **everything in
nixpkgs**, not just what's installed. Installed apps come first; anything
else is fetched into the store and run only after a screen that shows what
the package actually is (description, version, homepage, license, which
programs it ships, whether it's already downloaded, and what it'll be
allowed to touch). Nothing is ever installed, and everything it fetches
runs in a sandbox (bubblewrap by default).

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
| 2 | Your on-demand apps (`programs.launch.apps`) | `󰐃 pinned` |
| 3 | Installed command-line tools, one row per package | `󰆍 installed` |
| 4 | nixpkgs packages that ship a desktop file | `󰇚 nixpkgs` |
| 5 | Everything else in nixpkgs with a binary | `󰇚 nixpkgs` (dim) |

From rofi, graphical packages start in the background (with a "Fetching…"
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
programs.launch.apps = {
  vlc = { };
  kicad = { };
  sketchy-tool = { backend = "microvm"; };
};
```

That gives you a `vlc`, `kicad` and `sketchy-tool` command that fetch on
first use and run sandboxed, the `launch` command, the shell hooks, a login
service that builds the catalog, and the rofi mode if `programs.rofi.enable`
is set. With no apps, `programs.launch.enable = true;` gives you the rest.
The package alone is `inputs.nix-launch.packages.${system}.default` (or
`pkgs.nix-launch` via `overlays.default`).

See [`examples/`](examples) for a full home-manager setup, the NixOS
registry pin, the rofi theme, and Hyprland / Sway bindings and blur rules.
Compositor config is deliberately not managed by the module.

## Apps, backends and profiles

Everything is optional and follows one pattern: named entries, with
built-in ones you can use, tweak, or add to.

```nix
programs.launch = {
  # Where things run. Built in: none, bwrap, microvm, and default (= bwrap).
  backends.default.type = "bwrap";

  # What they can reach. Built in (src/presets.json):
  #   isolated  nothing: no network, empty home, no files
  #   shell     own persistent home, opened files + current dir
  #   net       shell + network
  #   gui       shell + wayland, x11, audio, gpu, your GTK/Qt/font theming
  #   gui-net   gui + net
  #   full      gui-net + your real home + the session bus
  #   default   gui-net
  profiles.default.network = "none";          # tweak one field of a built-in
  profiles.files = {                          # or build on them
    inherits = [ "default" ];
    paths."~/Projects" = "rw";
  };

  apps = {
    vlc.access.args = "ro";                   # per-app additions
    kicad.profile = "files";
    idea = { attr = "jetbrains.idea"; profile = "files"; };
    lspci = { attr = "pciutils"; backend = "none"; };
  };
};
```

- **Apps** (`apps.<command>`): `attr` (defaults to the name), `bin` (defaults
  to `meta.mainProgram`), `backend` and `profile` (default `"default"`), and
  `access` for per-app overrides. Each becomes a command on PATH and a
  pinned launcher entry. Listing an app is the approval: it runs with no
  prompt, which is what you want when a file manager opens a video in `vlc`.
- **Uninstalled packages** (from the list or the command-not-found hook)
  run in the `default` backend with the `default` profile, after the
  confirm screen.
- **Profiles** combine with `inherits` (applied in order, then the
  profile's own fields win). Unset fields are off. `paths`, `env` and
  `devices` merge instead of replacing.

| profile field | values |
|---|---|
| `network` | `none`, `full` |
| `home` | `none` (throwaway), `private` (`~/.local/share/launch/homes/<app>`), `full` |
| `args` | files given on the command line: `none`, `ro` (the file), `rw` (its directory) |
| `cwd` | directory it's started from: `none`, `ro`, `rw` (never `$HOME` or `/`) |
| `wayland`, `x11`, `audio`, `gpu`, `dbus`, `theming` | booleans |
| `paths` | `{ "~/dir" = "ro" \| "rw"; }` |
| `env`, `devices` | extra environment variables, device nodes |

### Backends

- **`none`**: runs directly. Use it for tools that need the real system
  (`lspci`, `ncdu`, screenshot tools) or things bwrap can't host.
- **`bwrap`**: [bubblewrap](https://github.com/containers/bubblewrap)
  namespaces. Starts instantly, and GUI, GPU and audio work natively.
  What it can't do: xdg-desktop-portals (so no portal file chooser or
  screen capture inside the sandbox) and FUSE (AppImages). The app's own
  file dialog only sees what its profile shares.
- **`microvm`**: a throwaway KVM virtual machine with its own kernel, for
  things you trust least. See below.

### The microvm backend

Every run boots the same small image (the stock kernel plus a ~1 MB
initramfs, `nix/microvm-guest.nix`) and configures it for that one app:

- Your `/nix/store` is shared in read-only, so whatever the host fetched
  runs inside. Each shared folder from the profile (home, `paths`, cwd,
  opened files) gets its own `virtiofsd`, read-only where the profile says.
  Nothing else on the host is visible.
- `network = "full"` is QEMU's user-mode network: outbound connections
  and DNS, with nothing reachable from outside. `"none"` gives the VM no
  network device at all.
- The app runs as your UID on a terminal with your window size, or, from
  a pipe or script, with exact stdin/stdout/stderr bytes. Its exit status
  becomes `launch`'s, so it works in scripts like a local command.
- About 1.5–2 seconds per run. Nothing persists unless the profile shares
  it (`home = "private"` keeps a home between runs).

```nix
programs.launch.apps.sketchy-tool = { backend = "microvm"; profile = "isolated"; };
programs.launch.backends.microvm = { memory = 4096; vcpus = 4; };  # defaults: 2048, 2
```

Current limits: command-line apps only (no Wayland, audio or GPU yet, so
GUI profiles get a warning on the confirm screen), x86_64-linux only, and
needs read/write access to `/dev/kvm`. An opened file is shared by sharing
its directory (virtiofs shares directories). `LAUNCH_VM_DEBUG=1` shows the
guest's boot log and keeps the run's temp directory.

### Options (`programs.launch.*`)

| option | default | |
|---|---|---|
| `enable` | `apps != { }` | |
| `apps`, `backends`, `profiles` | see above | |
| `nixpkgs` | `"nixpkgs"` | Flake uninstalled packages come from. Pin the registry entry (`examples/nixos.nix`) so it matches your system. |
| `terminal` | `null` | Terminal for CLI tools started from rofi; `null` uses `$TERMINAL`, then `xterm`. |
| `iconThemes` | Papirus-Dark, Papirus, Adwaita, breeze, hicolor | Searched for icons of packages that aren't installed. |
| `colors` | `{}` | Overrides for the Catppuccin Mocha palette, e.g. `{ accent = "#89b4fa"; }`. |
| `enableZshIntegration` / `enableBashIntegration` | home-manager's default | command-not-found hook. Turns nix-index's own hook off. |
| `indexService.enable` | `true` | Build the catalog at login when it's stale. |
| `rofi.enable` | `programs.rofi.enable` | Add the rofi script mode. |
| `rofi.modeName` | `"apps"` | |
| `nixIndexPackage` | `programs.nix-index.package` | Provides `nix-locate`. |

The scalar options map to environment variables read by `launch`
(`LAUNCH_NIXPKGS`, `LAUNCH_TERMINAL`, `LAUNCH_ICON_THEMES`, `LAUNCH_COLORS`
as JSON), so the bare package can be configured without the module too.

### Without the module

`lib.mkOnDemandApp pkgs { attr = "kicad"; }` makes a plain, unsandboxed
command that runs `nix shell nixpkgs#kicad`. It also shows up in the
launcher as pinned, because it ships `share/launch/pinned/<name>.json`
(`{"name", "attr", "bin", "description"}`); anything else can do the same.

## CLI

```
launch                  fuzzy picker (fzf + preview)
launch run CMD [ARGS]   run CMD, offering nixpkgs packages if it's missing
launch exec APP [ARGS]  run one of programs.launch.apps (what its command does)
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
- **Running** is the same for every path in: `nix build` the package
  (with a "Fetching…" notification when there's no terminal to show
  progress), then exec it directly or inside the backend with the
  resolved profile. For microvm that means booting the guest with this
  run's shares and command (`src/vm-init.sh` is its whole init).
- **rofi** talks to the same script through the
  [script mode protocol](https://davatorium.github.io/rofi/current/rofi-script.5/);
  the confirm screen is a second page using rofi's message box.

Requires `nix` with flakes enabled and unprivileged user namespaces (the
default on NixOS) for bwrap. The package brings its own `nix-locate`,
bubblewrap, fzf and `notify-send`; the module adds QEMU, virtiofsd and the
guest image only when something uses the microvm backend.

## License

MIT
