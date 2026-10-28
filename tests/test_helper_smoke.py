"""Safety tests for the optional no-network Windscribe helper smoke script."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "helper-smoke.sh"


class HelperSmokeGuardTests(unittest.TestCase):
    def test_shell_syntax(self):
        run = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_explicit_opt_in_required_before_any_podman_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "called"
            fake = root / "podman"
            fake.write_text("#!/bin/sh\nprintf called > \"$MARKER\"\nexit 99\n")
            fake.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"], MARKER=str(marker))
            for args in ([], ["random-container"], ["--smoke"], ["--smoke", "a", "b"]):
                run = subprocess.run(["sh", str(SCRIPT), *args], env=env,
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 2, (args, run.stderr))
                self.assertFalse(marker.exists(), "Podman was called without explicit opt-in")


if __name__ == "__main__":
    unittest.main()
