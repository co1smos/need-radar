import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import need_radar.scheduled as scheduled

ROOT = Path(__file__).resolve().parents[1]
HERMES_TMPDIR = Path.home() / ".hermes" / "cache" / "scratch"


class ScheduledOperationCheckTests(unittest.TestCase):
    def test_automatic_missed_check_selects_latest_slot_past_grace(self):
        if not HERMES_TMPDIR.is_dir():
            self.skipTest("Hermes scratch directory is unavailable")
        with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR) as temporary_directory:
            temporary_directory = Path(temporary_directory)
            config_path = temporary_directory / "scheduled.json"
            config_path.write_text(json.dumps({
                "version": 1,
                "enabled": True,
                "schedule": {
                    "interval_seconds": 3600,
                    "anchor_at": "2026-10-08T00:00:00Z",
                    "grace_seconds": 300,
                },
                "budget": {"requests": 1, "tokens": 1},
                "timeout_seconds": 30,
                "state_db": "schedule.sqlite3",
                "output_root": "schedule-runs",
                "serve_fixture": "unused-serve-fixture.json",
                "shadow_fixture": "unused-shadow-fixture.json",
            }), encoding="utf-8")
            observed_at = datetime(2026, 10, 8, 1, 0, 30, tzinfo=timezone.utc)

            class FixedDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return observed_at

            output = io.StringIO()
            with (
                patch.object(scheduled, "datetime", FixedDatetime),
                patch.object(sys, "argv", ["need-radar-scheduled", "missed", "--config", str(config_path)]),
                patch.dict(os.environ, {"TMPDIR": str(HERMES_TMPDIR)}),
                contextlib.redirect_stdout(output),
            ):
                exit_code = scheduled.main()

        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "missed")
        self.assertEqual(result["scheduled_for"], "2026-10-08T00:00:00Z")

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
