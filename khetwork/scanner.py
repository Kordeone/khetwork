"""Bounded, fail-closed orchestration for an external *isolated* VPN probe runner.

This module does not start Windscribe, touch the primary VPN or change routes.
A future verified runner must exclusively own its VPN namespace and credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# The only protocol confirmed in the existing KHOMELAB deployment so far.
SUPPORTED_PROTOCOLS = frozenset({"wstunnel:443"})
LOCATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,79}\Z")
SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY,
  location TEXT NOT NULL,
  protocol TEXT NOT NULL,
  checked_at INTEGER NOT NULL,
  success INTEGER NOT NULL CHECK(success IN (0, 1)),
  latency_ms REAL,
  reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_recent
  ON attempts(location, protocol, checked_at DESC);
"""


@dataclass(frozen=True)
class Candidate:
    location: str
    protocol: str


def inventory(path: Path) -> list[Candidate]:
    """Inventory is manually curated until CLI discovery is safely integrated."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Inventory must be a JSON list")
    result: list[Candidate] = []
    seen = set()
    for item in data:
        if not isinstance(item, dict) or set(item) != {"location", "protocol"}:
            raise ValueError("Each candidate needs exactly location and protocol")
        location, protocol = item["location"], item["protocol"]
        if not isinstance(location, str) or not LOCATION.fullmatch(location):
            raise ValueError("Invalid location label")
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError(f"Unverified or unsupported protocol: {protocol!r}")
        candidate = Candidate(location, protocol)
        if candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def due_candidates(db: sqlite3.Connection, candidates: list[Candidate], now: int,
                   interval_seconds: int, limit: int) -> list[Candidate]:
    last = {(location, protocol): checked_at for location, protocol, checked_at in
            db.execute("""SELECT location, protocol, MAX(checked_at)
                          FROM attempts GROUP BY location, protocol""")}
    # Never-tested first, then the oldest checked — bounded rotation.
    due = [c for c in candidates if (c.location, c.protocol) not in last or
           now - last[c.location, c.protocol] >= interval_seconds]
    due.sort(key=lambda c: (last.get((c.location, c.protocol), -1),
                            c.location, c.protocol))
    return due[:limit]


def probe(runner: Path, candidate: Candidate, timeout: int) -> tuple[bool, float | None, str]:
    """Runner contract: executable LOCATION PROTOCOL -> one JSON line.

    Successful exit: {"ok": true, "latency_ms": 123.4}; failure:
    {"ok": false, "reason": "connection-failed"}. All other results fail closed.
    The runner MUST implement isolation; this orchestrator does not claim it.
    """
    try:
        process = subprocess.Popen(
            [str(runner), candidate.location, candidate.protocol],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    except OSError:
        return False, None, "runner-unavailable"
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The runner owns an entire isolated VPN session. Kill its process
        # group, not just the wrapper, if it fails to clean up on time.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        return False, None, "runner-timeout"
    if process.returncode != 0:
        return False, None, "runner-error"
    try:
        data = json.loads(stdout)
        if not isinstance(data, dict) or type(data.get("ok")) is not bool:
            raise ValueError("Invalid result")
        if not data["ok"]:
            return False, None, "probe-failed"
        latency = data["latency_ms"]
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not (0 <= latency < 1_000_000):
            raise ValueError("Invalid latency")
        return True, float(latency), "ok"
    except (ValueError, KeyError, TypeError):
        return False, None, "invalid-runner-result"


def run_scan(db: sqlite3.Connection, candidates: list[Candidate], runner: Path,
             limit: int, timeout: int, interval_hours: float, now: int | None = None) -> list[dict]:
    if not runner.is_file() or not runner.stat().st_mode & 0o111:
        raise ValueError("Runner must be an executable file")
    if not 1 <= limit <= 10 or not 1 <= timeout <= 300 or not 0.25 <= interval_hours <= 168:
        raise ValueError("Invalid scan limit, timeout or interval")
    now = int(time.time()) if now is None else now
    targets = due_candidates(db, candidates, now, int(interval_hours * 3600), limit)
    results = []
    for c in targets:
        ok, latency, reason = probe(runner, c, timeout)
        checked_at = int(time.time()) if now is None else now
        db.execute("""INSERT INTO attempts(location, protocol, checked_at, success, latency_ms, reason)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                   (c.location, c.protocol, checked_at, int(ok), latency, reason))
        db.commit()
        results.append({"location": c.location, "protocol": c.protocol,
                        "checked_at": checked_at, "ok": ok,
                        "latency_ms": latency, "reason": reason})
    return results


def report(db: sqlite3.Connection, count: int = 20) -> list[dict]:
    rows = db.execute("""SELECT location, protocol, checked_at, success, latency_ms, reason
                       FROM attempts ORDER BY checked_at DESC, id DESC LIMIT ?""", (count,))
    return [{"location": a, "protocol": b, "checked_at": c, "ok": bool(d),
             "latency_ms": e, "reason": f} for a, b, c, d, e, f in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Khetwork Windscribe scanner prototype; no live runner bundled")
    parser.add_argument("command", choices=("due", "scan", "report"))
    parser.add_argument("--db", type=Path, default=Path("scanner-results.sqlite3"))
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--interval-hours", type=float, default=2.0)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.limit <= 10 or not 1 <= args.timeout <= 300 or not 0.25 <= args.interval_hours <= 168:
            raise ValueError("Invalid scan limit, timeout or interval")
        if args.command != "report" and args.inventory is None:
            raise ValueError("--inventory is required")
        if args.command == "scan" and args.runner is None:
            raise ValueError("--runner is required; no unsafe direct Windscribe fallback")
        candidates = inventory(args.inventory) if args.inventory is not None else []
        with open_database(args.db) as db:
            if args.command == "due":
                result = [vars(c) for c in due_candidates(db, candidates, int(time.time()),
                          int(args.interval_hours * 3600), args.limit)]
            elif args.command == "scan":
                result = run_scan(db, candidates, args.runner.resolve(), args.limit,
                                  args.timeout, args.interval_hours)
            else:
                result = report(db)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"scanner: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
