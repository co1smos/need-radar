"""Disabled-by-default, bounded Reddit + X product operation."""

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import time
import urllib.parse
import uuid

from need_radar import __main__ as serve
from need_radar import deepseek_verify, shadow
from need_radar.discord_delivery import MAX_ATTEMPTS as DISCORD_MAX_ATTEMPTS
from need_radar.discord_delivery import CHANNEL_ID, FakeDiscordTransport, run_delivery, validate_payload
from need_radar.observability import JsonlSpanSink, LangfuseBoundary, Tracer
from need_radar.source_adapter import prepare_source_results


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
AGGREGATE_LIMIT_MICRO_USD = 250_000
REDDIT_ROUTE = "tikhub.x.reddit-app-fetch-subreddit-feed"
X_ROUTE = "x.x.search-posts-recent"
X_QUERY = '("coding agent" OR "MCP server")'
REDDIT_RESERVATION_MICRO_USD = 1_000
X_RESERVATION_MICRO_USD = 50_000
MODEL_INPUT_TOKENS = 16_384
MODEL_OUTPUT_TOKENS = 1_024
MODEL_RESERVATION_MICRO_USD = 6_144
MAX_MODEL_CALLS = 23
MAX_TOTAL_REQUESTS = 25
MAX_MODEL_TOKENS_PER_REQUEST = MODEL_INPUT_TOKENS + MODEL_OUTPUT_TOKENS
MAX_AGGREGATE_MODEL_TOKENS = MAX_MODEL_CALLS * MAX_MODEL_TOKENS_PER_REQUEST
MAX_MODEL_REQUEST_BYTES = MODEL_INPUT_TOKENS
PRODUCT_BUDGET_DB = Path.home() / ".local" / "state" / "need-radar" / "product-cycle" / "budget.sqlite3"
REMOVALS_DIRECTORY = ".product-removals"
MODEL_SHARED_SETTINGS = {
    "provider": "deepseek-direct",
    "model": "deepseek-flash",
    "model_settings": {
        "temperature": 0,
        "max_input_tokens": MODEL_INPUT_TOKENS,
        "max_output_tokens": MODEL_OUTPUT_TOKENS,
        "thinking": "disabled",
        "tools": "disabled",
        "browsing": "disabled",
    },
    "schema": {
        "version": 1,
        "candidate_fields": ["title", "friction", "evidence"],
        "evidence_fields": ["item_id", "excerpt"],
    },
    "retry_policy": {"max_attempts": 1},
    "evidence_policy": {"scope": "frozen_context", "citation": "exact_excerpt"},
    "request_limit": 1,
    "max_tokens": MODEL_INPUT_TOKENS + MODEL_OUTPUT_TOKENS,
    "candidate_limit": 10,
}
REQUIRED_GATES = (
    "one_shot_execution",
    "reddit_route_schema",
    "x_route_schema",
    "provider_access",
    "source_billing_caps",
    "existing_source_holds",
    "model_access",
    "discord_permission_and_readback",
    "retention_and_cleanup",
)


class ProductError(Exception):
    def __init__(self, code, stage="activation_preflight"):
        super().__init__(code)
        self.code = code
        self.stage = stage


class ProductBudget:
    """Durable aggregate charge ledger; an unsettled reservation survives restart."""

    def __init__(self, path, limit=AGGREGATE_LIMIT_MICRO_USD, token_limit=MAX_AGGREGATE_MODEL_TOKENS):
        self.path = Path(path).expanduser().absolute()
        if self.path.resolve().is_relative_to(ROOT):
            raise ProductError("product_budget_must_be_outside_repository")
        if type(limit) is not int or limit != AGGREGATE_LIMIT_MICRO_USD or token_limit != MAX_AGGREGATE_MODEL_TOKENS:
            raise ProductError("aggregate_budget_must_be_usd_0_25")
        if self.path.is_symlink():
            raise ProductError("product_budget_path_cannot_be_symlinked")
        _private_dir(self.path.parent)
        self.limit = limit
        self.token_limit = token_limit
        self.policy_hash = _sha({"limit": limit, "max_requests": MAX_TOTAL_REQUESTS, "token_limit": token_limit})
        self._init()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        os.chmod(self.path, 0o600)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _init(self):
        with self._database() as database:
            database.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            database.execute("""CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                status TEXT NOT NULL, reservation_micro_usd INTEGER NOT NULL,
                charged_micro_usd INTEGER, request_sha256 TEXT NOT NULL,
                token_reservation INTEGER NOT NULL,
                tokens INTEGER, error_code TEXT, created_at TEXT NOT NULL,
                provider_call_id TEXT, provider_request_id TEXT, accounting_evidence TEXT)""")
            columns = {row[1] for row in database.execute("PRAGMA table_info(attempts)")}
            for name in ("provider_call_id", "provider_request_id", "accounting_evidence"):
                if name not in columns:
                    database.execute(f"ALTER TABLE attempts ADD COLUMN {name} TEXT")
            database.execute("CREATE TABLE IF NOT EXISTS cycles (run_id TEXT PRIMARY KEY, claimed_at TEXT NOT NULL)")
            current = database.execute("SELECT value FROM metadata WHERE key='policy_sha256'").fetchone()
            if current is None:
                database.execute("INSERT INTO metadata VALUES ('policy_sha256', ?)", (self.policy_hash,))
            elif current[0] != self.policy_hash:
                raise ProductError("aggregate_budget_policy_changed")
        os.chmod(self.path, 0o600)

    def claim_one_shot(self, run_id):
        with self._database() as database:
            database.execute("BEGIN IMMEDIATE")
            count = database.execute("SELECT COUNT(*) FROM cycles").fetchone()[0]
            if count:
                database.rollback()
                raise ProductError("one_shot_product_run_already_claimed")
            database.execute("INSERT INTO cycles VALUES (?, ?)", (run_id, _now()))
            database.commit()

    def reserve(self, run_id, stage, request_sha256, amount_micro_usd, token_reservation=0):
        if (
            not isinstance(run_id, str) or not run_id
            or not isinstance(stage, str) or not stage
            or not isinstance(request_sha256, str) or len(request_sha256) != 64
            or type(amount_micro_usd) is not int or amount_micro_usd <= 0
            or type(token_reservation) is not int or token_reservation < 0
        ):
            raise ProductError("request_reservation_invalid", stage)
        attempt_id = uuid.uuid4().hex
        with self._database() as database:
            database.execute("BEGIN IMMEDIATE")
            rows = database.execute(
                "SELECT status, reservation_micro_usd, charged_micro_usd FROM attempts"
            ).fetchall()
            if any(status in {"reserved", "unknown", "over_limit", "failed"} for status, _, _ in rows):
                database.rollback()
                raise ProductError("unreconciled_charge_holds_further_spending", stage)
            if len(rows) >= MAX_TOTAL_REQUESTS:
                database.rollback()
                raise ProductError("aggregate_request_ceiling_reached", stage)
            committed = sum(charged if charged is not None else reservation for _, reservation, charged in rows)
            if committed + amount_micro_usd > self.limit:
                database.rollback()
                raise ProductError("aggregate_spend_ceiling_reached", stage)
            token_rows = database.execute("SELECT token_reservation, tokens FROM attempts").fetchall()
            reserved_tokens = sum(tokens if tokens is not None else reservation for reservation, tokens in token_rows)
            if reserved_tokens + token_reservation > self.token_limit:
                database.rollback()
                raise ProductError("aggregate_token_ceiling_reached", stage)
            database.execute(
                """INSERT INTO attempts (
                    attempt_id, run_id, stage, status, reservation_micro_usd,
                    charged_micro_usd, request_sha256, token_reservation, tokens,
                    error_code, created_at, provider_call_id, provider_request_id,
                    accounting_evidence
                ) VALUES (?, ?, ?, 'reserved', ?, NULL, ?, ?, NULL, NULL, ?, NULL, NULL, NULL)""",
                (attempt_id, run_id, stage, amount_micro_usd, request_sha256, token_reservation, _now()),
            )
            database.commit()
        return attempt_id

    def settle(
        self, attempt_id, charge_micro_usd=None, tokens=None, error_code=None, *,
        stop_on_failure=False, provider_call_id=None, provider_request_id=None,
        accounting_evidence=None,
    ):
        if charge_micro_usd is not None and (type(charge_micro_usd) is not int or charge_micro_usd < 0):
            raise ProductError("reported_charge_invalid")
        if tokens is not None and (type(tokens) is not int or tokens < 0):
            raise ProductError("reported_token_usage_invalid")
        for value in (provider_call_id, provider_request_id):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
                raise ProductError("provider_accounting_identity_invalid")
        evidence = json.dumps(serve.redact(accounting_evidence), ensure_ascii=False, sort_keys=True) if accounting_evidence is not None else None
        with self._database() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT reservation_micro_usd FROM attempts WHERE attempt_id=? AND status='reserved'",
                (attempt_id,),
            ).fetchone()
            if row is None:
                database.rollback()
                raise ProductError("budget_reservation_missing")
            if charge_micro_usd is None:
                database.execute(
                    """UPDATE attempts SET status='unknown', error_code=?, provider_call_id=?,
                    provider_request_id=?, accounting_evidence=? WHERE attempt_id=?""",
                    (error_code or "charge_or_outcome_unknown", provider_call_id, provider_request_id, evidence, attempt_id),
                )
            else:
                status = "over_limit" if charge_micro_usd > row[0] else "failed" if stop_on_failure and error_code else "settled"
                database.execute(
                    """UPDATE attempts SET status=?, charged_micro_usd=?, tokens=?, error_code=?,
                    provider_call_id=?, provider_request_id=?, accounting_evidence=? WHERE attempt_id=?""",
                    (status, charge_micro_usd, tokens, error_code, provider_call_id, provider_request_id, evidence, attempt_id),
                )
            database.commit()

    def summary(self):
        with self._database() as database:
            rows = database.execute(
                "SELECT status, reservation_micro_usd, charged_micro_usd FROM attempts"
            ).fetchall()
            token_rows = database.execute(
                "SELECT token_reservation, tokens FROM attempts"
            ).fetchall()
        charged = sum(amount for status, _reserve, amount in rows if amount is not None)
        reserved = sum(reserve for status, reserve, amount in rows if amount is None)
        return {
            "limit_micro_usd": self.limit,
            "model_unit_limit": self.token_limit,
            "tokens_used_or_reserved": sum(tokens if tokens is not None else reservation for reservation, tokens in token_rows),
            "charged_or_usage_estimated_micro_usd": charged,
            "reserved_or_unknown_micro_usd": reserved,
            "requests_reserved_or_dispatched": len(rows),
            "account_billed_usd": None,
            "account_billing_evidence": "not independently queried",
            "unresolved_attempts": sum(status in {"reserved", "unknown", "over_limit", "failed"} for status, _, _ in rows),
        }

    def model_calls_for(self, arm):
        with self._database() as database:
            rows = database.execute("SELECT stage FROM attempts").fetchall()
        return sum(stage == arm or stage.startswith(arm + "_") for (stage,) in rows)

    def attempts(self):
        with self._database() as database:
            rows = database.execute(
                "SELECT attempt_id, run_id, stage, status, reservation_micro_usd, charged_micro_usd, request_sha256, token_reservation, tokens, error_code, created_at, provider_call_id, provider_request_id, accounting_evidence FROM attempts ORDER BY created_at"
            ).fetchall()
        fields = (
            "attempt_id", "run_id", "stage", "status", "reservation_micro_usd",
            "charged_micro_usd", "request_sha256", "model_units_reserved", "tokens",
            "error_code", "created_at", "provider_call_id", "provider_request_id", "accounting_evidence",
        )
        attempts = [dict(zip(fields, row)) for row in rows]
        for attempt in attempts:
            if attempt["accounting_evidence"] is not None:
                attempt["accounting_evidence"] = json.loads(attempt["accounting_evidence"])
        return attempts


def _now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha(value):
    encoded = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _private_dir(path):
    path = Path(path).absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ProductError("private_paths_cannot_contain_symlinks")
        except FileNotFoundError:
            current.mkdir(mode=0o700)
        except OSError:
            raise ProductError("private_directory_unavailable") from None
    if not path.is_dir():
        raise ProductError("private_directory_required")
    if path.lstat().st_uid != os.geteuid():
        raise ProductError("private_directory_must_be_user_owned")
    os.chmod(path, 0o700)
    return path


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(serve.redact(value), stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _replace_json(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(serve.redact(value), stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _lineage_ref(directory, relative, stage=None):
    path = Path(directory) / relative
    if path.is_symlink() or not path.is_file():
        return None
    value = None
    if path.suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            value = None
    lineage = value.get("lineage") if isinstance(value, dict) else None
    if not isinstance(lineage, dict):
        database_path = path.parent / "lineage.sqlite3"
        if database_path.is_file():
            try:
                with sqlite3.connect(database_path) as database:
                    row = database.execute(
                        "SELECT stage, trace_id, span_id, artifact_id, input_artifact_id "
                        "FROM stages WHERE artifact_path=? ORDER BY sequence DESC LIMIT 1",
                        (path.name,),
                    ).fetchone()
                if row:
                    lineage = dict(zip(("stage", "trace_id", "span_id", "artifact_id", "input_artifact_id"), row))
            except sqlite3.Error:
                lineage = None
    return {
        "path": str(relative),
        "sha256": _sha(path.read_bytes()),
        **({key: lineage.get(key) for key in ("run_id", "trace_id", "stage", "span_id", "artifact_id", "input_stage", "input_artifact_id", "input_sha256") if lineage.get(key) is not None} if isinstance(lineage, dict) else {}),
        **({"expected_stage": stage} if stage else {}),
    }


def _artifact_status(path, default="not_run"):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default
    return value.get("status", default) if isinstance(value, dict) else default


def validate_activation(activation):
    if not isinstance(activation, dict) or activation.get("version") != 1:
        raise ProductError("activation_manifest_unsupported")
    if activation.get("enabled") is not True:
        raise ProductError("product_dispatch_disabled")
    gates = activation.get("gates")
    missing = [
        gate for gate in REQUIRED_GATES
        if not isinstance(gates, dict)
        or not isinstance(gates.get(gate), dict)
        or gates[gate].get("verified") is not True
        or not isinstance(gates[gate].get("evidence"), str)
        or not gates[gate]["evidence"].strip()
    ]
    if missing:
        raise ProductError("execution_gates_unverified:" + ",".join(missing))
    budget = activation.get("budget")
    if not isinstance(budget, dict) or budget.get("aggregate_limit_micro_usd") != AGGREGATE_LIMIT_MICRO_USD:
        raise ProductError("aggregate_budget_must_be_usd_0_25")
    if activation.get("retention_days") not in range(1, 8):
        raise ProductError("retention_must_not_exceed_seven_days")
    if activation.get("routes") != {"reddit": REDDIT_ROUTE, "x": X_ROUTE}:
        raise ProductError("source_routes_must_match_bounded_product_contract")
    if activation.get("model") != "deepseek-flash" or activation.get("channel_id") != CHANNEL_ID:
        raise ProductError("model_or_discord_destination_mismatch")
    if activation.get("source_reservations_micro_usd") != {
        "reddit": REDDIT_RESERVATION_MICRO_USD,
        "x": X_RESERVATION_MICRO_USD,
    }:
        raise ProductError("source_request_reservations_mismatch")
    return activation


def _default_http_request(method, url, headers, body, timeout, max_bytes):
    parsed = urllib.parse.urlsplit(url)
    allowed = {"treg.to", "api.deepseek.com", "discord.com"}
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
        raise ProductError("outbound_host_not_allowed", "dispatch")
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=max(0.1, timeout))
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    deadline = time.monotonic() + timeout
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        chunks, total = [], 0
        while total <= max_bytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("request_deadline_exceeded")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65_536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            raise ProductError("response_size_limit_exceeded", "dispatch")
        return response.status, {key.lower(): value for key, value in response.getheaders()}, b"".join(chunks)
    finally:
        connection.close()


class DiscordRestTransport:
    def __init__(self, token, request, *, injected=False):
        self.token = token
        self.request = request
        self.injected = injected
        self.name = "discord_rest_injected_http" if injected else "discord_rest_bot_api"

    @staticmethod
    def _nonce(payload_identity):
        return payload_identity[:25]

    def _message_id(self, value):
        if (
            isinstance(value, str)
            and value.isascii()
            and value.isdigit()
            and 17 <= len(value) <= 20
            and value[0] != "0"
            and int(value) < (1 << 64)
            and self.token not in value
        ):
            return value
        return None

    def send(self, payload):
        validate_payload(payload)
        boundary = "needradar-" + uuid.uuid4().hex
        nonce = self._nonce(payload["payload_identity"])
        value = {
            "content": payload["content"],
            "nonce": nonce,
            "enforce_nonce": True,
            "allowed_mentions": {"parse": []},
        }
        attachment = payload["attachments"][0] if payload["attachments"] else None
        if attachment:
            value["attachments"] = [{"id": 0, "filename": attachment["filename"]}]
            body = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
                "Content-Type: application/json\r\n\r\n"
                + json.dumps(value, ensure_ascii=False)
                + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{attachment['filename']}\"\r\n"
                "Content-Type: text/markdown; charset=utf-8\r\n\r\n"
                + attachment["content"]
                + f"\r\n--{boundary}--\r\n"
            ).encode("utf-8")
            content_type = f"multipart/form-data; boundary={boundary}"
        else:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        try:
            status, _headers, raw = self.request(
                "POST", f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
                {"Authorization": f"Bot {self.token}", "Content-Type": content_type, "Accept": "application/json"},
                body, 15, 8 * 1024 * 1024,
            )
        except Exception:
            raise RuntimeError("discord_send_transport_error") from None
        result = _json_body(raw)
        message_id = self._message_id(result.get("id")) if isinstance(result, dict) else None
        if status not in {200, 201} or message_id is None:
            raise TimeoutError("discord_send_outcome_ambiguous")
        return {"message_id": message_id}

    def find_by_payload_identity(self, payload_identity):
        query = urllib.parse.urlencode({"limit": 100})
        try:
            status, _headers, raw = self.request(
                "GET", f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages?{query}",
                {"Authorization": f"Bot {self.token}", "Accept": "application/json"},
                None, 15, 2 * 1024 * 1024,
            )
        except Exception:
            raise RuntimeError("discord_readback_transport_error") from None
        messages = _json_body(raw)
        if status != 200 or not isinstance(messages, list):
            raise RuntimeError("discord_readback_unavailable")
        nonce = self._nonce(payload_identity)
        message = next((item for item in messages if isinstance(item, dict) and item.get("nonce") == nonce), None)
        if message is None:
            return {"status": "inconclusive"}
        return {"message_id": self._message_id(message.get("id")), "payload_identity": payload_identity}

    def find_for_removal(self, payload_identity):
        if not _valid_payload_identity(payload_identity):
            return {"status": "identity_mismatch"}
        # shortcut: scan the latest 100 messages; paginate if reconciliation must cover older posts.
        query = urllib.parse.urlencode({"limit": 100})
        try:
            status, _headers, raw = self.request(
                "GET", f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages?{query}",
                {"Authorization": f"Bot {self.token}", "Accept": "application/json"},
                None, 15, 2 * 1024 * 1024,
            )
        except Exception:
            return {"status": "ambiguous"}
        messages = _json_body(raw)
        if status != 200 or not isinstance(messages, list):
            return {"status": "denied" if status in {401, 403} else "ambiguous"}
        matches = [item for item in messages if isinstance(item, dict) and item.get("nonce") == self._nonce(payload_identity)]
        if not matches:
            return {"status": "inconclusive"}
        if len(matches) != 1:
            return {"status": "ambiguous"}
        message = matches[0]
        author = message.get("author")
        message_id = self._message_id(message.get("id"))
        if (
            message_id is None or message.get("channel_id") != CHANNEL_ID
            or not isinstance(author, dict) or author.get("bot") is not True
        ):
            return {"status": "identity_mismatch"}
        return {
            "status": "found",
            "message_id": message_id,
            "channel_id": CHANNEL_ID,
            "payload_identity": payload_identity,
            "bot_created": True,
        }

    def delete_message(self, channel_id, message_id, payload_identity):
        if (
            channel_id != CHANNEL_ID or self._message_id(message_id) is None
            or not _valid_payload_identity(payload_identity)
        ):
            return {"status": "identity_mismatch"}
        headers = {"Authorization": f"Bot {self.token}", "Accept": "application/json"}
        try:
            status, _response_headers, raw = self.request(
                "GET", "https://discord.com/api/v10/users/@me", headers, None, 15, 64 * 1024,
            )
        except Exception:
            return {"status": "ambiguous"}
        user = _json_body(raw)
        bot_id = self._message_id(user.get("id")) if status == 200 and isinstance(user, dict) else None
        if bot_id is None:
            return {"status": "denied" if status in {401, 403} else "ambiguous"}
        try:
            status, _response_headers, raw = self.request(
                "GET", f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages/{message_id}",
                headers, None, 15, 256 * 1024,
            )
        except Exception:
            return {"status": "ambiguous"}
        if status == 404:
            return {"status": "already_absent"}
        message = _json_body(raw)
        author = message.get("author") if isinstance(message, dict) else None
        if status != 200 or not isinstance(message, dict):
            return {"status": "denied" if status in {401, 403} else "ambiguous"}
        if (
            message.get("id") != message_id or message.get("channel_id") != channel_id
            or message.get("nonce") != self._nonce(payload_identity)
            or not isinstance(author, dict) or author.get("bot") is not True or author.get("id") != bot_id
        ):
            return {"status": "identity_mismatch"}
        try:
            status, _response_headers, _raw = self.request(
                "DELETE", f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages/{message_id}",
                headers, None, 15, 64 * 1024,
            )
        except Exception:
            return {"status": "ambiguous"}
        if status in {200, 204}:
            return {"status": "deleted"}
        if status == 404:
            return {"status": "already_absent"}
        return {"status": "denied" if status in {401, 403} else "ambiguous"}


class UnavailableDiscordTransport:
    def find_for_removal(self, _payload_identity):
        return {"status": "unavailable"}

    def delete_message(self, _channel_id, _message_id, _payload_identity):
        return {"status": "unavailable"}


def _json_body(raw):
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None


def _route_request(request, token, org, route, params, reservation, timeout, max_bytes):
    query = urllib.parse.urlencode(params)
    url = f"https://treg.to/call/{route}?{query}"
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "X-Treg-Token": token,
        "X-Treg-Route-Max-Cost": f"{reservation / 1_000_000:.6f}",
    }
    if org:
        headers["X-Treg-Org"] = org
    status, response_headers, body = request("GET", url, headers, None, timeout, max_bytes)
    return status, {str(k).lower(): str(v) for k, v in response_headers.items()}, body, url


def _source_origin(source, route, params, run_id, acquired_at, call_id=None):
    origin = {
        "provider": "tikhub" if source == "reddit" else "x",
        "endpoint": route,
        "request_parameters": params,
        "request_parameters_sha256": _sha(params),
        "run_id": run_id,
        "acquired_at": acquired_at,
        "coverage": "first_page_max_10",
    }
    if call_id:
        origin["call_id"] = call_id
    return origin


def _source_state_dirs(injected=None):
    from scripts import collect_reddit, collect_x

    defaults = {"reddit": collect_reddit.STATE_DEFAULT, "x": collect_x.DEFAULT_STATE_DIR}
    if injected is None:
        return defaults
    if not isinstance(injected, dict) or set(injected) != set(defaults):
        raise ProductError("isolated_source_accounting_directories_required", "source_accounting")
    resolved = {}
    for source, value in injected.items():
        path = Path(value).expanduser().absolute()
        canonical = defaults[source].absolute()
        unsafe_link = collect_reddit.path_has_symlink(path) if source == "reddit" else collect_x._has_symlink_component(path)
        if (
            unsafe_link or path == canonical or path in canonical.parents or canonical in path.parents
            or path.resolve(strict=False).is_relative_to(ROOT)
        ):
            raise ProductError("isolated_source_accounting_directories_required", "source_accounting")
        resolved[source] = path
    if resolved["reddit"] == resolved["x"] or resolved["reddit"] in resolved["x"].parents or resolved["x"] in resolved["reddit"].parents:
        raise ProductError("isolated_source_accounting_directories_required", "source_accounting")
    return resolved


def _check_existing_holds(state_dirs=None):
    """Check source ledgers under their collector locks before credentials load."""
    from scripts import collect_reddit, collect_x

    paths = _source_state_dirs(state_dirs)
    try:
        with collect_reddit.locked_state(paths["reddit"]) as (_state_path, reddit_state):
            try:
                collect_reddit.require_incident_reconciled(reddit_state)
            except Exception:
                raise ProductError("reddit_canonical_accounting_hold_unresolved", "existing_source_holds") from None
            if reddit_state.get("pending") is not None or reddit_state.get("limit_breach") is True:
                raise ProductError("reddit_canonical_accounting_hold_unresolved", "existing_source_holds")
            if collect_reddit.MAX_SPEND_MICRO_USD - reddit_state["spent_micro_usd"] - reddit_state["reserved_micro_usd"] < REDDIT_RESERVATION_MICRO_USD:
                raise ProductError("reddit_cumulative_source_budget_exhausted", "existing_source_holds")
            if reddit_state["successful_requests"] + reddit_state["reserved_success_slots"] >= collect_reddit.MAX_SUCCESSFUL_REQUESTS:
                raise ProductError("reddit_cumulative_request_limit_exhausted", "existing_source_holds")
    except ProductError:
        raise
    except Exception:
        raise ProductError("reddit_canonical_accounting_state_unreadable", "existing_source_holds") from None
    try:
        with collect_x._ticket_lock(paths["x"]):
            x_state = collect_x.load_state(paths["x"])
            if any(item.get("status") in {"reserved", "unknown", "cap_violation"} for item in x_state["attempts"]):
                raise ProductError("x_canonical_accounting_hold_unresolved", "existing_source_holds")
            if x_state["charged_micro_usd"] + X_RESERVATION_MICRO_USD > collect_x.BUDGET_MICRO_USD:
                raise ProductError("x_cumulative_source_budget_exhausted", "existing_source_holds")
            if x_state["successful_requests"] >= collect_x.SUCCESS_LIMIT:
                raise ProductError("x_cumulative_request_limit_exhausted", "existing_source_holds")
    except ProductError:
        raise
    except Exception:
        raise ProductError("x_canonical_accounting_state_unreadable", "existing_source_holds") from None


def _reserve_source_accounting(source, state_dir, attempt_id, run_id, route, params, reservation, now, retention_days):
    from scripts import collect_reddit, collect_x

    if source == "reddit":
        try:
            with collect_reddit.locked_state(state_dir, now=now) as (state_path, state):
                try:
                    collect_reddit.require_incident_reconciled(state)
                except Exception:
                    raise ProductError("reddit_canonical_accounting_hold_unresolved", "source_acquisition") from None
                if state.get("pending") is not None or state.get("limit_breach") is True:
                    raise ProductError("reddit_canonical_accounting_hold_unresolved", "source_acquisition")
                if state["successful_requests"] + state["reserved_success_slots"] >= collect_reddit.MAX_SUCCESSFUL_REQUESTS:
                    raise ProductError("reddit_cumulative_request_limit_exhausted", "source_acquisition")
                if state["spent_micro_usd"] + state["reserved_micro_usd"] + reservation > collect_reddit.MAX_SPEND_MICRO_USD:
                    raise ProductError("reddit_cumulative_source_budget_exhausted", "source_acquisition")
                safe_params = collect_reddit.sanitize(params, [])
                state["attempts"] += 1
                state["reserved_micro_usd"] += reservation
                state["reserved_success_slots"] += 1
                state["pending"] = {
                    "attempt": state["attempts"], "product_attempt_id": attempt_id,
                    "route": "feed", "endpoint": route, "reserved_micro_usd": reservation,
                    "success_slot_reserved": True, "started_at": collect_reddit.timestamp(now),
                    "request_parameters_expires_at": collect_reddit.timestamp(now + timedelta(days=retention_days)),
                    "request_parameters": safe_params,
                    "request_parameters_sha256": hashlib.sha256(json.dumps(safe_params, sort_keys=True).encode()).hexdigest(),
                    "run_id": run_id,
                }
                collect_reddit.atomic_json(state_path, state)
        except ProductError:
            raise
        except Exception:
            raise ProductError("reddit_canonical_accounting_reservation_failed", "source_acquisition") from None
        return

    try:
        with collect_x._ticket_lock(state_dir):
            state = collect_x.load_state(state_dir)
            if any(item.get("status") in {"reserved", "unknown", "cap_violation"} for item in state["attempts"]):
                raise ProductError("x_canonical_accounting_hold_unresolved", "source_acquisition")
            if state["successful_requests"] >= collect_x.SUCCESS_LIMIT:
                raise ProductError("x_cumulative_request_limit_exhausted", "source_acquisition")
            if state["charged_micro_usd"] + reservation > collect_x.BUDGET_MICRO_USD:
                raise ProductError("x_cumulative_source_budget_exhausted", "source_acquisition")
            request = {"method": "GET", "endpoint": route, "parameters": params, "run_id": run_id}
            state["attempt_count"] += 1
            state["attempts"].append(collect_x._attempt_record(attempt_id, request, reservation))
            collect_x._save_state(state_dir, state)
    except ProductError:
        raise
    except Exception:
        raise ProductError("x_canonical_accounting_reservation_failed", "source_acquisition") from None


def _settle_source_accounting(source, state_dir, attempt_id, status, reservation, charge, call_id, now):
    from scripts import collect_reddit, collect_x

    if source == "reddit":
        try:
            with collect_reddit.locked_state(state_dir, now=now) as (state_path, state):
                pending = state.get("pending")
                if not isinstance(pending, dict) or pending.get("product_attempt_id") != attempt_id:
                    raise ProductError("reddit_canonical_accounting_reservation_missing", "source_acquisition")
                successful = isinstance(status, int) and not isinstance(status, bool) and 200 <= status < 300
                if successful:
                    state["successful_requests"] += 1
                state["reserved_success_slots"] -= 1
                if charge is None:
                    pending.update({"charge_known": False, "call_id": call_id, "http_status": status, "success_slot_reserved": False, "http_success": successful})
                    settlement = "unknown_charge"
                else:
                    state["spent_micro_usd"] += charge
                    over_limit = charge > pending["reserved_micro_usd"] or state["spent_micro_usd"] + state["reserved_micro_usd"] - pending["reserved_micro_usd"] > collect_reddit.MAX_SPEND_MICRO_USD
                    if over_limit:
                        state["limit_breach"] = True
                        pending.update({"charge_known": True, "known_charge_micro_usd": charge, "call_id": call_id, "http_status": status, "http_success": successful, "success_slot_reserved": False, "reconciliation_evidence_required": True})
                        settlement = "budget_violation"
                    elif not call_id:
                        state["reserved_micro_usd"] -= pending["reserved_micro_usd"]
                        pending.update({"charge_known": True, "known_charge_micro_usd": charge, "http_status": status, "http_success": successful, "success_slot_reserved": False, "reconciliation_evidence_required": True})
                        settlement = "call_id_missing"
                    else:
                        state["reserved_micro_usd"] -= pending["reserved_micro_usd"]
                        state["pending"] = None
                        settlement = "settled"
                collect_reddit.atomic_json(state_path, state)
                return settlement
        except ProductError:
            raise
        except Exception:
            raise ProductError("reddit_canonical_accounting_settlement_failed", "source_acquisition") from None

    try:
        with collect_x._ticket_lock(state_dir):
            state = collect_x.load_state(state_dir)
            attempt = next((item for item in state["attempts"] if item.get("id") == attempt_id), None)
            if attempt is None or attempt.get("status") != "reserved":
                raise ProductError("x_canonical_accounting_reservation_missing", "source_acquisition")
            successful = isinstance(status, int) and not isinstance(status, bool) and 200 <= status < 300
            attempt["http_status"] = status
            if successful:
                state["successful_requests"] += 1
            if charge is None:
                attempt.update({"status": "unknown", "failure": "billing or call identity unavailable"})
                settlement = "unknown_charge"
            else:
                attempt["cost_micro_usd"] = charge
                state["charged_micro_usd"] += charge
                if charge > reservation or state["charged_micro_usd"] > collect_x.BUDGET_MICRO_USD:
                    attempt["status"] = "cap_violation"
                    settlement = "budget_violation"
                elif not call_id:
                    attempt.update({"status": "unknown", "failure": "call identity unavailable"})
                    settlement = "call_id_missing"
                else:
                    attempt["call_id"] = call_id
                    attempt["reserved_micro_usd"] = 0
                    attempt["status"] = "success" if successful else "failed"
                    settlement = "settled"
            collect_x._save_state(state_dir, state)
            return settlement
    except ProductError:
        raise
    except Exception:
        raise ProductError("x_canonical_accounting_settlement_failed", "source_acquisition") from None


def _created_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _reddit_rows(body, window_start, window_end, origin, token):
    from scripts import collect_reddit

    try:
        nodes, has_next, _cursor = collect_reddit.feed_data(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return [], ["response_invalid"]
    selected, errors = [], []
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("response_invalid")
            continue
        cells = node.get("cells")
        metadata = next(
            (item for item in cells if isinstance(item, dict) and item.get("__typename") == "MetadataCell"),
            None,
        ) if isinstance(cells, list) else None
        try:
            time_node = (
                {**node, "createdAt": metadata.get("createdAt", node.get("createdAt"))}
                if isinstance(metadata, dict) else node
            )
            created = collect_reddit.post_time(time_node)
        except (ValueError, TypeError):
            created = None
        if created is None:
            errors.append("response_invalid")
            continue
        created = created.astimezone(UTC)
        if not window_start <= created < window_end:
            continue
        encoded_id = node.get("id")
        source_id = None
        if isinstance(encoded_id, str):
            try:
                decoded_id = base64.b64decode(encoded_id + "=" * (-len(encoded_id) % 4), validate=True).decode()
                if decoded_id.startswith("t3_"):
                    source_id = decoded_id
            except (ValueError, UnicodeDecodeError):
                pass
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append("response_invalid")
            continue
        title = node.get("title")
        text = node.get("selftext", node.get("text"))
        if isinstance(cells, list):
            title_cell = next((item for item in cells if isinstance(item, dict) and item.get("__typename") == "TitleCell"), None)
            preview = next((item for item in cells if isinstance(item, dict) and item.get("__typename") == "PreviewTextCell"), None)
            title = title_cell.get("title") if isinstance(title_cell, dict) else title
            text = preview.get("text") if isinstance(preview, dict) else text
            if isinstance(metadata, dict):
                node = {**node, "url": metadata.get("detailsLink", node.get("url"))}
        url = collect_reddit.post_url(node, [token], "AI_Agents")
        if not isinstance(title, str) or not title.strip() or (text is not None and not isinstance(text, str)):
            errors.append("response_invalid")
            continue
        selected.append({
            "id": source_id,
            "title": title,
            "selftext": text or "",
            "author": metadata.get("authorName", node.get("author")) if isinstance(metadata, dict) else node.get("author"),
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "permalink": url,
            "edited": node.get("edited") if isinstance(node.get("edited"), bool) else None,
            "deleted": node.get("deleted") if isinstance(node.get("deleted"), bool) else None,
            "partial": True,
            "origin": origin,
        })
        if len(selected) == 10:
            break
    if has_next is True or len(selected) == 10:
        # A first page and post cap never establish source completeness.
        pass
    return selected, errors


def _x_rows(body, window_start, window_end, origin):
    values = body.get("data") if isinstance(body, dict) else None
    if not isinstance(values, list) or len(values) > 10:
        return [], ["response_invalid"]
    selected, errors = [], []
    for row in values:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            errors.append("response_invalid")
            continue
        created = _created_datetime(row.get("created_at"))
        if created is None or not isinstance(row.get("text"), str):
            errors.append("response_invalid")
            continue
        if not window_start <= created < window_end:
            continue
        history = row.get("edit_history_tweet_ids")
        edited = len(history) > 1 if isinstance(history, list) else None
        references = row.get("referenced_tweets")
        replies = [item.get("id") for item in references if isinstance(item, dict) and item.get("type") == "replied_to"] if isinstance(references, list) else []
        selected.append({
            "id": row["id"],
            "text": row["text"],
            "article_title": row.get("article", {}).get("title") if isinstance(row.get("article"), dict) else None,
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "url": f"https://x.com/i/web/status/{row['id']}",
            "conversation_id": row.get("conversation_id"),
            "in_reply_to_id": replies[0] if len(replies) == 1 else None,
            "edited": edited,
            "deleted": False,
            "partial": True,
            "origin": origin,
        })
        if len(selected) == 10:
            break
    return selected, errors


class ProductRunner:
    def __init__(self, config, output, run_id, scheduled_for, boundaries=None):
        self.config = config
        self.output = _private_dir(output)
        self.run_id = run_id
        self.scheduled_for = scheduled_for
        self.boundaries = boundaries or {}
        self.request = self.boundaries.get("http_request") or _default_http_request
        self.invalid_boundaries = bool(self.boundaries) and not {
            "http_request", "credential_loader", "source_state_dirs",
        } <= self.boundaries.keys()
        self.dispatches = {"source": 0, "model": 0, "discord": 0}
        self.source_state_dirs = None
        self.trace_id = uuid.uuid4().hex
        self.tracer = Tracer(
            run_id,
            self.trace_id,
            LangfuseBoundary(self.output / "product-trace.jsonl", serve.redact),
            JsonlSpanSink(self.output / "product-logs.jsonl"),
            serve.redact,
        )
        self.root_span_id = None
        self.now = self.boundaries.get("now") or (lambda: datetime.now(UTC))
        self.injected = "http_request" in self.boundaries
        self.started = time.monotonic()
        self.deadline = self.started + config["timeout_seconds"]
        self.delivery_reserve_seconds = min(90, config["timeout_seconds"] / 2)
        self.delivery_complete = False

    def _remaining(self, maximum, *, delivery=False):
        remaining = self.deadline - time.monotonic()
        if delivery:
            requests_left = max(1, DISCORD_MAX_ATTEMPTS * 2 - self.dispatches["discord"])
            return min(maximum, remaining / requests_left) if remaining > 0 else 0
        if not self.delivery_complete:
            remaining -= self.delivery_reserve_seconds
        if remaining <= 0:
            raise ProductError("operation_deadline_reserved_for_delivery", "deadline")
        return min(maximum, remaining)

    def _request(self, kind, method, url, headers, body, timeout, max_bytes):
        timeout = self._remaining(timeout, delivery=kind == "discord")
        if timeout <= 0:
            raise ProductError("operation_deadline_exceeded", "deadline")
        self.dispatches[kind] += 1
        return self.request(method, url, headers, body, timeout, max_bytes)

    def _load_activation(self):
        path = Path(self.config["activation_file"])
        if path.is_symlink() or not path.is_file():
            raise ProductError("private_activation_manifest_missing")
        info = path.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ProductError("private_activation_manifest_permissions_invalid")
        try:
            activation = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ProductError("activation_manifest_unreadable") from None
        return validate_activation(activation)

    def _credentials(self):
        loader = self.boundaries.get("credential_loader")
        if self.injected and not callable(loader):
            raise ProductError("injected_credentials_required_for_offline_boundary", "credentials")
        try:
            if loader:
                values = loader()
            else:
                from scripts import collect_x
                from need_radar.deepseek_verify import load_api_key

                treg_token, treg_org = collect_x._load_credentials()
                values = {
                    "treg_token": treg_token,
                    "treg_org": treg_org,
                    "deepseek_api_key": load_api_key(),
                    "discord_bot_token": _load_discord_token(),
                }
        except ProductError:
            raise
        except Exception:
            raise ProductError("approved_runtime_credentials_unavailable", "credentials") from None
        if not isinstance(values, dict) or not all(isinstance(values.get(key), str) and values[key] for key in (
            "treg_token", "deepseek_api_key", "discord_bot_token",
        )):
            raise ProductError("approved_runtime_credentials_unavailable", "credentials")
        return values

    def run(self):
        with self.tracer.span(
            "product_cycle",
            inputs={"mode": "product", "scheduled_for": self.scheduled_for},
            attributes={"evidence_kind": "synthetic_injected_boundary" if self.injected else "authentic_live"},
        ) as span:
            self.root_span_id = span["span_id"]
            result = self._run()
            if result.get("status") != "success":
                span["status"] = "failed"
            span["attributes"].update({
                "result_status": result.get("status"),
                "dispatches": dict(self.dispatches),
            })
            span["output"] = {
                key: result.get(key)
                for key in ("status", "stage", "source_status", "serve_status", "delivery_status", "shadow_status", "budget", "error")
                if result.get(key) is not None
            }
        health_path = self.output / "product-health.json"
        if health_path.is_file() and not health_path.is_symlink():
            try:
                health = json.loads(health_path.read_text(encoding="utf-8"))
                health.setdefault("observability", {}).update({
                    "trace_id": self.trace_id,
                    "span_count": self.tracer.span_count,
                    "local_exported_span_count": self.tracer.exported_span_count,
                    "local_export_failures": serve.redact(self.tracer.export_failures),
                    "remote_export": {"enabled": False, "status": "unverified", "verification": "not_attempted"},
                })
                _replace_json(health_path, health)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        return result

    def _run(self):
        _private_dir(self.output)
        status = "failed"
        stage = "activation_preflight"
        artifacts = {}
        try:
            if self.invalid_boundaries:
                raise ProductError("injected_http_and_credential_boundaries_required")
            activation = self._load_activation()
            self.source_state_dirs = _source_state_dirs(
                self.boundaries.get("source_state_dirs") if self.injected else None
            )
            if self.injected:
                output_root = Path(self.config["output_root"]).expanduser().absolute()
                if any(
                    state_dir == output_root or state_dir in output_root.parents or output_root in state_dir.parents
                    for state_dir in self.source_state_dirs.values()
                ):
                    raise ProductError("source_accounting_must_outlive_retained_product_artifacts", "source_accounting")
            budget_path = Path(self.boundaries.get("product_budget_db", self.config["product_budget_db"])).expanduser().absolute()
            output_root = Path(self.config["output_root"]).expanduser().absolute()
            if budget_path == output_root or budget_path in output_root.parents or output_root in budget_path.parents:
                raise ProductError("product_budget_must_outlive_retained_product_artifacts", "source_accounting")
            if not self.injected and budget_path != PRODUCT_BUDGET_DB:
                raise ProductError("product_budget_path_must_use_persistent_canonical_ledger")
            if not self.injected:
                _check_existing_holds()
            _private_dir(self.config["output_root"])
            ledger = ProductBudget(budget_path)
            ledger.claim_one_shot(self.run_id)
            cleanup(self.config["output_root"], now=self.now())
            retention_created = self.now().astimezone(UTC)
            retention_days = activation["retention_days"]
            _write_json(self.output / ".product-retention.json", {
                "operation": "need-radar-product-cycle",
                "schema_version": 1,
                "run_id": self.run_id,
                "retention": {
                    "created_at": retention_created.isoformat().replace("+00:00", "Z"),
                    "retention_days": retention_days,
                    "expires_at": (retention_created + timedelta(days=retention_days)).isoformat().replace("+00:00", "Z"),
                },
            })
            stage = "credentials"
            credentials = self._credentials()
            cycle_time = self.now().astimezone(UTC).replace(microsecond=0)
            cycle_start = cycle_time - timedelta(days=7)
            # Keep five seconds beyond X's ten-second end_time dispatch minimum.
            cycle_end = cycle_time - timedelta(seconds=15)
            window = {"start": cycle_start.isoformat().replace("+00:00", "Z"), "end": cycle_end.isoformat().replace("+00:00", "Z")}
            evidence_kind = "synthetic_injected_boundary" if self.injected else "authentic_live"
            stage = "source_acquisition"
            with self.tracer.span(
                "source_acquisition",
                inputs={"window": window, "routes": {"reddit": REDDIT_ROUTE, "x": X_ROUTE}, "request_limits": {"max_results": 10}},
                attributes={"evidence_kind": evidence_kind, "pagination": "disabled"},
            ) as acquisition_span:
                acquired, acquisition_status = self._acquire(ledger, credentials, window, activation, evidence_kind)
                acquisition_path = self.output / "acquisition.json"
                acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
                acquisition_id = uuid.uuid4().hex
                acquisition["lineage"] = {
                    "run_id": self.run_id, "trace_id": self.trace_id,
                    "stage": "source_acquisition", "span_id": acquisition_span["span_id"],
                    "artifact_id": acquisition_id, "input_stage": None,
                    "input_sha256": _sha({"window": window, "routes": [REDDIT_ROUTE, X_ROUTE]}),
                }
                for source in ("reddit", "x"):
                    for envelope in (acquired[source], acquisition["source_results"][source]):
                        origin = envelope.get("origin")
                        if isinstance(origin, dict):
                            origin["acquisition_artifact_id"] = acquisition_id
                _replace_json(acquisition_path, acquisition)
                acquisition_span["attributes"]["result_status"] = acquisition_status
                acquisition_span["output"] = {
                    "artifact_id": acquisition_id,
                    "artifact_path": "acquisition.json",
                    "artifact_sha256": _sha(acquisition_path.read_bytes()),
                    "source_errors": acquisition["coverage"]["source_errors"],
                }
            artifacts["acquisition"] = "acquisition.json"
            if acquisition_status == "unknown_charge_or_outcome":
                raise ProductError("unknown_source_charge_holds_further_spending", "source_acquisition")
            if acquisition_status == "source_accounting_unresolved":
                raise ProductError("source_call_identity_unresolved", "source_acquisition")
            if acquisition_status == "source_budget_violation":
                raise ProductError("source_charge_exceeded_reserved_cumulative_ceiling", "source_acquisition")
            if acquisition_status == "deadline_exceeded":
                raise ProductError("operation_deadline_exceeded", "deadline")
            fixture = {
                "notice": "bounded product run; source text is untrusted evidence",
                "provider": "deepseek-direct",
                "evidence_kind": evidence_kind,
                "source_results": acquired,
                "normalization_model": {},
                "model": {},
                "judge": {},
                "experiment": {"shared_settings": MODEL_SHARED_SETTINGS, "shadow_model": {}, "shadow_assessment": {}},
            }
            fixture["lineage"] = {
                "run_id": self.run_id,
                "trace_id": self.trace_id,
                "stage": "product_input",
                "artifact_id": uuid.uuid4().hex,
                "input_stage": "source_acquisition",
                "input_artifact_id": acquisition_id,
                "input_sha256": _sha(acquisition_path.read_bytes()),
            }
            input_path = self.output / "product-input.json"
            _write_json(input_path, fixture)
            artifacts["input"] = input_path.name
            model_call = lambda kind, prompt: self._model_call(kind, prompt, credentials, ledger, evidence_kind)
            stage = "serve"
            delivery = {"status": "skipped_serve_failed"}
            delivery_started = False

            def deliver_canonical_report(_report_meta):
                nonlocal delivery, delivery_started, stage
                if delivery_started:
                    return
                delivery_started = True
                stage = "delivery"
                try:
                    transport = self.boundaries.get("discord_transport") or DiscordRestTransport(
                        credentials["discord_bot_token"],
                        lambda method, url, headers, body, timeout, max_bytes: self._request(
                            "discord", method, url, headers, body, timeout, max_bytes,
                        ),
                        injected=self.injected,
                    )
                    delivery = run_delivery(self.output / "serve", transport=transport)
                    artifacts["delivery"] = "serve/discord-delivery.json"
                except Exception as error:
                    delivery = {"status": "failed", "error": type(error).__name__}
                finally:
                    self.delivery_complete = True

            old_mask = os.umask(0o077)
            try:
                serve_status = serve.run(
                    input_path, self.output / "serve", invoke=model_call, mode="product",
                    after_report=deliver_canonical_report,
                )
            finally:
                os.umask(old_mask)
            artifacts["serve"] = "serve"
            if serve_status in {"success", "no_findings"} and not delivery_started:
                delivery = {"status": "failed", "error": "canonical_report_delivery_hook_not_reached"}
            shadow_status = "skipped_serve_failed"
            if serve_status in {"success", "no_findings"}:
                stage = "shadow"
                try:
                    old_mask = os.umask(0o077)
                    try:
                        shadow_status = shadow.run(self.output / "serve", input_path, self.output / "shadow", invoke=model_call, mode="product")
                    finally:
                        os.umask(old_mask)
                    artifacts["shadow"] = "shadow"
                except Exception as error:
                    shadow_status = "failed"
                    artifacts["shadow_error"] = type(error).__name__
            status = "success" if serve_status in {"success", "no_findings"} else "serve_failed"
            stage = "complete" if status == "success" else "serve"
            artifacts["model_calls"] = sorted(path.name for path in self.output.glob("model-call-*.json"))
            extraction = _artifact_status(self.output / "serve" / "candidates.json")
            candidate_value = json.loads((self.output / "serve" / "candidates.json").read_text(encoding="utf-8")) if (self.output / "serve" / "candidates.json").is_file() else {}
            judge_status = _artifact_status(
                self.output / "serve" / "assessment.json",
                "not_required_no_candidates" if extraction == "no_findings" or not candidate_value.get("candidates") else "not_run",
            )
            render_status = "success" if (self.output / "serve" / "report.html").is_file() else "failed" if serve_status in {"success", "no_findings"} else "not_run"
            comparison_status = _artifact_status(self.output / "shadow" / "comparison.json")
            health = {
                "operation": "need-radar-product-cycle",
                "schema_version": 1,
                "run_id": self.run_id,
                "scheduled_for": self.scheduled_for,
                "status": status,
                "stage": stage,
                "evidence_kind": evidence_kind,
                "verification_limit": "injected boundaries are synthetic; live access, rights, charges, and delivery require independent checks" if self.injected else "technical preflight does not certify source/provider rights; provider billing and Discord delivery require independent evidence",
                "window": window,
                "routes": {"reddit": REDDIT_ROUTE, "x": X_ROUTE},
                "source_status": acquisition_status,
                "serve_status": serve_status,
                "serve_judge_status": judge_status,
                "visual_render_status": render_status,
                "delivery_status": delivery.get("status"),
                "shadow_status": shadow_status,
                "shadow_comparison_status": comparison_status,
                "budget": ledger.summary(),
                "reservations": ledger.attempts(),
                "lineage": {
                    "product_cycle": {"run_id": self.run_id, "trace_id": self.trace_id, "span_id": self.root_span_id},
                    "acquisition": _lineage_ref(self.output, "acquisition.json", "source_acquisition"),
                    "product_input": _lineage_ref(self.output, "product-input.json", "product_input"),
                    "snapshot": _lineage_ref(self.output, "serve/snapshot.json", "snapshot"),
                    "serve": _lineage_ref(self.output, "serve/report.md", "report"),
                    "shadow": _lineage_ref(self.output, "shadow/comparison.json", "comparison"),
                    "delivery": _lineage_ref(self.output, "serve/discord-delivery.json", "discord_delivery") if "delivery" in artifacts else None,
                },
                "retention": {
                    "created_at": retention_created.isoformat().replace("+00:00", "Z"),
                    "retention_days": retention_days,
                    "expires_at": (retention_created + timedelta(days=retention_days)).isoformat().replace("+00:00", "Z"),
                },
                "artifacts": artifacts,
                "observability": {
                    "trace_id": self.trace_id,
                    "span_count": self.tracer.span_count,
                    "local_exported_span_count": self.tracer.exported_span_count,
                    "local_export_failures": serve.redact(self.tracer.export_failures),
                    "remote_export": {"enabled": False, "status": "unverified", "verification": "not_attempted"},
                },
            }
            _write_json(self.output / "product-health.json", health)
            warnings = []
            if delivery.get("status") not in {"delivered", "delivered_reconciled"}:
                warnings.append("discord_delivery_unresolved")
            if shadow_status != "complete":
                warnings.append("shadow_incomplete")
            if judge_status not in {"success", "not_required_no_candidates"}:
                warnings.append("serve_judge_unavailable")
            if render_status == "failed":
                warnings.append("visual_render_failed")
            if shadow_status == "complete" and comparison_status not in {"complete", "success"}:
                warnings.append("shadow_comparison_inconclusive")
            if serve_status not in {"success", "no_findings"}:
                warnings.append("serve_failed")
            return {"status": status, "stage": stage, "evidence_kind": evidence_kind, "source_status": acquisition_status, "serve_status": serve_status, "delivery_status": delivery.get("status"), "shadow_status": shadow_status, "budget": ledger.summary(), "dispatches": dict(self.dispatches), "warnings": warnings, "output_path": str(self.output)}
        except ProductError as error:
            status, stage = "blocked", error.stage
            reason = error.code
        except Exception as error:
            status, stage = "failed", stage
            reason = type(error).__name__
        retention_created = locals().get("retention_created") or self.now().astimezone(UTC)
        retention_days = activation["retention_days"] if "activation" in locals() else 7
        artifacts["model_calls"] = sorted(path.name for path in self.output.glob("model-call-*.json"))
        health = {
            "operation": "need-radar-product-cycle", "schema_version": 1, "run_id": self.run_id, "scheduled_for": self.scheduled_for,
            "status": status, "stage": stage, "reason": reason,
            "evidence_kind": "synthetic_injected_boundary" if self.injected else "authentic_live",
            "budget": ledger.summary() if "ledger" in locals() else None,
            "reservations": ledger.attempts() if "ledger" in locals() else [],
            "lineage": {
                "product_cycle": {"run_id": self.run_id, "trace_id": self.trace_id, "span_id": self.root_span_id},
                "acquisition": _lineage_ref(self.output, "acquisition.json", "source_acquisition"),
                "product_input": _lineage_ref(self.output, "product-input.json", "product_input"),
                "snapshot": _lineage_ref(self.output, "serve/snapshot.json", "snapshot"),
                "serve": _lineage_ref(self.output, "serve/report.md", "report"),
                "shadow": _lineage_ref(self.output, "shadow/comparison.json", "comparison"),
                "delivery": _lineage_ref(self.output, "serve/discord-delivery.json", "discord_delivery"),
            },
            "observability": {
                "trace_id": self.trace_id,
                "span_count": self.tracer.span_count,
                "local_exported_span_count": self.tracer.exported_span_count,
                "local_export_failures": serve.redact(self.tracer.export_failures),
                "remote_export": {"enabled": False, "status": "unverified", "verification": "not_attempted"},
            },
            "retention": {
                "created_at": retention_created.isoformat().replace("+00:00", "Z"),
                "retention_days": retention_days,
                "expires_at": (retention_created + timedelta(days=retention_days)).isoformat().replace("+00:00", "Z"),
            },
            "artifacts": artifacts,
        }
        try:
            _write_json(self.output / "product-health.json", health)
        except (OSError, ValueError):
            pass
        return {"status": status, "stage": stage, "error": reason, "evidence_kind": health["evidence_kind"], "budget": health["budget"], "dispatches": dict(self.dispatches), "output_path": str(self.output)}

    def _acquire(self, ledger, credentials, window, activation, evidence_kind):
        start, end = _created_datetime(window["start"]), _created_datetime(window["end"])
        now_text = window["end"]
        x_params = {
            "query": X_QUERY, "end_time": window["end"],
            "sort_order": "recency",
            "max_results": 10,
            "tweet.fields": "created_at,conversation_id,edit_history_tweet_ids,referenced_tweets",
        }
        reddit_params = {"subreddit_name": "AI_Agents", "sort": "NEW", "need_format": "false"}
        sources, errors, unknown, budget_violation, accounting_unresolved = {}, {}, False, False, False
        all_bodies = {}
        routes = (
            ("reddit", REDDIT_ROUTE, reddit_params, REDDIT_RESERVATION_MICRO_USD),
            ("x", X_ROUTE, x_params, X_RESERVATION_MICRO_USD),
        )
        for source, route, params, reservation in routes:
            if unknown:
                sources[source] = {"partial": True, "origin": _source_origin(source, route, params, self.run_id, now_text), "posts": [], "errors": ["charge_unknown"]}
                continue
            try:
                request_timeout = self._remaining(15)
            except ProductError:
                sources[source] = {
                    "partial": True,
                    "origin": _source_origin(source, route, params, self.run_id, now_text),
                    "posts": [],
                    "errors": ["deadline_exceeded"],
                }
                errors[source] = "deadline_exceeded"
                break
            try:
                attempt = ledger.reserve(self.run_id, f"source_{source}", _sha({"route": route, "params": params}), reservation)
            except ProductError:
                if not budget_violation:
                    raise
                errors[source] = "source_budget_violation"
                sources[source] = {
                    "partial": True,
                    "origin": _source_origin(source, route, params, self.run_id, now_text),
                    "posts": [],
                    "errors": ["response_invalid"],
                }
                break
            try:
                _reserve_source_accounting(
                    source, self.source_state_dirs[source], attempt, self.run_id,
                    route, params, reservation, self.now().astimezone(UTC), activation["retention_days"],
                )
            except ProductError as error:
                ledger.settle(attempt, 0, error_code=error.code)
                raise
            source_settlement = None
            product_settled = False
            try:
                status, headers, raw, url = _route_request(
                    lambda method, request_url, request_headers, body, timeout, max_bytes: self._request(
                        "source", method, request_url, request_headers, body, timeout, max_bytes,
                    ),
                    credentials["treg_token"], credentials.get("treg_org"), route, params,
                    reservation, request_timeout, 8 * 1024 * 1024 if source == "reddit" else 1 * 1024 * 1024,
                )
                charge = _charge(headers.get("x-treg-cost-micro"))
                from scripts import collect_reddit, collect_x
                safe_headers = (
                    collect_reddit.sanitize(headers, (credentials["treg_token"], credentials.get("treg_org", "")))
                    if source == "reddit"
                    else collect_x._safe_value(headers, (credentials["treg_token"], credentials.get("treg_org", "")))
                )
                call_id = headers.get("x-treg-call-id")
                safe_call_id = safe_headers.get("x-treg-call-id") if isinstance(safe_headers, dict) else None
                if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 256 or safe_call_id != call_id:
                    call_id = None
                served_via = headers.get("x-treg-served-via", "")
                served_by = headers.get("x-treg-served-by", "")
                expected_provider = "tikhub" if source == "reddit" else "x"
                body, body_error = None, None
                try:
                    decoded = json.loads(raw)
                    secrets = (credentials["treg_token"], credentials.get("treg_org", ""))
                    body = collect_reddit.sanitize(decoded, secrets) if source == "reddit" else collect_x._safe_value(decoded, secrets)
                    if collect_reddit.contains_withheld(body) if source == "reddit" else body is None:
                        raise ValueError("response_sanitization_failed")
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                    body_error = "response_invalid"
                all_bodies[source] = {
                    "attempt_id": attempt,
                    "reserved_micro_usd": reservation,
                    "charged_micro_usd": charge,
                    "http_status": status,
                    "headers": {key: safe_headers[key] for key in ("x-treg-cost-micro", "x-treg-call-id", "x-treg-served-by", "x-treg-served-via", "x-treg-cache") if key in safe_headers},
                    "request": {"url": url, "parameters": params},
                    "body": body,
                    "error": body_error,
                }
                try:
                    source_settlement = _settle_source_accounting(
                        source, self.source_state_dirs[source], attempt, status, reservation,
                        charge, call_id, self.now().astimezone(UTC),
                    )
                except ProductError as error:
                    ledger.settle(
                        attempt, charge, error_code=error.code, stop_on_failure=True,
                        provider_call_id=call_id,
                        accounting_evidence={
                            "provider": "tikhub" if source == "reddit" else "x",
                            "route": route, "http_status": status,
                            "charged_micro_usd": charge, "reservation_micro_usd": reservation,
                            "request_sha256": _sha(params),
                        },
                    )
                    raise
                ledger.settle(
                    attempt, charge,
                    error_code=(
                        "source_charge_unknown" if charge is None else
                        "source_call_id_missing" if not call_id else
                        f"source_accounting_{source_settlement}" if source_settlement != "settled" else
                        f"http_{status}" if status != 200 else None
                    ),
                    stop_on_failure=source_settlement != "settled",
                    provider_call_id=call_id,
                    accounting_evidence={
                        "provider": "tikhub" if source == "reddit" else "x",
                        "route": route, "http_status": status,
                        "charged_micro_usd": charge, "reservation_micro_usd": reservation,
                        "request_sha256": _sha(params),
                        "account_billed_usd": None,
                    },
                )
                product_settled = True
                if charge is None:
                    unknown = True
                    sources[source] = {"partial": True, "origin": _source_origin(source, route, params, self.run_id, now_text), "posts": [], "errors": ["charge_unknown"]}
                    continue
                if source_settlement != "settled":
                    if source_settlement == "budget_violation":
                        budget_violation = True
                        errors[source] = "source_budget_violation"
                    elif source_settlement == "call_id_missing":
                        accounting_unresolved = True
                        errors[source] = "source_accounting_unresolved"
                    else:
                        unknown = True
                        errors[source] = "unknown_charge_or_outcome"
                    sources[source] = {"partial": True, "origin": _source_origin(source, route, params, self.run_id, now_text, call_id), "posts": [], "errors": ["response_invalid"]}
                    break
                origin = _source_origin(source, route, params, self.run_id, now_text, call_id)
                if (
                    charge > reservation
                    or served_via.lower().startswith("overflow:")
                    or headers.get("x-treg-cache", "").lower() == "hit"
                    or served_by.casefold() != expected_provider
                    or not call_id
                ):
                    errors[source] = "response_invalid"
                    budget_violation = charge > reservation
                    sources[source] = {"partial": True, "origin": origin, "posts": [], "errors": ["response_invalid"]}
                    accounting_unresolved = not call_id
                    if accounting_unresolved:
                        unknown = True
                        break
                    continue
                if body_error:
                    errors[source] = "response_invalid"
                    sources[source] = {"partial": True, "origin": origin, "posts": [], "errors": ["response_invalid"]}
                    continue
                if status != 200:
                    errors[source] = "request_failed"
                    rows, parse_errors = [], ["request_failed"]
                elif source == "reddit":
                    rows, parse_errors = _reddit_rows(body, start, end, origin, credentials["treg_token"])
                else:
                    rows, parse_errors = _x_rows(body, start, end, origin)
                sources[source] = {"partial": True, "origin": origin, "posts": rows, "errors": parse_errors}
                if parse_errors:
                    errors[source] = "response_invalid"
            except ProductError:
                raise
            except Exception as error:
                if product_settled:
                    raise
                if source_settlement is None:
                    try:
                        _settle_source_accounting(
                            source, self.source_state_dirs[source], attempt, None, reservation,
                            None, None, self.now().astimezone(UTC),
                        )
                    except ProductError as accounting_error:
                        ledger.settle(
                            attempt, error_code=accounting_error.code, stop_on_failure=True,
                            accounting_evidence={"provider": "tikhub" if source == "reddit" else "x", "route": route, "request_sha256": _sha(params)},
                        )
                        raise
                ledger.settle(
                    attempt, error_code=type(error).__name__,
                    accounting_evidence={
                        "provider": "tikhub" if source == "reddit" else "x",
                        "route": route, "outcome": "unknown",
                        "reservation_micro_usd": reservation, "request_sha256": _sha(params),
                    },
                )
                unknown = True
                errors[source] = "charge_unknown"
                sources[source] = {"partial": True, "origin": _source_origin(source, route, params, self.run_id, now_text), "posts": [], "errors": ["charge_unknown"]}
        for source, route, params, _reservation in routes:
            if source not in sources:
                sources[source] = {
                    "partial": True,
                    "origin": _source_origin(source, route, params, self.run_id, now_text),
                    "posts": [],
                    "errors": ["not_attempted_after_source_hold"],
                }
                errors[source] = "not_attempted_after_source_hold"
        source_results = {"evidence_kind": evidence_kind, **sources}
        _write_json(self.output / "acquisition.json", {
            "schema_version": 1, "run_id": self.run_id, "window": window,
            "requested": {"reddit": reddit_params, "x": x_params},
            "responses": all_bodies,
            "coverage": {
                "partial": True, "source_errors": errors, "max_posts_per_source": 10, "pagination": "disabled",
                "x_window_limitation": "The fixed interval ends 15 seconds before cycle time to meet end_time dispatch eligibility, excluding the newest posts; provider start is implicit and may advance during Reddit delay, so early-window posts may be missing. Local admission remains [window.start, window.end).",
            },
            "source_results": source_results,
        })
        status = "deadline_exceeded" if "deadline_exceeded" in errors.values() else "source_accounting_unresolved" if accounting_unresolved else "unknown_charge_or_outcome" if unknown else "source_budget_violation" if budget_violation else "partial" if errors or any(item["partial"] for item in sources.values()) else "bounded"
        return source_results, status

    def _model_call(self, stage, prompt, credentials, ledger, evidence_kind):
        arm = "serve" if stage.startswith("serve_") else "shadow" if stage.startswith("shadow_") else "normalization"
        if arm in {"serve", "shadow"} and ledger.model_calls_for(arm) >= 11:
            raise ProductError("equal_extraction_arm_request_allocation_reached", stage)
        if stage == "source_normalization" and ledger.model_calls_for("source_normalization") >= 1:
            raise ProductError("normalization_request_allocation_reached", stage)
        if sum(1 for path in self.output.glob("model-call-*.json")) >= MAX_MODEL_CALLS:
            raise ProductError("model_request_ceiling_reached", stage)
        request_payload = {
            "model": "deepseek-flash",
            "messages": prompt["messages"],
            "max_tokens": MODEL_OUTPUT_TOKENS,
            "thinking": {"type": "disabled"},
            "stream": False,
            "temperature": 0,
        }
        body = json.dumps(request_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(body) > MAX_MODEL_REQUEST_BYTES:
            raise ProductError("model_request_byte_ceiling_reached", stage)
        request_timeout = self._remaining(20)
        attempt = ledger.reserve(
            self.run_id, stage, _sha(body), MODEL_RESERVATION_MICRO_USD,
            token_reservation=MAX_MODEL_TOKENS_PER_REQUEST,
        )
        call_id = uuid.uuid4().hex
        with self.tracer.span(
            "deepseek_model_request",
            inputs={"call_id": call_id, "stage": stage, "prompt": prompt, "request_sha256": _sha(body)},
            attributes={"provider": "deepseek-direct", "requested_model": "deepseek-flash", "attempt_id": attempt},
        ) as model_span:
            result = deepseek_verify.invoke_prompt(
                prompt, credentials["deepseek_api_key"],
                transport=lambda _url, headers, request_body, timeout, max_bytes: self._request(
                    "model", "POST", "https://api.deepseek.com/chat/completions", headers, request_body,
                    min(timeout, self._remaining(min(20, timeout))), max_bytes,
                ),
                max_input_tokens=MODEL_INPUT_TOKENS,
                max_output_tokens=MODEL_OUTPUT_TOKENS,
                timeout=request_timeout,
                max_request_bytes=MAX_MODEL_REQUEST_BYTES,
                temperature=0,
            )
            model_span["attributes"].update({
                "returned_model": result.get("returned_model"),
                "result_status": result.get("status"),
                "usage": result.get("usage"),
                "usage_details": result.get("usage_details"),
                "usage_derived_estimated_cost_micro_usd": result.get("usage_derived_estimated_cost_micro_usd"),
                "unknown_charge_or_outcome": result.get("unknown_charge_or_outcome"),
            })
            model_span["output"] = {
                "status": result.get("status"),
                "requested_model": result.get("requested_model"),
                "returned_model": result.get("returned_model"),
                "usage": result.get("usage"),
                "usage_details": result.get("usage_details"),
                "usage_derived_estimated_cost_micro_usd": result.get("usage_derived_estimated_cost_micro_usd"),
                "account_billed_usd": None,
                "unknown_charge_or_outcome": result.get("unknown_charge_or_outcome"),
                "error": result.get("error"),
            }
        usage = result.get("usage")
        cost = result.get("usage_derived_estimated_cost_micro_usd")
        unknown = result.get("unknown_charge_or_outcome") is True or cost is None
        ledger.settle(
            attempt,
            None if unknown else cost,
            usage.get("tokens") if isinstance(usage, dict) else None,
            result.get("error"),
            stop_on_failure=result.get("status") != "provider_response",
            provider_request_id=result.get("request_id"),
            accounting_evidence={
                "provider": "deepseek-direct",
                "requested_model": result.get("requested_model"),
                "returned_model": result.get("returned_model"),
                "http_status": result.get("http_status"),
                "usage": result.get("usage_details"),
                "usage_derived_estimated_cost_micro_usd": cost,
                "account_billed_usd": None,
            },
        )
        if isinstance(cost, int) and cost > MODEL_RESERVATION_MICRO_USD:
            result = {**result, "status": "failure", "error": "reported_usage_cost_exceeds_reservation"}
        record = {
            "schema_version": 1, "call_id": call_id, "run_id": self.run_id,
            "stage": stage, "boundary": evidence_kind, "prompt": prompt,
            "prompt_sha256": _sha(serve.json_bytes(prompt)), "request": request_payload,
            "requested_model": result.get("requested_model", "deepseek-flash"),
            "returned_model": result.get("returned_model"), "status": result.get("status"),
            "usage": usage, "usage_details": result.get("usage_details"),
            "usage_derived_estimated_cost_micro_usd": cost,
            "account_billed_usd": None, "unknown_charge_or_outcome": unknown,
            "response": result.get("response"), "error": result.get("error"),
            "request_id": result.get("request_id"),
            "budget_attempt_id": attempt,
            "reservation_micro_usd": MODEL_RESERVATION_MICRO_USD,
            "model_units_reserved": MAX_MODEL_TOKENS_PER_REQUEST,
            "request_sha256": _sha(body),
            "lineage": {
                "run_id": self.run_id,
                "trace_id": self.trace_id,
                "stage": "deepseek_model_request",
                "span_id": model_span["span_id"],
                "artifact_id": call_id,
                "input_stage": "product_cycle",
                "input_sha256": _sha(serve.json_bytes(prompt)),
            },
        }
        _write_json(self.output / f"model-call-{call_id}.json", record)
        return {**result, "product_call_id": call_id}


def _charge(value):
    if not isinstance(value, str) or not value.isdigit() or len(value) > 18:
        return None
    return int(value)


def _load_discord_token():
    profile_home = Path(os.environ.get("HERMES_HOME", "").strip() or Path.home() / ".hermes").expanduser()
    path = profile_home / ".env"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError
        values = []
        names = ("DISCORD_BOT_TOKEN", "'DISCORD_BOT_TOKEN'", '"DISCORD_BOT_TOKEN"')
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = re.sub(r"^export[^\S\r\n]+", "", line.strip())
            name, separator, value = line.partition("=")
            if name.strip() not in names:
                if any(re.match(rf"^{re.escape(candidate)}(?:$|[ \t=:])", line) for candidate in names):
                    raise ValueError
                continue
            # Accept Hermes assignment forms only when they contain a literal bot token.
            match = re.fullmatch(
                r'''(?:"([A-Za-z0-9._-]+)"(?:\s*\#.*)?|'([A-Za-z0-9._-]+)'(?:\s*\#.*)?|([A-Za-z0-9._-]+)(?:\s+\#.*)?)''',
                value.strip(),
            )
            if not separator or match is None:
                raise ValueError
            values.append(next(item for item in match.groups() if item is not None))
        if len(values) != 1 or not values[0]:
            raise ValueError
        return values[0]
    except (OSError, UnicodeError, ValueError):
        raise ProductError("discord_credential_unavailable", "credentials") from None


def run_product_cycle(config, output, run_id, scheduled_for, boundaries=None):
    return ProductRunner(config, output, run_id, scheduled_for, boundaries).run()


def _valid_payload_identity(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_discord_message_id(value):
    return isinstance(value, str) and value.isascii() and value.isdigit() and 17 <= len(value) <= 20 and value[0] != "0"


def _private_record(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077


def _cleanup_root(output_root):
    root = Path(output_root).expanduser().absolute()
    if root.resolve().is_relative_to(ROOT) or root.is_symlink():
        raise ProductError("cleanup_root_must_be_private_and_outside_repository", "cleanup")
    if not root.exists():
        raise ProductError("cleanup_root_unavailable", "cleanup")
    try:
        root_info = root.lstat()
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid():
            raise ProductError("cleanup_root_must_be_user_owned", "cleanup")
        _private_dir(root)
    except OSError:
        raise ProductError("cleanup_root_unavailable", "cleanup") from None
    return root


def _validated_run_id(run_id):
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ProductError("pilot_run_id_invalid", "cleanup")


def _retention_marker(run_dir, run_id):
    _validated_run_id(run_id)
    if run_dir.is_symlink():
        raise ProductError("pilot_run_directory_unsafe", "cleanup")
    try:
        info = run_dir.lstat()
    except OSError:
        raise ProductError("pilot_run_unavailable", "cleanup") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ProductError("pilot_run_directory_unsafe", "cleanup")
    retention_path = run_dir / ".product-retention.json"
    markers = (retention_path,) if retention_path.exists() or retention_path.is_symlink() else (run_dir / "product-health.json",)
    for marker in markers:
        if not _private_record(marker):
            raise ProductError("pilot_run_retention_marker_unsafe", "cleanup")
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ProductError("pilot_run_retention_marker_invalid", "cleanup") from None
        retention = value.get("retention") if isinstance(value, dict) else None
        created = _created_datetime(retention.get("created_at")) if isinstance(retention, dict) else None
        expires = _created_datetime(retention.get("expires_at")) if isinstance(retention, dict) else None
        days = retention.get("retention_days") if isinstance(retention, dict) else None
        if (
            isinstance(value, dict) and value.get("operation") == "need-radar-product-cycle"
            and value.get("schema_version") == 1 and value.get("run_id") == run_id
            and created is not None and expires is not None and type(days) is int
            and 1 <= days <= 7 and expires == created + timedelta(days=days)
        ):
            return marker, retention
    raise ProductError("pilot_run_retention_marker_invalid", "cleanup")


def _private_run_tree(run_dir):
    for current, directories, files in os.walk(run_dir, followlinks=False):
        current_path = Path(current)
        info = current_path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o077 or current_path.is_mount()
        ):
            raise ProductError("pilot_run_path_unsafe", "cleanup")
        for name in directories + files:
            path = current_path / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid():
                raise ProductError("pilot_run_path_unsafe", "cleanup")
            if stat.S_ISDIR(info.st_mode):
                if info.st_mode & 0o077 or path.is_mount():
                    raise ProductError("pilot_run_path_unsafe", "cleanup")
            elif not stat.S_ISREG(info.st_mode):
                raise ProductError("pilot_run_path_unsafe", "cleanup")


def _removal_path(root, run_id, create=False):
    _validated_run_id(run_id)
    directory = root / REMOVALS_DIRECTORY
    try:
        info = directory.lstat()
    except FileNotFoundError:
        if not create:
            return directory / f"{run_id}.json"
        directory.mkdir(mode=0o700)
        info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ProductError("pilot_removal_directory_unsafe", "cleanup")
    return directory / f"{run_id}.json"


def _pilot_run(output_root, run_id):
    _validated_run_id(run_id)
    root = _cleanup_root(output_root)
    child = root / run_id
    marker, _retention = _retention_marker(child, run_id)
    return root, child, marker


def _delivery_removal_identity(run_dir, run_id):
    delivery_path = run_dir / "serve" / "discord-delivery.json"
    if not delivery_path.exists():
        return None, True
    serve_dir = run_dir / "serve"
    try:
        serve_info = serve_dir.lstat()
    except OSError:
        raise ProductError("pilot_delivery_path_unsafe", "cleanup") from None
    if not stat.S_ISDIR(serve_info.st_mode) or serve_info.st_uid != os.geteuid() or serve_info.st_mode & 0o077:
        raise ProductError("pilot_delivery_path_unsafe", "cleanup")
    if not _private_record(delivery_path):
        raise ProductError("pilot_delivery_artifact_unsafe", "cleanup")
    try:
        delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProductError("pilot_delivery_artifact_invalid", "cleanup") from None
    if not isinstance(delivery, dict):
        raise ProductError("pilot_delivery_artifact_invalid", "cleanup")
    destination = delivery.get("destination") if isinstance(delivery, dict) else None
    lineage = delivery.get("lineage") if isinstance(delivery, dict) else None
    message_id = delivery.get("message_id") if isinstance(delivery, dict) else None
    identity = delivery.get("payload_identity") if isinstance(delivery, dict) else None
    if delivery.get("status") not in ("delivered", "delivered_reconciled", "ambiguous", "pending", "sending", "retrying"):
        return None, True
    if (
        not isinstance(destination, dict) or destination.get("platform") != "discord"
        or destination.get("channel_id") != CHANNEL_ID
        or not isinstance(lineage, dict) or lineage.get("stage") != "discord_delivery"
        or lineage.get("input_stage") != "report"
        or not _valid_payload_identity(identity)
    ):
        raise ProductError("pilot_delivery_identity_invalid", "cleanup")
    payload = delivery.get("payload")
    source = delivery.get("source")
    lineage_db = run_dir / "serve" / "lineage.sqlite3"
    report_path = run_dir / "serve" / "report.md"
    if not _private_record(report_path):
        raise ProductError("pilot_delivery_lineage_unsafe", "cleanup")
    if not isinstance(source, dict) or not isinstance(payload, dict):
        raise ProductError("pilot_delivery_lineage_invalid", "cleanup")
    report_sha = serve.digest(report_path.read_bytes())
    expected_identity = serve.digest(serve.json_bytes({
        "channel_id": CHANNEL_ID,
        "report_sha256": source.get("sha256"),
    }))
    if (
        source.get("artifact_path") != "report.md" or source.get("sha256") != report_sha
        or identity != expected_identity or payload.get("payload_identity") != identity
        or payload.get("report_sha256") != report_sha
    ):
        raise ProductError("pilot_delivery_identity_invalid", "cleanup")
    removal_identity = {
        "channel_id": CHANNEL_ID,
        "message_id": message_id if _valid_discord_message_id(message_id) else None,
        "payload_identity": identity,
    }
    if not _private_record(lineage_db):
        return removal_identity, False
    try:
        database_uri = f"file:{urllib.parse.quote(str(lineage_db))}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as database:
            report_row = database.execute(
                "SELECT run_id, status, artifact_path, output_sha256, artifact_id "
                "FROM stages WHERE stage='report' ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            delivery_row = database.execute(
                "SELECT run_id, status, artifact_path, output_sha256, artifact_id, input_artifact_id, input_sha256 "
                "FROM stages WHERE stage='discord_delivery' ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return removal_identity, False
    artifact_sha = serve.digest(delivery_path.read_bytes())
    if (
        report_row is None or delivery_row is None
        or report_row[1] not in {"success", "no_findings"} or report_row[2] != "report.md"
        or report_row[3] != report_sha
        or report_row[3] != source.get("sha256")
        or source.get("artifact_path") != "report.md"
        or report_row[4] != source.get("artifact_id")
        or delivery_row[0] != report_row[0] or delivery_row[2] != "discord-delivery.json"
        or delivery_row[1] != delivery.get("status")
        or delivery_row[3] != artifact_sha or delivery_row[4] != lineage.get("artifact_id")
        or delivery_row[5] != report_row[4] or delivery_row[6] != report_row[3]
        or lineage.get("run_id") != report_row[0]
        or lineage.get("input_artifact_id") != report_row[4]
        or lineage.get("input_sha256") != report_row[3]
        or payload.get("payload_identity") != identity or payload.get("report_sha256") != report_sha
    ):
        return removal_identity, False
    return removal_identity, True


def _read_removal_record(path, run_id):
    if not path.exists():
        return None
    if not _private_record(path):
        raise ProductError("pilot_removal_record_unsafe", "cleanup")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProductError("pilot_removal_record_invalid", "cleanup") from None
    if not isinstance(value, dict):
        raise ProductError("pilot_removal_record_invalid", "cleanup")
    allowed = {
        "operation", "schema_version", "run_id", "requested_at", "last_attempt_at",
        "channel_id", "message_id", "payload_identity", "retention", "local_status",
        "discord_status", "provider_copy_deletion",
    }
    if set(value) - allowed or value.get("provider_copy_deletion") != "unverified_not_attempted":
        raise ProductError("pilot_removal_record_invalid", "cleanup")
    identity = {key: value.get(key) for key in ("channel_id", "message_id", "payload_identity")}
    retention = value.get("retention")
    created = _created_datetime(retention.get("created_at")) if isinstance(retention, dict) else None
    expires = _created_datetime(retention.get("expires_at")) if isinstance(retention, dict) else None
    days = retention.get("retention_days") if isinstance(retention, dict) else None
    if (
        not isinstance(value, dict) or value.get("operation") != "need-radar-product-removal"
        or value.get("schema_version") != 1 or value.get("run_id") != run_id
        or created is None or expires is None or type(days) is not int
        or not isinstance(retention, dict) or set(retention) != {"created_at", "expires_at", "retention_days"}
        or not 1 <= days <= 7 or expires != created + timedelta(days=days)
        or _created_datetime(value.get("requested_at")) is None
        or (value.get("last_attempt_at") is not None and _created_datetime(value["last_attempt_at"]) is None)
        or value.get("local_status") not in ("pending", "removed", "partial")
        or value.get("discord_status") not in (
            "pending", "not_requested", "not_applicable", "unavailable", "inconclusive", "ambiguous",
            "identity_mismatch", "denied", "deleted", "already_absent",
        )
        or identity["channel_id"] not in (None, CHANNEL_ID)
        or (identity["message_id"] is not None and not _valid_discord_message_id(identity["message_id"]))
        or (identity["payload_identity"] is not None and not _valid_payload_identity(identity["payload_identity"]))
        or (identity["message_id"] is not None and identity["payload_identity"] is None)
        or (identity["payload_identity"] is not None and identity["channel_id"] != CHANNEL_ID)
        or (value.get("discord_status") in {"deleted", "already_absent"} and identity["message_id"] is not None)
    ):
        raise ProductError("pilot_removal_record_invalid", "cleanup")
    return value


def _save_removal_record(path, value):
    if path.exists():
        if not _private_record(path):
            raise ProductError("pilot_removal_record_unsafe", "cleanup")
        _replace_json(path, value)
    else:
        _write_json(path, value)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_run_content(run_dir, retention_marker):
    if not run_dir.exists():
        return "removed"
    try:
        _private_run_tree(run_dir)
        for child in sorted(run_dir.iterdir(), key=lambda path: path.name):
            if child == retention_marker:
                continue
            if stat.S_ISDIR(child.lstat().st_mode):
                shutil.rmtree(child)
            else:
                child.unlink()
        retention_marker.unlink()
        run_dir.rmdir()
    except (OSError, ProductError):
        return "partial"
    return "removed"


def remove_run(output_root, run_id, transport=None, now=None):
    """Remove one validated pilot run locally and attempt one Discord deletion."""
    _validated_run_id(run_id)
    root = _cleanup_root(output_root)
    removal_path = _removal_path(root, run_id)
    removal = _read_removal_record(removal_path, run_id)
    run_dir = root / run_id
    delivery_metadata_unresolved = False
    if removal is None:
        _root, run_dir, retention_marker = _pilot_run(root, run_id)
        _private_run_tree(run_dir)
        retention = json.loads(retention_marker.read_text(encoding="utf-8"))["retention"]
        try:
            identity, lineage_valid = _delivery_removal_identity(run_dir, run_id)
        except (OSError, ProductError):
            identity, lineage_valid = None, False
        delivery_metadata_unresolved = not lineage_valid
        discord_status = "inconclusive" if delivery_metadata_unresolved else "pending" if identity else "not_applicable"
        removal = {
            "operation": "need-radar-product-removal",
            "schema_version": 1,
            "run_id": run_id,
            "requested_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "channel_id": identity["channel_id"] if identity else None,
            "message_id": identity["message_id"] if identity else None,
            "payload_identity": identity["payload_identity"] if identity else None,
            "retention": {key: retention[key] for key in ("created_at", "expires_at", "retention_days")},
            "local_status": "pending",
            "discord_status": discord_status,
            "provider_copy_deletion": "unverified_not_attempted",
        }
        removal_path = _removal_path(root, run_id, create=True)
        _save_removal_record(removal_path, removal)
    elif run_dir.exists() or run_dir.is_symlink():
        if run_dir.is_symlink():
            raise ProductError("pilot_run_directory_unsafe", "cleanup")
        try:
            info = run_dir.lstat()
        except OSError:
            raise ProductError("pilot_run_directory_unsafe", "cleanup") from None
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ProductError("pilot_run_directory_unsafe", "cleanup")
        marker_path = run_dir / ".product-retention.json"
        health_path = run_dir / "product-health.json"
        if not any(path.exists() or path.is_symlink() for path in (marker_path, health_path)):
            raise ProductError("pilot_run_retention_marker_invalid", "cleanup")
        retention_marker, marker_retention = _retention_marker(run_dir, run_id)
        if {key: marker_retention[key] for key in ("created_at", "expires_at", "retention_days")} != removal["retention"]:
            raise ProductError("pilot_removal_record_invalid", "cleanup")
        _private_run_tree(run_dir)

    if run_dir.exists():
        removal["local_status"] = _remove_run_content(run_dir, retention_marker)
        _save_removal_record(removal_path, removal)
    elif not run_dir.exists():
        removal["local_status"] = "removed"
        _save_removal_record(removal_path, removal)

    discord_status = removal["discord_status"]
    if transport is None and discord_status == "pending":
        discord_status = "not_requested"
        removal["discord_status"] = discord_status
        _save_removal_record(removal_path, removal)
    if transport is not None and not delivery_metadata_unresolved and discord_status not in {"deleted", "already_absent", "not_applicable", "identity_mismatch"}:
        if removal["payload_identity"] is None:
            discord_status = "unavailable" if discord_status == "pending" else discord_status
        else:
            message_id = removal["message_id"]
            if message_id is None:
                finder = getattr(transport, "find_for_removal", None)
                try:
                    match = finder(removal["payload_identity"]) if callable(finder) else {"status": "unavailable"}
                except Exception:
                    match = {"status": "ambiguous"}
                match_status = match.get("status") if isinstance(match, dict) else "ambiguous"
                if not isinstance(match_status, str):
                    match_status = "ambiguous"
                if (
                    match_status == "found" and match.get("channel_id") == CHANNEL_ID
                    and match.get("payload_identity") == removal["payload_identity"]
                    and match.get("bot_created") is True
                    and _valid_discord_message_id(match.get("message_id"))
                ):
                    message_id = match["message_id"]
                    removal["message_id"] = message_id
                    removal["channel_id"] = CHANNEL_ID
                    _save_removal_record(removal_path, removal)
                else:
                    discord_status = match_status if match_status in {
                        "inconclusive", "ambiguous", "denied", "identity_mismatch", "unavailable",
                    } else "ambiguous"
            if message_id is not None:
                deleter = getattr(transport, "delete_message", None)
                try:
                    result = deleter(removal["channel_id"], message_id, removal["payload_identity"]) if callable(deleter) else None
                    discord_status = result.get("status") if isinstance(result, dict) else "unavailable"
                except Exception:
                    discord_status = "ambiguous"
                if not isinstance(discord_status, str) or discord_status not in {
                    "deleted", "already_absent", "identity_mismatch", "denied", "ambiguous", "unavailable",
                }:
                    discord_status = "ambiguous"
        removal["discord_status"] = discord_status
        removal["last_attempt_at"] = (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
        if discord_status in {"deleted", "already_absent"}:
            removal.update({"channel_id": None, "message_id": None, "payload_identity": None})
        _save_removal_record(removal_path, removal)
    outcome = {
        "status": "success" if removal["local_status"] == "removed" and removal["discord_status"] in {"deleted", "already_absent", "not_applicable"} else "partial",
        "run_id": run_id,
        "local": {"status": removal["local_status"]},
        "discord": {"status": removal["discord_status"]},
        "provider_copy_deletion": "unverified_not_attempted",
    }
    return outcome


def cleanup(output_root, now=None, transport=None):
    root = Path(output_root).expanduser().absolute()
    if root.resolve().is_relative_to(ROOT) or root.is_symlink():
        raise ProductError("cleanup_root_must_be_private_and_outside_repository", "cleanup")
    if not root.exists():
        return {"status": "success", "removed": 0, "runs": []}
    root = _cleanup_root(root)
    moment = now or datetime.now(UTC)
    removed = 0
    partial = False
    runs = []
    for child in root.iterdir():
        if child.name == REMOVALS_DIRECTORY:
            continue
        health_path = child / "product-health.json"
        marker_path = child / ".product-retention.json"
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            child_info = child.lstat()
            if not stat.S_ISDIR(child_info.st_mode) or child_info.st_uid != os.geteuid() or child_info.st_mode & 0o077:
                continue
            for record_path in (marker_path, health_path):
                if record_path.is_symlink() or not record_path.is_file():
                    continue
                record_info = record_path.lstat()
                if not stat.S_ISREG(record_info.st_mode) or record_info.st_uid != os.geteuid() or record_info.st_mode & 0o077:
                    continue
                try:
                    health = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if not isinstance(health, dict):
                    continue
                retention = health.get("retention")
                retention = retention if isinstance(retention, dict) else {}
                created = _created_datetime(retention.get("created_at"))
                expires = _created_datetime(retention.get("expires_at"))
                days = retention.get("retention_days")
                if (
                    health.get("operation") != "need-radar-product-cycle"
                    or health.get("schema_version") != 1
                    or health.get("run_id") != child.name
                    or created is None or expires is None or type(days) is not int
                    or not 1 <= days <= 7 or expires != created + timedelta(days=days)
                ):
                    continue
                if moment.astimezone(UTC) >= expires:
                    if re.fullmatch(r"[0-9a-f]{32}", child.name):
                        removal_path = _removal_path(root, child.name)
                        run_transport = None if removal_path.exists() else transport
                        outcome = remove_run(root, child.name, transport=run_transport, now=moment)
                        runs.append(outcome)
                        removed += outcome["local"]["status"] == "removed"
                        partial = partial or outcome["status"] != "success"
                    else:
                        shutil.rmtree(child)
                        removed += 1
                break
        except (OSError, KeyError, TypeError, ValueError, ProductError, json.JSONDecodeError):
            continue
    return {"status": "partial" if partial else "success", "removed": removed, "runs": runs}


def main(argv=None, boundaries=None):
    parser = argparse.ArgumentParser(description="Run one enabled product cycle or remove private pilot artifacts.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", required=True, type=Path)
    cleanup_parser = commands.add_parser("cleanup")
    cleanup_parser.add_argument("--output-root", required=True, type=Path)
    cleanup_parser.add_argument("--run-id", help="remove or reconcile this UUID pilot run")
    cleanup_parser.add_argument("--discord", action="store_true", help="request one Discord deletion using the configured bot")
    args = parser.parse_args(argv)
    try:
        if args.command == "cleanup":
            transport = None
            if args.discord:
                transport = boundaries.get("discord_transport") if isinstance(boundaries, dict) else None
                if transport is None:
                    try:
                        transport = DiscordRestTransport(
                            _load_discord_token(),
                            _default_http_request,
                        )
                    except ProductError:
                        transport = UnavailableDiscordTransport()
            result = (
                remove_run(args.output_root, args.run_id, transport=transport)
                if args.run_id else cleanup(args.output_root, transport=transport)
            )
        else:
            from need_radar import scheduled

            config = scheduled._read_config(args.config)
            if not config.get("enabled"):
                result = {"status": "disabled"}
            elif config.get("mode") != "product":
                raise ValueError("run config must select product mode")
            else:
                result = scheduled.run_cycle(config, boundaries=boundaries)
    except (OSError, ValueError, ProductError) as error:
        result = {"status": "failed", "error": str(error)}
    print(json.dumps(serve.redact(result), sort_keys=True))
    return 0 if result.get("status") in {"success", "disabled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
