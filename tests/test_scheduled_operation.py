import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HERMES_TMPDIR = Path.home() / ".hermes" / "cache" / "scratch"


class ScheduledOperationCheckTests(unittest.TestCase):
    def test_runnable_offline_end_to_end_check(self):
        if not HERMES_TMPDIR.is_dir():
            self.skipTest("Hermes scratch directory is unavailable")
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(HERMES_TMPDIR),
        }
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_scheduled_operation.py")],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
