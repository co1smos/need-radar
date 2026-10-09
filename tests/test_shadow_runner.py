import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import uuid
import unittest

import network_guard


ROOT = Path(__file__).resolve().parents[1]
HERMES_TMPDIR = Path.home() / ".hermes" / "cache" / "scratch"


def safe_environment():
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(HERMES_TMPDIR),
    }


def candidate(item_id, excerpt, title):
    return {
        "title": title,
        "friction": f"Synthetic friction evidenced by {excerpt}.",
        "evidence": [{"item_id": item_id, "excerpt": excerpt}],
    }


def settings(candidate_limit=10, max_tokens=50, request_limit=1):
    return {
        "provider": "synthetic_fixture",
        "model": "offline-recorded-response",
        "model_settings": {"fixture_mode": "synthetic"},
        "schema": {
            "version": 1,
            "candidate_fields": ["title", "friction", "evidence"],
            "evidence_fields": ["item_id", "excerpt"],
        },
        "retry_policy": {"max_attempts": 1},
        "evidence_policy": {"scope": "frozen_context", "citation": "exact_excerpt"},
        "request_limit": request_limit,
        "max_tokens": max_tokens,
        "candidate_limit": candidate_limit,
    }


def outcome(response=None, *, status="synthetic_response", error=None, requests=1, tokens=10):
    result = {"status": status, "usage": {"requests": requests, "tokens": tokens}}
    if response is not None:
        result["response"] = response
    if error is not None:
        result["error"] = error
    return result


class ShadowRunnerTests(unittest.TestCase):
    def setUp(self):
        if not HERMES_TMPDIR.is_dir():
            self.skipTest("Hermes TMPDIR is unavailable")
        network_guard.install()
        self.temporary_directory = tempfile.TemporaryDirectory(dir=HERMES_TMPDIR)
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def fixture_path(self, shadow_model, shared_settings=None):
        path = self.root / f"shadow-fixture-{uuid.uuid4().hex}.json"
        path.write_text(json.dumps({
            "provider": "synthetic_fixture",
            "experiment": {
                "shared_settings": shared_settings or settings(),
                "shadow_model": shadow_model,
            },
        }), encoding="utf-8")
        return path

    def run_shadow(self, serve_output, fixture, output):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "need_radar.shadow",
                "--serve-dir",
                str(serve_output),
                "--fixture",
                str(fixture),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            env=safe_environment(),
            capture_output=True,
            text=True,
        )

    def run_serve(self, shared_settings=None, model=None):
        fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text(encoding="utf-8"))
        fixture["model"] = model or fixture["model"]
        fixture["model"].setdefault("usage", {"requests": 1, "tokens": 10})
        fixture["experiment"] = {"shared_settings": shared_settings or settings()}
        fixture_path = self.root / f"serve-fixture-{uuid.uuid4().hex}.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        output = self.root / "serve"
        result = subprocess.run(
            [sys.executable, "-m", "need_radar", "--fixture", str(fixture_path), "--output", str(output)],
            cwd=ROOT,
            env=safe_environment(),
            capture_output=True,
            text=True,
        )
        return result, output

    def test_offline_end_to_end_runner_reports_observed_parity(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_shadow_runner.py")],
            cwd=ROOT,
            env=safe_environment(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["evidence_kind"], "synthetic_offline")
        self.assertEqual(summary["shadow_status"], "complete")
        self.assertTrue(summary["serve_report_preserved"])
        self.assertTrue(summary["parity"]["same_context"])
        self.assertTrue(summary["parity"]["shared_settings_identical"])
        self.assertTrue(summary["parity"]["only_extraction_instruction_differs"])
        self.assertTrue(summary["parity"]["same_execution_seam"])

    def test_candidate_cap_and_zero_findings_are_not_padded(self):
        serve_result, serve = self.run_serve(settings(candidate_limit=1))
        self.assertEqual(serve_result.returncode, 0, serve_result.stderr)
        first = candidate("reddit:fixture-1", "I manually copy the same project context", "Synthetic finding one")
        second = candidate("reddit:fixture-1", "session resets", "Synthetic finding two")
        fixture = self.fixture_path(outcome([first, second]), settings(candidate_limit=1))
        output = self.root / "shadow"
        result = self.run_shadow(serve, fixture, output)

        self.assertEqual(result.returncode, 0, result.stderr)
        comparison = json.loads((output / "comparison.json").read_text())
        v0 = json.loads((serve / "candidates.json").read_text())
        v1 = json.loads((output / "v1" / "candidates.json").read_text())
        self.assertEqual(comparison["status"], "complete")
        self.assertEqual(len(v0["candidates"]), 1)
        self.assertEqual(len(v1["candidates"]), 1)
        self.assertTrue(v1["candidate_limit_applied"])
        self.assertEqual(v1["submitted_candidate_count"], 1)
        self.assertTrue(comparison["parity"]["same_context"])
        self.assertEqual(comparison["arms"]["v0"]["resource_usage"], comparison["arms"]["v1"]["resource_usage"])
        v0_call = json.loads((serve / "model-response.json").read_text())
        v1_call = json.loads((output / "v1" / "model-response.json").read_text())
        self.assertEqual(v0_call["resolved_shared_settings"], v1_call["resolved_shared_settings"])
        self.assertFalse((output / "report.md").exists())

        zero_fixture = self.fixture_path(outcome([]), settings(candidate_limit=1))
        zero_output = self.root / "shadow-zero"
        zero = self.run_shadow(serve, zero_fixture, zero_output)
        self.assertEqual(zero.returncode, 0, zero.stderr)
        zero_result = json.loads((zero_output / "v1" / "candidates.json").read_text())
        self.assertEqual(zero_result["status"], "no_findings")
        self.assertEqual(zero_result["candidates"], [])

    def test_shadow_arm_failure_is_redacted_and_does_not_change_serve(self):
        serve_result, serve = self.run_serve(settings())
        self.assertEqual(serve_result.returncode, 0, serve_result.stderr)
        report_before = (serve / "report.md").read_bytes()
        fixture = self.fixture_path(outcome(status="failure", error="authorization: Bearer sk-SYNTHETICSECRET123456"))
        output = self.root / "shadow-failure"
        result = self.run_shadow(serve, fixture, output)

        self.assertEqual(result.returncode, 1)
        comparison = json.loads((output / "comparison.json").read_text())
        v1_call = json.loads((output / "v1" / "model-response.json").read_text())
        self.assertEqual(comparison["status"], "incomplete")
        self.assertEqual(comparison["arms"]["v0"]["status"], "success")
        self.assertEqual(comparison["arms"]["v1"]["status"], "extraction_failure")
        self.assertTrue(comparison["incomplete_reasons"])
        self.assertIn("[REDACTED]", v1_call["error"])
        self.assertNotIn("sk-SYNTHETICSECRET123456", v1_call["error"])
        for path in output.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"sk-SYNTHETICSECRET123456", path.read_bytes(), path.name)
        self.assertTrue(v1_call["lineage"]["artifact_id"])
        self.assertTrue(v1_call["lineage"]["input_artifact_id"])
        self.assertFalse((output / "report.md").exists())
        self.assertEqual((serve / "report.md").read_bytes(), report_before)

    def test_token_and_request_overages_mark_comparison_incomplete(self):
        serve_result, serve = self.run_serve(settings(max_tokens=50))
        self.assertEqual(serve_result.returncode, 0, serve_result.stderr)
        report_before = (serve / "report.md").read_bytes()
        for name, v1_model in (
            ("token", outcome([], tokens=99)),
            ("request", outcome([], requests=2)),
        ):
            with self.subTest(resource=name):
                fixture = self.fixture_path(v1_model, settings(max_tokens=50))
                output = self.root / f"shadow-{name}-overage"
                result = self.run_shadow(serve, fixture, output)
                self.assertEqual(result.returncode, 1)
                comparison = json.loads((output / "comparison.json").read_text())
                model_response = json.loads((output / "v1" / "model-response.json").read_text())
                self.assertEqual(comparison["arms"]["v0"]["status"], "success")
                self.assertEqual(comparison["arms"]["v1"]["status"], "resource_failure")
                self.assertEqual(model_response["status"], "resource_failure")
                self.assertEqual((serve / "report.md").read_bytes(), report_before)

    def test_settings_mismatch_is_incomplete_and_does_not_run_shadow_arm(self):
        serve_result, serve = self.run_serve(settings(max_tokens=50))
        self.assertEqual(serve_result.returncode, 0, serve_result.stderr)
        fixture = self.fixture_path(outcome([]), settings(max_tokens=49))
        output = self.root / "shadow-settings-mismatch"
        result = self.run_shadow(serve, fixture, output)
        self.assertEqual(result.returncode, 1)
        comparison = json.loads((output / "comparison.json").read_text())
        v1_call = json.loads((output / "v1" / "model-response.json").read_text())
        self.assertEqual(comparison["status"], "incomplete")
        self.assertEqual(comparison["arms"]["v0"]["status"], "success")
        self.assertEqual(comparison["arms"]["v1"]["status"], "not_run_parity_failure")
        self.assertFalse(comparison["parity"]["shared_settings_identical"])
        self.assertEqual(v1_call["resolved_shared_settings"]["max_tokens"], 49)
        self.assertEqual(v1_call["attempted_requests"], 0)
        self.assertFalse(v1_call["invoked"])
        self.assertIsNone(v1_call["response"])
        self.assertIn("shared extraction settings", v1_call["error"])
        self.assertFalse(comparison["parity"]["same_execution_seam"])

    def test_v0_resource_overage_does_not_suppress_independent_shadow_attempt(self):
        serve_result, serve = self.run_serve(
            settings(max_tokens=50),
            {
                "status": "synthetic_response",
                "response": [],
                "usage": {"requests": 1, "tokens": 99},
            },
        )
        self.assertEqual(serve_result.returncode, 1)
        fixture = self.fixture_path(outcome([]), settings(max_tokens=50))
        output = self.root / "shadow-v0-resource-overage"
        result = self.run_shadow(serve, fixture, output)
        self.assertEqual(result.returncode, 1)
        comparison = json.loads((output / "comparison.json").read_text())
        v1_call = json.loads((output / "v1" / "model-response.json").read_text())
        self.assertEqual(comparison["status"], "incomplete")
        self.assertEqual(comparison["arms"]["v0"]["status"], "resource_failure")
        self.assertEqual(comparison["arms"]["v1"]["status"], "no_findings")
        self.assertEqual(v1_call["attempted_requests"], 1)

    def test_zero_request_ceiling_prevents_fixture_call(self):
        from need_radar.__main__ import execute_extraction, make_prompt

        shared = settings(request_limit=0)
        calls = []
        status, error, artifact, response, _ = execute_extraction(
            make_prompt([], version="v1", shared_settings=shared),
            lambda prompt: calls.append(prompt),
            shared,
        )
        self.assertEqual(status, "resource_failure")
        self.assertIn("request ceiling prevents", error)
        self.assertEqual(artifact["attempted_requests"], 0)
        self.assertEqual(response, None)
        self.assertEqual(calls, [])

    def test_failed_serve_is_not_replaced_by_shadow(self):
        serve_result, serve = self.run_serve(
            settings(),
            {"status": "failure", "error": "synthetic serve failure", "usage": {"requests": 1, "tokens": 10}},
        )
        self.assertEqual(serve_result.returncode, 1)
        report_before = (serve / "report.md").read_bytes()
        fixture = self.fixture_path(outcome([]))
        output = self.root / "shadow-after-serve-failure"
        result = self.run_shadow(serve, fixture, output)
        self.assertEqual(result.returncode, 1)
        self.assertIn("status=incomplete", result.stdout + result.stderr)
        comparison = json.loads((output / "comparison.json").read_text())
        self.assertEqual(comparison["arms"]["v0"]["status"], "extraction_failure")
        self.assertEqual(comparison["arms"]["v1"]["status"], "no_findings")
        self.assertEqual((serve / "report.md").read_bytes(), report_before)
        self.assertFalse((output / "report.md").exists())

    def test_network_credentials_and_live_state_are_denied_here_and_in_child(self):
        network_guard.install()
        with self.assertRaises(PermissionError):
            socket.socket()
        for path in (network_guard.CREDENTIALS_PATH, network_guard.LIVE_STATE_PATH):
            with self.assertRaises(PermissionError):
                open(path, "rb")
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os, socket\n"
                "assert not any(any(x in k.lower() for x in ('secret','token','credential','api_key','password')) for k in os.environ)\n"
                "try: socket.socket()\n"
                "except PermissionError: print('network=denied')\n"
                "else: raise SystemExit('network=allowed')\n"
                "for p in ('/home/ubuntu/projects/need-radar/credentials.env','/home/ubuntu/.local/state/need-radar'):\n"
                " try: open(p, 'rb')\n"
                " except PermissionError: print('protected=denied')\n"
                " else: raise SystemExit('protected=allowed')",
            ],
            cwd=ROOT,
            env=safe_environment(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn("network=denied", child.stdout)
        self.assertIn("protected=denied", child.stdout)

    def test_candidate_cap_defaults_to_ten_when_omitted(self):
        from need_radar.__main__ import redact, resolve_shared_settings

        shared = settings()
        del shared["candidate_limit"]
        resolved, errors = resolve_shared_settings({"experiment": {"shared_settings": shared}})
        self.assertEqual(errors, [])
        self.assertEqual(resolved["candidate_limit"], 10)
        self.assertEqual(redact({"max_tokens": 50, "tokens": 7}), {"max_tokens": 50, "tokens": 7})


if __name__ == "__main__":
    unittest.main()
