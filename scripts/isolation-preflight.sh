#!/bin/sh
# Non-destructive experiment: never launches Windscribe or manipulates primary VPN.
# Intended to run on KHOMELAB with the existing rootless Podman container.
set -eu
container="${1:-khroxy-windscribe-v4-online}"
primary_ns="$(podman exec "$container" readlink /proc/1/ns/net)"
echo "Primary network namespace: $primary_ns"
podman exec --user wsclient -e "PRIMARY_NS=$primary_ns" "$container" \
  unshare --user --map-root-user --net --mount --ipc --pid --fork sh -ec '
    mount --make-rprivate /
    # These overlays are ephemeral and never modify the primary files.
    mount -t tmpfs -o size=2m scan-run /run
    mount -t tmpfs -o size=2m scan-etc /etc/windscribe
    mount -t tmpfs -o size=2m scan-var /var/lib/windscribe
    mount -t tmpfs -o size=4m scan-home /home
    mkdir -p /home/wsclient/.config/Windscribe /home/wsclient/.local/share/Windscribe
    current_ns="$(readlink /proc/self/ns/net)"
    test "$current_ns" != "$PRIMARY_NS" || { echo "FAIL: network shared"; exit 1; }
    test ! -e /run/windscribe/helper.sock || { echo "FAIL: helper socket visible"; exit 1; }
    test ! -e /home/wsclient/.config/Windscribe/Windscribe2.conf || { echo "FAIL: original config visible"; exit 1; }
    test -z "$(ip route show default)" || { echo "FAIL: unexpected route"; exit 1; }
    echo "PASS: private network, runtime, state and home"
    echo "This is a preflight only; a second Windscribe service has NOT been started."
  '
