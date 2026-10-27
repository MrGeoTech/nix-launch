# The one VM image the microvm backend boots: the stock kernel plus a tiny
# initramfs (static busybox, the virtio modules, and ../src/vm-init.sh).
# There is no root filesystem and no NixOS: the host's /nix/store is shared
# in read-only over virtiofs, and everything else about a run (mounts,
# user, network, the command) arrives in a per-launch env.sh, so this image
# never changes between apps.
{
  lib,
  runCommand,
  linuxPackages,
  pkgsStatic,
  makeModulesClosure,
  kmod,
  xz,
  cpio,
  gzip,
}: let
  kernel = linuxPackages.kernel;
  modules = makeModulesClosure {
    # Newer nixpkgs splits modules into their own output.
    kernel = kernel.modules or kernel;
    firmware = runCommand "no-firmware" {} "mkdir -p $out/lib/firmware";
    rootModules = ["virtio_mmio" "virtio_console" "virtiofs" "virtio_net"];
    allowMissing = true;
  };
in
  runCommand "nix-launch-microvm-guest-${kernel.version}" {
    nativeBuildInputs = [kmod xz cpio gzip];
    passthru = {inherit kernel;};
  } ''
    root=$PWD/root
    mkdir -p $root/bin $root/lib/m $root/{dev,proc,sys,tmp,run,etc,nix/store}

    # Real files, not store symlinks: /nix/store gets the host's store
    # mounted over it, which may not contain these paths.
    cp ${pkgsStatic.busybox}/bin/busybox $root/bin/busybox
    for applet in $($root/bin/busybox --list); do
      ln -sf busybox $root/bin/$applet
    done
    install -m755 ${../src/vm-init.sh} $root/init

    # Decompressed modules plus their load order, so busybox insmod is
    # all the guest needs.
    ver=${kernel.modDirVersion}
    for m in virtio_mmio virtio_console virtiofs virtio_net; do
      modprobe -d ${modules} -S $ver --show-depends $m
    done | awk '$1 == "insmod" { print $2 }' | awk '!seen[$0]++' > order
    while read -r ko; do
      name=$(basename "$ko"); name=''${name%.xz}; name=''${name%.zst}
      case "$ko" in
        *.xz) xz -dc "$ko" > $root/lib/m/$name ;;
        *) cp "$ko" $root/lib/m/$name ;;
      esac
      echo $name >> $root/lib/load-order
    done < order
    test -s $root/lib/load-order

    mkdir -p $out
    ln -s ${kernel}/bzImage $out/kernel
    (cd $root && find . | cpio -o -H newc --quiet | gzip -9) > $out/initrd
  ''
