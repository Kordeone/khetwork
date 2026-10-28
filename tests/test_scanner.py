import json
import stat
import tempfile
import unittest
from pathlib import Path
from khetwork.scanner import Candidate, due_candidates, inventory, open_database, probe, report, run_scan, main


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = open_database(self.root / "state.sqlite")
        self.addCleanup(self.db.close)
        self.candidates = [Candidate("Berlin-Berghain", "wstunnel:443"),
                           Candidate("Frankfurt-Wurstchen", "wstunnel:443")]

    def runner(self, contents):
        path = self.root / "runner"
        path.write_text("#!/usr/bin/env python3\n" + contents)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_inventory_deduplicates_and_rejects_invalid(self):
        inv = self.root / "candidates.json"
        inv.write_text(json.dumps([vars(self.candidates[0]), vars(self.candidates[0])]))
        self.assertEqual(inventory(inv), [self.candidates[0]])
        for data in ([{"location": "../oops", "protocol": "wstunnel:443"}],
                     [{"location": "Berlin", "protocol": "untested:443"}],
                     {"location": "Berlin"}):
            inv.write_text(json.dumps(data))
            with self.assertRaises(ValueError): inventory(inv)

    def test_due_rotation_last_checked(self):
        runner = self.runner('import json\nprint(json.dumps({"ok": True, "latency_ms": 35}))\n')
        self.assertEqual(len(run_scan(self.db, self.candidates, runner, 1, 5, 2, now=1000)), 1)
        self.assertEqual([c.location for c in due_candidates(self.db, self.candidates, 1050, 7200, 3)],
                         ["Frankfurt-Wurstchen"])
        self.assertEqual(len(run_scan(self.db, self.candidates, runner, 2, 5, 2, now=1050)), 1)
        self.assertEqual(due_candidates(self.db, self.candidates, 1500, 7200, 3), [])
        self.assertEqual(len(due_candidates(self.db, self.candidates, 9000, 7200, 3)), 2)

    def test_success_persisted(self):
        runner = self.runner('import json\nprint(json.dumps({"ok": True, "latency_ms": 12.5}))\n')
        result = run_scan(self.db, self.candidates[:1], runner, 1, 5, 2, now=1000)
        self.assertEqual(result[0]["latency_ms"], 12.5)
        self.assertEqual(report(self.db)[0]["reason"], "ok")
        self.db.close()
        with open_database(self.root / "state.sqlite") as other:
            self.assertEqual(report(other)[0]["latency_ms"], 12.5)

    def test_runner_error_timeout_and_invalid_data_fail_closed(self):
        for content, expected in [
            ('import sys\nsys.exit(1)\n', "runner-error"),
            ('print("not-json")\n', "invalid-runner-result"),
            ('import json\nprint(json.dumps({"ok": True, "latency_ms": -1}))\n', "invalid-runner-result"),
            ('import time\ntime.sleep(2)\n', "runner-timeout"),
            ('print("{}")\n', "invalid-runner-result"),
            ('import json\nprint(json.dumps({"ok": False, "reason": "secret=abc"}))\n', "probe-failed"),
        ]:
            runner = self.runner(content)
            ok, latency, reason = probe(runner, self.candidates[0], 1)
            self.assertFalse(ok)
            self.assertIsNone(latency)
            self.assertEqual(reason, expected)

    def test_requires_explicit_runner(self):
        inv = self.root / "candidates.json"
        inv.write_text(json.dumps([vars(self.candidates[0])]))
        self.assertEqual(main(["scan", "--inventory", str(inv), "--db", str(self.root / "cli.sqlite")]), 2)

    def test_invalid_runtime_limits_rejected(self):
        runner = self.runner('print("{}")\n')
        for limit, timeout, interval in [(0, 10, 2), (1, 0, 2), (1, 10, 0.1), (11, 10, 2)]:
            with self.assertRaises(ValueError):
                run_scan(self.db, self.candidates, runner, limit, timeout, interval)

    def test_no_shell_interpolation_for_location(self):
        location = Candidate("Berlin (test)", "wstunnel:443")
        runner = self.runner('import sys, json\nprint(json.dumps({"ok": True, "latency_ms": 1}))\n')
        self.assertTrue(probe(runner, location, 2)[0])


if __name__ == "__main__":
    unittest.main()
