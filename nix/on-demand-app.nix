# A command on PATH that runs a nixpkgs package through `nix shell` instead
# of installing it, plus the manifest `launch` uses to list it as a pinned
# on-demand app. Nothing is downloaded until the command is first run.
#
#   mkOnDemandApp pkgs { attr = "kicad"; }
#   mkOnDemandApp pkgs { attr = "jetbrains.idea"; bin = "idea"; }
#   mkOnDemandApp pkgs { attr = "openjdk25"; bin = "javac"; }
pkgs: {
  # Dot-separated nixpkgs attribute path, e.g. "jetbrains.idea".
  attr,
  # Executable inside the package. Defaults to meta.mainProgram, then the
  # last segment of `attr`.
  bin ? null,
  # Name of the command put on PATH. Defaults to `bin`.
  name ? null,
  # Flake the package is taken from at run time.
  flake ? "nixpkgs",
}: let
  inherit (pkgs) lib;
  pkg =
    lib.attrByPath (lib.splitString "." attr)
    (throw "mkOnDemandApp: unknown nixpkgs attribute `${attr}`")
    pkgs;
  binName =
    if bin != null
    then bin
    else pkg.meta.mainProgram or (lib.last (lib.splitString "." attr));
  cmd =
    if name != null
    then name
    else binName;
in
  pkgs.symlinkJoin {
    name = cmd;
    paths = [
      (pkgs.writeShellScriptBin cmd ''
        set -eu
        exec nix shell ${lib.escapeShellArg "${flake}#${attr}"} --command ${lib.escapeShellArg binName} "$@"
      '')
      (pkgs.writeTextDir "share/launch/pinned/${cmd}.json" (builtins.toJSON {
        name = cmd;
        inherit attr;
        bin = binName;
        description = pkg.meta.description or "";
      }))
    ];
  }
