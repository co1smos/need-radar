import argparse
import copy
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from contextlib import nullcontext
from pathlib import Path

from need_radar.__main__ import digest, json_bytes, redact
from need_radar.observability import ACTIVE_SPAN, JsonlSpanSink, LangfuseBoundary, Tracer


CHANNEL_ID = "1557157266824634469"
MAX_MESSAGE_CHARACTERS = 2000
MAX_ATTEMPTS = 3
DELIVERY_ARTIFACT = Path("discord-delivery.json")


class PermanentDeliveryError(Exception):
    pass


def build_payload(markdown):
    if not isinstance(markdown, str) or not markdown:
        raise ValueError("canonical Markdown must be a non-empty string")
    if redact(markdown) != markdown:
        raise ValueError("canonical Markdown contains secret-shaped data; delivery refused")

    report_sha256 = digest(markdown.encode("utf-8"))
    payload_identity = digest(json_bytes({"channel_id": CHANNEL_ID, "report_sha256": report_sha256}))
    if len(markdown) <= MAX_MESSAGE_CHARACTERS:
        content = markdown
        attachments = []
    else:
        content = f"Need Radar report {payload_identity}"
        attachments = [{
            "filename": f"need-radar-{report_sha256[:16]}.md",
            "sha256": report_sha256,
            "content": markdown,
        }]

    payload = {
        "channel_id": CHANNEL_ID,
        "content": content,
        "attachments": attachments,
        "canonical_markdown": markdown,
        "report_sha256": report_sha256,
        "payload_identity": payload_identity,
    }
    validate_payload(payload)
    return payload


def validate_payload(payload):
    markdown = payload.get("canonical_markdown")
    if not isinstance(markdown, str) or not markdown or redact(markdown) != markdown:
        raise ValueError("payload Markdown is missing or unsafe")
    if payload.get("channel_id") != CHANNEL_ID or not isinstance(payload.get("channel_id"), str):
        raise ValueError("payload channel must be the supplied Discord channel ID string")
    report_sha256 = digest(markdown.encode("utf-8"))
    expected_identity = digest(json_bytes({"channel_id": CHANNEL_ID, "report_sha256": report_sha256}))
    if payload.get("report_sha256") != report_sha256 or payload.get("payload_identity") != expected_identity:
        raise ValueError("payload identity does not match its canonical Markdown")
    content = payload.get("content")
    if not isinstance(content, str) or len(content) > MAX_MESSAGE_CHARACTERS:
        raise ValueError("Discord message content exceeds the 2000-character boundary")
    attachments = payload.get("attachments")
    if not isinstance(attachments, list):
        raise ValueError("payload attachments must be an array")
    if len(markdown) <= MAX_MESSAGE_CHARACTERS:
        if content != markdown or attachments:
            raise ValueError("short Markdown payload is not a deterministic projection")
    elif (
        content != f"Need Radar report {expected_identity}"
        or len(attachments) != 1
        or attachments[0] != {
            "filename": f"need-radar-{report_sha256[:16]}.md",
            "sha256": report_sha256,
            "content": markdown,
        }
    ):
        raise ValueError("oversize Markdown must be preserved as the deterministic attachment")


class FakeDiscordTransport:
    def __init__(self, fail_before_send=0, timeout_after_send=False, attachment_limit_bytes=None, readback_error=None):
        self.fail_before_send = fail_before_send
        self.timeout_after_send = timeout_after_send
        self.attachment_limit_bytes = attachment_limit_bytes
        self.readback_error = readback_error
        self.messages = []
        self.events = []

    def send(self, payload):
        validate_payload(payload)
        self.events.append("send")
        if self.attachment_limit_bytes is not None and any(
            len(attachment["content"].encode("utf-8")) > self.attachment_limit_bytes
            for attachment in payload["attachments"]
        ):
            raise PermanentDeliveryError("synthetic attachment size limit exceeded")
        if self.fail_before_send:
            self.fail_before_send -= 1
            raise TimeoutError("synthetic timeout before fake send")

        message_id = f"fake-message-{len(self.messages) + 1:06d}"
        message = {
            "message_id": message_id,
            "payload_identity": payload["payload_identity"],
            "payload": copy.deepcopy(payload),
        }
        self.messages.append(message)
        if self.timeout_after_send:
            self.timeout_after_send = False
            raise TimeoutError("synthetic timeout after fake send")
        return {"message_id": message_id}

    def find_by_payload_identity(self, payload_identity):
        self.events.append("readback")
        if self.readback_error:
            raise RuntimeError(self.readback_error)
        return next(
            (copy.deepcopy(message) for message in self.messages if message["payload_identity"] == payload_identity),
            None,
        )


def deliver_payload(payload, transport, on_update=None, tracer=None):
    validate_payload(payload)
    result = {
        "payload_identity": payload["payload_identity"],
        "status": "pending",
        "attempts": [],
        "message_id": None,
        "max_attempts": MAX_ATTEMPTS,
        "exactly_once_claimed": False,
    }

    def checkpoint():
        if on_update:
            on_update(copy.deepcopy(result))

    checkpoint()
    for attempt_number in range(1, MAX_ATTEMPTS + 1):
        attempt = {
            "number": attempt_number,
            "payload_identity": payload["payload_identity"],
            "status": "started",
            "error": None,
            "reconciliation": None,
            "message_id": None,
        }
        result["attempts"].append(attempt)
        result["status"] = "sending"
        checkpoint()

        span_context = tracer.span(
            "discord_delivery_attempt",
            inputs={"payload_identity": payload["payload_identity"], "attempt": attempt_number},
            attributes={"channel_id": CHANNEL_ID, "transport": "fake"},
        ) if tracer else nullcontext(None)
        with span_context as span:
            try:
                response = transport.send(payload)
                message_id = response.get("message_id") if isinstance(response, dict) else None
                if not isinstance(message_id, str) or not message_id:
                    raise TimeoutError("transport returned no message identity")
                attempt.update({"status": "sent", "message_id": message_id})
                result.update({"status": "delivered", "message_id": message_id})
                if span:
                    span["attributes"]["result_status"] = result["status"]
                    span["output"] = {"message_id": message_id, "payload_identity": payload["payload_identity"]}
                checkpoint()
                break
            except PermanentDeliveryError as error:
                attempt.update({"status": "rejected", "error": redact(str(error))})
                result["status"] = "failed"
                if span:
                    span["attributes"]["result_status"] = result["status"]
                    span["output"] = {"error": attempt["error"], "payload_identity": payload["payload_identity"]}
                checkpoint()
                break
            except Exception as error:
                attempt.update({"status": "ambiguous", "error": redact(str(error))})
                try:
                    message = transport.find_by_payload_identity(payload["payload_identity"])
                except Exception as readback_error:
                    attempt["reconciliation"] = {
                        "status": "unavailable",
                        "error": redact(str(readback_error)),
                    }
                    result["status"] = "ambiguous"
                    if span:
                        span["attributes"]["result_status"] = result["status"]
                        span["output"] = {"reconciliation": attempt["reconciliation"]}
                    checkpoint()
                    break

                if message is not None:
                    message_id = message.get("message_id")
                    if message.get("payload_identity") == payload["payload_identity"] and isinstance(message_id, str) and message_id:
                        attempt.update({
                            "status": "reconciled",
                            "message_id": message_id,
                            "reconciliation": {"status": "found"},
                        })
                        result.update({"status": "delivered_reconciled", "message_id": message_id})
                        if span:
                            span["attributes"]["result_status"] = result["status"]
                            span["output"] = {"message_id": message_id, "reconciliation": "found"}
                        checkpoint()
                        break
                    attempt["reconciliation"] = {"status": "invalid_match"}
                    result["status"] = "ambiguous"
                    if span:
                        span["attributes"]["result_status"] = result["status"]
                        span["output"] = {"reconciliation": attempt["reconciliation"]}
                    checkpoint()
                    break

                attempt["reconciliation"] = {"status": "not_found"}
                result["status"] = "retrying" if attempt_number < MAX_ATTEMPTS else "ambiguous"
                if span:
                    span["attributes"]["result_status"] = result["status"]
                    span["output"] = {"reconciliation": attempt["reconciliation"]}
                checkpoint()
    return result


def _load_frozen_report(run_directory):
    database_path = run_directory / "lineage.sqlite3"
    if not database_path.is_file():
        raise ValueError("run directory has no lineage database")
    with sqlite3.connect(database_path) as database:
        row = database.execute(
            "SELECT sequence, run_id, status, artifact_path, output_sha256, trace_id, span_id, artifact_id "
            "FROM stages WHERE stage = 'report' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        existing_delivery = database.execute(
            "SELECT 1 FROM stages WHERE stage = 'discord_delivery' LIMIT 1"
        ).fetchone()
    if row is None or row[2] not in {"success", "no_findings"} or row[3] != "report.md":
        raise ValueError("run directory has no successful canonical report stage")
    if existing_delivery:
        raise ValueError("a delivery record already exists for this run")
    report_path = run_directory / "report.md"
    if report_path.is_symlink() or not report_path.is_file() or report_path.resolve().parent != run_directory.resolve():
        raise ValueError("canonical report artifact is missing or unsafe")
    report_bytes = report_path.read_bytes()
    if digest(report_bytes) != row[4]:
        raise ValueError("canonical report no longer matches its frozen lineage hash")
    try:
        markdown = report_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("canonical report is not valid UTF-8") from error
    if redact(markdown) != markdown:
        raise ValueError("canonical report contains secret-shaped data; delivery refused")
    return {
        "sequence": row[0],
        "run_id": row[1],
        "status": row[2],
        "trace_id": row[5],
        "span_id": row[6],
        "artifact_id": row[7],
        "sha256": row[4],
        "markdown": markdown,
    }


def _persist_delivery(run_directory, database, delivery, source, artifact_id, span_id):
    value = redact(copy.deepcopy(delivery))
    value["lineage"] = {
        "run_id": source["run_id"],
        "trace_id": source["trace_id"],
        "stage": "discord_delivery",
        "span_id": span_id,
        "artifact_id": artifact_id,
        "input_stage": "report",
        "input_artifact_id": source["artifact_id"],
        "input_sha256": source["sha256"],
    }
    content = json_bytes(value)
    artifact_path = run_directory / DELIVERY_ARTIFACT
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=run_directory, prefix=".discord-delivery-", delete=False) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, artifact_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()

    details = redact({
        "payload_identity": value["payload_identity"],
        "attempt_count": len(value["attempts"]),
        "message_id": value["message_id"],
        "status": value["status"],
    })
    database.execute(
        "INSERT OR REPLACE INTO stages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source["sequence"] + 1,
            source["run_id"],
            "discord_delivery",
            value["status"],
            "report",
            source["sha256"],
            DELIVERY_ARTIFACT.as_posix(),
            digest(content),
            json.dumps(details, ensure_ascii=False, sort_keys=True),
            source["trace_id"],
            span_id,
            artifact_id,
            source["artifact_id"],
            None,
        ),
    )
    database.commit()


def run_delivery(run_directory, transport=None):
    run_directory = Path(run_directory)
    source = _load_frozen_report(run_directory)
    payload = build_payload(source["markdown"])
    if transport is None:
        transport = FakeDiscordTransport(timeout_after_send=True)
    if type(transport) is not FakeDiscordTransport:
        raise ValueError("offline delivery accepts only the injected fake transport")
    database = sqlite3.connect(run_directory / "lineage.sqlite3")
    artifact_id = uuid.uuid4().hex
    tracer = Tracer(
        source["run_id"],
        source["trace_id"],
        LangfuseBoundary(run_directory / "trace.jsonl", redact),
        JsonlSpanSink(run_directory / "logs.jsonl"),
        redact,
    )
    delivery = {
        "schema_version": 1,
        "status": "pending",
        "destination": {"platform": "discord", "channel_id": CHANNEL_ID},
        "source": {
            "artifact_path": "report.md",
            "artifact_id": source["artifact_id"],
            "sha256": source["sha256"],
        },
        "configuration": {
            "transport": "injected_fake",
            "fake_transport": {
                "planned_pre_send_timeouts": transport.fail_before_send,
                "timeout_after_send": transport.timeout_after_send,
                "attachment_limit_bytes": transport.attachment_limit_bytes,
                "readback_error": redact(transport.readback_error),
            },
            "message_character_limit": MAX_MESSAGE_CHARACTERS,
            "max_attempts": MAX_ATTEMPTS,
            "schedule_activated": False,
        },
        "hermes_interface": {
            "installation": "confirmed_by_read_only_help",
            "observed_command": "hermes send",
            "target_option": "--to discord:<channel_id>",
            "file_option": "--file",
            "attachment_syntax": "MEDIA:<path>",
            "delivery_compatibility": "unverified",
            "send_invoked": False,
        },
        "payload_identity": payload["payload_identity"],
        "payload": payload,
        "validation": {
            "status": "passed",
            "canonical_report_sha256_matches_lineage": payload["report_sha256"] == source["sha256"],
            "message_character_count": len(payload["content"]),
            "message_character_limit": MAX_MESSAGE_CHARACTERS,
            "attachment_count": len(payload["attachments"]),
            "attachment_sizes_bytes": [
                len(attachment["content"].encode("utf-8")) for attachment in payload["attachments"]
            ],
            "fake_transport_attachment_limit_bytes": transport.attachment_limit_bytes,
            "source_text_treated_as_untrusted_data": True,
        },
        "attempts": [],
        "message_id": None,
        "exactly_once_claimed": False,
        "verification": {
            "mode": "synthetic_fake_transport_only",
            "real_discord_send": "not_performed",
            "real_discord_readback": "not_performed",
            "external_links_verified": False,
        },
        "coverage": {
            "redaction": {"applied_before_persistence_and_export": True, "coverage": "best_effort", "complete": False},
            "actual_discord_attachment_limit": "not established by this offline check",
        },
    }

    def save(state):
        delivery.update(state)
        _persist_delivery(
            run_directory,
            database,
            delivery,
            source,
            artifact_id,
            delivery_span["span_id"],
        )

    token = ACTIVE_SPAN.set((source["trace_id"], source["span_id"]))
    try:
        with tracer.span(
            "discord_delivery",
            inputs={"report": delivery["source"], "payload": payload, "configuration": delivery["configuration"]},
            attributes={"channel_id": CHANNEL_ID, "transport": "injected_fake"},
        ) as delivery_span:
            result = deliver_payload(payload, transport, on_update=save, tracer=tracer)
            delivery.update(result)
            delivery_span["attributes"]["result_status"] = result["status"]
            delivery_span["output"] = {
                "status": result["status"],
                "payload_identity": result["payload_identity"],
                "attempt_count": len(result["attempts"]),
                "message_id": result["message_id"],
            }
    finally:
        ACTIVE_SPAN.reset(token)

    delivery["observability"] = {
        "trace_id": source["trace_id"],
        "span_count": tracer.span_count,
        "local_exported_span_count": tracer.exported_span_count,
        "local_export_failures": redact(tracer.export_failures),
        "remote_export": {"enabled": False, "status": "unverified", "verification": "not_attempted"},
    }
    save({})
    database.close()
    return delivery


def main():
    parser = argparse.ArgumentParser(description="Run the offline fake Discord delivery boundary for a frozen report.")
    parser.add_argument("--run-dir", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        result = run_delivery(arguments.run_dir)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"status={result['status']} attempts={len(result['attempts'])} "
        f"message_id={result['message_id']} real_discord_send=not_verified real_discord_readback=not_verified"
    )
    return 0 if result["status"] in {"delivered", "delivered_reconciled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
