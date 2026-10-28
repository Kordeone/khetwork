#!/usr/bin/env python3
"""OFFLINE TEST DOUBLE. Never connects to Windscribe. Never use for real rankings."""
import json
import sys

if len(sys.argv) != 3:
    sys.exit(2)
location, protocol = sys.argv[1:]
print(json.dumps({"ok": True, "latency_ms": 42.0}))
