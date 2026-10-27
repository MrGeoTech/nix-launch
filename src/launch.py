#!/usr/bin/env python3
"""launch -- one app launcher, two front ends.

Everything you can start lives in one ranked list:

  1. installed desktop apps        (.desktop files, most-used first)
  2. pinned on-demand apps         (see mkOnDemandApp in the flake)
  3. installed command-line tools  (one row per package, not per binary)
  4. nixpkgs apps                  (ship a .desktop file, not installed)
  5. everything else in nixpkgs    (ships a binary, not installed)

Anything from 4/5 is never installed: it runs through `nix shell`, and only
after a confirmation screen that shows what the package actually is.

Front ends, same logic underneath:
  launch                  fuzzy picker in the terminal (fzf + preview)
  launch run CMD [ARGS]   run CMD, offering nixpkgs packages if it's missing
                          (this is what the shells' command-not-found calls)
  launch info NAME        show the card for an app, command or package
  launch search TERM      print matching entries
  launch refresh          rebuild the nixpkgs catalog
  rofi -show apps         the same list as a rofi script mode
"""

import fcntl
import glob
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

HOME = os.path.expanduser("~")
USER = os.environ.get("USER", "")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", f"{HOME}/.cache"), "launch")
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", f"{HOME}/.local/state"), "launch")
CATALOG = os.path.join(CACHE_DIR, "catalog.json")
HISTORY = os.path.join(STATE_DIR, "history.json")
SELF = os.path.abspath(sys.argv[0])
META_NIX = os.environ.get("LAUNCH_META_NIX", os.path.join(os.path.dirname(__file__), "meta.nix"))

# Everything below can be set by the home-manager module (or by hand).
# The flake that uninstalled packages come from: `nix shell <NIXPKGS>#attr`.
NIXPKGS = os.environ.get("LAUNCH_NIXPKGS", "nixpkgs")
TERMINAL = os.environ.get("LAUNCH_TERMINAL") or os.environ.get("TERMINAL") or "xterm"
ICON_THEMES = os.environ.get("LAUNCH_ICON_THEMES", "Papirus-Dark:Papirus:Adwaita:breeze:hicolor").split(":")

# Catppuccin Mocha by default; LAUNCH_COLORS='{"accent": "#89b4fa"}' overrides keys.
P = {"text": "#cdd6f4", "subtext1": "#bac2de", "subtext0": "#a6adc8", "overlay1": "#7f849c",
     "overlay0": "#6c7086", "surface0": "#313244", "surface1": "#45475a", "accent": "#cba6f7",
     "blue": "#89b4fa", "green": "#a6e3a1", "peach": "#fab387", "yellow": "#f9e2af", "red": "#f38ba8"}
try:
    P.update(json.loads(os.environ.get("LAUNCH_COLORS") or "{}"))
except ValueError:
    pass

# Outputs nix-locate appends to attribute names (`klayout.out`).
OUTPUTS = {"out", "bin", "dev", "lib", "man", "doc", "info", "debug", "static", "py",
           "lib32", "terminfo", "data", "devdoc", "tools", "sbin", "utils", "gui", "cli",
           "qt", "python", "perl", "dist", "modules", "plugins", "share"}
# Nested package sets worth offering. Everything else nested is language
# libraries (python3xxPackages, haskellPackages, ...) that duplicate the
# top-level attrs and drown out the actual apps.
NESTED_OK = {"kdePackages", "jetbrains", "pantheon", "lxqt", "xfce", "mate", "cinnamon",
             "gnome", "xorg", "lomiri", "libretro"}

TIER_APP, TIER_PINNED, TIER_TOOL, TIER_PKG_APP, TIER_PKG = range(5)

STORE_RE = re.compile(r"^/nix/store/[0-9a-z]{32}-([^/]+)")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def data_dirs():
    dirs = [os.environ.get("XDG_DATA_HOME", f"{HOME}/.local/share")]
    dirs += os.environ.get("XDG_DATA_DIRS", "").split(":")
    # Launched from a compositor keybind, XDG_DATA_DIRS can be thin; these
    # are where NixOS/home-manager put things regardless.
    dirs += [f"{HOME}/.nix-profile/share", f"/etc/profiles/per-user/{USER}/share",
             "/run/current-system/sw/share", "/var/lib/flatpak/exports/share",
             f"{HOME}/.local/share/flatpak/exports/share"]
    seen, out = set(), []
    for d in dirs:
        if d and os.path.isdir(d):
            real = os.path.realpath(d)
            if real not in seen:
                seen.add(real)
                out.append(d)
    return out


def path_dirs():
    dirs = os.environ.get("PATH", "").split(":")
    dirs += [f"{HOME}/.nix-profile/bin", f"/etc/profiles/per-user/{USER}/bin",
             "/run/current-system/sw/bin", "/run/wrappers/bin"]
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def store_name(path):
    """`/nix/store/<hash>-ripgrep-15.2.0/bin/rg` -> `ripgrep-15.2.0`."""
    m = STORE_RE.match(path or "")
    return m.group(1) if m else None


def pname_of(name):
    """`ripgrep-15.2.0` -> `ripgrep`, `vim-full-9.1` -> `vim-full`."""
    if not name:
        return None
    return re.split(r"-(?=\d)", name, maxsplit=1)[0]


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


def notify(title, body="", icon="system-software-install", urgency="normal"):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "launch", "-u", urgency, "-i", icon, title, body],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)


def detach(argv, env=None):
    """Start something that outlives us (and rofi) without holding its stdout."""
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, cwd=HOME,
                         env=env)
    except OSError as err:
        notify(f"Couldn't start {argv[0]}", str(err), "dialog-error", "critical")


# --------------------------------------------------------------------------
# Catalog: nixpkgs packages with a binary or .desktop file, plus metadata
# --------------------------------------------------------------------------

def catalog_key():
    """Changes whenever the pinned nixpkgs or the nix-index database does."""
    parts = [NIXPKGS]
    for reg in (f"{HOME}/.config/nix/registry.json", "/etc/nix/registry.json"):
        for flake in read_json(reg, {}).get("flakes", []):
            if flake.get("from", {}).get("id") == NIXPKGS:
                to = flake.get("to", {})
                parts.append(to.get("narHash") or to.get("rev") or to.get("path") or "")
                break
        if len(parts) > 1:
            break
    db = os.environ.get("NIX_INDEX_DATABASE") or f"{HOME}/.cache/nix-index"
    parts.append(os.path.realpath(os.path.join(db, "files")))
    return "|".join(parts)


def nix_locate(pattern, *flags):
    r = subprocess.run(["nix-locate", "--regex", *flags, pattern],
                       capture_output=True, text=True, check=False)
    rows = []
    for line in r.stdout.splitlines():
        cols = line.split()
        if len(cols) < 4 or cols[0].startswith("("):
            continue
        attr = cols[0]
        head, _, last = attr.rpartition(".")
        if head and last in OUTPUTS:
            attr = head
        rows.append((attr, cols[-1]))
    return rows


def icon_index():
    names = set()
    for d in data_dirs():
        for theme in ICON_THEMES:
            for f in glob.glob(f"{d}/icons/{theme}/*/apps/*"):
                names.add(os.path.splitext(os.path.basename(f))[0])
        for f in glob.glob(f"{d}/pixmaps/*"):
            names.add(os.path.splitext(os.path.basename(f))[0])
    return names


def pick_icon(icons, *candidates):
    for c in candidates:
        if not c:
            continue
        for v in (c, c.lower()):
            if v in icons:
                return v
    return None


def main_bin(p):
    bins = p.get("bins") or []
    for c in (p.get("mainProgram"), p.get("pname"), p["attr"].rsplit(".", 1)[-1]):
        if c and c in bins:
            return c
    if p.get("mainProgram"):
        return p["mainProgram"]
    return bins[0] if bins else p["attr"].rsplit(".", 1)[-1]


def build_catalog(quiet=False):
    def say(msg):
        if not quiet:
            print(f"launch: {msg}", file=sys.stderr)

    say("scanning the nix-index database ...")
    bins, desktops, stores, desk_names = {}, {}, {}, set()
    for attr, path in nix_locate(r"^/bin/[^/]+$", "-t", "x", "-t", "s"):
        if not os.path.basename(path).startswith("."):
            bins.setdefault(attr, set()).add(os.path.basename(path))
        stores.setdefault(attr, store_name(path))
    for attr, path in nix_locate(r"^/share/applications/[^/]+\.desktop$"):
        desktops.setdefault(attr, set()).add(os.path.basename(path)[:-len(".desktop")])
        desk_names.add((store_name(path) or "").removesuffix("-wrapped"))
        stores.setdefault(attr, store_name(path))

    attrs = sorted(a for a in set(bins) | set(desktops) | {p["attr"] for p in pinned()}
                   if "." not in a or a.split(".", 1)[0] in NESTED_OK)

    say(f"reading metadata for {len(attrs)} packages (about a minute) ...")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(attrs, f)
        attrs_file = f.name
    try:
        r = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr",
             f"import {META_NIX} {{ attrsFile = {attrs_file}; flake = {json.dumps(NIXPKGS)}; }}"],
            capture_output=True, text=True, check=False)
    finally:
        os.unlink(attrs_file)
    if r.returncode != 0:
        sys.exit(f"launch: metadata evaluation failed:\n{r.stderr[-2000:]}")

    # nix-index records one attr per store path, so aliases (openjdk25,
    # libreoffice-fresh, ...) come back with no files. Borrow them from the
    # attr that builds the same pname-version.
    by_name = {}
    for a, name in stores.items():
        if name:
            by_name.setdefault(name.removesuffix("-wrapped"), a)

    icons = icon_index()
    pkgs = []
    for p in json.loads(r.stdout):
        a = p["attr"]
        src = a
        if a not in bins and a not in desktops:
            src = by_name.get(f"{p.get('pname')}-{p.get('version')}", a)
        p["bins"] = sorted(bins.get(src, ()))[:40]
        p["desktop"] = sorted(desktops.get(src, ()))
        # Desktop files often live in a sibling derivation (kicad-base),
        # sometimes indexed under an unrelated attr (python3xxPackages.kicad).
        p["gui"] = bool(p["desktop"]) or any(
            f"{p.get('pname')}{suffix}-{p.get('version')}" in desk_names for suffix in ("", "-base", "-unwrapped"))
        p["bin"] = main_bin(p)
        p["icon"] = pick_icon(icons, *p["desktop"], p.get("pname"), a.rsplit(".", 1)[-1], p["bin"])
        pkgs.append({k: v for k, v in p.items() if v not in (None, "", [], False) or k == "free"})
    pkgs.sort(key=lambda p: p["attr"].lower())
    write_json(CATALOG, {"key": catalog_key(), "built": int(time.time()), "pkgs": pkgs})
    say(f"indexed {len(pkgs)} packages -> {CATALOG}")


def refresh(force=False, quiet=False, wait=True):
    """Rebuild the catalog if stale. Returns False if another refresh holds the lock."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, "refresh.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            return False
        if force or read_json(CATALOG, {}).get("key") != catalog_key():
            build_catalog(quiet)
        elif not quiet:
            print("launch: catalog is up to date (use --force to rebuild)", file=sys.stderr)
    return True


def load_catalog(background_refresh=True):
    cat = read_json(CATALOG, None)
    if cat is None or cat.get("key") != catalog_key():
        if background_refresh:
            detach([SELF, "refresh", "--quiet", "--no-wait"])
    return {p["attr"]: p for p in (cat or {}).get("pkgs", [])}, cat is not None


# --------------------------------------------------------------------------
# What's on this machine
# --------------------------------------------------------------------------

def pinned():
    """On-demand apps: wrappers that ship share/launch/pinned/<name>.json
    ({"name", "attr", "bin", "description"}), e.g. from mkOnDemandApp."""
    out, seen = [], set()
    for d in data_dirs():
        for f in sorted(glob.glob(f"{d}/launch/pinned/*.json")):
            p = read_json(f, None)
            if p and p.get("name") not in seen:
                seen.add(p["name"])
                out.append(p)
    return out


def parse_desktop(path):
    entry, in_main = {}, False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    in_main = line == "[Desktop Entry]"
                    continue
                if in_main and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    entry.setdefault(k.strip(), v.strip())
    except OSError:
        return None
    return entry


def desktop_apps():
    desktop_env = set(os.environ.get("XDG_CURRENT_DESKTOP", "Hyprland").split(":"))
    seen, apps = set(), []
    for d in data_dirs():
        root = os.path.join(d, "applications")
        for path in glob.glob(f"{root}/**/*.desktop", recursive=True):
            did = os.path.relpath(path, root)[:-len(".desktop")].replace("/", "-")
            if did in seen:
                continue
            seen.add(did)
            e = parse_desktop(path)
            if not e or e.get("Type") != "Application" or "Exec" not in e:
                continue
            if e.get("NoDisplay") == "true" or e.get("Hidden") == "true":
                continue
            only = set(filter(None, e.get("OnlyShowIn", "").split(";")))
            if only and not only & desktop_env:
                continue
            if set(filter(None, e.get("NotShowIn", "").split(";"))) & desktop_env:
                continue
            if e.get("TryExec") and not shutil.which(e["TryExec"]) and not os.path.exists(e["TryExec"]):
                continue
            e["_id"], e["_path"] = did, path
            e["_store"] = pname_of(store_name(os.path.realpath(path)))
            apps.append(e)
    return apps


def path_bins():
    """Command name -> resolved path, for everything on PATH."""
    out = {}
    for d in path_dirs():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n not in out:
                out[n] = os.path.realpath(os.path.join(d, n))
    return out


def desktop_argv(e):
    """Exec= line -> argv, field codes dropped (we never pass files)."""
    out = []
    for tok in shlex.split(e["Exec"]):
        if tok in ("%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%v", "%m", "%k"):
            continue
        if tok == "%i":
            out += ["--icon", e["Icon"]] if e.get("Icon") else []
            continue
        out.append(tok.replace("%c", e.get("Name", "")).replace("%%", "%"))
    return out


# --------------------------------------------------------------------------
# History (most-used first)
# --------------------------------------------------------------------------

def history():
    return read_json(HISTORY, {})


def record(key):
    h = history()
    n = h.get(key, {}).get("n", 0)
    h[key] = {"n": n + 1, "t": int(time.time())}
    write_json(HISTORY, h)


def score(h, key):
    e = h.get(key)
    if not e:
        return 0.0
    days = (time.time() - e.get("t", 0)) / 86400
    return e.get("n", 0) / (1 + days / 14)


# --------------------------------------------------------------------------
# The unified list
# --------------------------------------------------------------------------

def entries(cat=None):
    """Every launchable thing, in display order. Each entry is a dict:
    key, tier, name, sub (one-line description), icon, search, meta."""
    if cat is None:
        cat, _ = load_catalog()
    h = history()
    out = []

    apps = desktop_apps()
    installed_pnames = {e["_store"] for e in apps if e.get("_store")}
    for e in apps:
        out.append({
            "key": f"d:{e['_id']}", "tier": TIER_APP, "badge": "", "name": e.get("Name", e["_id"]),
            "sub": e.get("Comment") or e.get("GenericName") or "",
            "icon": e.get("Icon"), "search": e.get("Name", e["_id"]),
            "meta": " ".join(filter(None, [e["_id"], e.get("GenericName"), e.get("Keywords", "").replace(";", " "),
                                           os.path.basename(desktop_argv(e)[0]) if desktop_argv(e) else "",
                                           e.get("Comment")])),
        })

    pins = pinned()
    pinned_attrs = {p["attr"] for p in pins}
    for p in pins:
        c = cat.get(p["attr"], {})
        if c.get("bins") and p["bin"] not in c["bins"]:
            continue  # nothing runnable (e.g. man-pages)
        out.append({
            "key": f"p:{p['name']}", "tier": TIER_PINNED if c.get("gui") or (not c.get("bins") and c.get("icon")) else TIER_TOOL,
            "badge": "pinned", "name": p["name"],
            "sub": p.get("description") or c.get("description", ""), "icon": c.get("icon"),
            "search": p["name"], "meta": " ".join(filter(None, [p["attr"], c.get("pname"), p.get("description")])),
        })

    # An installed package hides every nixpkgs variant of itself (ffmpeg_7,
    # curlMinimal, ...). The tool row goes to the variant whose version
    # matches what's actually on PATH, else the shortest attr name.
    on_path = path_bins()
    installed = {}
    for a, c in cat.items():
        pname = pname_of(f"{c.get('pname', a)}-{c.get('version', '0')}")
        real = store_name(on_path.get(c.get("bin"), ""))
        if pname in installed_pnames or a in pinned_attrs:
            installed[a] = None
        elif real and real != pname_of(real) and pname_of(real) == pname:
            installed[a] = real
    best = {}
    for a, real in installed.items():
        if real:
            c = cat[a]
            rank = (f"{c.get('pname')}-{c.get('version')}" != real, len(a), a)
            if real not in best or rank < best[real][0]:
                best[real] = (rank, a)
    for _, a in best.values():
        c = cat[a]
        out.append({
            "key": f"t:{a}", "tier": TIER_TOOL, "badge": "installed", "name": c.get("pname", a),
            "sub": c.get("description", ""), "icon": "utilities-terminal",
            "search": f"{c.get('pname', a)} {a}", "meta": " ".join(c.get("bins", [])[:10]),
        })

    for a, c in cat.items():
        if a in installed:
            continue
        out.append({
            "key": f"n:{a}", "tier": TIER_PKG_APP if c.get("gui") else TIER_PKG,
            "badge": "pkg-app" if c.get("gui") else "pkg",
            "name": c.get("pname", a), "sub": c.get("description", ""),
            "version": c.get("version"), "attr": a,
            "icon": c.get("icon") or "package-x-generic",
            "search": a if c.get("pname", a) == a else f"{c.get('pname')} {a}",
            "meta": c["bin"] if c.get("bin") and c["bin"] not in a else "",
        })

    out.sort(key=lambda e: (e["tier"], -score(h, e["key"]), e["name"].lower()))
    return out


# --------------------------------------------------------------------------
# Package cards (the "are you sure" information)
# --------------------------------------------------------------------------

def live_package(attr):
    """Metadata for one attr straight from nixpkgs, for when the catalog
    hasn't been built yet. A few seconds; only used as a fallback."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([attr], f)
        attrs_file = f.name
    try:
        r = subprocess.run(["nix", "eval", "--impure", "--json", "--expr",
                            f"import {META_NIX} {{ attrsFile = {attrs_file}; flake = {json.dumps(NIXPKGS)}; }}"],
                           capture_output=True, text=True, check=False)
    finally:
        os.unlink(attrs_file)
    got = json.loads(r.stdout) if r.returncode == 0 else []
    if not got:
        return None
    p = got[0]
    p["bins"] = sorted({os.path.basename(path) for a, path in
                        nix_locate(r"^/bin/[^/]+$", "-p", f"^{re.escape(attr)}$")})
    p["bin"] = main_bin(p)
    return p


def providers(cat, cmd):
    """nixpkgs packages that ship bin/CMD, best guess first."""
    found = [p for p in cat.values() if cmd in p.get("bins", ())]
    if not cat:
        attrs = {a for a, _ in nix_locate(rf"^/bin/{re.escape(cmd)}$")}
        found = [p for p in (live_package(a) for a in sorted(attrs)) if p]
    found.sort(key=lambda p: (p.get("bin") != cmd, p["attr"].rsplit(".", 1)[-1] != cmd,
                              "." in p["attr"], not p.get("gui"), len(p["attr"])))
    return found


def in_store(p):
    if not p.get("pname") or not p.get("version"):
        return False
    return bool(glob.glob(f"/nix/store/*-{glob.escape(p['pname'])}-{glob.escape(p['version'])}"))


def card(p):
    """Structured facts about a package, shared by both front ends."""
    bins = p.get("bins", [])
    others = [b for b in bins if b != p.get("bin")]
    facts = [("package", f"{NIXPKGS}#{p['attr']}")]
    if p.get("homepage"):
        facts.append(("homepage", p["homepage"]))
    if p.get("license"):
        unfree = not p.get("free", True) and "unfree" not in p["license"].lower()
        facts.append(("license", p["license"] + ("  (unfree)" if unfree else "")))
    run = p.get("bin", "")
    if others:
        run += f"   +{len(others)} more: " + ", ".join(others[:6]) + (" ..." if len(others) > 6 else "")
    facts.append(("runs", run))
    facts.append(("status", "already in the nix store, starts right away" if in_store(p)
                  else "not downloaded yet, fetched into the nix store on first run"))
    warn = []
    if p.get("broken"):
        warn.append("marked broken in nixpkgs, it may not build")
    if not p.get("free", True):
        warn.append("unfree license (allowed just for this run)")
    return {"title": p.get("pname", p["attr"]), "version": p.get("version", ""),
            "desc": p.get("description", ""), "long": (p.get("long") or "").strip(),
            "facts": facts, "warn": warn}


def nix_shell_argv(p, command=None):
    argv = ["nix", "shell"]
    if not p.get("free", True):
        argv.append("--impure")
    argv.append(f"{NIXPKGS}#{p['attr']}")
    if command:
        argv += ["--command", *command]
    return argv


def nix_env(p):
    env = dict(os.environ)
    if not p.get("free", True):
        env["NIXPKGS_ALLOW_UNFREE"] = "1"
    return env


# --------------------------------------------------------------------------
# Terminal front end
# --------------------------------------------------------------------------

def rgb(hexc):
    hexc = hexc.lstrip("#")
    r, g, b = (int(hexc[i:i + 2], 16) for i in (0, 2, 4))
    return f"\033[38;2;{r};{g};{b}m"


C = {"mauve": rgb(P["accent"]), "text": rgb(P["text"]), "sub": rgb(P["subtext0"]), "dim": rgb(P["overlay1"]),
     "green": rgb(P["green"]), "peach": rgb(P["peach"]), "blue": rgb(P["blue"]), "yellow": rgb(P["yellow"]),
     "bold": "\033[1m", "reset": "\033[0m"}
if not sys.stdout.isatty() and not os.environ.get("FZF_PREVIEW_COLUMNS"):
    C = {k: "" for k in C}

BADGE = {"": ("", "text"), "pinned": ("󰐃 pinned", "blue"), "installed": ("󰆍 installed", "green"),
         "pkg-app": ("󰇚 nixpkgs", "peach"), "pkg": ("󰇚 nixpkgs", "dim")}


def print_card(info, width=None, out=sys.stdout):
    width = width or min(shutil.get_terminal_size((88, 20)).columns, 100)
    w = max(width - 4, 30)
    print(f"  {C['bold']}{C['mauve']}{info['title']}{C['reset']}  {C['dim']}{info['version']}{C['reset']}", file=out)
    for line in textwrap.wrap(info["desc"], w):
        print(f"  {C['text']}{line}{C['reset']}", file=out)
    if info["long"] and info["long"] != info["desc"]:
        long = " ".join(info["long"].split())
        print(file=out)
        for line in textwrap.wrap(long, w)[:5]:
            print(f"  {C['sub']}{line}{C['reset']}", file=out)
    print(file=out)
    for k, v in info["facts"]:
        print(f"  {C['dim']}{k:<9}{C['reset']} {C['text']}{v}{C['reset']}", file=out)
    for wmsg in info["warn"]:
        print(f"  {C['yellow']}! {wmsg}{C['reset']}", file=out)


def ask(prompt):
    try:
        with open("/dev/tty") as tty:
            sys.stderr.write(prompt)
            sys.stderr.flush()
            return tty.readline().strip()
    except OSError:
        return ""


def confirm_and_run(cands, cmd, args):
    """Show what would run and ask first. Nothing is fetched before a yes."""
    if len(cands) == 1:
        p = cands[0]
        print_card(card(p), out=sys.stderr)
        b = cmd if cmd in p.get("bins", ()) or not p.get("bins") else p["bin"]
        a = ask(f"\n  {C['mauve']}Run {C['bold']}{b}{C['reset']}{C['mauve']} from {NIXPKGS}#{p['attr']}?{C['reset']} [y/N] ")
        if a.lower() not in ("y", "yes"):
            return 1
    else:
        print(f"  {C['text']}{cmd}{C['reset']}{C['dim']} isn't installed. Packages that provide it:{C['reset']}\n",
              file=sys.stderr)
        for i, p in enumerate(cands[:9], 1):
            tag = "" if in_store(p) else f"  {C['dim']}(not downloaded){C['reset']}"
            print(f"  {C['mauve']}{i}{C['reset']}  {C['bold']}{p['attr']}{C['reset']} "
                  f"{C['dim']}{p.get('version', '')}{C['reset']}{tag}", file=sys.stderr)
            if p.get("description"):
                print(f"     {C['sub']}{p['description']}{C['reset']}", file=sys.stderr)
        a = ask(f"\n  Show which? [1-{min(len(cands), 9)}, Enter to cancel] ")
        if not a.isdigit() or not 1 <= int(a) <= min(len(cands), 9):
            return 1
        print(file=sys.stderr)
        return confirm_and_run([cands[int(a) - 1]], cmd, args)
    record(f"n:{p['attr']}")
    os.execvpe("nix", nix_shell_argv(p, [b, *args]), nix_env(p))


def cmd_run(argv):
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        sys.exit("usage: launch run CMD [ARGS...]")
    cmd, args = argv[0], argv[1:]
    if "/" not in cmd and shutil.which(cmd):
        os.execvp(cmd, argv)
    cat, _ = load_catalog()
    cands = providers(cat, cmd)
    if not cands:
        print(f"{cmd}: command not found (and nothing in nixpkgs provides it)", file=sys.stderr)
        return 127
    return confirm_and_run(cands, cmd, args)


def find_entry(query, cat):
    """An entry by key, exact name, or attr."""
    es = entries(cat)
    for e in es:
        if e["key"] == query:
            return e
    q = query.lower()
    for e in es:
        if e["name"].lower() == q or e.get("attr") == query or e["key"].split(":", 1)[1] == query:
            return e
    return None


def cmd_info(argv):
    query = argv[-1] if argv else ""
    cat, _ = load_catalog()
    e = find_entry(query, cat)
    width = int(os.environ.get("FZF_PREVIEW_COLUMNS", 0)) or None
    if e is None:
        cands = providers(cat, query)
        if not cands:
            print(f"nothing called {query!r} here or in nixpkgs")
            return 1
        print_card(card(cands[0]), width)
        return 0
    kind, ident = e["key"].split(":", 1)
    if kind == "d":
        app = next(a for a in desktop_apps() if a["_id"] == ident)
        print(f"  {C['bold']}{C['mauve']}{app.get('Name', ident)}{C['reset']}  {C['green']}installed{C['reset']}")
        for line in textwrap.wrap(app.get("Comment") or app.get("GenericName") or "", (width or 80) - 4):
            print(f"  {C['text']}{line}{C['reset']}")
        print()
        for k, v in (("runs", app["Exec"]), ("terminal", app.get("Terminal", "false")), ("entry", app["_path"])):
            print(f"  {C['dim']}{k:<9}{C['reset']} {C['text']}{v}{C['reset']}")
        return 0
    if kind == "p":
        pin = next(p for p in pinned() if p["name"] == ident)
        p = cat.get(pin["attr"]) or {"attr": pin["attr"], "description": pin.get("description"), "bin": pin["bin"]}
        p = dict(p, bin=pin["bin"])
        info = card(p)
        info["facts"].insert(0, ("pinned", f"`{ident}` is a pinned on-demand app, no prompt"))
        print_card(info, width)
        return 0
    p = cat.get(ident) or live_package(ident)
    info = card(p)
    if kind == "t":
        info["facts"][-1] = ("status", f"installed: {shutil.which(p['bin']) or p['bin']}")
    print_card(info, width)
    return 0


def cmd_search(argv):
    terms = [t.lower() for t in argv]
    cat, _ = load_catalog()
    shown = 0
    for e in entries(cat):
        hay = f"{e['search']} {e['meta']} {e['sub']}".lower()
        if all(t in hay for t in terms):
            badge, col = BADGE[e["badge"]]
            print(f"{C['bold']}{e['name']}{C['reset']}  {C[col]}{badge}{C['reset']}  {C['sub']}{e['sub']}{C['reset']}")
            shown += 1
            if shown >= 40:
                break
    return 0 if shown else 1


def open_entry(key, cat, frontend):
    """Launch an entry. frontend is 'tty' (we own a terminal) or 'gui'."""
    kind, ident = key.split(":", 1)
    if kind == "d":
        app = next((a for a in desktop_apps() if a["_id"] == ident), None)
        if app is None:
            return 1
        record(key)
        argv = desktop_argv(app)
        if app.get("Terminal") == "true":
            if frontend == "tty":
                os.execvp(argv[0], argv)
            argv = [TERMINAL, "-e", *argv]
        detach(argv)
        return 0
    if kind == "c":
        detach(shlex.split(ident))
        return 0
    if kind == "p":
        pin = next(p for p in pinned() if p["name"] == ident)
        p = dict(cat.get(pin["attr"], {"attr": pin["attr"]}), bin=pin["bin"])
        if not p.get("bins"):
            p["gui"] = bool(p.get("icon"))  # no file info (unfree, not on Hydra): guess
        record(key)
        return run_package(p, pin["bin"], frontend)
    p = cat.get(ident) or live_package(ident)
    if p is None:
        return 1
    if kind == "t":
        record(key)
        if frontend == "tty":
            os.execvp(p["bin"], [p["bin"]])
        detach([TERMINAL, "-e", os.environ.get("SHELL", "zsh"), "-ic", f"{shlex.quote(p['bin'])}; exec $SHELL"])
        return 0
    # kind == "n": not installed, so confirm first.
    if frontend == "tty":
        return confirm_and_run([p], p["bin"], [])
    record(key)
    return run_package(p, p["bin"], frontend)


def run_package(p, binary, frontend):
    if frontend == "tty":
        os.execvpe("nix", nix_shell_argv(p, [binary]), nix_env(p))
    if p.get("gui", True):
        detach([SELF, "_spawn", p["attr"], binary])
    else:
        # A CLI tool from the GUI: open a terminal with it on PATH.
        detach([TERMINAL, "-e", *nix_shell_argv(p, [os.environ.get("SHELL", "zsh")])], nix_env(p))
    return 0


def cmd_spawn(argv):
    """Internal: fetch (with a notification) then exec a GUI package."""
    attr, binary = argv[0], argv[1]
    cat, _ = load_catalog(background_refresh=False)
    p = cat.get(attr) or {"attr": attr}
    icon = p.get("icon") or "system-software-install"
    if not in_store(p):
        notify(f"Fetching {p.get('pname', attr)}", f"{NIXPKGS}#{attr}, this can take a minute", icon)
    build = ["nix", "build", "--no-link", "--print-out-paths"]
    build += ["--impure"] if not p.get("free", True) else []
    r = subprocess.run(build + [f"{NIXPKGS}#{attr}"], env=nix_env(p), capture_output=True, text=True, check=False)
    if r.returncode != 0:
        notify(f"Couldn't start {p.get('pname', attr)}", r.stderr.strip()[-400:], "dialog-error", "critical")
        return 1
    # Same environment `nix shell` would give, without evaluating nixpkgs twice.
    bindirs = [f"{o}/bin" for o in r.stdout.split() if os.path.isdir(f"{o}/bin")]
    exe = next((f"{d}/{binary}" for d in bindirs if os.access(f"{d}/{binary}", os.X_OK)), None)
    if exe is None:
        notify(f"Couldn't start {p.get('pname', attr)}", f"{NIXPKGS}#{attr} has no bin/{binary}", "dialog-error", "critical")
        return 1
    env = dict(os.environ, PATH=":".join(bindirs + [os.environ.get("PATH", "")]))
    # TUI apps (bottom, btop, ...) ship a Terminal=true desktop file.
    desktop_files = [parse_desktop(f) or {} for o in r.stdout.split() for f in glob.glob(f"{o}/share/applications/*.desktop")]
    if any(e.get("Terminal") == "true" for e in desktop_files):
        os.execvpe(TERMINAL, [TERMINAL, "-e", exe], env)
    os.execve(exe, [binary], env)


def cmd_pick():
    if not shutil.which("fzf"):
        sys.exit("launch: fzf not found")
    cat, have = load_catalog()
    if not have:
        print("launch: nixpkgs catalog is being built in the background; showing installed things only",
              file=sys.stderr)
    lines = []
    for e in entries(cat):
        badge, col = BADGE[e["badge"]]
        badge = f"{C[col]}{badge}{C['reset']}  " if badge else ""
        lines.append(f"{e['key']}\t{C['bold']}{C['text']}{e['name']}{C['reset']}  {badge}{C['dim']}{e['sub']}{C['reset']}")
    me = shlex.quote(SELF)
    r = subprocess.run(
        ["fzf", "--ansi", "--no-sort", "--tiebreak=index", "--delimiter=\t", "--with-nth=2..",
         "--prompt=  ", "--pointer=▌", "--info=inline-right", "--layout=reverse", "--height=80%",
         "--border=rounded", "--border-label= launch ",
         f"--color=bg+:{P['surface0']},fg+:{P['text']},hl:{P['accent']},hl+:{P['accent']},pointer:{P['accent']},"
         f"prompt:{P['accent']},border:{P['surface1']},label:{P['subtext0']}",
         f"--preview={me} info {{1}}", "--preview-window=right,50%,wrap,border-left"],
        input="\n".join(lines), capture_output=False, stdout=subprocess.PIPE, text=True, check=False)
    if r.returncode != 0 or not r.stdout.strip():
        return 1
    return open_entry(r.stdout.split("\t", 1)[0], cat, "tty")


# --------------------------------------------------------------------------
# Rofi front end (script mode: https://davatorium.github.io/rofi/1.7.5/rofi-script.5/)
# --------------------------------------------------------------------------

SEP = "\x1e"  # row delimiter, so a row can hold a newline (two-line rows)


def esc(s):
    return html.escape(s or "", quote=False)


def rofi_row(text, display, info, icon=None, meta=None, nonselectable=False):
    opts = [f"display\x1f{display}", f"info\x1f{info}"]
    if icon:
        opts.append(f"icon\x1f{icon}")
    if meta:
        opts.append(f"meta\x1f{meta}")
    if nonselectable:
        opts.append("nonselectable\x1ftrue")
    return text.replace("\n", " ") + "\0" + "\x1f".join(o.replace(SEP, " ") for o in opts) + SEP


def rofi_opt(k, v):
    return f"\0{k}\x1f{v}{SEP}"


ROFI_BADGE = {"": "", "pinned": f"<span color='{P["blue"]}'>󰐃 pinned</span>  ",
              "installed": f"<span color='{P["green"]}'>󰆍 installed</span>  ",
              "pkg-app": f"<span color='{P["peach"]}'>󰇚 nixpkgs</span>  ",
              "pkg": f"<span color='{P["overlay1"]}'>󰇚 nixpkgs</span>  "}


def rofi_list(out, cat, have, message=None):
    out.append(rofi_opt("prompt", "󰍉"))
    out.append(rofi_opt("message", message or ""))
    if not have:
        out.append(rofi_row("indexing", f"<span color='{P["subtext0"]}'>Indexing nixpkgs in the background; "
                            "uninstalled packages show up in about a minute</span>", "noop",
                            "emblem-synchronizing", nonselectable=True))
    for e in entries(cat):
        name_col = P["text"] if e["tier"] <= TIER_TOOL else P["subtext1"]
        sub = esc(e["sub"])
        if len(sub) > 90:
            sub = sub[:88].rstrip() + "…"
        disp = (f"<span color='{name_col}' weight='bold'>{esc(e['name'])}</span>"
                + (f"  <span color='{P["overlay0"]}' size='small'>{esc(e.get('version'))}</span>" if e.get("version") else "")
                + f"\n<span size='small' color='{P["subtext0"]}'>{ROFI_BADGE[e['badge']]}{sub}</span>")
        out.append(rofi_row(e["search"], disp, e["key"], e.get("icon"), e.get("meta")))


def rofi_confirm(out, cands, cmd=None):
    p = cands[0]
    info = card(p)
    m = [f"<span size='large' weight='bold' color='{P["accent"]}'>{esc(info['title'])}</span>"
         f"  <span color='{P["overlay1"]}'>{esc(info['version'])}</span>",
         f"<span color='{P["text"]}'>{esc(info['desc'])}</span>"]
    if info["long"] and info["long"] != info["desc"]:
        long = " ".join(info["long"].split())
        m.append(f"<span size='small' color='{P["subtext0"]}'>{esc(long[:300] + ('…' if len(long) > 300 else ''))}</span>")
    m.append("")
    for k, v in info["facts"]:
        m.append(f"<span color='{P["overlay1"]}'>{k:<9}</span><span color='{P["subtext1"]}'>{esc(v)}</span>")
    for w in info["warn"]:
        m.append(f"<span color='{P["yellow"]}'>! {esc(w)}</span>")
    out.append(rofi_opt("prompt", "󰇚"))
    out.append(rofi_opt("message", "\n".join(m)))
    binary = cmd if cmd and cmd in p.get("bins", ()) else p["bin"]
    how = "" if p.get("gui") else " in a terminal"
    out.append(rofi_row(f"run {binary}", f"<b>󰐊  Run {esc(binary)}</b><span color='{P["subtext0"]}'>{how}</span>"
                        f"\n<span size='small' color='{P["overlay1"]}'>nix shell {esc(NIXPKGS)}#{esc(p['attr'])}, nothing gets installed</span>",
                        f"r:{p['attr']}:{binary}"))
    for other in [b for b in p.get("bins", []) if b != binary][:6]:
        out.append(rofi_row(f"run {other}", f"󰐊  Run {esc(other)} instead\n<span size='small' color='{P["overlay1"]}'>"
                            f"another program in this package</span>", f"r:{p['attr']}:{other}"))
    for alt in cands[1:6]:
        out.append(rofi_row(f"show {alt['attr']}", f"󰋼  {esc(alt['attr'])} <span color='{P["overlay1"]}'>{esc(alt.get('version'))}</span>"
                            f"\n<span size='small' color='{P["subtext0"]}'>{esc(alt.get('description'))}</span>",
                            f"n:{alt['attr']}", alt.get("icon") or "package-x-generic"))
    if p.get("homepage"):
        out.append(rofi_row("homepage", f"󰖟  Open homepage\n<span size='small' color='{P["overlay1"]}'>{esc(p['homepage'])}</span>",
                            f"w:{p['homepage']}"))
    out.append(rofi_row("back", f"󰌍  Back\n<span size='small' color='{P["overlay1"]}'>return to the list</span>", "b:"))


def cmd_rofi():
    retv = int(os.environ.get("ROFI_RETV", "0"))
    info = os.environ.get("ROFI_INFO", "")
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    out = []
    if retv == 0:
        sys.stdout.write("\0delim\x1f\x1e\n")
        out.append(rofi_opt("markup-rows", "true"))
    cat, have = load_catalog()

    if retv == 1 and info:
        kind, ident = info.split(":", 1)
        if kind == "n":
            p = cat.get(ident) or live_package(ident)
            if p:
                rofi_confirm(out, [p])
            else:
                rofi_list(out, cat, have, f"<span color='{P["red"]}'>Couldn't read {esc(NIXPKGS)}#{esc(ident)}</span>")
        elif kind == "r":
            attr, binary = ident.split(":", 1)
            p = cat.get(attr) or live_package(attr) or {"attr": attr}
            record(f"n:{attr}")
            run_package(p, binary, "gui")
        elif kind == "w":
            detach(["xdg-open", ident])
        elif kind == "b":
            rofi_list(out, cat, have)
        elif kind in ("d", "p", "t"):
            open_entry(info, cat, "gui")
    elif retv == 2 and arg.strip():
        # Free text that matched nothing: a command line, like the old run mode,
        # or the name of something to look for in nixpkgs.
        words = shlex.split(arg)
        if shutil.which(words[0]):
            detach(words)
        else:
            cands = providers(cat, words[0])
            if cands:
                rofi_confirm(out, cands, words[0])
            else:
                rofi_list(out, cat, have, f"<span color='{P["red"]}'>Nothing provides “{esc(words[0])}”</span>")
    else:
        rofi_list(out, cat, have)
    sys.stdout.write("".join(out))


# --------------------------------------------------------------------------

def main():
    if "ROFI_RETV" in os.environ:
        return cmd_rofi()
    argv = sys.argv[1:]
    if not argv:
        return cmd_pick()
    sub, rest = argv[0], argv[1:]
    if sub == "run":
        return cmd_run(rest)
    if sub in ("info", "search"):
        if not rest:
            sys.exit(f"usage: launch {sub} NAME")
        return cmd_info(rest) if sub == "info" else cmd_search(rest)
    if sub == "refresh":
        refresh(force="--force" in rest, quiet="--quiet" in rest, wait="--no-wait" not in rest)
        return 0
    if sub == "_spawn":
        return cmd_spawn(rest)
    if sub in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    return cmd_run(argv)


if __name__ == "__main__":
    sys.exit(main())
