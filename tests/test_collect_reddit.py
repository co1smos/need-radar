import contextlib
import hashlib
import html
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
from urllib.parse import parse_qs, quote, unquote, urlsplit
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
def block_socket_network(event, args):
    if event in SOCKET_AUDIT_EVENTS:
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

    def collect_text_recording(self, text):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["text"] = text
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            FixtureTransport([response(200, page)]),
            FakeCredentials(),
        )
        self.assertEqual(code, 0, stderr)
        records = [
            json.loads(path.read_text())
            for path in self.recordings_dir.glob("record-*.json")
        ]
        self.assertTrue(records)
        self.assertTrue(all(record["processing_complete"] for record in records))
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 4):
            persisted = unquote(persisted)
        return persisted

    def test_public_cli_displays_help(self):
        scratch = pathlib.Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
        sitecustomize = f"""import os, pathlib, sys
if os.environ.get("NEED_RADAR_OFFLINE_TESTS") == "1":
    blocked_network = {SOCKET_AUDIT_EVENTS!r}
    protected = {tuple(map(str, PROTECTED_PATHS))!r}
    filesystem = {BLOCKED_FILESYSTEM_EVENTS!r}
    def audit(event, args):
        if event in blocked_network:
            raise AssertionError("offline network access denied")
        if event in filesystem:
            for value in args[:2]:
                if isinstance(value, (str, bytes, os.PathLike)):
                    path = pathlib.Path(os.path.abspath(os.fsdecode(value)))
                    if any(path == pathlib.Path(item) or pathlib.Path(item) in path.parents for item in protected):
                        raise AssertionError("offline live path access denied")
    sys.addaudithook(audit)
"""
        probe = (
            "import runpy, sys\n"
            "for action in (lambda: sys.audit('socket.getaddrinfo', 'treg.to', 443), "
            "lambda: sys.audit('open', '/home/ubuntu/projects/need-radar/credentials.env', 'r', 0)):\n"
            "    try: action()\n"
            "    except AssertionError: pass\n"
            "    else: raise AssertionError('offline subprocess guard failed open')\n"
            f"sys.argv = [{str(CLI)!r}, '--help']\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        with tempfile.TemporaryDirectory(dir=scratch) as home, tempfile.TemporaryDirectory(dir=scratch) as guard_dir:
            (pathlib.Path(guard_dir) / "sitecustomize.py").write_text(sitecustomize)
            result = subprocess.run(
                [sys.executable, "-c", probe],
                capture_output=True,
                text=True,
                check=False,
                env={
                    "HOME": home,
                    "PATH": os.environ.get("PATH", ""),
                    "TMPDIR": str(scratch),
                    "NEED_RADAR_OFFLINE_TESTS": "1",
                    "PYTHONPATH": guard_dir,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("replay", result.stdout)

    def test_direct_connection_is_stopped_before_socket_creation(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))
        with patch("socket.getaddrinfo", return_value=[address]) as resolve:
            with self.assertRaisesRegex(AssertionError, "socket.__new__"):
                socket.create_connection(("treg.to", 443), timeout=1)
        resolve.assert_called()

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
            "url": "https://first-secret.example/second-secret/path?token=third-secret&credential=fourth-secret",
        }
        sanitized = collect_reddit.sanitize(payload, ["first-secret", "second-secret", "third-secret"])
        serialized = json.dumps(sanitized)

        self.assertNotIn("first-secret", serialized)
        self.assertNotIn("second-secret", serialized)
        self.assertNotIn("third-secret", serialized)
        self.assertNotIn("fourth-secret", serialized)
        self.assertNotIn("signature-secret", serialized)
        redacted_keys = [key for key in sanitized if key.startswith("[REDACTED")]
        self.assertEqual(len(redacted_keys), 2)
        self.assertEqual(len(set(sanitized)), len(sanitized))

    def test_structured_client_secret_variants_are_redacted_in_collection_and_replay(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        secret_values = {
            "clientSecret": "fixture-client-secret-camel",
            "ClientSecret": "fixture-client-secret-title",
            "CLIENTSECRET": "fixture-client-secret-upper",
            "client_secret": "fixture-client-secret-underscore",
            "client-secret": "fixture-client-secret-hyphen",
            r"client\u0053ecret": "fixture-client-secret-escaped",
            r"pass\u0077ord": "fixture-password-unicode-key",
            ("pass" + "\\" * 2 + "u0077ord"): "fixture-password-double-escaped-key",
        }
        post.update(secret_values)

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            FixtureTransport([response(200, page)]),
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        record_path = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(record_path.read_text())
        persisted = json.dumps(record)
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 2):
            persisted = unquote(collect_reddit.decode_escaped_ascii(persisted))
        for secret in secret_values.values():
            self.assertNotIn(secret, persisted)

        recorded_post = record["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        recorded_post.update(secret_values)
        record_path.write_text(json.dumps(record), encoding="utf-8")
        record_path.chmod(0o600)
        replay_code, replay_stdout, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(replay_code, 0, replay_stderr)
        replayed_record = json.loads(record_path.read_text())
        replayed = json.dumps(replayed_record)
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 2):
            replayed = unquote(collect_reddit.decode_escaped_ascii(replayed))
        for secret in secret_values.values():
            self.assertNotIn(secret, replayed)
            self.assertNotIn(secret, replay_stdout)

    def test_malformed_quoted_redaction_is_bounded_in_subprocess(self):
        scratch = pathlib.Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
        payload = 'password="' + "\\" * 1000 + "unterminated fixture-regression-secret"
        probe = f"""import os, pathlib, sys
sys.path.insert(0, {str(ROOT / 'scripts')!r})
blocked_network = {SOCKET_AUDIT_EVENTS!r}
protected = {tuple(map(str, PROTECTED_PATHS))!r}
filesystem = {BLOCKED_FILESYSTEM_EVENTS!r}
def audit(event, args):
    if event in blocked_network:
        raise AssertionError("offline network access denied")
    if event in filesystem:
        for value in args[:2]:
            if isinstance(value, (str, bytes, os.PathLike)):
                path = pathlib.Path(os.path.abspath(os.fsdecode(value)))
                if any(path == pathlib.Path(item) or pathlib.Path(item) in path.parents for item in protected):
                    raise AssertionError("offline live path access denied")
sys.addaudithook(audit)
from collect_reddit import safe_string
print(safe_string({payload!r}))
"""

        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            cwd=ROOT,
            env={
                "HOME": str(scratch),
                "PATH": os.environ.get("PATH", ""),
                "TMPDIR": str(scratch),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            timeout=2,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("fixture-regression-secret", result.stdout)

    def test_mixed_encoded_text_and_authorization_are_redacted_before_persistence(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        encoded_token = quote(quote("fixture-runtime-token", safe=""), safe="")
        encoded_query_key = "https://fixture-user:fixture-password@example.com/?X-Amz-Credential=fixture-query-credential"
        for _ in range(12):
            encoded_query_key = quote(encoded_query_key, safe="")
        post["text"] = (
            "See https://ordinary.example/item and encoded=" + encoded_token
            + "; Authorization: Bearer fixture-third-party-bearer"
            + '; authorization = "Basic fixture-third-party-basic"'
            + "\nPROXY-AUTHORIZATION:\tBasic fixture-proxy-bearer"
            + "\nQuery-key https://outer.example/?" + encoded_query_key + "=value"
        )
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials("fixture-runtime-token"),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 4):
            persisted = unquote(persisted)
        for secret in (
            "fixture-runtime-token",
            "fixture-third-party-bearer",
            "fixture-third-party-basic",
            "fixture-proxy-bearer",
            "fixture-user",
            "fixture-password",
            "fixture-query-credential",
        ):
            self.assertNotIn(secret, persisted)

    def test_mixed_html_and_percent_encoding_is_normalized_before_secret_redaction(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        encoded_tokens = (
            "&#37;26#115;ynthetic-token",
            "&#37;2526#115;ynthetic-token",
            "&amp;#37;26#115;ynthetic-token",
        )
        over_limit = "&#115;ynthetic-token"
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 1):
            over_limit = html.escape(over_limit, quote=True)
        self.assertIsNone(collect_reddit.decode_url_component(over_limit))
        post["text"] = (
            "hidden tokens " + "; ".join(encoded_tokens) + "; "
            "encoded authority https://fixture-user&#37;26#115;ynthetic-token.example.org/path"
        )
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            FixtureTransport([response(200, page)]),
            FakeCredentials("synthetic-token"),
        )

        self.assertEqual(code, 0, stderr)
        artifacts = "\n".join(
            path.read_text(encoding="utf-8") for path in self.recordings_dir.glob("*.json")
        )
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS):
            artifacts = html.unescape(unquote(artifacts))
        self.assertNotIn("synthetic-token", artifacts)

        record_path = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        recorded_post = (
            record["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        )
        recorded_post["text"] = (
            f"Authorization: Bearer {encoded_tokens[0]}; "
            "encoded authority https://fixture-user&#37;26#115;ynthetic-token.example.org/path"
        )
        record_path.write_text(json.dumps(record), encoding="utf-8")
        record_path.chmod(0o600)

        replay_code, replay_stdout, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(replay_code, 0, replay_stderr)
        self.assertEqual(json.loads(replay_stdout)["network_requests"], 0)
        artifacts = "\n".join(
            path.read_text(encoding="utf-8") for path in self.recordings_dir.glob("*.json")
        )
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS):
            artifacts = html.unescape(unquote(artifacts))
        self.assertNotIn("synthetic-token", artifacts)

    def test_sensitive_assignment_context_survives_embedded_url_splitting(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        encoded_cookie = quote(quote("fixture-cookie-after-url", safe=""), safe="")
        encoded_password = quote(quote("fixture-password-after-url", safe=""), safe="")
        encoded_cookie_key = quote("Cookie: redirect=", safe="")
        post["text"] = (
            "Cookie: redirect=https://ordinary.example/path; "
            f"session={encoded_cookie}; refresh=fixture-refresh-after-url\n"
            f'password="quoted https://ordinary.example/path {encoded_password}"\n'
            f"{encoded_cookie_key}https://ordinary.example/path; "
            "session=fixture-encoded-cookie-after-url"
        )
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 2):
            persisted = unquote(persisted)
        for secret in (
            "fixture-cookie-after-url",
            "fixture-refresh-after-url",
            "fixture-password-after-url",
            "fixture-encoded-cookie-after-url",
        ):
            self.assertNotIn(secret, persisted)

    def test_quoted_password_redacts_url_userinfo_before_context_is_split(self):
        persisted = self.collect_text_recording(
            'password: "quoted https://fixture-user:"first '
            'fixture-password-suffix"@example.org/path suffix"\n'
            'Cookie: redirect=https://fixture-user:"first '
            'fixture-cookie-userinfo-suffix"@example.org/path; '
            'session=fixture-cookie-session; refresh=fixture-cookie-refresh\n'
            'ordinary https://ordinary.example/path'
        )

        for secret in (
            "fixture-password-suffix",
            "fixture-cookie-userinfo-suffix",
            "fixture-cookie-session",
            "fixture-cookie-refresh",
        ):
            self.assertNotIn(secret, persisted)
        self.assertIn("https://ordinary.example/path", persisted)

    def test_redacts_backslash_escaped_and_encoded_assignments_in_prose(self):
        text = r'embedded {\"pass\u0077ord\":\"fixture-escaped-json-secret\"} safe-tail'
        for _ in range(2):
            text = quote(text, safe="")

        persisted = self.collect_text_recording(text)

        self.assertNotIn("fixture-escaped-json-secret", persisted)
        self.assertIn("safe-tail", persisted)

    def test_redacts_quoted_credentials_after_literal_escaped_whitespace(self):
        persisted = self.collect_text_recording(
            r'password:\n"first line fixture-newline-whitespace-secret"; '
            r'api_token:\t\'first line fixture-tab-whitespace-secret\'; safe-tail'
        )

        self.assertNotIn("fixture-newline-whitespace-secret", persisted)
        self.assertNotIn("fixture-tab-whitespace-secret", persisted)
        self.assertIn("safe-tail", persisted)

    def test_redacts_multiply_encoded_assignments_in_collection_and_replay(self):
        encoded_values = {}
        secrets = (
            "fixture-three-json-layers-secret",
            "fixture-four-json-layers-secret",
            "fixture-multiply-unicode-label-secret",
            "fixture-html-quoted-password with spaces",
            "fixture-multiline-json-newline-password with spaces",
            "fixture-multiline-json-tab-password with spaces",
            "fixture-multiline-json-return-password with spaces",
        )
        for layers, secret in zip((3, 4), secrets):
            value = f'password: "{secret}"'
            for _ in range(layers):
                value = json.dumps(value)
            encoded_values[f"encoded_{layers}"] = value
        encoded_values["unicode_label"] = (
            r'pass\\u005cu0077ord: "fixture-multiply-unicode-label-secret"'
        )
        encoded_values["html_quoted"] = (
            "password=&quot;fixture-html-quoted-password with spaces&quot;; safe-tail"
        )
        for whitespace, name, secret in zip(
            ("\n", "\t", "\r"),
            ("newline", "tab", "return"),
            secrets[4:],
        ):
            multiline = (
                f'password:{whitespace}"first line {secret}"; '
                f"fixture-{name}-safe-tail"
            )
            for _ in range(2):
                multiline = json.dumps(multiline)
            encoded_values[f"multiline_{name}"] = multiline

        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        untrusted_text = " | ".join(("Archived source", *encoded_values.values()))
        post["text"] = untrusted_text
        code, collection_stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            FixtureTransport([response(200, page)]),
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        record_path = next(self.recordings_dir.glob("record-*.json"))

        def recursively_decode(value):
            for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 4):
                normalized = html.unescape(
                    unquote(collect_reddit.decode_escaped_ascii(value) or value)
                )
                try:
                    decoded = json.loads(normalized)
                except (json.JSONDecodeError, ValueError):
                    decoded = None
                if isinstance(decoded, str):
                    value = decoded
                elif normalized == value:
                    break
                else:
                    value = normalized
            return value

        recorded = json.loads(record_path.read_text())
        recorded_post = recorded["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        persisted = "\n".join(
            recursively_decode(part) for part in recorded_post["text"].split(" | ")
        )
        all_artifacts = "\n".join(
            path.read_text() for path in self.recordings_dir.glob("*.json")
        )
        for secret in secrets:
            self.assertNotIn(secret, persisted)
            self.assertNotIn(secret, all_artifacts)
            self.assertNotIn(secret, collection_stdout + stderr)
        for name in ("newline", "tab", "return"):
            self.assertIn(f"fixture-{name}-safe-tail", persisted)

        recorded_post["text"] = untrusted_text
        record_path.write_text(json.dumps(recorded), encoding="utf-8")
        record_path.chmod(0o600)
        replay_code, replay_stdout, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(replay_code, 0, replay_stderr)
        replayed = json.loads(record_path.read_text())
        replayed_post = replayed["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        persisted = "\n".join(
            recursively_decode(part) for part in replayed_post["text"].split(" | ")
        )
        all_artifacts = "\n".join(
            path.read_text() for path in self.recordings_dir.glob("*.json")
        )
        for secret in secrets:
            self.assertNotIn(secret, persisted)
            self.assertNotIn(secret, all_artifacts)
            self.assertNotIn(secret, replay_stdout)
            self.assertNotIn(secret, replay_stderr)

    def test_fails_closed_on_overlimit_html_credentials_in_collection_and_replay(self):
        secret = "fixture-html-overlimit-password with spaces"
        encoded = f'"{secret}"'
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 1):
            encoded = html.escape(encoded, quote=True)
        text = "password=" + encoded
        persisted = self.collect_text_recording(text)
        self.assertNotIn(secret, persisted)

        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        record["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]["text"] = text
        source.write_text(json.dumps(record), encoding="utf-8")
        source.chmod(0o600)
        replay_code, replay_stdout, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(replay_code, 0, replay_stderr)
        all_artifacts = "\n".join(
            path.read_text() for path in self.recordings_dir.glob("*.json")
        )
        self.assertNotIn(secret, all_artifacts)
        self.assertNotIn(secret, replay_stdout)
        self.assertNotIn(secret, replay_stderr)

    def test_redacts_private_secret_and_api_token_assignments_in_prose(self):
        persisted = self.collect_text_recording(
            'private_key="fixture-private-key-secret"; '
            "SECRET-KEY: 'fixture-secret-key-secret'; "
            'api_token = "fixture-api-token-secret"; '
            "API-TOKEN=fixture-api-token-hyphen-secret"
        )

        for secret in (
            "fixture-private-key-secret",
            "fixture-secret-key-secret",
            "fixture-api-token-secret",
            "fixture-api-token-hyphen-secret",
        ):
            self.assertNotIn(secret, persisted)

    def test_normalizes_unicode_escapes_before_secret_and_query_classification(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["text"] = (
            r"plain \u005cu0073ynthetic-token "
            r"https://ordinary.example/\u005cu0073ynthetic-token "
            "https://ordinary.example/?pass%5Cu0077ord=fixture-query-secret"
        )

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            FixtureTransport([response(200, page)]),
            FakeCredentials("synthetic-token"),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 4):
            persisted = unquote(collect_reddit.decode_escaped_ascii(persisted))
        self.assertNotIn("synthetic-token", persisted)
        self.assertNotIn("fixture-query-secret", persisted)

    def test_residual_non_ascii_unicode_escapes_are_redacted_before_persistence(self):
        self.collect_text_recording(r"unicode note=\u79d8\u5bc6")
        record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        text = record["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]["text"]

        self.assertNotIn("79d8", text)
        self.assertNotIn("秘密", text)

    def test_excessively_encoded_dictionary_key_fails_closed_with_lineage(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post[r"\u0025" + "25" * 20 + "41"] = "fixture-untrusted-key-value"
        transport = FixtureTransport([response(200, page)])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2, stderr)
        report = json.loads(stdout)
        self.assertIn("response_sanitization_failed", report["errors"])
        self.assertEqual(len(transport.requests), 1)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertIsNone(state["pending"])
        self.assertEqual(state["successful_requests"], 1)
        record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        self.assertEqual(record["error"], "response_sanitization_failed")
        self.assertNotIn("fixture-untrusted-key-value", json.dumps(record))
        replay_code, replay_stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])
        self.assertEqual(replay_code, 2)
        self.assertIn("response_sanitization_failed", json.loads(replay_stdout)["errors"])

    def test_replay_rejects_sanitization_record_interrupted_before_settlement(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post[r"\u0025" + "25" * 20 + "41"] = "fixture-untrusted-key-value"
        with patch.object(collect_reddit.Collector, "settle", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli(
                    self.args("--max-feed-pages", "1", "--max-posts", "0"),
                    FixtureTransport([response(200, page)]),
                    FakeCredentials(),
                )

        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        self.assertFalse(record["processing_complete"])
        self.assertNotIn("fixture-untrusted-key-value", json.dumps(record))
        replay_code, replay_stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])
        self.assertEqual(replay_code, 2)
        self.assertIn("acquisition_processing_incomplete", json.loads(replay_stdout)["errors"])

    def test_redacts_multiline_private_key_backtick_and_unicode_escaped_key(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["text"] = "\n".join((
            "private_key: -----BEGIN PRIVATE KEY-----",
            "fixture-private-block-secret",
            "-----END PRIVATE KEY-----",
            "private key:\n-----BEGIN PRIVATE KEY-----",
            "fixture-spaced-private-block-secret",
            "-----END PRIVATE KEY-----",
            "password=`fixture backtick password secret`",
            'Cookie: session=fixture-cookie-before-url https://ordinary.example/path',
            'Authorization: Bearer fixture-auth-first-line\n  fixture-auth-folded-secret',
            'password: \"fixture-quoted-first-line\nfixture-quoted-second-line-secret\" safe-tail',
        ))
        post[r"pass\u0077ord"] = "fixture-unicode-key-secret"
        post["pass%255Cu0077ord"] = "fixture-multiply-encoded-key-secret"
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 4):
            persisted = unquote(persisted)
        for secret in (
            "fixture-private-block-secret",
            "fixture-spaced-private-block-secret",
            "fixture backtick password secret",
            "fixture-cookie-before-url",
            "fixture-auth-first-line",
            "fixture-auth-folded-secret",
            "fixture-quoted-first-line",
            "fixture-quoted-second-line-secret",
            "fixture-unicode-key-secret",
            "fixture-multiply-encoded-key-secret",
        ):
            self.assertNotIn(secret, persisted)
        self.assertIn("safe-tail", persisted)

    def test_redacts_standalone_and_unterminated_private_key_blocks_before_persistence(self):
        persisted = self.collect_text_recording(
            "before\n-----BEGIN PRIVATE KEY-----\n"
            "fixture-standalone-private-key-secret\n-----END PRIVATE KEY-----\n"
            "between\n-----BEGIN RSA PRIVATE KEY-----\n"
            "fixture-unterminated-private-key-secret"
        )

        self.assertNotIn("fixture-standalone-private-key-secret", persisted)
        self.assertNotIn("fixture-unterminated-private-key-secret", persisted)
        self.assertIn("between", persisted)

    def test_redacts_escaped_slashes_before_url_and_secret_scanning(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["text"] = (
            r'prose {"api":"fixture\/runtime-token"} URL '
            r'https:\/\/fixture-user:fixture-password@example.org\/fixture\/runtime-token'
            r'?X-Amz-Credential=fixture-escaped-url-secret'
        )
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            FixtureTransport([response(200, page)]),
            FakeCredentials("fixture/runtime-token"),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 4):
            persisted = unquote(persisted).replace(r"\/", "/")
        for secret in (
            "fixture/runtime-token",
            "fixture-user",
            "fixture-password",
            "fixture-escaped-url-secret",
        ):
            self.assertNotIn(secret, persisted)

    def test_replay_redacts_legacy_recordings_before_reuse(self):
        self.collect_text_recording("safe synthetic text")
        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        post = record["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        record["request"]["parameters"]["password"] = "fixture-replay-request-secret"
        post["text"] = (
            "-----BEGIN PRIVATE KEY-----\nfixture-replay-private-key-secret\n"
            "-----END PRIVATE KEY----- See "
            "https://fixture-user:fixture-password@example.org/path"
            "?X-Amz-Credential=fixture-replay-url-secret"
        )
        source.write_text(json.dumps(record))

        code, stdout, stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["counts"]["replay_sanitized_recordings"], 1)
        sanitized_record = json.loads(source.read_text())
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        parameters = sanitized_record["request"]["parameters"]
        self.assertEqual(parameters["[REDACTED_KEY]"], "[REDACTED]")
        self.assertEqual(
            sanitized_record["request_parameters_sha256"],
            hashlib.sha256(json.dumps(parameters, sort_keys=True).encode("utf-8")).hexdigest(),
        )
        replay_code, replay_stdout, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])
        self.assertEqual(replay_code, 0, replay_stderr)
        self.assertEqual(json.loads(replay_stdout)["counts"]["replay_sanitized_recordings"], 0)
        for secret in (
            "fixture-replay-private-key-secret",
            "fixture-user",
            "fixture-password",
            "fixture-replay-url-secret",
            "fixture-replay-request-secret",
        ):
            self.assertNotIn(secret, persisted)

    def test_redacts_non_http_uri_userinfo_in_collection_and_replay(self):
        source_text = (
            "connect postgres://fixture-postgres-user:fixture-postgres-password@localhost/db "
            "and redis://fixture-redis-user:fixture-redis-password@localhost/0 "
            "and Amqp+TLS://fixture-amqp-user:fixture-amqp-password@queue.internal/vhost "
            "and postgresql+srv://fixture-encoded%40user:fixture-encoded%2Fpassword@db.internal/name"
        )
        persisted = self.collect_text_recording(source_text)
        secrets = (
            "fixture-postgres-user",
            "fixture-postgres-password",
            "fixture-redis-user",
            "fixture-redis-password",
            "fixture-amqp-user",
            "fixture-amqp-password",
            "fixture-encoded",
        )
        for secret in secrets:
            self.assertNotIn(secret, persisted)
        for host in ("localhost/db", "localhost/0", "queue.internal/vhost", "db.internal/name"):
            self.assertIn(host, persisted)

        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        post = record["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["text"] = source_text
        source.write_text(json.dumps(record))

        code, stdout, stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["counts"]["replay_sanitized_recordings"], 1)
        replayed = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for secret in secrets:
            self.assertNotIn(secret, replayed)
            self.assertNotIn(secret, stdout)

    def test_redacts_triple_quoted_credentials_in_collection_and_replay(self):
        source_text = (
            'password = """fixture-triple-double-first\n'
            'fixture-triple-double-second""" public-tail\n'
            "api_token = '''fixture-triple-single-first\n"
            "fixture-triple-single-second''' another-public-tail\n"
            'password = """fixture-triple-unclosed-secret\n'
            "fixture-after-unclosed-secret"
        )
        persisted = self.collect_text_recording(source_text)
        secrets = (
            "fixture-triple-double-first",
            "fixture-triple-double-second",
            "fixture-triple-single-first",
            "fixture-triple-single-second",
            "fixture-triple-unclosed-secret",
            "fixture-after-unclosed-secret",
        )
        for secret in secrets:
            self.assertNotIn(secret, persisted)

        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        post = record["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["text"] = source_text
        source.write_text(json.dumps(record))

        code, stdout, stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["counts"]["replay_sanitized_recordings"], 1)
        replayed = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for secret in secrets:
            self.assertNotIn(secret, replayed)
            self.assertNotIn(secret, stdout)
        self.assertIn("public-tail", replayed)
        self.assertIn("another-public-tail", replayed)

    def test_redacts_folded_authorization_header_continuations(self):
        persisted = self.collect_text_recording(
            "Authorization: Bearer fixture-header-first-secret\r\n"
            "\tfixture-header-folded-secret\r\n"
            "Cookie: session=fixture-cookie-first-secret\r\n"
            "\tfixture-cookie-folded-secret\r\npublic-header-tail"
        )

        self.assertNotIn("fixture-header-first-secret", persisted)
        self.assertNotIn("fixture-header-folded-secret", persisted)
        self.assertNotIn("fixture-cookie-first-secret", persisted)
        self.assertNotIn("fixture-cookie-folded-secret", persisted)
        self.assertIn("public-header-tail", persisted)

    def test_redacts_whitespace_containing_quoted_url_userinfo(self):
        persisted = "\n".join((
            self.collect_text_recording(
                'Source https://fixture-user:"first fixture-userinfo-suffix"'
                '@example.org/path?X-Amz-Credential=fixture-url-query-secret'
            ),
            self.collect_text_recording(
                'Malformed https://fixture-user:"first fixture-unclosed-userinfo-secret'
                '@example.org/path'
            ),
            self.collect_text_recording(
                r'Escaped https://fixture-user:\"first fixture-escaped-userinfo-secret\"'
                '@example.org/path'
            ),
        ))

        for secret in (
            "fixture-user",
            "first",
            "fixture-userinfo-suffix",
            "fixture-url-query-secret",
            "fixture-unclosed-userinfo-secret",
            "fixture-escaped-userinfo-secret",
        ):
            self.assertNotIn(secret, persisted)

    def test_replay_rejects_recording_interrupted_before_settlement(self):
        transport = FixtureTransport([
            response(200, fixture_body("feed-page-2.json"), cost=1500)
        ])
        with patch.object(collect_reddit.Collector, "settle", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli(
                    self.args("--max-feed-pages", "1", "--max-posts", "0"),
                    transport,
                    FakeCredentials(),
                )

        source = next(self.recordings_dir.glob("record-*.json"))
        self.assertFalse(json.loads(source.read_text())["processing_complete"])

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["counts"]["feed_pages"], 0)
        self.assertIn("acquisition_processing_incomplete", report["errors"])
        self.assertIn("recorded_charge_exceeds_request_ceiling", report["errors"])
        self.assertEqual(report["capabilities"]["billing_evidence"]["status"], "blocked")

    def test_replay_rejects_recorded_charge_above_request_ceiling(self):
        transport = FixtureTransport([
            response(200, fixture_body("feed-page-2.json"), cost=1500)
        ])
        code, _, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )
        self.assertEqual(code, 2)
        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        record["error"] = None
        source.write_text(json.dumps(record))
        source.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["counts"]["feed_pages"], 0)
        self.assertIn("recorded_charge_exceeds_request_ceiling", report["errors"])

    def test_redacts_quoted_cookie_and_encoded_secrets_from_persisted_text(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        encoded_secret = "fixture-encoded-secret"
        for _ in range(7):
            encoded_secret = quote(encoded_secret, safe="")
        post["text"] = "\n".join((
            'Authorization: Bearer "fixture-bearer-secret"',
            'Authorization = \'Basic fixture-basic-secret\'',
            'Cookie: session=fixture-session-secret; refresh=fixture-refresh-secret',
            'api_key: "fixture-json-key"',
            "password = 'fixture-password'",
            'source says {"password": "fixture-prose-password", "api_key": "fixture-prose-key"}',
            f"ordinary https://example.org/path encoded={encoded_secret}",
        ))
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials("fixture-encoded-secret"),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 2):
            persisted = unquote(persisted)
        for secret in (
            "fixture-bearer-secret",
            "fixture-basic-secret",
            "fixture-session-secret",
            "fixture-refresh-secret",
            "fixture-json-key",
            "fixture-password",
            "fixture-prose-password",
            "fixture-prose-key",
            "fixture-encoded-secret",
        ):
            self.assertNotIn(secret, persisted)

    def test_redacts_escaped_multiline_and_quoted_url_credentials_before_persistence(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["text"] = "\n".join((
            'quoted JSON {"password": "fixture-escaped-secret\\" fixture-escaped-suffix"}',
            'password="first line\nfixture-multiline-password"',
            'See https://example.org/?X-Amz-Credential="fixture-url-secret"',
            'Malformed https://fixture-malformed-user:fixture-malformed-password@example.org:bad/path?X-Amz-Credential="fixture-malformed-url-secret"',
        ))
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 2):
            persisted = unquote(persisted)
        leaked = [secret for secret in (
            "fixture-escaped-secret",
            "fixture-escaped-suffix",
            "fixture-multiline-password",
            "fixture-url-secret",
            "fixture-malformed-user",
            "fixture-malformed-password",
            "fixture-malformed-url-secret",
        ) if secret in persisted]
        self.assertEqual(leaked, [])

        replay_code, replay_stdout, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])
        self.assertEqual(replay_code, 0, replay_stderr)
        self.assertNotIn("fixture-", replay_stdout)

    def test_redacts_credentials_across_url_boundaries_and_escaped_labels(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["text"] = "\n".join((
            r'source says {"\u0070\u0061\u0073\u0073\u0077\u006f\u0072\u0064": "fixture-unicode-label-secret"}',
            'password="before \\"fixture-escaped-quote-secret\\" after"',
            'Authorization: Bearer "fixture-authorization-secret"',
            'Cookie: session=fixture-cookie-before-url; redirect=https://ordinary.example/path; refresh=fixture-cookie-after-url',
            'See https://example.org/?X-Amz-Credential="fixture-query-url-secret"',
            'See https://fixture-user:"fixture-userinfo-secret"@example.org/path',
            'password="first line\nfixture-multiline-suffix-secret"',
        ))
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(collect_reddit.MAX_URL_DECODE_ROUNDS + 2):
            persisted = unquote(persisted)
        leaked = [secret for secret in (
            "fixture-unicode-label-secret",
            "fixture-escaped-quote-secret",
            "fixture-multiline-suffix-secret",
            "fixture-authorization-secret",
            "fixture-cookie-before-url",
            "fixture-cookie-after-url",
            "fixture-query-url-secret",
            "fixture-userinfo-secret",
        ) if secret in persisted]
        self.assertEqual(leaked, [])

    def test_multiline_authorization_and_cookie_values_are_redacted_before_persistence(self):
        texts = (
            'Authorization: "fixture-auth-first-line\nfixture-auth-multiline-secret"',
            'Cookie: "session=fixture-cookie-first-line\nfixture-cookie-multiline-secret"',
        )
        for text in texts:
            page = fixture_body("feed-page-1.json")
            page["data"]["subredditV3"]["elements"]["edges"][0]["node"]["text"] = text
            code, _, stderr = self.run_cli(
                self.args("--max-feed-pages", "1", "--max-posts", "0"),
                FixtureTransport([response(200, page)]),
                FakeCredentials(),
            )
            self.assertEqual(code, 0, stderr)

        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        self.assertNotIn("fixture-auth-first-line", persisted)
        self.assertNotIn("fixture-auth-multiline-secret", persisted)
        self.assertNotIn("fixture-cookie-first-line", persisted)
        self.assertNotIn("fixture-cookie-multiline-secret", persisted)

    def test_malformed_url_is_redacted_in_persisted_record(self):
        for value in (
            "https://fixture-user:fixture-password@example.com:bad/path?X-Amz-Credential=fixture-credential",
            "https://fixture-user:fixture-password@[invalid/path?token=fixture-credential",
            "/relative/path?token=fixture-credential",
            "https://example.com/path\n?token=fixture-credential",
            "https://example.com/path%ZZ?token=fixture-credential",
        ):
            with self.subTest(value=value):
                self.assertEqual(collect_reddit.safe_url(value), "[REDACTED_URL]")

        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        malformed = "https://fixture-user:fixture-password@example.com:bad/path?X-Amz-Credential=fixture-credential"
        post["url"] = malformed
        post["text"] = "copied from " + malformed
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("record-*.json"))
        for secret in ("fixture-user", "fixture-password", "fixture-credential"):
            self.assertNotIn(secret, persisted)
        self.assertIn("[REDACTED_URL]", persisted)

    def test_nested_signed_url_is_redacted_in_persisted_artifacts(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        nested_url = "https://example.com/item?X-Amz-Credential=fixture-nested-credential"
        over_encoded_url = nested_url
        for _ in range(10):
            over_encoded_url = quote(over_encoded_url, safe="")
        post["text"] = (
            "redirect https://example.com/redirect?next="
            + quote(nested_url, safe="")
            + "&mirror="
            + over_encoded_url
        )
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        self.assertNotIn("fixture-nested-credential", persisted)
        self.assertEqual(
            collect_reddit.safe_url("https://example.com/?q=hello%20world"),
            "https://example.com/?q=hello+world",
        )

    def test_encoded_url_components_and_query_keys_are_redacted_before_persistence(self):
        self.assertEqual(
            collect_reddit.safe_string("see https://example.com/?q=hello%20world"),
            "see https://example.com/?q=hello+world",
        )
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        encoded_path_secret = quote(quote("fixture/radar-1234", safe=""), safe="")
        nested_key = "https://fixture-user:fixture-password@example.com/?X-Amz-Credential=fixture-key-secret"
        encoded_key = quote(quote(nested_key, safe=""), safe="")
        encoded_dictionary_url = quote(quote(
            "https://fixture-dictionary-user:fixture-dictionary-password@example.com/public?X-Amz-Credential=fixture-dictionary-credential",
            safe="",
        ), safe="")
        encoded_token_key = quote(quote("fixture/radar-1234", safe=""), safe="")
        nested_value = "https://example.com/item?X-Amz-Credential=fixture-query-secret"
        encoded_value = nested_value
        for _ in range(12):
            encoded_value = quote(encoded_value, safe="")
        encoded_over_limit_path = "fixture/over-limit-secret"
        for _ in range(20):
            encoded_over_limit_path = quote(encoded_over_limit_path, safe="")
        post["text"] = (
            f"path https://example.com/{encoded_path_secret} "
            f"query-key https://example.com/?{encoded_key}=value "
            f"query-value https://example.com/?redirect={encoded_value} "
            f"deep-path https://example.com/{encoded_over_limit_path}"
        )
        post[encoded_dictionary_url] = "source-derived dictionary key"
        post[encoded_token_key] = "encoded runtime token key"
        post["access%255Ftoken"] = "fixture-access-value"
        transport = FixtureTransport([response(200, page)])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials("fixture/radar-1234"),
        )

        self.assertEqual(code, 0, stderr)
        persisted = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for _ in range(12):
            persisted = unquote(persisted)
        for secret in (
            "fixture-user",
            "fixture-password",
            "fixture-key-secret",
            "fixture-query-secret",
            "fixture/over-limit-secret",
            "fixture-dictionary-user",
            "fixture-dictionary-password",
            "fixture-dictionary-credential",
            "fixture/radar-1234",
            "fixture-access-value",
        ):
            self.assertNotIn(secret, persisted)
        self.assertNotIn("access_token", persisted)

    def test_html_encoded_sensitive_keys_are_redacted_in_collection_and_replay(self):
        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["pass&#119;ord"] = "fixture-html-value-84721"
        post["pass%26%23119%3Bord"] = "fixture-html-value-84722"
        post["url"] = (
            "https://example.org/?pass%26%23119%3Bord=fixture-html-value-84723"
            "&pass%2526%2523119%253Bord=fixture-html-value-84724"
        )
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            FixtureTransport([response(200, page)]),
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        record_path = next(self.recordings_dir.glob("record-*.json"))
        original = json.loads(record_path.read_text())
        secrets = (
            "fixture-html-value-84721",
            "fixture-html-value-84722",
            "fixture-html-value-84723",
            "fixture-html-value-84724",
        )
        for secret in secrets:
            self.assertNotIn(secret, record_path.read_text())

        recorded_post = (
            original["response"]["body"]["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        )
        recorded_post["pass&#119;ord"] = "fixture-html-value-84721"
        recorded_post["pass%26%23119%3Bord"] = "fixture-html-value-84722"
        recorded_post["url"] = (
            "https://example.org/?pass%26%23119%3Bord=fixture-html-value-84723"
            "&pass%2526%2523119%253Bord=fixture-html-value-84724"
        )
        record_path.write_text(json.dumps(original), encoding="utf-8")
        record_path.chmod(0o600)

        replay_code, _, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(replay_code, 0, replay_stderr)
        artifacts = "\n".join(path.read_text() for path in self.recordings_dir.glob("*.json"))
        for secret in secrets:
            self.assertNotIn(secret, artifacts)

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

    def test_post_permalink_requires_valid_id_and_safe_path_before_comment_dispatch(self):
        for url in (
            "https://www.reddit.com/r/example/comments/../about/",
            "https://www.reddit.com/r/example/comments/./title/",
            "https://www.reddit.com/r/example/comments/%2e%2e/title/",
            "https://www.reddit.com/r/example/comments/demo1/%252e%252e/",
            "https://www.reddit.com/r/example/comments/demo-1/title/",
            "https://www.reddit.com/r/example/comments/démø/title/",
            "https://www.reddit.com/r/example/comments/demo1/../about/",
        ):
            with self.subTest(url=url):
                self.assertIsNone(collect_reddit.post_url({"url": url}, [], "example"))

        page = fixture_body("feed-page-1.json")
        post = page["data"]["subredditV3"]["elements"]["edges"][0]["node"]
        post["url"] = "https://www.reddit.com/r/example/comments/../about/"
        transport = FixtureTransport([response(200, page)])
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-comment-pages", "1", "--max-posts", "1"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(urlsplit(transport.requests[0][0]).path, f"/call/{collect_reddit.FEED_ID}")
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["attempts"], 1)

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

    def test_repository_guard_discovers_worktrees_from_primary_and_worker(self):
        primary = self.root / "primary"
        common_git = primary / ".git"
        common_git.mkdir(parents=True)
        worker = self.root / "worker"
        other_worktree = self.root / "other-worktree"
        for name, worktree in (("worker", worker), ("other", other_worktree)):
            entry = common_git / "worktrees" / name
            entry.mkdir(parents=True)
            worktree.mkdir()
            (worktree / ".git").write_text(f"gitdir: {entry}\n")
            gitfile = worktree / ".git"
            gitfile_reference = os.path.relpath(gitfile, entry) if name == "other" else str(gitfile)
            (entry / "gitdir").write_text(gitfile_reference + "\n")
            (entry / "commondir").write_text("../..\n")

        with patch.object(collect_reddit, "__file__", str(primary / "scripts" / "collect_reddit.py")):
            self.assertTrue(collect_reddit.repository_path(other_worktree / "private-recordings"))
            replay_args = collect_reddit.parse_args([
                "replay",
                "--recordings-dir", str(other_worktree / "private-recordings"),
                "--state-dir", str(self.state_dir),
            ])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = collect_reddit.run_replay(replay_args, lambda: NOW)
            self.assertEqual(code, 2)
            self.assertIn("private_artifacts_must_be_outside_repository", stdout.getvalue())
            self.assertFalse((other_worktree / "private-recordings").exists())

        with patch.object(collect_reddit, "__file__", str(worker / "scripts" / "collect_reddit.py")):
            self.assertTrue(collect_reddit.repository_path(primary / "private-recordings"))
            self.assertTrue(collect_reddit.repository_path(other_worktree / "private-recordings"))

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

    def test_replay_preserves_malformed_timestamp_failure(self):
        page = fixture_body("feed-page-1.json")
        page["data"]["subredditV3"]["elements"]["edges"][0]["node"]["createdAt"] = 1e300
        transport = FixtureTransport([response(200, page)])
        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )
        self.assertEqual(code, 2)
        self.assertIn("feed_response_payload_invalid", json.loads(stdout)["errors"])

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["counts"]["feed_pages"], 0)
        self.assertEqual(report["counts"]["validation_failures"], 1)
        self.assertIn("feed_response_payload_invalid", report["errors"])
        source_record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        self.assertEqual(source_record["validation_errors"], ["feed_response_payload_invalid"])
        artifact = json.loads((self.recordings_dir / report["artifact"]).read_text())
        self.assertEqual(artifact["status"], "failed")

    def test_replay_preserves_recorded_unknown_request_failure(self):
        transport = FixtureTransport([TimeoutError("synthetic timeout")])
        code, _, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials()
        )
        self.assertEqual(code, 2)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["counts"]["request_failures"], 1)
        self.assertIn("request_outcome_unknown:TimeoutError", report["errors"])

    def test_replay_preserves_truncated_response_failure(self):
        truncated = response(200, fixture_body("feed-page-2.json"))
        truncated.truncated = True
        transport = FixtureTransport([truncated])
        code, _, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )
        self.assertEqual(code, 2)
        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        self.assertEqual(record["error"], "response_exceeded_recording_size_limit")
        record["error"] = None
        source.write_text(json.dumps(record))
        source.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["counts"]["feed_pages"], 0)
        self.assertIn("response_exceeded_recording_size_limit", report["errors"])

    def test_replay_preserves_unknown_billing_failure(self):
        unknown_billing = response(200, fixture_body("feed-page-2.json"))
        del unknown_billing.headers["X-Treg-Cost-Micro"]
        transport = FixtureTransport([unknown_billing])
        code, _, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )
        self.assertEqual(code, 2)
        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        self.assertEqual(record["error"], "billing_amount_unknown_reconciliation_required")
        record["error"] = None
        source.write_text(json.dumps(record))
        source.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["counts"]["feed_pages"], 0)
        self.assertEqual(report["counts"]["billing_unknown_requests"], 1)
        self.assertIn("billing_evidence_incomplete_reconciliation_required", report["errors"])
        self.assertEqual(report["capabilities"]["billing_evidence"]["status"], "blocked")
        self.assertEqual(report["billing_unknown_record_ids"], [record["record_id"]])

    def test_replay_reports_billing_uncertainty_alongside_payload_failure(self):
        page = fixture_body("feed-page-1.json")
        page["data"]["subredditV3"]["elements"]["edges"][0]["node"]["createdAt"] = 1e300
        transport = FixtureTransport([response(200, page)])
        code, _, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials()
        )
        self.assertEqual(code, 2)
        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        record["billing"]["charged_micro_usd"] = None
        source.write_text(json.dumps(record))
        source.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["counts"]["validation_failures"], 1)
        self.assertEqual(report["counts"]["billing_unknown_requests"], 1)
        self.assertIn("feed_response_payload_invalid", report["errors"])
        self.assertEqual(report["capabilities"]["billing_evidence"]["status"], "blocked")

    def test_replay_keeps_mixed_valid_and_invalid_feed_evidence_partial(self):
        invalid_page = fixture_body("feed-page-1.json")
        invalid_page["data"]["subredditV3"]["elements"]["edges"][0]["node"]["createdAt"] = 1e300
        transport = FixtureTransport([
            response(200, fixture_body("feed-page-1.json")),
            response(200, invalid_page),
        ])
        code, _, _ = self.run_cli(
            self.args("--max-feed-pages", "2", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )
        self.assertEqual(code, 2)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["counts"]["feed_pages"], 1)
        self.assertEqual(report["counts"]["validation_failures"], 1)
        self.assertIn("feed_response_payload_invalid", report["errors"])

    def test_replay_keeps_valid_evidence_when_an_envelope_is_invalid(self):
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"))])
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials()
        )
        self.assertEqual(code, 0, stderr)
        source = next(self.recordings_dir.glob("record-*.json"))
        valid_record = json.loads(source.read_text())
        invalid_record = dict(valid_record)
        invalid_record.pop("record_id")
        invalid_path = self.recordings_dir / "record-invalid-envelope.json"
        invalid_path.write_text(json.dumps(invalid_record))
        invalid_path.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["counts"]["feed_pages"], 1)
        self.assertEqual(report["counts"]["invalid_recordings"], 1)
        self.assertIn("recording_envelope_invalid", report["errors"])

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

    def test_replay_reports_invalid_record_envelope_without_crashing(self):
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"))])
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"), transport, FakeCredentials()
        )
        self.assertEqual(code, 0, stderr)
        source = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(source.read_text())
        del record["record_id"]
        source.write_text(json.dumps(record))
        source.chmod(0o600)
        malformed_record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        malformed_record["record_id"] = 12345678901234567890123456789012
        malformed_path = self.recordings_dir / "record-malformed-id.json"
        malformed_path.write_text(json.dumps(malformed_record))
        malformed_path.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["counts"]["invalid_recordings"], 2)
        self.assertEqual(report["counts"]["validation_failures"], 2)
        self.assertEqual(report["record_ids"], [])
        self.assertIn("recording_envelope_invalid", report["errors"])
        artifact = json.loads((self.recordings_dir / report["artifact"]).read_text())
        self.assertEqual(artifact["status"], "failed")

    def test_expiry_timestamp_overflow_does_not_interrupt_replay_cleanup(self):
        transport = FixtureTransport([response(200, fixture_body("feed-page-2.json"))])
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )
        self.assertEqual(code, 0, stderr)
        source = next(self.recordings_dir.glob("record-*.json"))
        malformed = json.loads(source.read_text())
        malformed["record_id"] = collect_reddit.uuid.uuid4().hex
        malformed["recorded_at"] = "0001-01-01T00:00:00+01:00"
        malformed_path = self.recordings_dir / ("record-" + malformed["record_id"] + ".json")
        malformed_path.write_text(json.dumps(malformed))
        malformed_path.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])

        self.assertEqual(code, 0)
        self.assertFalse(malformed_path.exists())
        self.assertEqual(json.loads(stdout)["counts"]["feed_pages"], 1)

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

    def test_replay_marks_transient_attempt_complete_after_settled_retry(self):
        transport = FixtureTransport([
            response(503, {"error": "synthetic transient"}, cost=1000, call_id="retry-1"),
            response(200, fixture_body("feed-page-2.json"), cost=1000, call_id="retry-2"),
        ])
        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )
        self.assertEqual(code, 0, stderr)
        records = [
            json.loads(path.read_text())
            for path in self.recordings_dir.glob("record-*.json")
        ]
        transient_record = next(
            record for record in records if record["response"]["http_status"] == 503
        )
        self.assertTrue(transient_record["processing_complete"])
        self.assertEqual(transient_record["error"], "transient_retry_response")

        replay_code, replay_stdout, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(replay_code, 0, replay_stderr)
        replay = json.loads(replay_stdout)
        self.assertNotIn("acquisition_processing_incomplete", replay["errors"])
        self.assertIn("transient_retry_response", replay["errors"])
        self.assertEqual(replay["counts"]["billing_unknown_requests"], 0)

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

    def test_overlong_retry_after_is_a_structured_failure(self):
        self.assertEqual(collect_reddit.retry_delay("0" * 5000, 1, NOW), 0)
        transport = FixtureTransport([
            response(
                503,
                {"error": "synthetic busy"},
                extra_headers={"Retry-After": "9" * 5000},
            ),
        ])

        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2)
        report = json.loads(stdout)
        self.assertIn("retry_after_exceeds_local_bound", report["errors"])
        records = [json.loads(path.read_text()) for path in self.recordings_dir.glob("record-*.json")]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["error"], "retry_after_exceeds_local_bound")

    def test_oversized_json_integer_response_is_recorded_as_validation_failure(self):
        body = json.dumps(fixture_body("feed-page-2.json")).encode("utf-8")
        body = body[:-1] + b',"token":"fixture-response-token","oversized":' + b"9" * 5000 + b"}"
        transport = FixtureTransport([response(200, body)])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2, stderr)
        report = json.loads(stdout)
        self.assertIn("feed_response_schema_invalid", report["errors"])
        record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        self.assertIn("feed_response_schema_invalid", record["validation_errors"])
        self.assertNotIn("fixture-response-token", json.dumps(record))
        self.assertNotIn("9" * 5000, json.dumps(record))
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertIsNone(state["pending"])
        self.assertEqual(state["successful_requests"], 1)
        replay_code, replay_stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])
        self.assertEqual(replay_code, 2)
        replay = json.loads(replay_stdout)
        self.assertIn("feed_response_schema_invalid", replay["errors"])

    def test_oversized_json_integer_record_is_removed_without_aborting_cleanup(self):
        directory = self.recordings_dir
        directory.mkdir(mode=0o700)
        malformed = directory / "record-malformed.json"
        malformed.write_text('{"recorded_at":"2026-10-07T12:00:00Z","expires_at":"2026-10-14T12:00:00Z","retention_days":7,"oversized":' + "9" * 5000 + "}")
        malformed.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(directory),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(code, 2)
        self.assertIn("no_unexpired_recordings_available", json.loads(stdout)["errors"])
        self.assertFalse(malformed.exists())

    def test_deeply_nested_response_records_failure_and_settles_request(self):
        body = b'{"code":200,"deep":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"
        transport = FixtureTransport([response(200, body)])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2, stderr)
        report = json.loads(stdout)
        self.assertIn("feed_response_schema_invalid", report["errors"])
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertIsNone(state["pending"])
        record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        self.assertIn("feed_response_schema_invalid", record["validation_errors"])

    def test_deeply_nested_feed_data_string_fails_with_replay_lineage(self):
        nested_data = "[" * 1100 + "0" + "]" * 1100
        transport = FixtureTransport([response(200, {"code": 200, "data": nested_data})])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2, stderr)
        report = json.loads(stdout)
        self.assertIn("feed_response_schema_invalid", report["errors"])
        record_path = next(self.recordings_dir.glob("record-*.json"))
        record = json.loads(record_path.read_text())
        self.assertIn("feed_response_schema_invalid", record["validation_errors"])
        record.pop("validation_errors")
        for malformed_data in (
            nested_data,
            '{"oversized":' + "9" * 5000 + "}",
        ):
            with self.subTest(data_length=len(malformed_data)):
                record["response"]["body"] = {"code": 200, "data": malformed_data}
                record_path.write_text(json.dumps(record))
                replay_code, replay_stdout, replay_stderr = self.run_cli([
                    "replay", "--recordings-dir", str(self.recordings_dir),
                    "--state-dir", str(self.state_dir),
                ])
                self.assertEqual(replay_code, 2, replay_stderr)
                replay = json.loads(replay_stdout)
                self.assertIn("feed_response_schema_invalid", replay["errors"])
                self.assertIn(record["record_id"], replay["record_ids"])
                self.assertGreater(replay["counts"]["validation_failures"], 0)

    def test_surrogate_response_is_sanitized_before_persistence(self):
        page = fixture_body("feed-page-1.json")
        page["data"]["subredditV3"]["elements"]["edges"][0]["node"]["text"] = "\ud800"
        transport = FixtureTransport([response(200, page)])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn('"status": "partial"', stdout)
        persisted = "\n".join(path.read_text(encoding="utf-8") for path in self.recordings_dir.glob("*.json"))
        self.assertNotIn("\ud800", persisted)

    def test_deeply_nested_recording_is_removed_without_aborting_cleanup(self):
        self.recordings_dir.mkdir(mode=0o700)
        malformed = self.recordings_dir / "record-deep.json"
        malformed.write_text(
            '{"recorded_at":"2026-10-07T12:00:00Z",'
            '"expires_at":"2026-10-14T12:00:00Z","retention_days":7,"body":'
            + "[" * 1100 + "0" + "]" * 1100 + "}"
        )
        malformed.chmod(0o600)

        code, stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(code, 2)
        self.assertIn("no_unexpired_recordings_available", json.loads(stdout)["errors"])
        self.assertFalse(malformed.exists())

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

    def test_known_charge_without_call_id_releases_reservation_and_reconciles_once(self):
        self.state_dir.mkdir(mode=0o700)
        state = collect_reddit.default_state()
        state["spent_micro_usd"] = 249000
        state_path = self.state_dir / "state.json"
        state_path.write_text(json.dumps(state))
        state_path.chmod(0o600)
        missing_call_id = collect_reddit.HTTPResponse(
            200,
            {"X-Treg-Cost-Micro": "1000"},
            json.dumps(fixture_body("feed-page-2.json")).encode(),
        )
        transport = FixtureTransport([missing_call_id])

        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2)
        self.assertIn("billing_call_id_missing_reconciliation_required", stdout)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["spent_micro_usd"], 250000)
        self.assertEqual(state["reserved_micro_usd"], 0)
        self.assertEqual(state["successful_requests"], 1)
        self.assertTrue(state["pending"]["charge_known"])

        reconcile = [
            "reconcile", "--state-dir", str(self.state_dir),
            "--call-id", "reconciled-call", "--charge-micro", "1000",
            "--request-outcome", "success", "--evidence", "synthetic ledger reference",
        ]
        reconcile_code, _, stderr = self.run_cli(reconcile)
        self.assertEqual(reconcile_code, 0, stderr)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["spent_micro_usd"], 250000)
        self.assertEqual(state["reserved_micro_usd"], 0)
        self.assertEqual(state["successful_requests"], 1)
        self.assertIsNone(state["pending"])

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

    def test_empty_comment_cursors_are_incomplete_at_all_levels(self):
        bodies = (
            ("empty_cursor", {"success": True, "comments": [], "more": {"has_more": True, "cursor": ""}}),
            ("empty_next_cursor", {"success": True, "comments": [], "more": {"has_more": True, "next_cursor": ""}}),
            ("nested", {
                "success": True,
                "comments": [{
                    "id": "synthetic-comment",
                    "body": "Synthetic comment",
                    "replies": {
                        "items": [],
                        "more": {"has_more": True, "next_cursor": ""},
                    },
                }],
                "more": {"has_more": False, "cursor": None},
            }),
        )
        for label, body in bodies:
            with self.subTest(level=label):
                self.state_dir = self.root / f"state-empty-cursor-{label}"
                self.recordings_dir = self.root / f"recordings-empty-cursor-{label}"
                transport = FixtureTransport([
                    response(200, fixture_body("feed-page-2.json")),
                    response(200, body),
                ])

                code, stdout, stderr = self.run_cli(
                    self.args("--max-feed-pages", "1", "--max-comment-pages", "2", "--max-posts", "1"),
                    transport,
                    FakeCredentials(),
                )

                self.assertEqual(code, 0, stderr)
                report = json.loads(stdout)
                incomplete = "without a usable cursor"
                self.assertIn(incomplete, report["capabilities"]["source_coverage"]["evidence"])
                self.assertEqual(report["counts"]["pending_cursors"], [])
                self.assertEqual(len(transport.requests), 2)
                saved_report = json.loads((self.recordings_dir / report["artifact"]).read_text())
                self.assertIn(incomplete, saved_report["capabilities"]["source_coverage"]["evidence"])
                self.assertEqual(saved_report["counts"]["pending_cursors"], [])

    def test_empty_comment_cursor_uses_nonempty_alternate(self):
        first = {
            "success": True,
            "comments": [],
            "more": {"has_more": True, "cursor": "", "next_cursor": "usable-cursor"},
        }
        final = {"success": True, "comments": [], "more": {"has_more": False, "cursor": None}}
        transport = FixtureTransport([
            response(200, fixture_body("feed-page-2.json")),
            response(200, first),
            response(200, final),
        ])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-comment-pages", "2", "--max-posts", "1"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(transport.requests), 3)
        comment_params = parse_qs(urlsplit(transport.requests[2][0]).query)
        self.assertEqual(comment_params["cursor"], ["usable-cursor"])

    def test_malformed_comment_entries_and_nested_replies_fail_collection_and_replay(self):
        malformed_bodies = (
            {"success": True, "comments": [None, 123, {}]},
            {
                "success": True,
                "comments": [{
                    "id": "synthetic-comment",
                    "body": "Synthetic comment",
                    "replies": {"items": [None], "more": {"has_more": False, "cursor": None}},
                }],
            },
            {
                "success": True,
                "comments": [{
                    "id": "synthetic-comment",
                    "body": "Synthetic comment",
                    "replies": {"items": [], "more": {"has_more": "false", "cursor": None}},
                }],
            },
            {"success": True, "comments": [], "more": {"has_more": False, "cursor": 123}},
            {"success": True, "comments": [], "more": None},
        )
        for index, body in enumerate(malformed_bodies):
            with self.subTest(index=index):
                self.state_dir = self.root / f"state-{index}"
                self.recordings_dir = self.root / f"recordings-{index}"
                transport = FixtureTransport([
                    response(200, fixture_body("feed-page-2.json")),
                    response(200, body),
                ])

                code, stdout, _ = self.run_cli(
                    self.args("--max-feed-pages", "1", "--max-comment-pages", "1", "--max-posts", "1"),
                    transport,
                    FakeCredentials(),
                )

                self.assertEqual(code, 2)
                collection = json.loads(stdout)
                self.assertEqual(collection["capabilities"]["comment_schema"]["status"], "failed")
                self.assertEqual(collection["counts"]["comments_seen"], 0)
                self.assertIn("comment_response_schema_invalid", collection["errors"])
                comment_record = next(
                    json.loads(path.read_text())
                    for path in self.recordings_dir.glob("record-*.json")
                    if json.loads(path.read_text())["endpoint"] == collect_reddit.COMMENTS_ID
                )
                self.assertIn("comment_response_schema_invalid", comment_record["validation_errors"])
                self.assertIsNotNone(comment_record["parent_record_id"])
                self.assertTrue(comment_record["run_id"])
                self.assertTrue(comment_record["trace_id"])

                replay_code, replay_stdout, _ = self.run_cli([
                    "replay", "--recordings-dir", str(self.recordings_dir),
                    "--state-dir", str(self.state_dir),
                ])

                self.assertEqual(replay_code, 0)
                replay = json.loads(replay_stdout)
                self.assertEqual(replay["validation"]["comment_schema"], "failed")
                self.assertIn("comment_response_schema_invalid", replay["errors"])

    def test_valid_nested_comment_replies_remain_supported(self):
        body = {
            "success": True,
            "comments": [{
                "id": "synthetic-comment",
                "body": "Synthetic parent comment",
                "replies": {
                    "items": [{"id": "synthetic-reply", "body": "Synthetic nested reply"}],
                    "more": {"has_more": False, "cursor": None},
                },
            }],
            "more": {"has_more": False, "cursor": None},
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
        self.assertEqual(report["capabilities"]["comment_schema"]["status"], "supported")
        self.assertEqual(report["counts"]["comments_seen"], 1)

        replay_code, replay_stdout, replay_stderr = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(replay_code, 0, replay_stderr)
        replay = json.loads(replay_stdout)
        self.assertEqual(replay["validation"]["comment_schema"], "supported")
        self.assertEqual(replay["counts"]["comments"], 1)

    def test_overlong_billing_header_persists_unknown_hold_and_replay_failure(self):
        oversized_charge = response(
            200,
            fixture_body("feed-page-2.json"),
            cost="9" * 5000,
        )
        transport = FixtureTransport([oversized_charge])

        code, stdout, _ = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2)
        self.assertIn("billing_amount_unknown_reconciliation_required", stdout)
        self.assertEqual(len(transport.requests), 1)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertFalse(state["pending"]["charge_known"])
        self.assertEqual(state["reserved_micro_usd"], collect_reddit.ROUTES["feed"]["max_charge_micro_usd"])
        record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        self.assertIsNone(record["billing"]["charged_micro_usd"])
        self.assertEqual(record["error"], "billing_amount_unknown_reconciliation_required")
        self.assertEqual(record["billing"]["charge_status"], "unknown")
        self.assertNotIn("x-treg-cost-micro", record["response"]["headers"])
        self.assertNotIn("9" * 5000, json.dumps(record))

        replay_code, replay_stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir),
            "--state-dir", str(self.state_dir),
        ])

        self.assertEqual(replay_code, 2)
        replay = json.loads(replay_stdout)
        self.assertEqual(replay["counts"]["billing_unknown_requests"], 1)
        self.assertEqual(replay["capabilities"]["billing_evidence"]["status"], "blocked")

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

    def test_opaque_feed_and_comment_cursors_are_dispatched_without_decoding(self):
        first_feed = fixture_body("feed-page-1.json")
        first_feed["data"]["subredditV3"]["elements"]["pageInfo"]["endCursor"] = "opaque%2Ffeed"
        first_feed["data"] = json.dumps(first_feed["data"])
        first_comments = {
            "success": True,
            "comments": [],
            "more": {"has_more": True, "cursor": "opaque%2Fcomment"},
        }
        final_comments = {
            "success": True,
            "comments": [],
            "more": {"has_more": False, "cursor": None},
        }
        transport = FixtureTransport([
            response(200, first_feed),
            response(200, fixture_body("feed-page-2.json")),
            response(200, first_comments),
            response(200, final_comments),
        ])

        code, _, stderr = self.run_cli(
            self.args("--max-feed-pages", "2", "--max-comment-pages", "2", "--max-posts", "1"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        feed_params = parse_qs(urlsplit(transport.requests[1][0]).query)
        comment_params = parse_qs(urlsplit(transport.requests[3][0]).query)
        self.assertEqual(feed_params["after"], ["opaque%2Ffeed"])
        self.assertEqual(comment_params["cursor"], ["opaque%2Fcomment"])
        records = [json.loads(path.read_text()) for path in self.recordings_dir.glob("record-*.json")]
        feed_record = next(
            record
            for record in records
            if record["endpoint"] == collect_reddit.FEED_ID
            and collect_reddit.feed_data(record["response"]["body"])[1] is True
        )
        feed_request_record = next(
            record
            for record in records
            if record["endpoint"] == collect_reddit.FEED_ID
            and record["request"]["parameters"].get("after") is not None
        )
        comment_record = next(
            record
            for record in records
            if record["endpoint"] == collect_reddit.COMMENTS_ID
            and record["request"]["parameters"].get("cursor") is not None
        )
        self.assertEqual(collect_reddit.feed_data(feed_record["response"]["body"])[2], "opaque/feed")
        self.assertNotIn("opaque%2Ffeed", feed_record["response"]["body"]["data"])
        self.assertEqual(feed_request_record["request"]["parameters"]["after"], "opaque/feed")
        self.assertEqual(comment_record["request"]["parameters"]["cursor"], "opaque/comment")

    def test_unencodable_feed_cursor_fails_before_reserving_next_page(self):
        page = fixture_body("feed-page-1.json")
        page["data"]["subredditV3"]["elements"]["pageInfo"].update(
            {"hasNextPage": True, "endCursor": "bad\ud800cursor"}
        )
        transport = FixtureTransport([response(200, page)])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-posts", "0"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2, stderr)
        report = json.loads(stdout)
        self.assertIn("response_cursor_not_utf8", report["errors"])
        self.assertEqual(len(transport.requests), 1)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["attempts"], 1)
        self.assertIsNone(state["pending"])
        record = json.loads(next(self.recordings_dir.glob("record-*.json")).read_text())
        self.assertIn("response_cursor_not_utf8", record["validation_errors"])
        replay_code, replay_stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])
        self.assertEqual(replay_code, 2)
        self.assertIn("response_cursor_not_utf8", json.loads(replay_stdout)["errors"])

    def test_unencodable_comment_cursor_fails_before_reserving_next_page(self):
        comments = {
            "success": True,
            "comments": [{
                "id": "synthetic-comment",
                "body": "Synthetic comment",
                "replies": {
                    "items": [],
                    "more": {"has_more": True, "cursor": "bad\ud800cursor"},
                },
            }],
            "more": {"has_more": True, "cursor": "opaque%2Fcomment"},
        }
        transport = FixtureTransport([
            response(200, fixture_body("feed-page-1.json")),
            response(200, comments),
        ])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-comment-pages", "1", "--max-posts", "1"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 2, stderr)
        report = json.loads(stdout)
        self.assertIn("response_cursor_not_utf8", report["errors"])
        self.assertEqual(len(transport.requests), 2)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["attempts"], 2)
        self.assertIsNone(state["pending"])
        comment_record = next(
            json.loads(path.read_text())
            for path in self.recordings_dir.glob("record-*.json")
            if json.loads(path.read_text())["endpoint"] == collect_reddit.COMMENTS_ID
        )
        self.assertIn("response_cursor_not_utf8", comment_record["validation_errors"])
        replay_code, replay_stdout, _ = self.run_cli([
            "replay", "--recordings-dir", str(self.recordings_dir), "--state-dir", str(self.state_dir)
        ])
        self.assertEqual(replay_code, 0)
        replay = json.loads(replay_stdout)
        self.assertIn("response_cursor_not_utf8", replay["errors"])
        self.assertGreater(replay["counts"]["validation_failures"], 0)

    def test_pending_opaque_cursors_are_sanitized_in_reports(self):
        first_feed = fixture_body("feed-page-1.json")
        first_feed["data"]["subredditV3"]["elements"]["pageInfo"]["endCursor"] = "opaque%2Ffeed"
        first_comments = {
            "success": True,
            "comments": [],
            "more": {"has_more": True, "cursor": "opaque%2Fcomment"},
        }
        transport = FixtureTransport([response(200, first_feed), response(200, first_comments)])

        code, stdout, stderr = self.run_cli(
            self.args("--max-feed-pages", "1", "--max-comment-pages", "1", "--max-posts", "1"),
            transport,
            FakeCredentials(),
        )

        self.assertEqual(code, 0, stderr)
        for artifact in (stdout, *(path.read_text() for path in self.recordings_dir.glob("*.json"))):
            self.assertNotIn("opaque%2Ffeed", artifact)
            self.assertNotIn("opaque%2Fcomment", artifact)

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
