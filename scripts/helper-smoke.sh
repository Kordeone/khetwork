#!/bin/sh
# Opt-in, no-network Windscribe helper smoke test inside the existing container.
# It does NOT launch the Windscribe client, connect a tunnel or change the parent.
set -eu

if [ "${1:-}" != "--smoke" ] || [ "$#" -ne 2 ]; then
  echo "Usage: sh scripts/helper-smoke.sh --smoke CONTAINER" >&2
  echo "No helper is started without the explicit --smoke option." >&2
  exit 2
fi

container="$2"
primary_netns="$(podman exec "$container" readlink /proc/1/ns/net)"
primary_pidns="$(podman exec "$container" readlink /proc/1/ns/pid)"
echo "Primary netns before: $primary_netns"
echo "Primary pidns before: $primary_pidns"

# Podman denies nested /proc mounts in this container. Keep the separate PID
# namespace, but do not ask unshare to remount /proc. /proc remains inherited,
# so this is only a bounded smoke test, NOT a production isolation model.
# An isolated /proc mount is a release gate for any live scanner runner.
if podman exec --user wsclient \
  -e "PRIMARY_NETNS=$primary_netns" \
  -e "PRIMARY_PIDNS=$primary_pidns" "$container" \
  unshare --user --map-root-user --net --mount --ipc --pid --fork \
  sh -ec '
    mount --make-rprivate /

    # Each private mount must succeed before we even attempt to start the helper.
    mount -t tmpfs -o size=4m scan-run /run
    mount -t tmpfs -o size=2m scan-etc /etc/windscribe
    mount -t tmpfs -o size=2m scan-var /var/lib/windscribe
    mount -t tmpfs -o size=4m scan-home /home
    mount -t tmpfs -o size=2m scan-log /var/log
    mount -t tmpfs -o size=2m scan-tmp /tmp

    umask 077
    mkdir -p /home/wsclient/.config/Windscribe /home/wsclient/.local/share/Windscribe

    # Refuse to launch if an inherited resolver bind cannot be covered.
    printf "nameserver 127.0.0.1\n" >/run/scan-resolv.conf
    mount --bind /run/scan-resolv.conf /etc/resolv.conf

    test "$(id -u)" -eq 0
    test "$(readlink /proc/self/ns/net)" != "$PRIMARY_NETNS"
    test "$(readlink /proc/self/ns/pid)" != "$PRIMARY_PIDNS"
    test -z "$(ip route show default)"
    test ! -e /run/windscribe/helper.sock
    test ! -e /run/dbus/system_bus_socket
    test ! -e /home/wsclient/.config/Windscribe/Windscribe2.conf

    echo "PASS: isolated root identity, PID, network, runtime, state, home and resolver"
    echo "Launching helper for a bounded OFFLINE startup smoke test only."

    /opt/windscribe/helper >/run/helper.log 2>&1 &
    helper_pid=$!
    sleep 3

    if kill -0 "$helper_pid" 2>/dev/null; then
      echo "PASS: helper process survived 3 seconds in scanner namespace"
      rc=0
    else
      echo "BLOCKED: helper exited in private namespace; needs separate service setup"
      rc=1
    fi

    kill "$helper_pid" 2>/dev/null || :
    wait "$helper_pid" 2>/dev/null || :
    exit "$rc"
  '; then
  smoke_rc=0
else
  smoke_rc=$?
fi

after_netns="$(podman exec "$container" readlink /proc/1/ns/net)"
[ "$after_netns" = "$primary_netns" ] || {
  echo "FAIL: primary container network namespace changed" >&2
  exit 1
}
echo "PASS: primary network namespace unchanged"
exit "$smoke_rc"
