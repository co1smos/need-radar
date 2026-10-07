import contextlib
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch


SOCKET_AUDIT_EVENTS = {
    "socket.__new__",
    "socket.bind",
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyaddr",
    "socket.gethostbyname",
    "socket.getnameinfo",
    "socket.sendto",
}
BLOCKED_SOCKET_EVENTS = []


def block_socket_network(event, args):
    if event in SOCKET_AUDIT_EVENTS:
        BLOCKED_SOCKET_EVENTS.append(event)
        raise AssertionError(f"socket activity blocked in offline tests: {event}")


sys.addaudithook(block_socket_network)


ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "collect_reddit.py"
sys.path.insert(0, str(ROOT / "scripts"))

import collect_reddit

PROTECTED_PATHS = (
    pathlib.Path("/home/ubuntu/projects/need-radar/credentials.env"),
    pathlib.Path("/home/ubuntu/.config/need-radar/credentials.env"),
    pathlib.Path("/home/ubuntu/.local/state/need-radar/ticket-3"),
)
BLOCKED_FILESYSTEM_EVENTS = {
    "open", "os.chmod", "os.chown", "os.mkdir", "os.remove", "os.rename",
    "os.rmdir", "os.scandir", "os.stat", "os.symlink", "os.truncate", "os.utime",
}


def block_protected_paths(event, args):
    if event not in BLOCKED_FILESYSTEM_EVENTS:
        return
    for value in args[:2]:
        if not isinstance(value, (str, bytes, os.PathLike)):
            continue
        path = pathlib.Path(os.path.abspath(os.fsdecode(value)))
        if any(path == protected or protected in path.parents for protected in PROTECTED_PATHS):
            raise AssertionError(f"live path access blocked in tests: {event}")


sys.addaudithook(block_protected_paths)


ACK = collect_reddit.LIVE_ACK
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
WINDOW_START = "2026-10-01T00:00:00Z"
WINDOW_END = "2026-10-07T00:00:00Z"


class FakeCredentials:
    def __init__(self, token="synthetic-token", org=None):
        self.token = token
        self.org = org
        self.loads = 0

    def load(self, token_type):
        self.loads += 1
        return collect_reddit.Credentials(self.token, self.org if token_type == "identity" else None)


class FixtureTransport:
    synthetic = True

    def __init__(self, results):
        self.results = list(results)
        self.requests = []
        self.on_request = None

    def request(self, url, headers, timeout):
        self.requests.append((url, dict(headers), timeout))
        if self.on_request:
            self.on_request(url, headers)
        if not self.results:
            raise AssertionError("unexpected transport request")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(status, body, cost=1000, call_id="synthetic-call", extra_headers=None):
    headers = {"X-Treg-Cost-Micro": str(cost), "X-Treg-Call-Id": call_id}
    if extra_headers:
        headers.update(extra_headers)
    encoded = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return collect_reddit.HTTPResponse(status, headers, encoded)


def approval_manifest(seeds=("example",), start=WINDOW_START, end=WINDOW_END):
    evidence = "synthetic test evidence only"
    routes = {
        "feed": {
            "id": "tikhub.x.reddit-app-fetch-subreddit-feed",
            "schema_verified": True,
            "schema_evidence": evidence,
            "permission_verified": True,
            "permission_evidence": evidence,
            "billing_verified": True,
            "billing_unit": "per_success",
            "billing_evidence": evidence,
            "max_charge_verified": True,
            "max_charge_micro_usd": collect_reddit.ROUTES["feed"]["max_charge_micro_usd"],
            "max_charge_evidence": evidence,
            "cost_cap_verified": True,
            "cost_cap_evidence": evidence,
        },
        "comments": {
            "id": "scrapecreators.x.v1-reddit-post-comments",
            "schema_verified": True,
            "schema_evidence": evidence,
            "permission_verified": True,
            "permission_evidence": evidence,
            "billing_verified": True,
            "billing_unit": "per_call",
            "billing_evidence": evidence,
            "max_charge_verified": True,
            "max_charge_micro_usd": collect_reddit.ROUTES["comments"]["max_charge_micro_usd"],
            "max_charge_evidence": evidence,
            "cost_cap_verified": True,
            "cost_cap_evidence": evidence,
        },
    }
    return {
        "ticket": "3",
        "source": "reddit",
        "source_permission": {"verified": True, "evidence": evidence},
        "seeds": {"verified": True, "values": list(seeds), "evidence": evidence},
        "window": {"verified": True, "start": start, "end": end, "evidence": evidence},
        "routes": routes,
        "provider_account": {
            "eligibility_verified": True,
            "credential_mode": "treg_managed",
            "token_type": "per_org",
            "overflow_disabled": True,
            "fallback_disabled": True,
            "own_provider_credentials_absent": True,
            "evidence": evidence,
        },
        "limits": {
            "verified": True,
            "successful_requests": 25,
            "spend_micro_usd": 250000,
            "evidence": evidence,
        },
        "retention": {
            "recording_permitted": True,
            "days": 7,
            "derived_removal_verified": True,
            "evidence": evidence,
        },
    }


def fixture_body(name):
    return json.loads((ROOT / "fixtures" / "reddit" / name).read_text())


class CollectRedditCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.defaults = patch.multiple(
            collect_reddit,
            STATE_DEFAULT=self.root / "live-state",
            APPROVAL_DEFAULT=self.root / "live-state" / "approval.json",
        )
        self.defaults.start()
        self.addCleanup(self.defaults.stop)
        self.state_dir = self.root / "state"
        self.recordings_dir = self.root / "recordings"
        self.approval_path = self.root / "approval.json"
        self.write_approval()
        self.env = patch.dict(os.environ, {"NEED_RADAR_REDDIT_LIVE_ACK": ACK})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def write_approval(self, value=None):
        self.approval_path.write_text(json.dumps(value or approval_manifest()))
        self.approval_path.chmod(0o600)

    def args(self, *extra, command="collect"):
        if command == "collect":
            return [
                "collect",
                "--subreddit", "example",
                "--window-start", WINDOW_START,
                "--window-end", WINDOW_END,
                "--max-feed-pages", "2",
                "--max-comment-pages", "2",
                "--max-posts", "3",
                "--approval", str(self.approval_path),
                "--state-dir", str(self.state_dir),
                "--recordings-dir", str(self.recordings_dir),
                "--allow-live-acquisition", ACK,
                *extra,
            ]
        return [command, *extra]

    def run_cli(self, argv, transport=None, credentials=None, sleeper=None):
        if argv and argv[0] == "collect" and transport is None:
            raise AssertionError("collect tests must inject a synthetic transport")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = collect_reddit.main(
                argv,
                transport=transport,
                credentials=None if isinstance(credentials, FakeCredentials) else credentials,
                credentials_loader=credentials.load if isinstance(credentials, FakeCredentials) else None,
                clock=lambda: NOW,
                sleeper=sleeper or (lambda _: None),
            )
        return result, stdout.getvalue(), stderr.getvalue()

    def test_public_cli_displays_help(self):
        protected_paths = PROTECTED_PATHS
        guard = (
            "import os, pathlib, runpy, sys\n"
            f"blocked_network = {SOCKET_AUDIT_EVENTS!r}\n"
            f"protected = {tuple(map(str, protected_paths))!r}\n"
            f"filesystem = {BLOCKED_FILESYSTEM_EVENTS!r}\n"
            "def audit(event, args):\n"
            "    if event in blocked_network:\n"
            "        raise AssertionError('network access denied in subprocess')\n"
            "    if event in filesystem:\n"
            "        for value in args[:2]:\n"
            "            if isinstance(value, (str, bytes, os.PathLike)):\n"
            "                path = pathlib.Path(os.path.abspath(os.fsdecode(value)))\n"
            "                if any(path == pathlib.Path(item) or pathlib.Path(item) in path.parents for item in protected):\n"
            "                    raise AssertionError('live path access denied in subprocess')\n"
            "sys.addaudithook(audit)\n"
            f"sys.argv = [{str(CLI)!r}, '--help']\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, "-c", guard],
                capture_output=True,
                text=True,
                check=False,
                env={"HOME": home, "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("replay", result.stdout)

    def test_direct_connection_is_stopped_before_socket_creation(self):
        BLOCKED_SOCKET_EVENTS.clear()
        address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))
        with patch("socket.getaddrinfo", return_value=[address]) as resolve:
            with self.assertRaisesRegex(AssertionError, "socket.__new__"):
                socket.create_connection(("treg.to", 443), timeout=1)
        resolve.assert_called()
        self.assertEqual(BLOCKED_SOCKET_EVENTS, ["socket.__new__"])

    def test_live_incident_hold_blocks_before_credentials_and_dispatch(self):
        args = collect_reddit.parse_args(self.args("--max-feed-pages", "1", "--max-posts", "0"))
        args.state_dir = str(collect_reddit.STATE_DEFAULT)
        args.recordings_dir = str(collect_reddit.STATE_DEFAULT / "recordings")
        credentials = FakeCredentials()
        transport = FixtureTransport([])

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = collect_reddit.run_collect(
                args, transport, credentials.load, lambda: NOW, lambda _: None, False
            )

        self.assertEqual(code, 2)
        self.assertIn("historical_unknown_outcome_hold", stdout.getvalue())
        self.assertEqual(credentials.loads, 0)
        self.assertEqual(transport.requests, [])
        state = json.loads((pathlib.Path(args.state_dir) / "state.json").read_text())
        self.assertEqual(state["incident_hold"]["reference"], collect_reddit.INCIDENT_REF)
        self.assertEqual(state["incident_hold"]["status"], "unresolved")

    def test_synthetic_transport_requires_explicit_test_credentials(self):
        transport = FixtureTransport([])
        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"), transport
        )
        self.assertEqual(code, 2)
        self.assertIn("synthetic_transport_requires_test_credentials", stdout)
        self.assertEqual(transport.requests, [])

    def test_redaction_covers_url_components_embedded_urls_and_keys_without_collisions(self):
        payload = {
            "api_key": "first-secret",
            "access_token": "second-secret",
            "message": "signed https://first-secret.example/path/first-secret?X-Amz-Signature=signature-secret.",
            "url": "https://first-secret.example/second-secret/path?token=third-secret",
        }
        sanitized = collect_reddit.sanitize(payload, ["first-secret", "second-secret", "third-secret"])
        serialized = json.dumps(sanitized)

        self.assertNotIn("first-secret", serialized)
        self.assertNotIn("second-secret", serialized)
        self.assertNotIn("third-secret", serialized)
        self.assertNotIn("signature-secret", serialized)
        redacted_keys = [key for key in sanitized if key.startswith("[REDACTED")]
        self.assertEqual(len(redacted_keys), 2)
        self.assertEqual(len(set(sanitized)), len(sanitized))

    def test_post_permalink_must_belong_to_approved_community(self):
        self.assertEqual(
            collect_reddit.post_url(
                {"url": "https://www.reddit.com/r/example/comments/demo1/title/"}, [], "example"
            ),
            "https://www.reddit.com/r/example/comments/demo1/title/",
        )
        for url in (
            "https://www.reddit.com/r/unapproved/comments/demo1/title/",
            "https://www.reddit.com/r/example/about/",
            "https://www.reddit.com/user/someone/",
        ):
            self.assertIsNone(collect_reddit.post_url({"url": url}, [], "example"))

    def test_post_permalink_userinfo_is_not_sanitized_into_a_comments_target(self):
        feed = fixture_body("feed-page-1.json")
        post = feed["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["url"] = "https://fixture-value@www.reddit.com/r/example/comments/demo1/title/"
        transport = FixtureTransport([
            response(200, feed),
            response(200, fixture_body("comments-page-1.json")),
        ])

        code, _, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-comment-pages", "1", "--max-posts", "1"),
            transport,
            FakeCredentials("fixture-value"),
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(urlsplit(transport.requests[0][0]).path, f"/call/{collect_reddit.FEED_ID}")

    def test_repository_guard_covers_primary_checkout(self):
        primary = ROOT.parents[2]
        self.assertTrue(collect_reddit.repository_path(primary / "private-recordings"))

    def test_request_deadline_interrupts_blocking_operation(self):
        with self.assertRaisesRegex(TimeoutError, "request_deadline_exceeded"):
            with collect_reddit.request_deadline(0.01):
                time.sleep(0.1)

    def test_record_expiry_accepts_earlier_inherited_expiry(self):
        record = {
            "recorded_at": collect_reddit.timestamp(NOW),
            "expires_at": collect_reddit.timestamp(NOW + collect_reddit.dt.timedelta(days=1)),
            "source_expires_at": collect_reddit.timestamp(NOW + collect_reddit.dt.timedelta(days=1)),
            "retention_days": 7,
        }
        self.assertEqual(collect_reddit.record_expiry(record), NOW + collect_reddit.dt.timedelta(days=1))
        self.assertIsNone(collect_reddit.record_expiry([]))

    def test_expiry_cleanup_removes_abandoned_temporary_recordings(self):
        self.recordings_dir.mkdir(mode=0o700)
        pending = self.recordings_dir / ".pending-abandoned"
        pending.write_text("{}")
        pending.chmod(0o600)

        collect_reddit.expire_recordings(self.recordings_dir, NOW)

        self.assertFalse(pending.exists())

    def test_expiry_cleanup_waits_for_active_atomic_write(self):
        self.recordings_dir.mkdir(mode=0o700)
        target = self.recordings_dir / "record.json"
        dump_started = threading.Event()
        finish_dump = threading.Event()
        cleanup_finished = threading.Event()
        errors = []
        original_dump = collect_reddit.json.dump

        def pause_dump(*args, **kwargs):
            dump_started.set()
            if not finish_dump.wait(2):
                raise AssertionError("synthetic writer did not resume")
            return original_dump(*args, **kwargs)

        def write_record():
            try:
                collect_reddit.atomic_json(target, {"complete": True})
            except Exception as error:
                errors.append(error)

        def cleanup():
            try:
                collect_reddit.expire_pending_files(self.recordings_dir)
            except Exception as error:
                errors.append(error)
            finally:
                cleanup_finished.set()

        with patch.object(collect_reddit.json, "dump", side_effect=pause_dump):
            writer = threading.Thread(target=write_record)
            cleaner = threading.Thread(target=cleanup)
            writer.start()
            try:
                self.assertTrue(dump_started.wait(1))
                cleaner.start()
                self.assertFalse(cleanup_finished.wait(0.05))
                self.assertEqual(len(list(self.recordings_dir.glob(".pending-*"))), 1)
            finally:
                finish_dump.set()
            writer.join(2)
            cleaner.join(2)

        self.assertFalse(writer.is_alive())
        self.assertFalse(cleaner.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(json.loads(target.read_text()), {"complete": True})
        self.assertEqual(list(self.recordings_dir.glob(".pending-*")), [])

    def test_locked_state_removes_abandoned_budget_temp_file(self):
        self.state_dir.mkdir(mode=0o700)
        pending = self.state_dir / ".pending-abandoned"
        pending.write_text("{}")
        pending.chmod(0o600)
        old = (NOW - collect_reddit.dt.timedelta(days=8)).timestamp()
        os.utime(pending, (old, old))

        with collect_reddit.locked_state(self.state_dir, NOW):
            pass

        self.assertFalse(pending.exists())

    def test_locked_state_migrates_legacy_counters_with_unresolved_incident_hold(self):
        self.state_dir.mkdir(mode=0o700)
        state = collect_reddit.default_state()
        state.pop("incident_hold")
        state.update({"attempts": 4, "spent_micro_usd": 1200})
        state_path = self.state_dir / "state.json"
        state_path.write_text(json.dumps(state))
        state_path.chmod(0o600)

        with collect_reddit.locked_state(self.state_dir, NOW) as (_, loaded):
            self.assertEqual(loaded["attempts"], 4)
            self.assertEqual(loaded["spent_micro_usd"], 1200)
            self.assertEqual(loaded["incident_hold"]["status"], "unresolved")
            self.assertIsNone(loaded["incident_hold"]["charge_micro_usd"])

        persisted = json.loads(state_path.read_text())
        self.assertEqual(persisted["incident_hold"]["reference"], collect_reddit.INCIDENT_REF)

    def test_legacy_known_charge_reconciles_without_double_counting(self):
        self.state_dir.mkdir(mode=0o700)
        state = collect_reddit.default_state()
        state.pop("incident_hold")
        state.update({"attempts": 1, "spent_micro_usd": 1000})
        state["pending"] = {
            "route": "feed", "reserved_micro_usd": 1000, "success_slot_reserved": False,
            "charge_known": True, "known_charge_micro_usd": 1000, "call_id": "legacy-call",
            "http_success": False,
        }
        state_path = self.state_dir / "state.json"
        state_path.write_text(json.dumps(state))
        state_path.chmod(0o600)
        args = [
            "reconcile", "--state-dir", str(self.state_dir), "--call-id", "legacy-call",
            "--charge-micro", "1000", "--request-outcome", "failed", "--evidence", "fixture ledger",
        ]

        code, _, _ = self.run_cli(args)

        self.assertEqual(code, 0)
        reconciled = json.loads(state_path.read_text())
        self.assertEqual(reconciled["spent_micro_usd"], 1000)
        self.assertEqual(reconciled["reserved_micro_usd"], 0)
        self.assertIsNone(reconciled["pending"])

    def test_locked_state_removes_abandoned_budget_temp_file(self):
        self.state_dir.mkdir(mode=0o700)
        pending = self.state_dir / ".pending-abandoned"
        pending.write_text("{}")
        pending.chmod(0o600)
        old = (NOW - collect_reddit.dt.timedelta(days=8)).timestamp()
        os.utime(pending, (old, old))

        with collect_reddit.locked_state(self.state_dir, NOW):
            pass

        self.assertFalse(pending.exists())

    def test_expired_pending_parameters_are_removed_without_clearing_hold_or_counters(self):
        state = collect_reddit.default_state()
        state.update({"attempts": 2, "spent_micro_usd": 1000, "reserved_micro_usd": 1000, "reserved_success_slots": 1})
        state["pending"] = {
            "route": "feed", "reserved_micro_usd": 1000, "success_slot_reserved": True,
            "started_at": collect_reddit.timestamp(NOW - collect_reddit.dt.timedelta(days=8)),
            "request_parameters": {"subreddit_name": "example"},
            "request_parameters_sha256": "fixture-hash",
        }

        collect_reddit.expire_pending_parameters(state, NOW)

        self.assertNotIn("request_parameters", state["pending"])
        self.assertEqual(state["attempts"], 2)
        self.assertEqual(state["spent_micro_usd"], 1000)
        self.assertIsNotNone(state["pending"])
        self.assertEqual(state["incident_hold"]["status"], "unresolved")

    def test_malformed_numeric_timestamp_produces_persisted_failure(self):
        page = fixture_body("feed-page-1.json")
        page["data"]["subredditV3"]["elements"]["edges"][0]["node"]["createdAt"] = 1e300
        transport = FixtureTransport([response(200, page)])

        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials()
        )

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertIn("feed_response_payload_invalid", report["errors"])
        artifacts = list(self.recordings_dir.glob("run-*.json"))
        self.assertEqual(len(artifacts), 1)
        self.assertIn("feed_response_payload_invalid", json.loads(artifacts[0].read_text())["errors"])

    def test_replay_fails_when_all_recorded_pages_are_invalid(self):
        transport = FixtureTransport([response(200, {"code": 200, "data": {}})])
        self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials()
        )
        args = ["replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)]

        code, stdout, _ = self.run_cli(args)

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["counts"]["validation_failures"], 0)
        artifact = self.recordings_dir / report["artifact"]
        self.assertEqual(json.loads(artifact.read_text())["status"], "failed")

    def test_reconciliation_records_overcharge_and_permanently_blocks_spending(self):
        transport = FixtureTransport([TimeoutError("synthetic timeout")])
        self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials()
        )
        reconcile = [
            "reconcile", "--state-dir", str(self.state_dir), "--call-id", "reconciled-call",
            "--charge-micro", "1500", "--request-outcome", "failed", "--evidence", "synthetic ledger reference",
        ]

        code, stdout, _ = self.run_cli(reconcile)

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout)["spent_micro_usd"], 1500)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertTrue(state["limit_breach"])
        self.assertEqual(state["spent_micro_usd"], 1500)
        blocked = FixtureTransport([])
        code, _, _ = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), blocked, FakeCredentials())
        self.assertEqual(code, 2)
        self.assertEqual(blocked.requests, [])

    def test_replay_expiry_inherits_earliest_source_expiry(self):
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"))])
        self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials())
        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        record["recorded_at"] = collect_reddit.timestamp(NOW - collect_reddit.dt.timedelta(days=6))
        inherited_expiry = NOW + collect_reddit.dt.timedelta(days=1)
        record["expires_at"] = collect_reddit.timestamp(inherited_expiry)
        source.write_text(json.dumps(record))
        source.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 0)
        replay = json.loads(stdout)
        artifact = json.loads((self.recordings_dir / replay["artifact"]).read_text())
        self.assertEqual(collect_reddit.parse_datetime(artifact["expires_at"]), inherited_expiry)

    def test_run_summary_inherits_earliest_source_expiry(self):
        instants = iter((NOW, NOW, NOW, NOW, NOW, NOW + collect_reddit.dt.timedelta(days=6)))
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"))])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = collect_reddit.main(
                self.args("--max-feed-pages", "1", "--max-posts", "0"),
                transport=transport,
                credentials_loader=FakeCredentials().load,
                clock=lambda: next(instants),
                sleeper=lambda _: None,
            )

        self.assertEqual(code, 0)
        report = json.loads(next(self.recordings_dir.glob("run-*.json")).read_text())
        self.assertEqual(
            collect_reddit.parse_datetime(report["expires_at"]),
            NOW + collect_reddit.dt.timedelta(days=7),
        )

    def test_collect_paginates_feed_and_comments_and_redacts_before_recording(self):
        first_feed = fixture_body("feed-page-1.json")
        first_post = first_feed["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        first_post["url"] += "?token=fixture-secret"
        first_post["text"] = "See https://fixture-secret.example/private/fixture-secret?sig=fixture-signed-secret."
        first_post["fixture-secret"] = "retained value one"
        first_post["[REDACTED_KEY]"] = "retained value two"
        first_feed["data"]["access_token"] = "nested-private-token"
        first_feed["data"] = json.dumps(first_feed["data"])
        first_feed["cache_url"] = "https://cache.tikhub.io/item?sign=fixture-signed-secret"
        transport = FixtureTransport([
            response(200, first_feed, call_id="feed-1"),
            response(200, fixture_body("feed-page-2.json"), call_id="feed-2"),
            response(200, fixture_body("comments-page-1.json"), call_id="comments-1"),
            response(200, fixture_body("comments-page-2.json"), call_id="comments-2"),
            response(200, fixture_body("comments-page-1.json"), call_id="comments-3"),
            response(200, fixture_body("comments-page-2.json"), call_id="comments-4"),
        ])
        credentials = FakeCredentials("fixture-secret")

        code, stdout, stderr = self.run_cli(self.args(), transport, credentials)

        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(transport.requests), 6)
        urls = [urlsplit(request[0]) for request in transport.requests]
        self.assertEqual(parse_qs(urls[0].query)["sort"], ["NEW"])
        self.assertEqual(parse_qs(urls[1].query)["after"], ["feed-cursor-2"])
        self.assertEqual(parse_qs(urls[2].query)["url"], [
            "https://www.reddit.com/r/example/comments/demo1/synthetic-post/"
        ])
        self.assertEqual(parse_qs(urls[3].query)["cursor"], ["comment-cursor-2"])
        self.assertEqual(parse_qs(urls[4].query)["url"], [
            "https://www.reddit.com/r/example/comments/demo2/synthetic-post/"
        ])
        self.assertEqual(parse_qs(urls[5].query)["cursor"], ["comment-cursor-2"])
        self.assertEqual(credentials.loads, 1)
        self.assertEqual(json.loads(stdout)["budget"]["successful_requests"], 6)
        self.assertEqual(json.loads(stdout)["network_requests"], 0)
        self.assertEqual(json.loads(stdout)["acquisition_attempts"], 6)
        self.assertEqual(json.loads(stdout)["capabilities"]["spending_control"]["status"], "partial")

        saved = list(self.recordings_dir.glob("record-*.json"))
        self.assertEqual(len(saved), 6)
        run_artifact = json.loads(next(self.recordings_dir.glob("run-*.json")).read_text())
        self.assertEqual(len(run_artifact["record_ids"]), 6)
        self.assertEqual(run_artifact["requested"]["feed_language"]["owner_approved"], False)
        self.assertEqual(run_artifact["validation"]["feed_schema"], "valid")
        self.assertEqual(run_artifact["retention_days"], 7)
        self.assertEqual(len(run_artifact["approval_sha256"]), 64)
        persisted = "\n".join(path.read_text() for path in saved)
        self.assertNotIn("fixture-secret", persisted)
        self.assertNotIn("fixture-signed-secret", persisted)
        self.assertNotIn("nested-private-token", persisted)
        self.assertIn("[REDACTED_KEY]#2", persisted)
        self.assertIn("?token=%5BREDACTED%5D", persisted)
        self.assertIn("sign=%5BREDACTED%5D", persisted)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in saved))
        self.assertEqual(self.recordings_dir.stat().st_mode & 0o777, 0o700)
        feed_record = next(
            json.loads(path.read_text())
            for path in saved
            if json.loads(path.read_text())["endpoint"] == collect_reddit.FEED_ID
        )
        saved_request = feed_record["request"]
        self.assertNotIn("x-treg-token", saved_request["headers"])
        self.assertIn("idempotency-key", saved_request["headers"])
        self.assertEqual(saved_request["headers"]["x-treg-route-max-cost"], "0.001000")
        self.assertNotIn("x-treg-org", saved_request["headers"])
        for _, headers, _ in transport.requests:
            self.assertEqual(headers["X-Treg-Token"], "fixture-secret")
            self.assertIn("X-Treg-Route-Max-Cost", headers)
            self.assertEqual(headers["Cache-Control"], "no-cache")
            self.assertNotIn("Authorization", headers)

    def test_bounded_retry_auth_stop_and_accounting_include_charged_errors(self):
        transient = FixtureTransport([
            response(503, {"error": "synthetic transient"}, cost=1000, call_id="retry-1"),
            response(200, fixture_body("feed-page-2.json"), cost=1000, call_id="retry-2"),
        ])
        code, _, stderr = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), transient, FakeCredentials())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(transient.requests), 2)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["attempts"], 2)
        self.assertEqual(state["successful_requests"], 1)
        self.assertEqual(state["spent_micro_usd"], 2000)

        auth = FixtureTransport([response(403, {"error": "permission denied"}, cost=0)])
        code, stdout, _ = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), auth, FakeCredentials())
        self.assertNotEqual(code, 0)
        self.assertEqual(len(auth.requests), 1)
        self.assertIn("permission", stdout.lower())

    def test_retry_after_is_honored_within_bound_and_long_wait_stops(self):
        waits = []
        retrying = FixtureTransport([
            response(503, {"error": "synthetic busy"}, cost=1000, extra_headers={"Retry-After": "3"}),
            response(200, fixture_body("feed-page-2.json"), cost=1000),
        ])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            retrying,
            FakeCredentials(),
            sleeper=waits.append,
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(waits, [3])
        self.assertEqual(json.loads(stdout)["budget"]["attempts"], 2)

        long_wait = FixtureTransport([
            response(503, {"error": "synthetic busy"}, cost=1000, extra_headers={"Retry-After": "31"}),
            response(200, fixture_body("feed-page-2.json"), cost=1000),
        ])
        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            long_wait,
            FakeCredentials(),
        )
        self.assertEqual(code, 2)
        self.assertEqual(len(long_wait.requests), 1)
        self.assertIn("retry_after_exceeds_local_bound", stdout)

    def test_success_ceiling_counts_successes_not_failed_attempts_across_runs(self):
        page = fixture_body("feed-page-1.json")
        page["data"]["subredditV3"]["elements"]["pageInfo"] = {
            "endCursor": "next", "hasNextPage": True
        }
        responses = []
        for index in range(24):
            page["data"]["subredditV3"]["elements"]["pageInfo"] = {
                "endCursor": f"cursor-{index}", "hasNextPage": index < 23
            }
            responses.append(response(200, page, call_id=f"success-{index}"))
        transport = FixtureTransport(responses)
        code, _, stderr = self.run_cli(self.args("--max-feed-pages", "24", "--max-posts", "0"), transport, FakeCredentials())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(transport.requests), 24)

        next_transport = FixtureTransport([
            response(503, {"error": "temporary"}, cost=1000),
            response(200, fixture_body("feed-page-2.json"), cost=1000),
        ])
        code, _, stderr = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), next_transport, FakeCredentials())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(next_transport.requests), 2)
        final_state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(final_state["successful_requests"], 25)
        self.assertEqual(final_state["attempts"], 26)
        self.assertEqual(final_state["spent_micro_usd"], 26000)

        blocked = FixtureTransport([])
        code, _, _ = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), blocked, FakeCredentials())
        self.assertNotEqual(code, 0)
        self.assertEqual(blocked.requests, [])

    def test_spend_cap_reservation_charged_error_and_no_overshoot(self):
        self.state_dir.mkdir(mode=0o700)
        state = collect_reddit.default_state()
        state["spent_micro_usd"] = 249000
        state_path = self.state_dir / "state.json"
        state_path.write_text(json.dumps(state))
        state_path.chmod(0o600)
        transport = FixtureTransport([response(400, {"error": "synthetic bad request"}, cost=1000)])

        def inspect_reservation(url, headers):
            state = json.loads((self.state_dir / "state.json").read_text())
            self.assertEqual(state["reserved_micro_usd"], 1000)
            self.assertEqual(headers["X-Treg-Route-Max-Cost"], "0.001000")

        transport.on_request = inspect_reservation
        code, _, _ = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials())
        self.assertNotEqual(code, 0)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["spent_micro_usd"], 250000)
        self.assertEqual(state["reserved_micro_usd"], 0)
        self.assertEqual(len(transport.requests), 1)

    def test_unknown_outcome_pauses_until_local_reconciliation(self):
        transport = FixtureTransport([TimeoutError("synthetic timeout")])
        code, _, _ = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials())
        self.assertNotEqual(code, 0)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["reserved_micro_usd"], 1000)
        self.assertIsNotNone(state["pending"])

        blocked_transport = FixtureTransport([])
        code, _, _ = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), blocked_transport, FakeCredentials())
        self.assertNotEqual(code, 0)
        self.assertEqual(blocked_transport.requests, [])

        reconcile = [
            "reconcile", "--state-dir", str(self.state_dir),
            "--call-id", "reconciled-call", "--charge-micro", "1000",
            "--request-outcome", "failed", "--evidence", "synthetic ledger reference",
        ]
        code, _, stderr = self.run_cli(reconcile)
        self.assertEqual(code, 0, stderr)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["spent_micro_usd"], 1000)
        self.assertIsNone(state["pending"])
        self.assertEqual(state["reserved_micro_usd"], 0)

    def test_unknown_charge_pauses_and_reconciliation_does_not_double_count_success(self):
        missing_charge = collect_reddit.HTTPResponse(
            200,
            {"X-Treg-Call-Id": "synthetic-call-without-charge"},
            json.dumps(fixture_body("feed-page-2.json")).encode(),
        )
        transport = FixtureTransport([missing_charge])
        code, _, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )
        self.assertNotEqual(code, 0)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["successful_requests"], 1)
        self.assertEqual(state["reserved_micro_usd"], 1000)
        self.assertEqual(state["reserved_success_slots"], 0)
        self.assertTrue(state["pending"]["http_success"])
        saved = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        self.assertIsNone(saved["billing"]["charged_micro_usd"])

        reconcile = [
            "reconcile", "--state-dir", str(self.state_dir),
            "--call-id", "wrong-call-id", "--charge-micro", "1000",
            "--request-outcome", "success", "--evidence", "synthetic ledger reference",
        ]
        code, _, _ = self.run_cli(reconcile)
        self.assertEqual(code, 2)
        unchanged = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(unchanged["reserved_micro_usd"], 1000)

        reconcile = [
            "reconcile", "--state-dir", str(self.state_dir),
            "--call-id", "synthetic-call-without-charge", "--charge-micro", "1000",
            "--request-outcome", "success", "--evidence", "synthetic ledger reference",
        ]
        code, _, stderr = self.run_cli(reconcile)
        self.assertEqual(code, 0, stderr)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["successful_requests"], 1)
        self.assertEqual(state["spent_micro_usd"], 1000)
        self.assertEqual(state["reserved_micro_usd"], 0)

    def test_same_ticket_concurrency_serializes_budget_reservations(self):
        self.state_dir.mkdir(mode=0o700)
        state = collect_reddit.default_state()
        state["spent_micro_usd"] = 249000
        state_path = self.state_dir / "state.json"
        state_path.write_text(json.dumps(state))
        state_path.chmod(0o600)
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"), cost=1000)])
        entered = threading.Event()
        release = threading.Event()

        def hold_request(url, headers):
            entered.set()
            release.wait(2)

        transport.on_request = hold_request
        results = []

        def run():
            results.append(collect_reddit.main(
                self.args("--max-feed-pages", "1", "--max-posts", "0"),
                transport=transport,
                credentials_loader=FakeCredentials().load,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ))

        first = threading.Thread(target=run)
        second = threading.Thread(target=run)
        with patch.object(collect_reddit, "print_json"):
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            time.sleep(0.05)
            release.set()
            first.join(2)
            second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(transport.requests), 1)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["spent_micro_usd"], 250000)

    def test_empty_success_is_counted_but_coverage_remains_partial(self):
        empty = {
            "code": 200,
            "data": {"subredditV3": {"elements": {
                "edges": [], "pageInfo": {"endCursor": None, "hasNextPage": False}
            }}},
        }
        transport = FixtureTransport([response(200, empty)])
        code, stdout, stderr = self.run_cli(self.args("--max-feed-pages", "1"), transport, FakeCredentials())
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["budget"]["successful_requests"], 1)
        self.assertEqual(result["capabilities"]["source_coverage"]["status"], "partial")
        self.assertIn("empty", result["capabilities"]["source_coverage"]["evidence"].lower())

    def test_http_success_with_provider_error_is_charged_but_not_counted_as_success(self):
        transport = FixtureTransport([
            response(200, {"code": 500, "message": "synthetic API error"}, cost=1000),
        ])

        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["successful_requests"], 0)
        self.assertEqual(state["spent_micro_usd"], 1000)
        self.assertEqual(state["attempts"], 1)
        self.assertIn("provider_api_request_failed", stdout)

    def test_comment_more_without_cursor_is_reported_as_incomplete(self):
        body = {
            "success": True,
            "comments": [],
            "more": {"has_more": True, "cursor": None},
        }
        transport = FixtureTransport([
            response(200, fixture_body("feed-page-2.json")),
            response(200, body),
        ])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-comment-pages", "1", "--max-posts", "1"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertIn("without a usable cursor", report["capabilities"]["source_coverage"]["evidence"])
        self.assertEqual(report["acquisition_attempts"], 2)

    def test_repeated_comment_cursor_stops_and_reports_incomplete_coverage(self):
        more = {"success": True, "comments": [], "more": {"has_more": True, "cursor": "same-cursor"}}
        transport = FixtureTransport([
            response(200, fixture_body("feed-page-2.json")),
            response(200, more),
            response(200, more),
        ])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-comment-pages", "3", "--max-posts", "1"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(len(transport.requests), 3)
        self.assertIn("comment cursor repeated", report["capabilities"]["source_coverage"]["evidence"])

    def test_post_limit_reports_uncommented_in_window_posts(self):
        transport = FixtureTransport([
            response(200, fixture_body("feed-page-1.json")),
            response(200, fixture_body("feed-page-2.json")),
            response(200, fixture_body("comments-page-2.json")),
        ])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "2", "--max-comment-pages", "1", "--max-posts", "1"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["counts"]["posts_in_window"], 2)
        self.assertIn("additional in-window posts", report["capabilities"]["source_coverage"]["evidence"])

    def test_missing_approval_or_ack_fails_closed_without_loading_credentials(self):
        self.approval_path.unlink()
        transport = FixtureTransport([])
        credentials = FakeCredentials()
        code, stdout, _ = self.run_cli(self.args("--plan"), transport, credentials)
        self.assertNotEqual(code, 0)
        self.assertEqual(transport.requests, [])
        self.assertEqual(credentials.loads, 0)
        self.assertIn("blocked", stdout.lower())

        self.write_approval()
        with patch.dict(os.environ, {}, clear=True):
            code, _, _ = self.run_cli(self.args(), transport, credentials)
        self.assertNotEqual(code, 0)
        self.assertEqual(transport.requests, [])
        self.assertEqual(credentials.loads, 0)

    def test_unverified_request_ceiling_fails_closed_before_credentials(self):
        approval = approval_manifest()
        approval["routes"]["feed"]["cost_cap_verified"] = False
        self.write_approval(approval)
        transport = FixtureTransport([])
        credentials = FakeCredentials()

        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            credentials,
        )

        self.assertEqual(code, 2)
        self.assertIn("feed_cost_cap_unverified", stdout)
        self.assertEqual(transport.requests, [])
        self.assertEqual(credentials.loads, 0)

    def test_plan_is_offline_and_does_not_load_credentials(self):
        transport = FixtureTransport([])
        credentials = FakeCredentials()

        code, stdout, stderr = self.run_cli(
            self.args("--plan"), transport, credentials
        )

        self.assertEqual(code, 0, stderr)
        plan = json.loads(stdout)
        self.assertEqual(plan["status"], "ready_for_operator_review")
        self.assertEqual(plan["network_requests"], 0)
        self.assertFalse(plan["feed_language"]["owner_approved"])
        self.assertEqual(transport.requests, [])
        self.assertEqual(credentials.loads, 0)

    def test_live_collection_rejects_alternate_budget_paths_before_credentials(self):
        credentials = FakeCredentials()
        args = collect_reddit.parse_args(self.args("--max-feed-pages", "1", "--max-posts", "0"))
        transport = FixtureTransport([])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = collect_reddit.run_collect(
                args,
                transport,
                credentials.load,
                lambda: NOW,
                lambda _: None,
                False,
            )

        self.assertEqual(code, 2)
        self.assertIn("live_state_paths_must_use_ticket_default", stdout.getvalue())
        self.assertEqual(transport.requests, [])
        self.assertEqual(credentials.loads, 0)

    def test_unmarked_transport_cannot_enable_synthetic_path_override(self):
        transport = FixtureTransport([])
        transport.synthetic = False
        credentials = FakeCredentials()

        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            credentials,
        )

        self.assertEqual(code, 2)
        self.assertIn("live_state_paths_must_use_ticket_default", stdout)
        self.assertEqual(transport.requests, [])
        self.assertEqual(credentials.loads, 0)

    def test_identity_team_slug_is_sent_only_as_request_header_not_recorded(self):
        approval = approval_manifest()
        approval["provider_account"]["token_type"] = "identity"
        self.write_approval(approval)
        credentials = FakeCredentials("synthetic-token", "private-team-slug")
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"))])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            credentials,
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(transport.requests[0][1]["X-Treg-Org"], "private-team-slug")
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        self.assertNotIn("private-team-slug", persisted)

    def test_replay_is_offline_private_and_expires_recordings(self):
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"))])
        code, _, stderr = self.run_cli(self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials())
        self.assertEqual(code, 0, stderr)
        expired = self.recordings_dir / "record-expired.json"
        record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        record["record_id"] = "expired"
        record["recorded_at"] = collect_reddit.timestamp(NOW - collect_reddit.dt.timedelta(days=8))
        record["expires_at"] = collect_reddit.timestamp(NOW - collect_reddit.dt.timedelta(days=1))
        expired.write_text(json.dumps(record))
        expired.chmod(0o600)

        replay_args = [
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ]
        with patch("socket.create_connection", side_effect=AssertionError("network used")):
            code, stdout, stderr = self.run_cli(replay_args)
        self.assertEqual(code, 0, stderr)
        replay = json.loads(stdout)
        self.assertEqual(replay["network_requests"], 0)
        self.assertEqual(replay["counts"]["feed_pages"], 1)
        self.assertEqual(replay["counts"]["feed_posts"], 1)
        self.assertEqual(replay["validation"]["feed_schema"], "supported")
        self.assertEqual(replay["capabilities"]["source_coverage"]["status"], "partial")
        self.assertFalse(expired.exists())
        artifacts = list(self.recordings_dir.glob("replay-*.json"))
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].stat().st_mode & 0o777, 0o600)

        shutil.rmtree(self.recordings_dir)
        missing_transport = FixtureTransport([])
        code, stdout, _ = self.run_cli(replay_args, missing_transport)
        self.assertNotEqual(code, 0)
        self.assertEqual(missing_transport.requests, [])
        self.assertIn("blocked", stdout.lower())

    def test_stricter_retention_is_applied_to_recording_and_replay(self):
        approval = approval_manifest()
        approval["retention"]["days"] = 3
        self.write_approval(approval)
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"))])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        self.assertEqual(record["retention_days"], 3)
        self.assertEqual(
            collect_reddit.parse_datetime(record["expires_at"]),
            NOW + collect_reddit.dt.timedelta(days=3),
        )
        code, stdout, stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])
        self.assertEqual(code, 0, stderr)
        replay_artifact = self.recordings_dir / json.loads(stdout)["artifact"]
        replay = json.loads(replay_artifact.read_text())
        self.assertEqual(replay["retention_days"], 3)


if __name__ == "__main__":
    unittest.main()
