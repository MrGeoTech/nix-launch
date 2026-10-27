# Bulk metadata for the launcher catalog. Called by `launch refresh` with a
# JSON list of nixpkgs attribute paths (the ones nix-index says ship a
# binary or a .desktop file) and evaluated against `flake` (LAUNCH_NIXPKGS,
# default the `nixpkgs` registry entry), i.e. exactly what
# `nix shell <flake>#...` will run.
# Reading meta never forces a build or download, and tryEval drops the
# handful of attrs that throw (aliases, removed packages).
{
  attrsFile,
  flake ? "nixpkgs",
}: let
  pkgs = (builtins.getFlake flake).legacyPackages.${builtins.currentSystem};
  inherit (pkgs) lib;

  str = v:
    if builtins.isString v
    then v
    else null;
  licenses = m: let
    l = m.license or [];
  in
    if builtins.isList l
    then l
    else [l];

  one = attr: let
    r = builtins.tryEval (let
      p = lib.attrByPath (lib.splitString "." attr) null pkgs;
      m = p.meta or {};
      lic = builtins.concatStringsSep ", " (builtins.filter (x: x != null) (map (x:
        if builtins.isAttrs x
        then x.spdxId or x.shortName or null
        else str x) (licenses m)));
      home = m.homepage or null;
      long = str (m.longDescription or null);
      info = {
        attr = attr;
        pname = str (p.pname or null);
        version = str (p.version or null);
        description = str (m.description or null);
        long =
          if long == null
          then null
          else builtins.substring 0 700 long;
        homepage =
          if builtins.isList home
          then str (builtins.head home)
          else str home;
        license = lic;
        free = builtins.all (x: !(builtins.isAttrs x) || (x.free or true)) (licenses m);
        mainProgram = str (m.mainProgram or null);
        broken = m.broken or false;
      };
    in
      if p == null || !(lib.isDerivation p)
      then null
      else builtins.deepSeq info info);
  in
    if r.success
    then r.value
    else null;
in
  builtins.filter (x: x != null) (map one (builtins.fromJSON (builtins.readFile attrsFile)))
