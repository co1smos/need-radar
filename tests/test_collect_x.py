import json
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import network_guard

network_guard.install()

import collect_x


def approved_inputs(retention_seconds=collect_x.RETENTION_SECONDS, **limit_overrides):
    limits = {
        "page_size": 2,
        "max_pages": 3,
        "max_items": 6,
        "max_attempts_per_page": 2,
        "max_total_attempts": 6,
        "timeout_seconds": 2,
        "retry_delay_seconds": 0,
    }
    limits.update(limit_overrides)
    return {
        "approval_id": "synthetic-test-only",
        "provider": "treg-managed-anyapi",
        "route": "anyapi.x.search.posts",
        "query": "AI application workflow",
        "query_type": "Latest",
        "window": {"since": "2026-10-01", "until": "2026-10-03"},
        "limits": limits,
        "internal_source_policy": "explicitly-allow-synthetic-source",
        "gates": {
            gate: {
                "verified": True,
                "evidence": "synthetic test fixture",
                **({"retention_seconds": retention_seconds, "derived_removal_verified": True} if gate == "retention_and_removal" else {}),
            }
            for gate in collect_x.REQUIRED_GATES
        },
    }


def response_body(name):
    return json.loads((ROOT / "fixtures" / "x" / name).read_text())


class MemoryResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.closed = False

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.body)
        body, self.body = self.body[:size], self.body[size:]
        return body

    def close(self):
        self.closed = True


class LocalProvider:
    def __init__(self, steps, before_reply=None, delay=0):
        self.steps = list(steps)
        self.last_step = self.steps[-1] if self.steps else {"status": 500}
        self.before_reply = before_reply
        self.delay = delay
        self.requests = []
        self.responses = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, _url, data, headers, _timeout):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            index = len(self.requests)
            request = {"body": json.loads(data), "headers": dict(headers)}
            self.requests.append(request)
            step = self.steps.pop(0) if self.steps else self.last_step
            self.last_step = step
        try:
            if self.before_reply:
                self.before_reply(request)
            if self.delay:
                time.sleep(self.delay)
            if callable(step):
                step = step(request, index)
            response = MemoryResponse(step.get("status", 200), step.get("headers", {}), step.get("body", {}))
            self.responses.append(response)
            return response
        finally:
            with self.lock:
                self.active -= 1

    def close(self):
        pass


def cli_environment():
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT / "tests"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": os.environ.get("TMPDIR", ""),
    }


class NetworkGuardTests(unittest.TestCase):
    def test_network_credentials_and_live_state_are_denied(self):
        with self.assertRaisesRegex(PermissionError, "network access"):
            socket.socket()
        with self.assertRaisesRegex(PermissionError, "credential access"):
            sys.audit("open", network_guard.CREDENTIALS_PATH, "r", 0)
        with self.assertRaisesRegex(PermissionError, "live state access"):
            sys.audit("open", str(Path(network_guard.LIVE_STATE_PATH) / "state.json"), "r", 0)

    def test_cli_children_inherit_network_denial(self):
        result = subprocess.run(
            [sys.executable, "-c", "import socket; socket.socket()"],
            capture_output=True,
            text=True,
            env=cli_environment(),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("network access is disabled", result.stderr)


class LiveGateTests(unittest.TestCase):
    def test_live_review_is_enforced_inside_reusable_collector(self):
        with mock.patch.object(collect_x, "_load_credentials", side_effect=AssertionError("must not load credentials")) as loader:
            with self.assertRaisesRegex(collect_x.CollectorError, "review"):
                collect_x.collect(approved_inputs())
        loader.assert_not_called()

    def test_offline_collection_requires_explicit_synthetic_credentials(self):
        for token in (None, "live-token-looking-value"):
            with self.subTest(token_supplied=token is not None), tempfile.TemporaryDirectory() as temporary:
                state_dir = Path(temporary) / "state"
                offline_transport = collect_x.OfflineTransport(lambda *_args: None)
                with mock.patch.object(collect_x, "_load_credentials", side_effect=AssertionError("credentials must not load")) as loader:
                    with self.assertRaisesRegex(collect_x.CollectorError, "explicit synthetic token"):
                        collect_x.collect(
                            approved_inputs(),
                            state_dir=state_dir,
                            offline_transport=offline_transport,
                            token=token,
                        )
                loader.assert_not_called()
                self.assertFalse(state_dir.exists())

    def test_cli_maps_failed_collector_summary_to_nonzero(self):
        inputs = approved_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            approval_path = Path(temporary) / "approval.json"
            review_path = Path(temporary) / "review.json"
            approval_path.write_text(json.dumps(inputs))
            review = {
                "ticket": 9,
                "collector_sha256": collect_x._script_sha256(),
                "result": "approved",
                "checks": ["budget_reservations", "secret_handling"],
            }
            review_path.write_text(json.dumps(review))
            summary = {
                "outcome": "failed",
                "stop_reason": "spend_limit",
                "acquisition_failure": "request_failure",
            }
            with mock.patch.object(collect_x, "collect", return_value=summary) as collect:
                with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                    status = collect_x.main([
                        "collect", "--ticket", "9", "--approval-file", str(approval_path),
                        "--review-file", str(review_path),
                    ])
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.getvalue()), summary)
            collect.assert_called_once_with(inputs, review=review)

    def test_status_reports_unreconciled_attempt_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            state = collect_x._new_state()
            state["attempt_count"] = 1
            state["attempts"].append({"status": "unknown", "cost_micro_usd": 0})
            collect_x._save_state(state_dir, state)
            with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                status = collect_x.main(["status", "--state-dir", str(state_dir)])
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "paused")

    def test_live_review_does_not_allow_custom_budget_paths(self):
        review = {
            "ticket": 9,
            "collector_sha256": collect_x._script_sha256(),
            "result": "approved",
            "checks": ["budget_reservations", "secret_handling"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(collect_x, "_http_request") as request:
                with self.assertRaisesRegex(collect_x.CollectorError, "canonical"):
                    collect_x.collect(
                        approved_inputs(),
                        state_dir=Path(temporary) / "state",
                        recording_dir=Path(temporary) / "recordings",
                        token="synthetic-secret",
                        review=review,
                    )
                request.assert_not_called()


    def test_live_collection_without_recorded_approval_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            recording_dir = Path(temporary) / "recordings"
            environment = cli_environment()
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "collect_x.py"),
                    "collect",
                    "--ticket",
                    "9",
                ],
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("approved input", result.stderr.lower())


class CollectionTests(unittest.TestCase):
    def collect(self, provider, root, inputs=None, token="synthetic-secret", **kwargs):
        state_dir = Path(root) / "state"
        recording_dir = Path(root) / "recordings"
        return collect_x.collect(
            inputs or approved_inputs(),
            state_dir,
            recording_dir,
            token=token,
            org="synthetic-team",
            offline_transport=collect_x.OfflineTransport(provider),
            **kwargs,
        ), state_dir, recording_dir

    def test_pagination_window_validation_and_conversation_limitations(self):
        first_page = response_body("page-1.json")
        first_page["found"] = first_page["output"].pop("found")
        provider = LocalProvider(
            [
                {
                    "body": first_page,
                    "headers": {
                        "X-Treg-Cost-Micro": "750",
                        "X-Treg-Call-Id": "call-1",
                        "X-Treg-Served-Via": "direct",
                    },
                },
                {"body": response_body("page-2.json"), "headers": {"X-Treg-Cost-Micro": "750", "X-Treg-Call-Id": "call-2"}},
            ]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, state_dir, recording_dir = self.collect(provider, temporary)
                recordings = [json.loads(path.read_text()) for path in recording_dir.glob("*.json")]
                first, second = sorted(recordings, key=lambda record: record["request"]["page_number"])
                self.assertEqual(result["successful_requests"], 2)
                self.assertEqual(result["stop_reason"], "provider_no_next_cursor")
                self.assertEqual(first["request"]["query"], "AI application workflow since:2026-10-01 until:2026-10-03")
                self.assertIsNone(first["request"]["cursor"])
                self.assertEqual(second["request"]["cursor"], "synthetic-cursor-2")
                self.assertEqual([item["id"] for item in first["response"]["body"]["items"]], ["990000000000000001"])
                self.assertEqual(first["validation"]["out_of_window_count"], 1)
                self.assertEqual(second["response"]["body"]["items"][0]["createdUtc"], 1790942400)
                self.assertTrue(second["response"]["body"]["items"][0]["isReply"])
                coverage = result["coverage"]
                self.assertIn("search/reply samples only", coverage["conversation"])
                self.assertIn("not established", coverage["conversation"])
                self.assertIn("provider cursor ended", coverage["pagination"])
                self.assertEqual(collect_x.load_state(state_dir)["successful_requests"], 2)
                self.assertEqual(
                    first["coverage"]["pagination"],
                    "next cursor recorded; continuation available in this run",
                )
                self.assertEqual(first["billing"]["served_via"], "direct")
        finally:
            provider.close()

    def test_page_limit_stops_with_incomplete_pagination(self):
        provider = LocalProvider(
            [{"body": response_body("page-1.json"), "headers": {"X-Treg-Cost-Micro": "750"}}]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, _ = self.collect(
                    provider, temporary, approved_inputs(max_pages=1, max_items=2, max_total_attempts=2)
                )
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(result["stop_reason"], "page_limit")
                self.assertIn("incomplete", result["coverage"]["pagination"])
        finally:
            provider.close()

    def test_missing_cursor_does_not_claim_pagination_exhaustion(self):
        body = response_body("page-1.json")
        del body["output"]["data"]["nextCursor"]
        provider = LocalProvider([{"body": body, "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, recording_dir = self.collect(provider, temporary)
                recording = json.loads(next(recording_dir.glob("*.json")).read_text())
                self.assertEqual(result["stop_reason"], "cursor_missing")
                self.assertIn("does not establish exhaustion", result["coverage"]["pagination"])
                self.assertFalse(recording["response"]["body"]["next_cursor_present"])
        finally:
            provider.close()

    def test_provider_response_cannot_exceed_requested_page_size(self):
        body = response_body("page-1.json")
        body["output"]["data"]["items"].append(
            {"id": "990000000000000004", "text": "over-limit", "createdUtc": 1790856000}
        )
        provider = LocalProvider([{"body": body, "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, recording_dir = self.collect(provider, temporary)
                recording = json.loads(next(recording_dir.glob("*.json")).read_text())
                self.assertEqual(result["stop_reason"], "provider_page_limit_exceeded")
                self.assertEqual(result["items_this_run"], 0)
                self.assertIsNone(recording["response"]["body"])
                self.assertIn("response_page_limit_exceeded", recording["validation"]["errors"])
        finally:
            provider.close()

    def test_served_source_cannot_change_between_pages(self):
        alternate = response_body("page-2.json")
        alternate["source"]["id"] = "unapproved-alternate"
        provider = LocalProvider(
            [
                {"body": response_body("page-1.json"), "headers": {"X-Treg-Cost-Micro": "750"}},
                {"body": alternate, "headers": {"X-Treg-Cost-Micro": "750"}},
            ]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, recording_dir = self.collect(provider, temporary)
                records = [json.loads(path.read_text()) for path in recording_dir.glob("*.json")]
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(result["stop_reason"], "provider_source_changed")
                second = next(record for record in records if record["request"]["page_number"] == 2)
                self.assertIsNone(second["response"]["body"])
                self.assertIn("served_source_changed", second["validation"]["errors"])
        finally:
            provider.close()

    def test_treg_overflow_response_is_recorded_and_stops_pagination(self):
        provider = LocalProvider(
            [{
                "body": response_body("page-1.json"),
                "headers": {
                    "X-Treg-Cost-Micro": "750",
                    "X-Treg-Served-Via": "overflow:synthetic-relay",
                },
            }]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, recording_dir = self.collect(provider, temporary)
                recording = json.loads(next(recording_dir.glob("*.json")).read_text())
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(result["stop_reason"], "provider_overflow")
                self.assertEqual(recording["billing"]["served_via"], "overflow:synthetic-relay")
                self.assertIn("provider_overflow", recording["validation"]["errors"])
        finally:
            provider.close()

    def test_bounded_transient_retry_counts_attempts_not_failed_successes(self):
        provider = LocalProvider(
            [
                {"status": 503, "body": response_body("transient-error.json"), "headers": {"X-Treg-Cost-Micro": "0"}},
                {"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}},
            ]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, state_dir, _ = self.collect(provider, temporary)
                state = collect_x.load_state(state_dir)
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(state["attempt_count"], 2)
                self.assertEqual(result["successful_requests"], 1)
                self.assertEqual(state["charged_micro_usd"], 750)
        finally:
            provider.close()

    def test_authentication_failure_stops_without_retry(self):
        provider = LocalProvider(
            [{"status": 401, "body": response_body("auth-error.json"), "headers": {"X-Treg-Cost-Micro": "0"}}]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, state_dir, _ = self.collect(provider, temporary)
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(result["stop_reason"], "authentication_failure")
                self.assertEqual(collect_x.load_state(state_dir)["successful_requests"], 0)
        finally:
            provider.close()

    def test_missing_cost_cap_gate_fails_before_transport(self):
        provider = LocalProvider(
            [{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                inputs = approved_inputs()
                del inputs["gates"]["route_cost_cap"]
                with self.assertRaisesRegex(collect_x.CollectorError, "route_cost_cap"):
                    self.collect(provider, temporary, inputs)
                self.assertEqual(len(provider.requests), 0)
        finally:
            provider.close()

    def test_twenty_five_successes_and_failed_attempts_are_counted_separately(self):
        def page(request, index):
            body = response_body("page-1.json")
            body["output"]["data"]["items"] = []
            body["output"]["data"]["nextCursor"] = f"cursor-{index + 1}"
            return {"body": body, "headers": {"X-Treg-Cost-Micro": "750"}}

        steps = [
            {"status": 503, "body": response_body("transient-error.json"), "headers": {"X-Treg-Cost-Micro": "0"}},
            *([page] * 25),
        ]
        provider = LocalProvider(steps)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                inputs = approved_inputs(max_pages=25, max_items=50, max_total_attempts=50)
                result, state_dir, _ = self.collect(provider, temporary, inputs)
                state = collect_x.load_state(state_dir)
                self.assertEqual(result["successful_requests"], 25)
                self.assertEqual(result["stop_reason"], "success_limit")
                self.assertEqual(state["attempt_count"], 26)
                self.assertEqual(state["charged_micro_usd"], 25 * 750)
                self.assertEqual(len(provider.requests), 26)
                second, _, _ = self.collect(provider, temporary, inputs)
                self.assertEqual(second["stop_reason"], "success_limit")
                self.assertEqual(len(provider.requests), 26)
        finally:
            provider.close()

    def test_charged_errors_persist_and_route_cap_uses_remaining_balance(self):
        provider = LocalProvider(
            [
                {"status": 400, "body": response_body("transient-error.json"), "headers": {"X-Treg-Cost-Micro": "200000"}},
                {"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "50000"}},
            ]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                inputs = approved_inputs()
                _, state_dir, _ = self.collect(provider, temporary, inputs)
                self.assertEqual(provider.requests[0]["headers"]["X-Treg-Route-Max-Cost"], "0.250000")
                # A new collector instance resumes the same ticket-wide budget.
                result, _, _ = self.collect(provider, temporary, inputs)
                state = collect_x.load_state(state_dir)
                self.assertEqual(provider.requests[1]["headers"]["X-Treg-Route-Max-Cost"], "0.050000")
                self.assertEqual(result["successful_requests"], 1)
                self.assertEqual(state["charged_micro_usd"], collect_x.BUDGET_MICRO_USD)
                self.assertEqual(result["stop_reason"], "spend_limit")
                self.assertEqual(result["outcome"], "bounded_stop")
                stopped, _, _ = self.collect(provider, temporary, inputs)
                self.assertEqual(stopped["stop_reason"], "spend_limit")
        finally:
            provider.close()

    def test_reservation_is_persisted_before_dispatch_and_headers_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            synced_directories = []
            original_fsync = os.fsync

            def track_fsync(descriptor):
                descriptor_path = f"/proc/self/fd/{descriptor}"
                if os.path.isdir(descriptor_path):
                    synced_directories.append(os.readlink(descriptor_path))
                original_fsync(descriptor)

            def inspect_reservation(_request):
                state = collect_x.load_state(state_dir)
                self.assertEqual(state["attempts"][-1]["status"], "reserved")
                self.assertEqual(state["attempts"][-1]["reserved_micro_usd"], 250000)
                self.assertIn(str(state_dir), synced_directories)

            provider = LocalProvider(
                [{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}],
                before_reply=inspect_reservation,
            )
            try:
                with mock.patch.object(collect_x.os, "fsync", side_effect=track_fsync):
                    self.collect(provider, temporary)
                headers = provider.requests[0]["headers"]
                self.assertEqual(headers["X-Treg-Token"], "synthetic-secret")
                self.assertEqual(headers["X-Treg-Org"], "synthetic-team")
                self.assertNotIn("Authorization", headers)
                self.assertEqual(headers["Cache-Control"], "no-cache")
            finally:
                provider.close()

    def test_unknown_charge_or_outcome_persists_reservation_and_pauses(self):
        provider = LocalProvider(
            [
                {"body": response_body("empty.json"), "headers": {}},
                {"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}},
            ]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                first, state_dir, _ = self.collect(provider, temporary)
                second, _, _ = self.collect(provider, temporary)
                state = collect_x.load_state(state_dir)
                self.assertEqual(first["stop_reason"], "unknown_charge_or_outcome")
                self.assertEqual(second["stop_reason"], "unreconciled_attempt")
                self.assertEqual(state["attempts"][-1]["status"], "unknown")
                self.assertEqual(state["charged_micro_usd"], 0)
                self.assertEqual(len(provider.requests), 1)
        finally:
            provider.close()

    def test_corrupt_budget_state_fails_closed_before_transport(self):
        provider = LocalProvider(
            [{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                state_dir = Path(temporary) / "state"
                state_dir.mkdir(mode=0o700)
                state = collect_x._new_state()
                state["charged_micro_usd"] = -1
                collect_x._private_json(state_dir / "state.json", state)
                with self.assertRaisesRegex(collect_x.CollectorError, "state is invalid"):
                    self.collect(provider, temporary)
                self.assertEqual(len(provider.requests), 0)
        finally:
            provider.close()

    def test_response_cost_above_reserved_cap_fails_closed(self):
        provider = LocalProvider(
            [{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "250001"}}]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, state_dir, _ = self.collect(provider, temporary)
                state = collect_x.load_state(state_dir)
                self.assertEqual(result["stop_reason"], "reported_cost_exceeds_reservation")
                self.assertEqual(state["attempts"][-1]["status"], "cap_violation")
                self.assertEqual(state["charged_micro_usd"], 250001)
        finally:
            provider.close()

    def test_empty_success_counts_as_successful_request(self):
        provider = LocalProvider(
            [{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, state_dir, _ = self.collect(provider, temporary)
                recording = json.loads(next((Path(temporary) / "recordings").glob("*.json")).read_text())
                self.assertEqual(result["successful_requests"], 1)
                self.assertEqual(recording["response"]["body"]["items"], [])
                self.assertEqual(recording["validation"]["evidence_completeness"], "empty_success")
                self.assertEqual(result["outcome"], "success")
                self.assertTrue(provider.responses[0].closed)
        finally:
            provider.close()

    def test_secret_redaction_precedes_private_persistence(self):
        body = response_body("page-1.json")
        body["TREG_TOKEN"] = "field-token-secret"
        body["Authorization"] = "authorization-field-secret"
        body["Set-Cookie"] = "cookie-field-secret"
        body["output"]["data"]["items"][0]["text"] = (
            'Synthetic text synthetic-secret Bearer abc123 api_key=leakme "token": "quoted-leak" '
            'https://url-user-secret:url-pass-secret@example.test/data?X-Amz-Signature=aws-leak&ok=signed-other-leak#fragment-secret '
            'Cookie: sessionid=cookie-leak Authorization: Basic basic-leak '
            'Authorization=Basic basic-assignment-secret\nCookie=sessionid=cookie-one; other=cookie-two'
        )
        provider = LocalProvider([{"body": body, "headers": {
            "X-Treg-Cost-Micro": "750",
            "X-Treg-Call-Id": "https://example.test/call?X-Amz-Signature=header-signature&x=header-query-secret",
        }}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                _, _, recording_dir = self.collect(provider, temporary)
                recording_path = next(recording_dir.glob("*.json"))
                persisted = recording_path.read_text()
                self.assertNotIn("synthetic-secret", persisted)
                self.assertNotIn("abc123", persisted)
                self.assertNotIn("leakme", persisted)
                self.assertNotIn("quoted-leak", persisted)
                for secret in (
                    "url-user-secret", "url-pass-secret", "aws-leak", "signed-other-leak", "fragment-secret",
                    "cookie-leak", "basic-leak", "field-token-secret", "authorization-field-secret",
                    "cookie-field-secret", "header-signature", "header-query-secret",
                    "basic-assignment-secret", "cookie-one", "cookie-two",
                ):
                    self.assertNotIn(secret, persisted)
                self.assertIn("[REDACTED]", persisted)
                self.assertEqual(recording_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(recording_dir.stat().st_mode & 0o777, 0o700)
                self.assertNotIn("X-Treg-Token", json.loads(persisted))
        finally:
            provider.close()

    def test_query_is_rejected_if_redaction_would_change_approved_text(self):
        provider = LocalProvider([{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                inputs = approved_inputs()
                inputs["query"] = "AI access_token=synthetic-secret workflow"
                with self.assertRaisesRegex(collect_x.CollectorError, "query.*sanitiz"):
                    self.collect(provider, temporary, inputs)
                self.assertEqual(provider.requests, [])
        finally:
            provider.close()

    def test_shorter_approved_retention_and_corrupt_artifacts_are_cleaned(self):
        provider = LocalProvider([{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, recording_dir = self.collect(provider, temporary, approved_inputs(retention_seconds=2 * 86400))
                recording_path = next(recording_dir.glob("*.json"))
                recording = json.loads(recording_path.read_text())
                self.assertEqual(recording["expires_at_epoch"] - recording["recorded_at_epoch"], 2 * 86400)
                self.assertEqual(recording["collector_sha256"], collect_x._script_sha256())
                self.assertEqual(recording["approval"]["gates"]["retention_and_removal"]["retention_seconds"], 2 * 86400)
                self.assertEqual(result["outcome"], "success")

                corrupt = recording_dir / "corrupt.json"
                pending = recording_dir / ".pending-interrupted"
                corrupt.write_text("{")
                pending.write_text("partial")
                target = recording_dir / "outside-target"
                symlink = recording_dir / "linked.json"
                target.write_text("must not be read")
                symlink.symlink_to(target)
                collect_x._clean_expired(recording_dir)
                self.assertFalse(corrupt.exists())
                self.assertFalse(pending.exists())
                self.assertFalse(symlink.exists())
                self.assertTrue(target.exists())
        finally:
            provider.close()

    def test_cleanup_runs_before_unreconciled_attempt_return(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            recording_dir = Path(temporary) / "recordings"
            state = collect_x._new_state()
            state["attempt_count"] = 1
            state["attempts"].append({"status": "unknown", "cost_micro_usd": 0})
            collect_x._save_state(state_dir, state)
            recording_dir.mkdir(mode=0o700)
            corrupt = recording_dir / "bad.json"
            pending = recording_dir / ".pending-crash"
            corrupt.write_text("bad json")
            pending.write_text("partial")
            result = collect_x.collect(
                approved_inputs(), state_dir, recording_dir, token="synthetic-secret",
                offline_transport=collect_x.OfflineTransport(LocalProvider([])),
            )
            self.assertEqual(result["stop_reason"], "unreconciled_attempt")
            self.assertFalse(corrupt.exists())
            self.assertFalse(pending.exists())

    def test_response_read_has_size_deadline_and_close_bounds(self):
        class SlowResponse(MemoryResponse):
            def read(self, size=-1):
                time.sleep(0.01)
                return super().read(min(size, 1))

        response = SlowResponse(200, {}, b"x" * (collect_x.MAX_RESPONSE_BYTES + 1))
        started = time.monotonic()
        with collect_x._request_deadline(0.04):
            with self.assertRaises(collect_x.ResponseReadError):
                collect_x._read_response(response)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(response.closed)

    def test_live_http_adapter_enforces_deadline_without_network(self):
        class SlowResponse:
            status = 200
            headers = {"X-Treg-Cost-Micro": "750"}
            closed = False

            def read(self, _size):
                time.sleep(0.01)
                return b"x"

            def close(self):
                self.closed = True

        response = SlowResponse()
        opener = mock.Mock()
        opener.open.return_value = response
        started = time.monotonic()
        with mock.patch.object(collect_x.urllib.request, "build_opener", return_value=opener):
            result = collect_x._http_request("https://invalid.test", b"{}", {}, 0.04)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertEqual(result.failure, "response_deadline_exceeded")
        self.assertTrue(response.closed)

    def test_oversized_response_is_capped_recorded_and_accounted(self):
        provider = LocalProvider([{
            "body": b"x" * (collect_x.MAX_RESPONSE_BYTES + 1),
            "headers": {"X-Treg-Cost-Micro": "750"},
        }])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, state_dir, recording_dir = self.collect(provider, temporary)
                record = json.loads(next(recording_dir.glob("*.json")).read_text())
                state = collect_x.load_state(state_dir)
                self.assertEqual(result["stop_reason"], "response_read_failure")
                self.assertEqual(result["outcome"], "failed")
                self.assertEqual(record["response"]["body_bytes"], collect_x.MAX_RESPONSE_BYTES)
                self.assertFalse(record["response"]["body_complete"])
                self.assertEqual(state["successful_requests"], 1)
                self.assertEqual(state["charged_micro_usd"], 750)
                self.assertTrue(provider.responses[0].closed)
        finally:
            provider.close()

    def test_json_null_failure_can_be_replayed(self):
        provider = LocalProvider([{"body": None, "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                self.collect(provider, temporary)
                recording = next((Path(temporary) / "recordings").glob("*.json"))
                replay = collect_x.replay_recording(recording)
                self.assertEqual(replay["response"]["body_format"], "json")
                self.assertEqual(replay["replay_validation"]["errors"], ["response_not_object"])
        finally:
            provider.close()

    def test_schema_failure_retains_sanitized_body_for_replay_validation(self):
        body = {"error": "invalid provider schema", "url": "https://example.test/?X-Amz-Signature=hidden"}
        provider = LocalProvider([{"body": body, "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, recording_dir = self.collect(provider, temporary)
                recording_path = next(recording_dir.glob("*.json"))
                record = json.loads(recording_path.read_text())
                self.assertEqual(result["outcome"], "failed")
                self.assertEqual(record["response"]["evidence_body"]["error"], "invalid provider schema")
                self.assertNotIn("hidden", json.dumps(record))
                replay = collect_x.replay_recording(recording_path)
                self.assertEqual(replay["replay_validation"], record["validation"])
        finally:
            provider.close()

    def test_transport_failure_replays_recorded_validation(self):
        def fail_request(*_args):
            raise TimeoutError("synthetic transport timeout")

        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            recording_dir = Path(temporary) / "recordings"
            result = collect_x.collect(
                approved_inputs(),
                state_dir,
                recording_dir,
                token="synthetic-secret",
                offline_transport=collect_x.OfflineTransport(fail_request),
            )
            recording_path = next(recording_dir.glob("*.json"))
            recording = json.loads(recording_path.read_text())
            self.assertEqual(result["stop_reason"], "unknown_charge_or_outcome")
            self.assertEqual(recording["validation"]["errors"], ["unknown_transport_outcome"])
            replay = collect_x.replay_recording(recording_path)
            self.assertEqual(replay["replay_validation"], recording["validation"])

    def test_missing_billing_failure_replays_recorded_validation(self):
        provider = LocalProvider([{"body": response_body("empty.json"), "headers": {}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, recording_dir = self.collect(provider, temporary)
                recording_path = next(recording_dir.glob("*.json"))
                recording = json.loads(recording_path.read_text())
                self.assertEqual(result["stop_reason"], "unknown_charge_or_outcome")
                self.assertEqual(recording["validation"]["errors"], ["billing_header_missing_or_invalid"])
                replay = collect_x.replay_recording(recording_path)
                self.assertEqual(replay["replay_validation"], recording["validation"])
        finally:
            provider.close()

    def test_redacted_cursor_failure_replays_from_sanitized_diagnostic(self):
        body = response_body("page-1.json")
        body["output"]["data"]["nextCursor"] = "token=cursor-secret"
        provider = LocalProvider([{"body": body, "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                self.collect(provider, temporary)
                recording_path = next((Path(temporary) / "recordings").glob("*.json"))
                recording = json.loads(recording_path.read_text())
                self.assertEqual(recording["validation"]["errors"], ["next_cursor_redacted"])
                self.assertEqual(recording["validation_context"]["sanitized_transformations"], ["next_cursor_redacted"])
                self.assertNotIn("cursor-secret", recording_path.read_text())
                replay = collect_x.replay_recording(recording_path)
                self.assertEqual(replay["replay_validation"], recording["validation"])
        finally:
            provider.close()

    def test_failed_transient_request_remains_failed_when_spend_cap_stops_retry(self):
        provider = LocalProvider([{
            "status": 503,
            "body": response_body("transient-error.json"),
            "headers": {"X-Treg-Cost-Micro": str(collect_x.BUDGET_MICRO_USD)},
        }])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                result, _, _ = self.collect(provider, temporary)
                self.assertEqual(result["stop_reason"], "spend_limit")
                self.assertEqual(result["acquisition_failure"], "request_failure")
                self.assertEqual(result["outcome"], "failed")
        finally:
            provider.close()

    def test_failed_transient_request_remains_failed_when_attempt_cap_stops_retry(self):
        provider = LocalProvider([{
            "status": 503,
            "body": response_body("transient-error.json"),
            "headers": {"X-Treg-Cost-Micro": "750"},
        }])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                state_dir = Path(temporary) / "state"
                recording_dir = Path(temporary) / "recordings"
                state = collect_x._new_state()
                state["attempt_count"] = 1
                state["attempts"].append({"status": "failed", "http_status": 503, "cost_micro_usd": 0})
                collect_x._save_state(state_dir, state)
                result = collect_x.collect(
                    approved_inputs(max_total_attempts=2),
                    state_dir,
                    recording_dir,
                    token="synthetic-secret",
                    offline_transport=collect_x.OfflineTransport(provider),
                )
                self.assertEqual(result["stop_reason"], "attempt_limit")
                self.assertEqual(result["acquisition_failure"], "request_failure")
                self.assertEqual(result["outcome"], "failed")
        finally:
            provider.close()

    def test_excluded_item_is_preserved_with_reason(self):
        provider = LocalProvider([{"body": response_body("page-1.json"), "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                self.collect(provider, temporary)
                record = json.loads(next((Path(temporary) / "recordings").glob("*.json")).read_text())
                excluded = record["validation"]["excluded_items"]
                self.assertEqual(excluded[0]["reason"], "out_of_window")
                self.assertEqual(excluded[0]["item"]["id"], "990000000000000002")
        finally:
            provider.close()

    def test_same_ticket_concurrent_collectors_are_serialized(self):
        provider = LocalProvider(
            [{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}] * 2,
            delay=0.1,
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                errors = []

                def run():
                    try:
                        self.collect(
                            provider,
                            temporary,
                            approved_inputs(max_pages=1, max_items=2, max_total_attempts=2),
                        )
                    except Exception as error:
                        errors.append(error)

                threads = [threading.Thread(target=run) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                self.assertFalse(errors)
                self.assertEqual(provider.max_active, 1)
                self.assertEqual(len(provider.requests), 2)
        finally:
            provider.close()

    def test_offline_replay_never_uses_transport_and_expired_recording_is_deleted(self):
        provider = LocalProvider(
            [{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}]
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                _, _, recording_dir = self.collect(provider, temporary)
                recording = next(recording_dir.glob("*.json"))
                replayed = collect_x.replay_recording(recording)
                self.assertEqual(replayed["source"], "offline_recording")
                self.assertEqual(replayed["response"]["body"]["items"], [])
                command = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "collect_x.py"), "replay", "--recording", str(recording)],
                capture_output=True,
                text=True,
                env=cli_environment(),
                check=False,
                )
                self.assertEqual(command.returncode, 0, command.stderr)
                self.assertEqual(json.loads(command.stdout)["network_requests"], 0)
                self.assertEqual(len(provider.requests), 1)

                expired = Path(temporary) / "expired.json"
                data = json.loads(recording.read_text())
                data["recorded_at_epoch"] = int(time.time()) - collect_x.RETENTION_SECONDS
                data["expires_at_epoch"] = data["recorded_at_epoch"] + collect_x.RETENTION_SECONDS
                expired.write_text(json.dumps(data))
                os.chmod(expired, 0o600)
                with self.assertRaisesRegex(collect_x.CollectorError, "expired"):
                    collect_x.replay_recording(expired)
                self.assertFalse(expired.exists())
        finally:
            provider.close()

    def test_replay_cli_exports_only_counts_and_reason_codes(self):
        body = response_body("page-1.json")
        body["output"]["data"]["items"] = [{
            "id": "private-excluded-id",
            "text": "private-excluded-source-text",
            "createdUtc": collect_x._date_epoch("2026-10-03"),
            "url": "https://example.test/private-excluded-url",
        }]
        provider = LocalProvider([{"body": body, "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                _, _, recording_dir = self.collect(provider, temporary)
                recording = next(recording_dir.glob("*.json"))
                command = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "collect_x.py"), "replay", "--recording", str(recording)],
                    capture_output=True,
                    text=True,
                    env=cli_environment(),
                    check=False,
                )
                self.assertEqual(command.returncode, 0, command.stderr)
                summary = json.loads(command.stdout)
                for private_value in (
                    "private-excluded-id",
                    "private-excluded-source-text",
                    "private-excluded-url",
                    str(collect_x._date_epoch("2026-10-03")),
                ):
                    self.assertNotIn(private_value, command.stdout)
                self.assertEqual(summary["validation"]["excluded_item_count"], 1)
                self.assertEqual(summary["validation"]["out_of_window_count"], 1)
                self.assertEqual(summary["validation"]["exclusion_reason_counts"], {"out_of_window": 1})
                self.assertNotIn("coverage", summary)
                self.assertNotIn("excluded_items", summary["validation"])
        finally:
            provider.close()

    def test_review_record_must_match_current_collector_before_credentials_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            approval = Path(temporary) / "approved.json"
            review = Path(temporary) / "review.json"
            approval.write_text(json.dumps(approved_inputs()))
            review.write_text(json.dumps({"ticket": 9, "collector_sha256": "stale", "result": "approved"}))
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "collect_x.py"),
                    "collect",
                    "--ticket", "9",
                    "--approval-file", str(approval),
                    "--review-file", str(review),
                ],
                capture_output=True,
                text=True,
                env=cli_environment(),
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("review", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
