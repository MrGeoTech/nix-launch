# NixOS side (optional but recommended): pin the `nixpkgs` registry entry to
# the nixpkgs your system is built from, so `nix shell nixpkgs#...` (what
# launch runs) uses the same revision instead of whatever is newest.
{inputs, ...}: {
  nix.registry.nixpkgs.flake = inputs.nixpkgs;
  nix.settings.experimental-features = ["nix-command" "flakes"];
}
