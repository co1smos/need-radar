import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import collect_x


def approved_inputs(**limit_overrides):
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
            gate: {"verified": True, "evidence": "synthetic test fixture"}
            for gate in collect_x.REQUIRED_GATES
        },
    }


def response_body(name):
    return json.loads((ROOT / "fixtures" / "x" / name).read_text())


class LocalProvider:
    def __init__(self, steps, before_reply=None, delay=0):
        self.steps = list(steps)
        self.last_step = self.steps[-1] if self.steps else {"status": 500}
        self.before_reply = before_reply
        self.delay = delay
        self.requests = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request_body = self.rfile.read(int(self.headers["Content-Length"]))
                with provider.lock:
                    provider.active += 1
                    provider.max_active = max(provider.max_active, provider.active)
                    index = len(provider.requests)
                    request = {
                        "body": json.loads(request_body),
                        "headers": dict(self.headers.items()),
                    }
                    provider.requests.append(request)
                    step = provider.steps.pop(0) if provider.steps else provider.last_step
                    provider.last_step = step
                if provider.before_reply:
                    provider.before_reply(request)
                if provider.delay:
                    time.sleep(provider.delay)
                if callable(step):
                    step = step(request, index)
                status = step.get("status", 200)
                payload = step.get("body", {})
                response = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                for key, value in step.get("headers", {}).items():
                    self.send_header(key, str(value))
                self.end_headers()
                self.wfile.write(response)
                with provider.lock:
                    provider.active -= 1

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/call/anyapi.x.search.posts"

    def __call__(self, _url, data, headers, timeout):
        request = Request(self.url, data=data, headers=headers, method="POST")
        try:
            return build_opener().open(request, timeout=timeout)
        except HTTPError as error:
            return error

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class LiveGateTests(unittest.TestCase):
    def test_live_collection_without_recorded_approval_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            recording_dir = Path(temporary) / "recordings"
            environment = {"PATH": os.environ.get("PATH", "")}
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
            transport=provider,
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
                stopped, _, _ = self.collect(provider, temporary, inputs)
                self.assertEqual(stopped["stop_reason"], "spend_limit")
        finally:
            provider.close()

    def test_reservation_is_persisted_before_dispatch_and_headers_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"

            def inspect_reservation(_request):
                state = collect_x.load_state(state_dir)
                self.assertEqual(state["attempts"][-1]["status"], "reserved")
                self.assertEqual(state["attempts"][-1]["reserved_micro_usd"], 250000)

            provider = LocalProvider(
                [{"body": response_body("empty.json"), "headers": {"X-Treg-Cost-Micro": "750"}}],
                before_reply=inspect_reservation,
            )
            try:
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
        finally:
            provider.close()

    def test_secret_redaction_precedes_private_persistence(self):
        body = response_body("page-1.json")
        body["output"]["data"]["items"][0]["text"] = (
            'Synthetic text synthetic-secret Bearer abc123 api_key=leakme "token": "quoted-leak"'
        )
        provider = LocalProvider([{"body": body, "headers": {"X-Treg-Cost-Micro": "750"}}])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                _, _, recording_dir = self.collect(provider, temporary)
                recording_path = next(recording_dir.glob("*.json"))
                persisted = recording_path.read_text()
                self.assertNotIn("synthetic-secret", persisted)
                self.assertNotIn("abc123", persisted)
                self.assertNotIn("leakme", persisted)
                self.assertNotIn("quoted-leak", persisted)
                self.assertIn("[REDACTED]", persisted)
                self.assertEqual(recording_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(recording_dir.stat().st_mode & 0o777, 0o700)
                self.assertNotIn("X-Treg-Token", json.loads(persisted))
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
                env={"PATH": os.environ.get("PATH", "")},
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("review", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
