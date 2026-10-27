{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
  nix-index,
  fzf,
  libnotify,
  bubblewrap,
  # Defaults baked into the wrapper (all still overridable from the
  # environment). The home-manager module fills these from its options.
  settings ? {},
}: let
  envVars = lib.filterAttrs (_: v: v != null) {
    LAUNCH_NIXPKGS = settings.nixpkgs or null;
    LAUNCH_TERMINAL = settings.terminal or null;
    LAUNCH_ICON_THEMES =
      if settings ? iconThemes
      then lib.concatStringsSep ":" settings.iconThemes
      else null;
    LAUNCH_COLORS =
      if (settings.colors or {}) != {}
      then builtins.toJSON settings.colors
      else null;
  };
in
  stdenvNoCC.mkDerivation {
    pname = "nix-launch";
    version = "0.1.0";
    src = lib.fileset.toSource {
      root = ../src;
      fileset = ../src;
    };

    nativeBuildInputs = [makeWrapper];
    buildInputs = [python3];
    dontBuild = true;

    installPhase = ''
      runHook preInstall
      install -Dm755 launch.py $out/bin/launch
      install -Dm644 meta.nix $out/share/nix-launch/meta.nix
      install -Dm644 presets.json $out/share/nix-launch/presets.json
      patchShebangs $out/bin/launch
      wrapProgram $out/bin/launch \
        --set LAUNCH_META_NIX $out/share/nix-launch/meta.nix \
        --set LAUNCH_PRESETS $out/share/nix-launch/presets.json \
        ${lib.concatStringsSep " " (lib.mapAttrsToList (k: v: "--set-default ${k} ${lib.escapeShellArg v}") envVars)} \
        --prefix PATH : ${lib.makeBinPath [nix-index fzf libnotify bubblewrap]}
      runHook postInstall
    '';

    doInstallCheck = true;
    installCheckPhase = ''
      $out/bin/launch --help >/dev/null
    '';

    meta = {
      description = "App launcher over installed apps and all of nixpkgs, with a confirm-before-fetch flow (terminal + rofi)";
      license = lib.licenses.mit;
      mainProgram = "launch";
      platforms = lib.platforms.linux;
    };
  }
