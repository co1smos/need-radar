import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import network_guard
from need_radar.discord_delivery import (
    CHANNEL_ID,
    MAX_ATTEMPTS,
    MAX_MESSAGE_CHARACTERS,
    FakeDiscordTransport,
    build_payload,
    deliver_payload,
    run_delivery,
)


ROOT = Path(__file__).resolve().parents[1]
HERMES_TMPDIR = Path.home() / ".hermes" / "cache" / "scratch"


class DiscordDeliveryTests(unittest.TestCase):
    def run_subprocess(self, command, **options):
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(HERMES_TMPDIR),
        }
        return subprocess.run(command, env=environment, **options)

    def test_payload_identity_and_destination_are_deterministic(self):
        first = build_payload("# Frozen report\n")
        second = build_payload("# Frozen report\n")
        changed = build_payload("# Different report\n")

        self.assertEqual(first, second)
        self.assertNotEqual(first["payload_identity"], changed["payload_identity"])
        self.assertEqual(first["channel_id"], CHANNEL_ID)
        self.assertIsInstance(first["channel_id"], str)
        self.assertEqual(first["content"], "# Frozen report\n")

    def test_message_limit_switches_to_exact_markdown_attachment(self):
        at_limit = "x" * MAX_MESSAGE_CHARACTERS
        payload_at_limit = build_payload(at_limit)
        self.assertEqual(len(payload_at_limit["content"]), MAX_MESSAGE_CHARACTERS)
        self.assertEqual(payload_at_limit["attachments"], [])
        self.assertEqual(payload_at_limit["content"], at_limit)

        oversize = at_limit + "x"
        payload = build_payload(oversize)
        self.assertLessEqual(len(payload["content"]), MAX_MESSAGE_CHARACTERS)
        self.assertEqual(len(payload["attachments"]), 1)
        self.assertEqual(payload["attachments"][0]["content"], oversize)
        self.assertEqual(
            payload["attachments"][0]["sha256"],
            hashlib.sha256(oversize.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(str(HERMES_TMPDIR), payload["content"])

    def test_timeout_is_reconciled_before_any_resend(self):
        payload = build_payload("# Frozen report\n")
        transport = FakeDiscordTransport(timeout_after_send=True)
        result = deliver_payload(payload, transport)

        self.assertEqual(result["status"], "delivered_reconciled")
        self.assertEqual(result["message_id"], transport.messages[0]["message_id"])
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(transport.events, ["send", "readback"])
        self.assertEqual(len(transport.messages), 1)
        self.assertFalse(result["exactly_once_claimed"])

    def test_bounded_retry_reuses_same_payload_identity(self):
        payload = build_payload("# Frozen report\n")
        transport = FakeDiscordTransport(fail_before_send=2)
        result = deliver_payload(payload, transport)

        self.assertEqual(result["status"], "delivered")
        self.assertEqual(len(result["attempts"]), 3)
        self.assertEqual(transport.events, ["send", "readback", "send", "readback", "send"])
        self.assertEqual(
            {attempt["payload_identity"] for attempt in result["attempts"]},
            {payload["payload_identity"]},
        )

    def test_retry_limit_is_bounded_and_readback_failure_stays_ambiguous(self):
        payload = build_payload("# Frozen report\n")
        exhausted_transport = FakeDiscordTransport(fail_before_send=MAX_ATTEMPTS)
        exhausted = deliver_payload(payload, exhausted_transport)
        self.assertEqual(exhausted["status"], "ambiguous")
        self.assertEqual(len(exhausted["attempts"]), MAX_ATTEMPTS)
        self.assertEqual(len(exhausted_transport.messages), 0)

        ambiguous_transport = FakeDiscordTransport(timeout_after_send=True, readback_error="synthetic read-back outage")
        ambiguous = deliver_payload(payload, ambiguous_transport)
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertEqual(len(ambiguous["attempts"]), 1)
        self.assertEqual(ambiguous_transport.events, ["send", "readback"])

    def test_fake_attachment_limit_is_a_persistable_permanent_failure(self):
        markdown = "# Frozen report\n" + ("x" * MAX_MESSAGE_CHARACTERS)
        payload = build_payload(markdown)
        transport = FakeDiscordTransport(attachment_limit_bytes=16)
        result = deliver_payload(payload, transport)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["status"], "rejected")
        self.assertEqual(transport.events, ["send"])

    def test_secret_shaped_markdown_is_refused(self):
        with self.assertRaisesRegex(ValueError, "secret-shaped"):
            build_payload("api_key=sk-SYNTHETICSECRET1234567890")

    def test_end_to_end_fake_delivery_persists_trace_and_report_lineage(self):
        with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR) as temporary_directory:
            run_directory = Path(temporary_directory) / "run"
            serve = self.run_subprocess(
                [sys.executable, "-m", "need_radar", "--output", str(run_directory)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(serve.returncode, 0, serve.stderr)
            self.assertIn("status=success", serve.stdout)

            delivered = self.run_subprocess(
                [sys.executable, "-m", "need_radar.discord_delivery", "--run-dir", str(run_directory)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(delivered.returncode, 0, delivered.stderr)
            self.assertIn("status=delivered_reconciled attempts=1", delivered.stdout)
            self.assertIn("real_discord_send=not_verified", delivered.stdout)
            self.assertIn("real_discord_readback=not_verified", delivered.stdout)

            artifact_path = run_directory / "discord-delivery.json"
            delivery = json.loads(artifact_path.read_text())
            self.assertEqual(delivery["destination"]["channel_id"], CHANNEL_ID)
            self.assertIsInstance(delivery["destination"]["channel_id"], str)
            self.assertEqual(delivery["status"], "delivered_reconciled")
            self.assertEqual(delivery["hermes_interface"]["observed_command"], "hermes send")
            self.assertEqual(delivery["hermes_interface"]["delivery_compatibility"], "unverified")
            self.assertFalse(delivery["hermes_interface"]["send_invoked"])
            self.assertEqual(delivery["attempts"][0]["reconciliation"]["status"], "found")
            self.assertEqual(delivery["attempts"][0]["payload_identity"], delivery["payload_identity"])
            self.assertEqual(delivery["validation"]["status"], "passed")
            self.assertTrue(delivery["validation"]["canonical_report_sha256_matches_lineage"])
            self.assertLessEqual(
                delivery["validation"]["message_character_count"],
                delivery["validation"]["message_character_limit"],
            )
            self.assertFalse(delivery["exactly_once_claimed"])
            self.assertEqual(delivery["verification"]["real_discord_send"], "not_performed")
            self.assertEqual(delivery["verification"]["real_discord_readback"], "not_performed")
            self.assertFalse(delivery["configuration"]["schedule_activated"])
            self.assertNotIn(str(run_directory), delivery["payload"]["content"])

            with sqlite3.connect(run_directory / "lineage.sqlite3") as database:
                row = database.execute(
                    "SELECT status, input_stage, input_sha256, artifact_path, output_sha256, "
                    "trace_id, span_id, artifact_id, input_artifact_id FROM stages WHERE stage='discord_delivery'"
                ).fetchone()
                report = database.execute(
                    "SELECT output_sha256, artifact_id, span_id FROM stages WHERE stage='report'"
                ).fetchone()
            self.assertEqual(row[:2], ("delivered_reconciled", "report"))
            self.assertEqual(row[2], report[0])
            self.assertEqual(row[3], "discord-delivery.json")
            self.assertEqual(row[4], hashlib.sha256(artifact_path.read_bytes()).hexdigest())
            self.assertEqual(row[5], delivery["lineage"]["trace_id"])
            self.assertEqual(row[7], delivery["lineage"]["artifact_id"])
            self.assertEqual(row[8], report[1])

            trace_events = [json.loads(line) for line in (run_directory / "trace.jsonl").read_text().splitlines()]
            report_span = next(event for event in trace_events if event["name"] == "report")
            delivery_span = next(event for event in trace_events if event["name"] == "discord_delivery")
            attempt_span = next(event for event in trace_events if event["name"] == "discord_delivery_attempt")
            self.assertEqual(delivery_span["parent_span_id"], report_span["id"])
            self.assertEqual(attempt_span["parent_span_id"], delivery_span["id"])
            self.assertEqual(row[6], delivery_span["id"])
            self.assertFalse(delivery["observability"]["remote_export"]["enabled"])

            persisted = b"".join(path.read_bytes() for path in run_directory.iterdir() if path.is_file())
            self.assertNotIn(b"SYNTHETICONLY1234567890", persisted)

    def test_oversize_attachment_rejection_persists_failure_evidence(self):
        with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR) as temporary_directory:
            temporary_path = Path(temporary_directory)
            fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
            fixture["model"]["response"][0]["friction"] = "synthetic long report " + ("x" * 2200)
            fixture_path = temporary_path / "fixture.json"
            run_directory = temporary_path / "run"
            fixture_path.write_text(json.dumps(fixture))
            serve = self.run_subprocess(
                [
                    sys.executable,
                    "-m",
                    "need_radar",
                    "--fixture",
                    str(fixture_path),
                    "--output",
                    str(run_directory),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(serve.returncode, 0, serve.stderr)

            result = run_delivery(run_directory, FakeDiscordTransport(attachment_limit_bytes=32))
            artifact = json.loads((run_directory / "discord-delivery.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(artifact["validation"]["attachment_count"], 1)
            self.assertEqual(artifact["validation"]["fake_transport_attachment_limit_bytes"], 32)
            self.assertEqual(artifact["attempts"][0]["status"], "rejected")
            self.assertIn("synthetic attachment size limit exceeded", artifact["attempts"][0]["error"])
            self.assertEqual(artifact["coverage"]["actual_discord_attachment_limit"], "not established by this offline check")
            self.assertEqual(artifact["lineage"]["input_stage"], "report")

    def test_current_process_and_subprocess_deny_network_and_protected_paths(self):
        network_guard.install()
        with self.assertRaises(PermissionError):
            socket.socket()
        for protected_path in (
            "/home/ubuntu/projects/need-radar/credentials.env",
            "/home/ubuntu/.local/state/need-radar",
        ):
            with self.subTest(path=protected_path), self.assertRaises(PermissionError):
                open(protected_path, "rb")

        script = """
import os
import socket

if 'NEED_RADAR_TEST_API_TOKEN' in os.environ:
    raise SystemExit('credential environment was inherited')
try:
    socket.socket()
except PermissionError:
    print('network=denied')
else:
    raise SystemExit('network=allowed')
for name, path in (
    ('credentials', '/home/ubuntu/projects/need-radar/credentials.env'),
    ('live_state', '/home/ubuntu/.local/state/need-radar'),
):
    try:
        open(path, 'rb')
    except PermissionError:
        print(name + '=denied')
    else:
        raise SystemExit(name + '=allowed')
"""
        os.environ["NEED_RADAR_TEST_API_TOKEN"] = "synthetic-only"
        try:
            result = self.run_subprocess(
                [sys.executable, "-c", script],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        finally:
            del os.environ["NEED_RADAR_TEST_API_TOKEN"]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("network=denied", result.stdout)
        self.assertIn("credentials=denied", result.stdout)
        self.assertIn("live_state=denied", result.stdout)


if __name__ == "__main__":
    unittest.main()
