import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path.home() / ".hermes" / "cache" / "scratch"


class EvidenceReplayTests(unittest.TestCase):
    def test_runnable_synthetic_report_replay(self):
        if not SCRATCH.is_dir():
            self.skipTest("Hermes scratch directory is unavailable")
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
            output = Path(temporary) / "proof"
            environment = {
                "TMPDIR": str(SCRATCH),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
            }
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "check_evidence_replay.py"), "--output", str(output)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["evidence_kind"], "synthetic_offline")
        self.assertEqual(summary["source_qualified_identities"], 2)
        self.assertEqual(summary["content_versions"], 3)
        self.assertEqual(summary["network_requests"], 0)
        self.assertEqual(summary["artifact_lineage_hashes"], "verified")


if __name__ == "__main__":
    unittest.main()
