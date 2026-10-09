import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import network_guard


network_guard.install()

from need_radar.snapshot import read_frozen_snapshot
from need_radar.source_adapter import adapt_source_results, prepare_source_results


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "source_results" / "synthetic_reddit_x.json"


class SourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def safe_environment(self, hermes_tmp):
        return {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(hermes_tmp),
            "HOME": str(hermes_tmp),
        }

    def run_cli(self, fixture_path, output, hermes_tmp):
        return subprocess.run(
            [sys.executable, "-m", "need_radar", "--fixture", str(fixture_path), "--output", str(output)],
            cwd=ROOT,
            env=self.safe_environment(hermes_tmp),
            capture_output=True,
            text=True,
        )

    def write_fixture(self, directory, fixture, name="input.json"):
        path = Path(directory) / name
        path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        return path

    def test_mock_reddit_and_x_results_normalize_serve_and_replay(self):
        prepared = prepare_source_results(self.fixture["source_results"])
        normalization = adapt_source_results(
            self.fixture["source_results"],
            self.fixture["normalization_model"]["response"],
        )
        self.assertEqual(normalization["status"], "partial")
        self.assertEqual(normalization["errors"], [])
        self.assertEqual(len(prepared["records"]), 5)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hermes_tmp = root / "hermes-tmp"
            hermes_tmp.mkdir()
            with mock.patch.dict(os.environ, self.safe_environment(hermes_tmp), clear=True):
                output = root / "serve"
                result = self.run_cli(FIXTURE, output, hermes_tmp)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=success", result.stdout)
            snapshot = read_frozen_snapshot(output / "snapshot.json")
            items = snapshot["items"]
            self.assertEqual(
                [item["id"] for item in items],
                [
                    "reddit:reddit-post-001",
                    "reddit:reddit-comment-001",
                    "reddit:reddit-comment-deleted",
                    "x:100000000000000001",
                    "x:100000000000000002",
                ],
            )
            post, comment, deleted, x_post, x_reply = items
            self.assertEqual(post["timestamp"], "2026-10-01T12:00:00Z")
            self.assertEqual(post["references"][0]["value"], "https://reddit.example/r/synthetic/comments/reddit-post-001")
            self.assertEqual(post["discovery_origin"]["query"], "synthetic agent context workflow")
            self.assertTrue(post["flags"]["edited"])
            self.assertEqual(comment["relationships"]["thread_id"], "reddit:reddit-post-001")
            self.assertEqual(comment["relationships"]["reply_to_id"], "reddit:reddit-post-001")
            self.assertTrue(comment["flags"]["partial_coverage"])
            self.assertTrue(deleted["flags"]["deleted"])
            self.assertTrue(deleted["flags"]["withheld"])
            self.assertEqual(deleted["text"], "")
            self.assertEqual(x_post["timestamp"], 1790856000)
            self.assertEqual(x_reply["kind"], "reply")
            self.assertEqual(x_reply["relationships"]["thread_id"], "x:100000000000000001")
            self.assertEqual(x_reply["relationships"]["reply_to_id"], "x:100000000000000001")
            self.assertEqual(x_reply["normalized_text"], "The user manually copies project notes into each session.")

            prompt = json.loads((output / "source-normalization-prompt.json").read_text())
            system_message, user_message = prompt["messages"]
            self.assertEqual(system_message["role"], "system")
            self.assertEqual(user_message["role"], "user")
            self.assertIn("UNTRUSTED SOURCE RESULTS", user_message["content"])
            self.assertNotIn("Ignore all rules", system_message["content"])
            self.assertNotIn("Synthetic removed content", user_message["content"])
            self.assertIn('"content_fields"', user_message["content"])
            normalization_call = json.loads((output / "source-normalization-response.json").read_text())
            self.assertEqual(normalization_call["boundary"], "synthetic_fixture")
            with sqlite3.connect(output / "lineage.sqlite3") as database:
                lineage = database.execute(
                    "SELECT stage, input_stage FROM stages ORDER BY sequence"
                ).fetchall()
            self.assertEqual(
                lineage[:4],
                [
                    ("source_normalization_prompt", None),
                    ("source_normalization_model_call", "source_normalization_prompt"),
                    ("source_normalization_validation", "source_normalization_model_call"),
                    ("snapshot", "source_normalization_validation"),
                ],
            )
            log_events = [json.loads(line) for line in (output / "logs.jsonl").read_text().splitlines()]
            normalization_log = next(
                event for event in log_events if event["stage"] == "source_normalization_validation"
            )
            self.assertTrue(normalization_log["attributes"]["artifact_id"])

            candidates = json.loads((output / "candidates.json").read_text())
            self.assertEqual(candidates["status"], "success")
            self.assertEqual(len(candidates["candidates"]), 1)
            report = (output / "report.md").read_text()
            self.assertIn("Agent context handoff is repetitive", report)

            replay_fixture = {
                "notice": snapshot["notice"],
                "items": items,
                "model": self.fixture["model"],
            }
            replay_path = self.write_fixture(root, replay_fixture, "replay.json")
            replay_output = root / "replay"
            replay = self.run_cli(replay_path, replay_output, hermes_tmp)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            replay_snapshot = read_frozen_snapshot(replay_output / "snapshot.json")
            self.assertEqual(replay_snapshot["items"], items)
            self.assertEqual((replay_output / "report.md").read_bytes(), (output / "report.md").read_bytes())

    def test_source_results_preserve_synthetic_judge_for_assessment(self):
        fixture = json.loads(json.dumps(self.fixture))
        evidence_id = "reddit:reddit-comment-001"
        fixture["judge"] = {
            "status": "synthetic_response",
            "response": [{
                "verdict": "NEEDS_EVIDENCE",
                "reason": "The cited report grounds repeated context rebuilding, but its practical impact is not established.",
                "evidence_ids": [evidence_id],
                "dimensions": {
                    "friction_clarity": {
                        "status": "supported",
                        "reason": "The author explicitly describes rebuilding tool context after resets.",
                        "evidence_ids": [evidence_id],
                    },
                    "pain_value": {
                        "status": "supported",
                        "reason": "The report describes repeated setup effort.",
                        "evidence_ids": [evidence_id],
                    },
                    "evidence_size": {
                        "status": "unknown",
                        "reason": "Frequency and practical consequences are not established.",
                        "evidence_ids": [evidence_id],
                    },
                    "solvability": {
                        "status": "unknown",
                        "reason": "The report does not establish a bounded intervention.",
                        "evidence_ids": [evidence_id],
                    },
                    "scope_fit": {
                        "status": "supported",
                        "reason": "The workflow concerns context use with coding agents.",
                        "evidence_ids": [evidence_id],
                    },
                },
                "uncertainties": [
                    "The frequency and consequence of repeated setup are unknown.",
                    "A bounded intervention is not established.",
                ],
            }],
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hermes_tmp = Path.home() / ".hermes" / "cache" / "scratch"
            fixture_path = self.write_fixture(root, fixture)
            output = root / "serve"
            with mock.patch.dict(os.environ, self.safe_environment(hermes_tmp), clear=True):
                result = self.run_cli(fixture_path, output, hermes_tmp)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=success", result.stdout)
            consolidation = json.loads((output / "consolidation.json").read_text())
            self.assertEqual(consolidation["arm"], "serve")
            self.assertEqual(len(consolidation["clusters"]), 1)
            assessment_result = json.loads((output / "assessment.json").read_text())
            self.assertEqual(assessment_result["status"], "success")
            self.assertEqual(assessment_result["assessments"][0]["verdict"], "NEEDS_EVIDENCE")
            self.assertEqual(assessment_result["coverage"]["kind"], "synthetic_offline")
            self.assertFalse(assessment_result["coverage"]["provider_invoked"])

    def test_invalid_normalizer_output_is_persisted_and_fails_closed(self):
        invalid = json.loads(json.dumps(self.fixture))
        invalid["normalization_model"]["response"]["results"][0]["authoritative_source_id"] = "invented"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hermes_tmp = root / "hermes-tmp"
            hermes_tmp.mkdir()
            fixture_path = self.write_fixture(root, invalid)
            output = root / "run"
            result = self.run_cli(fixture_path, output, hermes_tmp)
            self.assertEqual(result.returncode, 1)
            self.assertIn("status=invalid_model_output", result.stdout)
            validation = json.loads((output / "source-normalization.json").read_text())
            self.assertEqual(validation["status"], "invalid_model_output")
            self.assertFalse((output / "snapshot.json").exists())

    def test_malformed_source_rows_are_skipped_with_partial_provenance(self):
        partial = json.loads(json.dumps(self.fixture))
        partial["source_results"]["reddit"]["posts"][0]["comments"].append(None)
        partial["source_results"]["reddit"]["origin"]["access_token"] = "synthetic-redaction-probe"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hermes_tmp = root / "hermes-tmp"
            hermes_tmp.mkdir()
            fixture_path = self.write_fixture(root, partial)
            output = root / "run"
            result = self.run_cli(fixture_path, output, hermes_tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = read_frozen_snapshot(output / "snapshot.json")
            self.assertEqual(snapshot["source_normalization"]["status"], "partial")
            self.assertTrue(any("posts[0].comments[2] is malformed" in error for error in snapshot["source_normalization"]["errors"]))
            self.assertTrue(all(item["flags"]["partial_coverage"] for item in snapshot["items"][:3]))
            self.assertTrue(all(item["flags"]["incomplete"] for item in snapshot["items"][:3]))
            self.assertEqual(snapshot["items"][0]["discovery_origin"]["access_token"], "[REDACTED]")
            for artifact in output.iterdir():
                if artifact.is_file():
                    self.assertNotIn(b"synthetic-redaction-probe", artifact.read_bytes(), artifact.name)

    def test_network_credentials_and_live_state_are_denied_in_process_and_child(self):
        with tempfile.TemporaryDirectory() as directory:
            hermes_tmp = Path(directory) / "hermes-tmp"
            hermes_tmp.mkdir()
            with mock.patch.dict(os.environ, self.safe_environment(hermes_tmp), clear=True):
                with self.assertRaises(PermissionError):
                    socket.socket()
                with self.assertRaises(PermissionError):
                    open(network_guard.CREDENTIALS_PATH, "rb")
                with self.assertRaises(PermissionError):
                    open(network_guard.LIVE_STATE_PATH, "wb")

                script = """
import os, socket, network_guard
network_guard.install()
assert os.environ['TMPDIR'] == os.environ['HERMES_TMP_EXPECTED']
try:
    socket.socket()
except PermissionError:
    pass
else:
    raise SystemExit('network was not denied')
for path, mode in ((network_guard.CREDENTIALS_PATH, 'rb'), (network_guard.LIVE_STATE_PATH, 'wb')):
    try:
        open(path, mode)
    except PermissionError:
        pass
    else:
        raise SystemExit('protected path was not denied')
if any(any(marker in name.lower() for marker in ('secret', 'token', 'credential', 'api_key', 'password')) for name in os.environ):
    raise SystemExit('credential-named environment variable was inherited')
"""
                child_env = self.safe_environment(hermes_tmp)
                child_env["HERMES_TMP_EXPECTED"] = str(hermes_tmp)
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=ROOT,
                    env=child_env,
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
