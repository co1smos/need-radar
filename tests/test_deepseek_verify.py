import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import network_guard
from need_radar import deepseek_verify as verify


ROOT = Path(__file__).resolve().parents[1]
HERMES_TMPDIR = Path.home() / ".hermes" / "cache" / "scratch"
TODAY = "2026-10-13"


def safe_environment(extra=None):
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(HERMES_TMPDIR),
    }
    if extra:
        environment.update(extra)
    return environment


def response_body(prompt_tokens=50, completion_tokens=2, content="[]", model=verify.MODEL):
    return json.dumps({
        "id": "synthetic-request-id",
        "model": model,
        "system_fingerprint": "synthetic-fingerprint",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 10},
            "prompt_cache_hit_tokens": 10,
            "prompt_cache_miss_tokens": prompt_tokens - 10,
        },
    }).encode()


class DeepSeekVerificationTests(unittest.TestCase):
    def setUp(self):
        if not HERMES_TMPDIR.is_dir():
            self.skipTest("Hermes TMPDIR is unavailable")
        network_guard.install()
        self.temp = tempfile.TemporaryDirectory(dir=HERMES_TMPDIR)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_route(self, requests=1):
        return verify.run_verification(
            requests,
            TODAY,
            state_dir=self.root / "state",
            api_key="synthetic-test-key",
            today=verify.datetime.strptime(TODAY, "%Y-%m-%d").date(),
        )

    def stub_response(self, status=200, body=None):
        return status, {"x-request-id": "synthetic-request-id"}, body or response_body()

    def test_pricing_reservation_is_ceil_of_peak_token_ceiling(self):
        self.assertEqual(verify.MAX_REQUEST_RESERVATION_MICRO_USD, 608)
        self.assertEqual(3 * verify.MAX_REQUEST_RESERVATION_MICRO_USD, 1824)
        self.assertLess(3 * verify.MAX_REQUEST_RESERVATION_MICRO_USD, verify.BUDGET_LIMIT_MICRO_USD)
        self.assertEqual(verify._cost_micro_usd(1000, 256), 608)

    def test_success_uses_shared_seam_and_persists_private_replay(self):
        with mock.patch.object(verify, "_send", return_value=self.stub_response()) as send:
            report = self.run_route()

        self.assertEqual(send.call_count, 1)
        body, key = send.call_args.args
        decoded = json.loads(body)
        payload = decoded
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(decoded["model"], "deepseek-flash")
        self.assertEqual(key, "synthetic-test-key")
        self.assertEqual(decoded["max_tokens"], 256)
        self.assertEqual(decoded["thinking"], {"type": "disabled"})
        self.assertIn("synthetic", decoded["messages"][1]["content"].lower())
        self.assertEqual(decoded["messages"], verify.SYNTHETIC_PROMPT["messages"])
        self.assertEqual(report["calls"][0]["status"], "success")
        self.assertEqual(report["calls"][0]["usage"], {"requests": 1, "tokens": 52})
        self.assertEqual(report["calls"][0]["usage_derived_estimated_cost_micro_usd"], 18)
        self.assertIsNone(report["calls"][0]["account_billed_usd"])
        self.assertEqual(report["budget"]["requests_reserved_or_dispatched"], 1)
        self.assertEqual(report["budget"]["usage_derived_estimate_micro_usd"], 18)
        self.assertEqual(report["budget"]["account_billed_usd"], None)
        self.assertEqual(report["observability"]["local_span_count"], 1)
        self.assertEqual(report["observability"]["local_spans_written"], 1)
        run_directory = Path(report["evidence_directory"])
        replay = next(run_directory.glob("*.replay.json"))
        evidence = json.loads(replay.read_text())
        self.assertEqual(evidence["response"]["returned_model"], verify.MODEL)
        self.assertEqual(evidence["response"]["assistant_content"], "[]")
        self.assertEqual(evidence["response"]["usage"]["prompt_tokens"], 50)
        self.assertEqual(evidence["response"]["usage"]["prompt_cache_miss_tokens"], 40)
        self.assertFalse(evidence["telemetry"]["external_export_enabled"])
        self.assertEqual(stat_mode(replay), 0o600)
        self.assertEqual(stat_mode(run_directory), 0o700)
        traces = [json.loads(line) for line in (run_directory / "trace.jsonl").read_text().splitlines()]
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["name"], "deepseek_model_call")
        self.assertEqual(traces[0]["metadata"]["model"], verify.MODEL)
        self.assertEqual(
            evidence["request_sha256"],
            verify._sha256(json.dumps(
                evidence["request"], ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            ).encode()),
        )

    def test_response_content_redacts_the_in_memory_credential(self):
        details, _ = verify._response_details(
            200,
            {},
            response_body(content='{"secret":"synthetic-test-key"}'),
            "synthetic-test-key",
        )
        self.assertNotIn("synthetic-test-key", details["assistant_content"])
        self.assertIn("[REDACTED]", details["assistant_content"])

    def test_model_mismatch_fails_without_retry(self):
        with mock.patch.object(
            verify, "_send", return_value=self.stub_response(body=response_body(model="other-model"))
        ) as send:
            report = self.run_route(requests=3)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["calls"][0]["error"], "provider_model_mismatch")
        self.assertEqual(report["budget"]["requests_reserved_or_dispatched"], 1)

    def test_three_successes_share_persistent_request_and_spend_caps(self):
        with mock.patch.object(verify, "_send", return_value=self.stub_response()) as send:
            report = self.run_route(requests=3)
            fourth = self.run_route()
        self.assertEqual(send.call_count, 3)
        self.assertEqual(len(report["calls"]), 3)
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["budget"]["requests_reserved_or_dispatched"], 3)
        self.assertEqual(report["budget"]["usage_derived_estimate_micro_usd"], 54)
        self.assertEqual(report["budget"]["held_unknown_or_in_flight_micro_usd"], 0)
        self.assertEqual(fourth["calls"][0]["attempted_requests"], 0)

    def test_unknown_outcome_keeps_reservation_and_blocks_later_dispatch(self):
        with mock.patch.object(verify, "_send", side_effect=TimeoutError("synthetic timeout")) as send:
            first = self.run_route()
            second = self.run_route()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(first["calls"][0]["error"], "transport_outcome_unknown")
        self.assertEqual(first["budget"]["held_unknown_or_in_flight_micro_usd"], 608)
        self.assertEqual(second["calls"][0]["attempted_requests"], 0)
        self.assertEqual(second["calls"][0]["error"], "an earlier request outcome is unknown; reconcile before dispatch")
        error = {"type": "VerificationError", "message": "transport_outcome_unknown"}
        run_directory = Path(first["evidence_directory"])
        trace = json.loads((run_directory / "trace.jsonl").read_text().splitlines()[0])
        log = json.loads((run_directory / "logs.jsonl").read_text().splitlines()[0])
        self.assertEqual(trace["status"], "failed")
        self.assertEqual(trace["error"], error)
        self.assertEqual(log["status"], "failed")
        self.assertEqual(log["error"], error)
        otlp = json.loads((run_directory / "langfuse-otlp.jsonl").read_text())
        otlp_span = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(otlp_span["status"], {"code": 2, "message": "transport_outcome_unknown"})
        otlp_attributes = {item["key"]: item["value"]["stringValue"] for item in otlp_span["attributes"]}
        self.assertEqual(json.loads(otlp_attributes["need_radar.error"]), error)

    def test_known_failed_call_cannot_be_retried_across_cli_runs(self):
        with mock.patch.object(verify, "_send", return_value=self.stub_response(status=429)) as send:
            first = self.run_route()
            second = self.run_route()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(first["calls"][0]["error"], "http_status_429")
        self.assertEqual(second["calls"][0]["attempted_requests"], 0)
        self.assertEqual(second["calls"][0]["error"], "an earlier request failed; retries are disabled")
        self.assertEqual(second["budget"]["requests_reserved_or_dispatched"], 1)

    def test_response_validation_and_pricing_date_fail_closed(self):
        with mock.patch.object(
            verify, "_send", return_value=self.stub_response(body=response_body(content="not-json"))
        ) as send:
            report = self.run_route()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(report["calls"][0]["error"], "invalid_or_incomplete_model_response")
        with self.assertRaises(verify.VerificationError):
            verify.run_verification(
                1, "2026-10-12", state_dir=self.root / "other-state", api_key="synthetic",
                today=verify.datetime.strptime(TODAY, "%Y-%m-%d").date(),
            )

    def test_nonempty_json_array_fails_without_retry(self):
        with mock.patch.object(
            verify, "_send", return_value=self.stub_response(body=response_body(content="[123]"))
        ) as send:
            report = self.run_route(requests=3)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["calls"][0]["error"], "invalid_or_incomplete_model_response")

    def test_budget_spend_ceiling_rejects_without_dispatch(self):
        store = verify.BudgetStore(self.root / "state")
        store.reserve("synthetic-historical-attempt", "synthetic-hash", TODAY)
        store.settle("synthetic-historical-attempt", "success", usage_estimate_micro_usd=49_500)
        with self.assertRaisesRegex(verify.BudgetLimit, "cumulative spend reservation would exceed \\$0\\.05"):
            store.reserve("synthetic-over-budget", "synthetic-hash", TODAY)

    def test_http_transport_does_not_follow_redirect_or_retry(self):
        class FakeResponse:
            status = 302

            def getheader(self, name):
                return "synthetic-request-id" if name == "x-request-id" else None

            def read1(self, size):
                return b""

        class FakeConnection:
            sock = None

            def __init__(self):
                self.requests = []
                self.closed = False

            def request(self, method, path, body, headers):
                self.requests.append((method, path, body, headers))

            def getresponse(self):
                return FakeResponse()

            def close(self):
                self.closed = True

        connection = FakeConnection()
        with mock.patch.object(verify.http.client, "HTTPSConnection", return_value=connection) as factory:
            status, headers, body = verify._send(b"{}", "synthetic-key")
        self.assertEqual(factory.call_args.kwargs, {"timeout": 20})
        self.assertEqual(status, 302)
        self.assertEqual(len(connection.requests), 1)
        self.assertEqual(connection.requests[0][:2], ("POST", "/chat/completions"))
        self.assertEqual(connection.requests[0][3]["Authorization"], "Bearer synthetic-key")
        self.assertTrue(connection.closed)
        self.assertEqual(body, b"")

    def test_subprocess_cli_network_denial_keeps_unknown_reservation(self):
        credential = self.root / "deepseek.env"
        credential.write_text("DEEPSEEK_API_KEY=synthetic-subprocess-key\n")
        credential.chmod(0o600)
        state = self.root / "subprocess-state"
        code = (
            "import sys; from pathlib import Path; from datetime import datetime, timezone; "
            "import network_guard; network_guard.install(); "
            "from need_radar import deepseek_verify as module; "
            "module.DEFAULT_STATE_DIR = Path(sys.argv[1]); "
            "module._utc_now = lambda: datetime(2026, 10, 13, tzinfo=timezone.utc); "
            "module.load_api_key = lambda: 'synthetic-subprocess-key'; "
            "sys.argv = ['deepseek_verify', '--pricing-verified-on', '2026-10-13']; "
            "raise SystemExit(module.main())"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(state)],
            cwd=ROOT,
            env=safe_environment(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["calls"][0]["error"], "transport_outcome_unknown")
        self.assertEqual(report["budget"]["held_unknown_or_in_flight_micro_usd"], 608)
        self.assertNotIn("synthetic-subprocess-key", result.stdout + result.stderr)
        connection = sqlite3.connect(state / "budget.sqlite3")
        try:
            row = connection.execute("SELECT status, reserved_micro_usd FROM attempts").fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("unknown", 608))

    def test_request_limit_and_canonical_state_path_are_rejected(self):
        with self.assertRaises(verify.VerificationError):
            verify.run_verification(4, TODAY, state_dir=self.root / "state", api_key="synthetic")
        with self.assertRaises(verify.VerificationError):
            verify.BudgetStore(verify.CANONICAL_STATE_DIR / "ticket-12")

    def test_subprocess_denies_network_credentials_and_canonical_state(self):
        network_command = [
            sys.executable,
            "-c",
            "import network_guard; network_guard.install(); "
            "from need_radar.deepseek_verify import _send; _send(b'{}', 'synthetic-key')",
        ]
        network = subprocess.run(
            network_command,
            cwd=ROOT,
            env=safe_environment(),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(network.returncode, 0)
        self.assertIn("network access is disabled", network.stderr)

        protected_credential = self.root / "credential.env"
        protected_credential.write_text("DEEPSEEK_API_KEY=synthetic-only\n")
        protected_credential.chmod(0o600)
        credential_code = (
            "import sys, network_guard; network_guard.install(); "
            "from need_radar.deepseek_verify import load_api_key; "
            "load_api_key(sys.argv[1])"
        )
        credential = subprocess.run(
            [sys.executable, "-c", credential_code, str(protected_credential)],
            cwd=ROOT,
            env=safe_environment({"NEED_RADAR_TEST_DENY_PATH": str(protected_credential)}),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(credential.returncode, 0)
        self.assertIn("credential access is disabled", credential.stderr)

        canonical = subprocess.run(
            [
                sys.executable,
                "-c",
                "import network_guard; network_guard.install(); "
                "from pathlib import Path; "
                "Path('/home/ubuntu/.local/state/need-radar/test-denied').write_text('x')",
            ],
            cwd=ROOT,
            env=safe_environment(),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(canonical.returncode, 0)
        self.assertIn("live state access is disabled", canonical.stderr)

    def test_subprocess_cli_offline_validation_never_reads_credentials(self):
        code = (
            "import sys; from datetime import datetime, timezone; "
            "import network_guard; network_guard.install(); "
            "from need_radar import deepseek_verify as module; "
            "original_load = module.load_api_key; "
            "module._utc_now = lambda: datetime(2026, 10, 13, tzinfo=timezone.utc); "
            "module.load_api_key = lambda: original_load(network_guard.DEEPSEEK_CREDENTIALS_PATH); "
            "sys.argv = ['deepseek_verify', '--pricing-verified-on', '2026-10-13']; "
            "raise SystemExit(module.main())"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
            ],
            cwd=ROOT,
            env=safe_environment(),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('"status": "blocked"', result.stdout)
        self.assertIn("credential access is disabled", result.stdout)


def stat_mode(path):
    return Path(path).stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
