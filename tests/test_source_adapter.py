import base64
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
from need_radar.source_adapter import (
    adapt_source_results,
    prepare_source_results,
    source_results_from_recordings,
    validate_canonical_evidence,
)


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
        self.assertEqual(normalization["coverage"]["sources"]["reddit"], {
            "status": "partial",
            "input_count": 3,
            "prepared_count": 3,
            "returned_count": 2,
            "omitted_count": 1,
            "omissions_unknown": True,
            "failure_count": 0,
            "omission_reasons": ["reddit:reddit-comment-deleted: withheld"],
            "failure_reasons": [],
        })
        self.assertEqual(normalization["coverage"]["sources"]["x"]["status"], "synthetic_complete")
        self.assertEqual(normalization["coverage"]["sources"]["x"]["input_count"], 2)
        self.assertEqual(normalization["coverage"]["sources"]["x"]["returned_count"], 2)

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
            self.assertIn("## Source coverage", (output / "report.md").read_text())
            self.assertNotIn("## Source coverage", (replay_output / "report.md").read_text())
            self.assertIn("Agent context handoff is repetitive", (replay_output / "report.md").read_text())

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

    def test_normalizer_failure_persists_per_source_coverage(self):
        failed = json.loads(json.dumps(self.fixture))
        failed["normalization_model"]["status"] = "timeout"
        failed["normalization_model"]["error"] = "synthetic timeout"
        hermes_tmp = Path.home() / ".hermes" / "cache" / "scratch"
        with tempfile.TemporaryDirectory(dir=hermes_tmp) as directory:
            root = Path(directory)
            fixture_path = self.write_fixture(root, failed)
            output = root / "run"
            result = self.run_cli(fixture_path, output, hermes_tmp)

            self.assertEqual(result.returncode, 1)
            self.assertIn("status=normalization_failure", result.stdout)
            validation = json.loads((output / "source-normalization.json").read_text())

        self.assertEqual(validation["status"], "normalization_failure")
        for source in ("reddit", "x"):
            source_coverage = validation["coverage"]["sources"][source]
            self.assertEqual(source_coverage["status"], "partial")
            self.assertGreater(source_coverage["failure_count"], 0)
            self.assertIn("normalizer call failed: synthetic timeout", source_coverage["failure_reasons"])

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
            reddit_coverage = snapshot["source_normalization"]["coverage"]["sources"]["reddit"]
            self.assertEqual(reddit_coverage["input_count"], 4)
            self.assertEqual(reddit_coverage["omitted_count"], 2)
            self.assertEqual(reddit_coverage["failure_count"], 1)
            self.assertEqual(snapshot["items"][0]["discovery_origin"]["access_token"], "[REDACTED]")
            for artifact in output.iterdir():
                if artifact.is_file():
                    self.assertNotIn(b"synthetic-redaction-probe", artifact.read_bytes(), artifact.name)

    def test_missing_source_is_not_reported_as_an_empty_success(self):
        source_results = json.loads(json.dumps(self.fixture["source_results"]))
        del source_results["x"]
        normalization_response = {
            "results": [
                row for row in self.fixture["normalization_model"]["response"]["results"]
                if row["record_id"].startswith("reddit:")
            ]
        }

        result = adapt_source_results(source_results, normalization_response)

        self.assertEqual(result["status"], "partial")
        x_coverage = result["coverage"]["sources"]["x"]
        self.assertEqual(x_coverage["status"], "missing")
        self.assertEqual(x_coverage["input_count"], 0)
        self.assertEqual(x_coverage["returned_count"], 0)
        self.assertEqual(x_coverage["omitted_count"], 0)
        self.assertTrue(x_coverage["omissions_unknown"])
        self.assertEqual(x_coverage["failure_count"], 1)
        self.assertIn("response envelope is missing or malformed", x_coverage["failure_reasons"][0])

        source_results["x"] = {"partial": False, "origin": {"query": "synthetic"}, "posts": []}
        empty_result = adapt_source_results(source_results, normalization_response)
        self.assertEqual(empty_result["coverage"]["sources"]["x"]["status"], "empty")
        self.assertEqual(empty_result["coverage"]["sources"]["x"]["failure_count"], 0)

    def test_recording_envelopes_keep_source_identity_provenance_and_partial_context(self):
        reddit_id = base64.b64encode(b"t3_post-1").decode().rstrip("=")
        reddit = {
            "provider": "recorded-reddit-route",
            "endpoint": "recorded-reddit-endpoint",
            "recorded_at": "2026-10-13T12:00:00Z",
            "request_parameters_sha256": "r" * 64,
            "request": {"parameters": {"subreddit_name": "synthetic"}},
            "response": {
                "http_status": 200,
                "body": {"data": {"subredditV3": {"elements": {
                    "edges": [{"node": {
                        "id": reddit_id,
                        "cells": [
                            {"__typename": "MetadataCell", "createdAt": "2026-10-12T11:00:00.000Z"},
                            {"__typename": "TitleCell", "title": "Synthetic Reddit title"},
                            {"__typename": "PreviewTextCell", "text": "Synthetic Reddit preview"},
                        ],
                    }}],
                    "pageInfo": {"endCursor": "synthetic-next-page"},
                }}}},
            },
        }
        x = {
            "http_status": 200,
            "recorded_at_epoch": 1791892800,
            "request": {
                "method": "GET",
                "route": "synthetic-x-route",
                "window": "synthetic-window",
                "parameters": {"query": "synthetic"},
            },
            "response": {
                "data": [{
                    "id": "100000000000000001",
                    "created_at": "2026-10-12T10:00:00.000Z",
                    "text": "Synthetic X post",
                    "article": {"title": "Synthetic X article title"},
                    "conversation_id": "100000000000000001",
                    "edit_history_tweet_ids": ["100000000000000001"],
                    "referenced_tweets": [
                        {"type": "replied_to", "id": "100000000000000000"},
                        {"type": "quoted", "id": "100000000000000002"},
                        {"type": "retweeted", "id": "100000000000000003"},
                    ],
                }],
                "meta": {"result_count": 1, "next_token": "synthetic-next-page"},
            },
        }
        source_results = source_results_from_recordings(
            reddit, x, {"reddit": "a" * 64, "x": "b" * 64}
        )
        prepared = prepare_source_results(source_results)
        response = {
            "results": [
                {"record_id": record["id"], "normalized_text": "local fake normalized text"}
                for record in prepared["records"]
            ]
        }

        result = adapt_source_results(source_results, response)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["evidence_kind"], "authentic_private_recording_offline_replay")
        self.assertEqual({item["id"] for item in result["items"]}, {f"reddit:t3_post-1", "x:100000000000000001"})
        reddit_item = next(item for item in result["items"] if item["source"] == "reddit")
        x_item = next(item for item in result["items"] if item["source"] == "x")
        self.assertEqual(reddit_item["timestamp"], "2026-10-12T11:00:00.000Z")
        self.assertEqual(reddit_item["text"], "Synthetic Reddit title\nSynthetic Reddit preview")
        self.assertEqual(reddit_item["discovery_origin"]["recording_sha256"], "a" * 64)
        self.assertTrue(reddit_item["flags"]["partial_coverage"])
        self.assertIsNone(reddit_item["flags"]["edited"])
        self.assertIsNone(reddit_item["flags"]["deleted"])
        self.assertIsNone(reddit_item["relationships"]["reply_to_id"])
        self.assertEqual(x_item["timestamp"], "2026-10-12T10:00:00.000Z")
        self.assertEqual(x_item["text"], "Synthetic X post\nSynthetic X article title")
        self.assertEqual(x_item["relationships"]["thread_id"], "x:100000000000000001")
        self.assertEqual(x_item["relationships"]["reply_to_id"], "x:100000000000000000")
        self.assertTrue(x_item["flags"]["partial_coverage"])
        self.assertFalse(x_item["flags"]["edited"])
        self.assertIsNone(x_item["flags"]["deleted"])
        self.assertEqual(x_item["discovery_origin"]["recording_sha256"], "b" * 64)
        self.assertEqual(x_item["references"], [])
        self.assertFalse(any(item["kind"] == "comment" for item in result["items"]))
        self.assertEqual(validate_canonical_evidence(result["items"]), [])

        no_pagination = json.loads(json.dumps(source_results))
        no_pagination["reddit"]["partial"] = None
        missing_coverage = prepare_source_results(no_pagination)
        self.assertTrue(any("envelope partial flag must be boolean" in error for error in missing_coverage["errors"]))

        unsupported_reddit = json.loads(json.dumps(reddit))
        del unsupported_reddit["response"]["body"]["data"]["subredditV3"]["elements"]["edges"]
        unsupported_x = json.loads(json.dumps(x))
        unsupported_x["response"]["data"] = {"unexpected": []}
        unsupported = source_results_from_recordings(unsupported_reddit, unsupported_x)
        rejected = adapt_source_results(unsupported, {"results": []})
        self.assertEqual(rejected["items"], [])
        self.assertEqual(rejected["coverage"]["sources"]["reddit"]["status"], "failed")
        self.assertEqual(rejected["coverage"]["sources"]["x"]["status"], "failed")

    def test_nonempty_frozen_context_reaches_shared_v0_extraction_seam(self):
        from need_radar import __main__ as app

        fixture = json.loads(json.dumps(self.fixture))
        fixture["model"] = {"status": "synthetic_response", "response": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hermes_tmp = root / "hermes-tmp"
            hermes_tmp.mkdir()
            fixture_path = self.write_fixture(root, fixture)
            output = root / "serve"
            with mock.patch.dict(os.environ, self.safe_environment(hermes_tmp), clear=True):
                with mock.patch.object(app, "execute_extraction", wraps=app.execute_extraction) as extraction:
                    status = app.run(fixture_path, output)

            self.assertEqual(status, "no_findings")
            extraction.assert_called_once()
            prompt = extraction.call_args.args[0]
            source_items = json.loads(prompt["messages"][1]["content"].split("\n", 1)[1])
            snapshot = read_frozen_snapshot(output / "snapshot.json")
            snapshot_ids = {item["id"] for item in snapshot["items"]}
            self.assertTrue(source_items)
            self.assertTrue(all(item["id"] in snapshot_ids for item in source_items))
            response = json.loads((output / "model-response.json").read_text())
            self.assertEqual(response["execution_seam"], "execute_extraction")
            self.assertEqual(response["status"], "success")

    def test_no_findings_report_keeps_missing_source_degradation_visible(self):
        fixture = json.loads(json.dumps(self.fixture))
        del fixture["source_results"]["x"]
        fixture["normalization_model"]["response"]["results"] = [
            row for row in fixture["normalization_model"]["response"]["results"]
            if row["record_id"].startswith("reddit:")
        ]
        fixture["model"]["response"] = []
        hermes_tmp = Path.home() / ".hermes" / "cache" / "scratch"
        with tempfile.TemporaryDirectory(dir=hermes_tmp) as directory:
            root = Path(directory)
            fixture_path = self.write_fixture(root, fixture)
            output = root / "run"
            result = self.run_cli(fixture_path, output, hermes_tmp)
            report = (output / "report.md").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Status: no_findings", report)
        self.assertIn("No findings.", report)
        self.assertIn("x: missing", report)
        self.assertIn("failures 1", report)
        self.assertIn("omissions unknown", report)

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
