#!/bin/sh
# PID 1 of the microvm guest (busybox). Mounts the host's store and the
# per-launch share, sources the env.sh launch wrote there (mounts, user,
# network, the command as "$@"), runs the command as that user on the
# serial console, reports its exit status back through the share, and
# powers off.
export PATH=/bin
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev
mkdir -p /dev/pts /dev/shm
mount -t devpts devpts /dev/pts
mount -t tmpfs tmpfs /dev/shm
mount -t tmpfs tmpfs /tmp
mount -t tmpfs tmpfs /run
# insmod, unlike modprobe, doesn't pick up "module.param=" from the kernel
# command line, and that's where qemu lists the virtio-mmio devices.
while read -r m; do
  params=$(tr ' ' '\n' < /proc/cmdline | sed -n "s/^${m%.ko}\.//p")
  insmod "/lib/m/$m" $params 2>/dev/null
done < /lib/load-order
if grep -q launch.debug /proc/cmdline; then
  echo "cmdline: $(cat /proc/cmdline)"
  for d in /sys/bus/virtio/devices/*; do echo "virtio: $d $(cat $d/device 2>/dev/null) $(cat $d/driver_override 2>/dev/null) -> $(readlink $d/driver)"; done
  ls /sys/bus/platform/devices | grep -i virtio
  set -x
fi

# The app's stdio. With a terminal on the host side it's a virtio console
# (a real tty: Ctrl-C, job control); without one, three raw virtio-serial
# ports (stdin, stdout, stderr), so pipes get exact bytes and stdin's EOF
# arrives as EOF. Kernel messages stay on ttyS0 (a log, or the screen with
# LAUNCH_VM_DEBUG).
port() {
  for p in /sys/class/virtio-ports/*; do
    [ "$(cat "$p/name" 2>/dev/null)" = "$1" ] && echo "/dev/${p##*/}" && return
  done
}
if grep -q launch.tty /proc/cmdline; then
  console=/dev/hvc0 input=/dev/hvc0 errout=/dev/hvc0 tty=1
else
  input=$(port launch.in) console=$(port launch.out) errout=$(port launch.err) tty=
fi
fail() {
  echo "launch-vm: $*" > "${errout:-/dev/ttyS0}"
  [ -e /dev/ttyS0 ] && echo "launch-vm: $*" > /dev/ttyS0
  echo 125 > /run/launch/status 2>/dev/null
  sync
  reboot -f > /dev/null 2>&1
}

# Helpers env.sh calls.
share() { # TAG PATH
  mkdir -p "$2" && mount -t virtiofs "$1" "$2" || fail "couldn't mount $2"
}
scratch() { # PATH
  mkdir -p "$1" && mount -t tmpfs tmpfs "$1"
}

mkdir -p /run/launch
mount -t virtiofs launch /run/launch || fail "no launch share"
mount -t virtiofs -o ro store /nix/store || fail "no store share"
. /run/launch/env.sh

echo "root:x:0:0::/:/bin/sh" > /etc/passwd
echo "$LAUNCH_USER:x:$LAUNCH_UID:$LAUNCH_GID::$HOME:/bin/sh" >> /etc/passwd
echo "root:x:0:" > /etc/group
echo "users:x:$LAUNCH_GID:" >> /etc/group
echo "127.0.0.1 localhost launch" > /etc/hosts
hostname launch

ip link set lo up
if [ "$LAUNCH_NET" = 1 ]; then
  # qemu's user-mode network always uses these addresses, so no DHCP.
  ip link set eth0 up
  ip addr add 10.0.2.15/24 dev eth0
  ip route add default via 10.0.2.2
  echo "nameserver 10.0.2.3" > /etc/resolv.conf
fi

cd "$LAUNCH_CWD" 2>/dev/null || cd "$HOME" || cd /

if [ -n "$tty" ]; then
  # Hold it open so the size sticks (tty settings reset on last close).
  exec 3<> "$console"
  [ -n "$LAUNCH_ROWS" ] && stty -F "$console" rows "$LAUNCH_ROWS" cols "$LAUNCH_COLS"
  # setsid -c makes the console the controlling terminal, so Ctrl-C and
  # job control reach the app.
  setsid -c chpst -u "$LAUNCH_USER:users" "$@" <> "$console" >&0 2>&0
else
  chpst -u "$LAUNCH_USER:users" "$@" < "$input" > "$console" 2> "$errout"
fi
echo $? > /run/launch/status
sync
reboot -f > /dev/null 2>&1
