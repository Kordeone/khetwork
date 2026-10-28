# Khetwork — initial Windscribe scanner prototype

Khetwork is intended to allow selection of **KHROXY or XRAT at installation time**. Neither tool is a dependency of the initial scanner. This repository currently contains only a standalone scanner prototype, **not** the Khetwork installer or a live VPN gateway.

## What works now

- Bounded, oldest-first candidate selection with a default 2-hour revisit interval.
- One external probe per candidate, up to 3 candidates per run (maximum 10).
- SQLite audit history including success/failure, latency, and check timestamp.
- Hard failure on missing runner; **no direct network or primary VPN fallback**.
- Strict runner-result validation, subprocess timeout, sanitized error codes.
- A disposable, read-only-to-primary namespace/isolation preflight for KHOMELAB.
- No runtime dependencies beyond Python 3.11+ (and `podman`/`unshare` for optional preflight).

## Does NOT work yet

- **No real Windscribe connection is made.** There is no working isolated Windscribe helper/client runner yet.
- No automatic discovery of Windscribe locations, no throughput or multi-protocol measurements.
- The scanner does not verify a runner's security boundary itself. **Only use a runner that has independently passed namespace, filesystem, socket, route and egress tests.**
- No integrated scheduler: call the CLI every 2 hours using a future scoped scheduler; the CLI filters recently checked entries.
- The preflight's `/home` overlay might be rejected by Podman's mount restrictions; a failure is a genuine blocker, not a reason to loosen primary permissions.

## Offline tests

```sh
python3 -m unittest discover -s tests -v
```

## Offline demonstration

```sh
chmod +x scripts/example-runner.py
printf '%s\n' '[{"location":"Berlin-Berghain","protocol":"wstunnel:443"}]' > /tmp/khetwork-candidates.json
python3 -m khetwork.scanner due --db /tmp/khetwork-scanner.sqlite3 --inventory /tmp/khetwork-candidates.json
python3 -m khetwork.scanner scan --db /tmp/khetwork-scanner.sqlite3 --inventory /tmp/khetwork-candidates.json --runner scripts/example-runner.py
python3 -m khetwork.scanner report --db /tmp/khetwork-scanner.sqlite3
```

`example-runner.py` is a synthetic test fixture, not a real VPN benchmark. Never interpret its results as Windscribe performance.

## First guarded KHOMELAB experiment

From a **trusted checkout on KHOMELAB** after reviewing the script:

```sh
sh scripts/isolation-preflight.sh khroxy-windscribe-v4-online
```

It enters a temporary nested user/network/mount/IPC/PID namespace as `wsclient`, overlays `/run`, `/etc/windscribe`, `/var/lib/windscribe`, and `/home`, and verifies primary sockets/configuration are hidden. **It never launches Windscribe**, changes persistent VPN settings, modifies host network rules, or touches the existing credentials.

If this passes, the next prototype milestone is to launch a distinct helper/client with private D-Bus and runtime state and probe one `wstunnel:443` location. That will require a separate safety and live-network verification stage, not simply relying on the preflight.

## Security choices

No credentials in the repository. Do not run the scanner using the primary Windscribe account's live directories. No host-wide firewall, hotspot, SSH or Nginx modifications. Do not expose an HTTP management listener. Existing KHROXY and XRAT installations remain independent.
