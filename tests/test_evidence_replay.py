import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from need_radar import evidence
from need_radar.__main__ import select_items

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path.home() / ".hermes" / "cache" / "scratch"


class EvidenceReplayTests(unittest.TestCase):
    def test_exact_content_shares_storage_and_selection_but_keeps_identity_history(self):
        original_text = "Synthetic evidence with exact shared content."
        imports = [
            {"source": "reddit", "native_id": "shared", "text": original_text, "discovery_origin": "query:one"},
            {"source": "reddit", "native_id": "shared", "text": original_text, "discovery_origin": "query:two"},
            {"source": "reddit", "native_id": "alias", "text": original_text, "discovery_origin": "query:three"},
            {"source": "x", "native_id": "shared", "text": original_text, "discovery_origin": "query:four"},
        ]
        ledger = {}
        imported = evidence.import_normalized_items(ledger, imports)

        self.assertEqual(len(ledger["contents"]), 1)
        self.assertEqual(set(ledger["versions"]), {"reddit:shared", "reddit:alias", "x:shared"})
        self.assertNotIn("text", ledger["versions"]["reddit:shared"][imported[0]["content_version"]])
        self.assertEqual(len(imported), 3)
        self.assertEqual(imported[0]["discovery_origins"], [
            "query:four", "query:one", "query:three", "query:two",
        ])

        before_replay = json.dumps(ledger, sort_keys=True)
        self.assertEqual(evidence.import_normalized_items(ledger, imports), imported)
        self.assertEqual(json.dumps(ledger, sort_keys=True), before_replay)
        self.assertEqual(len(select_items(imported)), 1)
        self.assertEqual([reference["id"] for reference in select_items(imported)[0]["identity_versions"]], [
            "reddit:alias", "reddit:shared", "x:shared",
        ])
        bare_items = [
            {key: value for key, value in item.items() if key not in {"identity_versions", "content_version"}}
            for item in imported
        ]
        self.assertEqual(len(select_items(bare_items)), 1)
        self.assertEqual([reference["id"] for reference in select_items(bare_items)[0]["identity_versions"]], [
            "reddit:alias", "reddit:shared", "x:shared",
        ])

        edited = evidence.import_normalized_items(ledger, [{
            "source": "reddit",
            "native_id": "shared",
            "text": "Synthetic edited evidence with new exact content.",
            "discovery_origin": "query:edit",
        }])
        self.assertEqual(len(ledger["contents"]), 2)
        selected = select_items(edited)
        self.assertEqual(len(edited), 3)
        self.assertEqual(len(selected), 2)
        original = next(item for item in selected if item["text"] == original_text)
        self.assertEqual([reference["id"] for reference in original["identity_versions"]], [
            "reddit:alias", "reddit:shared", "x:shared",
        ])
        self.assertEqual(original["discovery_origins"], [
            "query:four", "query:one", "query:three", "query:two",
        ])
        edited_item = next(item for item in selected if item["text"] != original_text)
        self.assertEqual(edited_item["identity_versions"][0]["discovery_origins"], ["query:edit"])

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
        self.assertEqual(summary["source_qualified_identities"], 3)
        self.assertEqual(summary["identity_versions"], 4)
        self.assertEqual(summary["content_versions"], 2)
        self.assertEqual(summary["independent_items_before_edit"], 1)
        self.assertEqual(summary["independent_items_after_edit"], 2)
        self.assertEqual(summary["network_requests"], 0)
        self.assertEqual(summary["artifact_lineage_hashes"], "verified")


if __name__ == "__main__":
    unittest.main()
