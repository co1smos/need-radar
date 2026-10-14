"""Bounded, synthetic-only direct DeepSeek route verification."""

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time
import uuid

from need_radar.__main__ import execute_extraction, json_bytes, redact
from need_radar.observability import JsonlSpanSink, LangfuseBoundary, Tracer


MODEL = "deepseek-flash"
HOST = "api.deepseek.com"
ENDPOINT = f"https://{HOST}/chat/completions"
CREDENTIAL_FILE = Path.home() / ".config" / "need-radar" / "deepseek.env"
DEFAULT_STATE_DIR = Path.home() / ".config" / "need-radar" / "deepseek-verification"
CANONICAL_STATE_DIR = Path.home() / ".local" / "state" / "need-radar"
PRICE_SNAPSHOT_DATE = "2026-10-13"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
INPUT_CACHE_MISS_PEAK_USD_PER_MILLION = 0.30
OUTPUT_PEAK_USD_PER_MILLION = 1.20
INPUT_PEAK_MICRO_USD_PER_MILLION = 300_000
OUTPUT_PEAK_MICRO_USD_PER_MILLION = 1_200_000
MAX_INPUT_TOKENS = 1_000
MAX_OUTPUT_TOKENS = 256
MAX_REQUESTS = 3
BUDGET_LIMIT_MICRO_USD = 50_000
MAX_REQUEST_RESERVATION_MICRO_USD = (
    MAX_INPUT_TOKENS * INPUT_PEAK_MICRO_USD_PER_MILLION
    + MAX_OUTPUT_TOKENS * OUTPUT_PEAK_MICRO_USD_PER_MILLION
    + 999_999
) // 1_000_000
MAX_REQUEST_BODY_BYTES = 1_000
MAX_RESPONSE_BYTES = 262_144
REQUEST_TIMEOUT_SECONDS = 20
SCHEMA_VERSION = 1
POLICY = {
    "model": MODEL,
    "endpoint": ENDPOINT,
    "price_snapshot_date": PRICE_SNAPSHOT_DATE,
    "input_cache_miss_peak_usd_per_million": INPUT_CACHE_MISS_PEAK_USD_PER_MILLION,
    "output_peak_usd_per_million": OUTPUT_PEAK_USD_PER_MILLION,
    "input_peak_micro_usd_per_million": INPUT_PEAK_MICRO_USD_PER_MILLION,
    "output_peak_micro_usd_per_million": OUTPUT_PEAK_MICRO_USD_PER_MILLION,
    "max_input_tokens": MAX_INPUT_TOKENS,
    "max_output_tokens": MAX_OUTPUT_TOKENS,
    "max_requests": MAX_REQUESTS,
    "budget_limit_micro_usd": BUDGET_LIMIT_MICRO_USD,
    "max_request_reservation_micro_usd": MAX_REQUEST_RESERVATION_MICRO_USD,
    "max_request_body_bytes": MAX_REQUEST_BODY_BYTES,
    "max_response_bytes": MAX_RESPONSE_BYTES,
    "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
}
POLICY_SHA256 = hashlib.sha256(json_bytes(POLICY)).hexdigest()
SYNTHETIC_PROMPT = {
    "config": {"purpose": "route_verification", "synthetic_only": True},
    "messages": [
        {
            "role": "system",
            "content": "Return exactly [] as a JSON array. Do not use tools.",
        },
        {
            "role": "user",
            "content": (
                "Synthetic Need Radar model-route verification only; no user, Reddit, "
                "X, or private source content. Reply only with []."
            ),
        },
    ],
}


class VerificationError(Exception):
    pass


class BudgetLimit(VerificationError):
    pass


def _utc_now():
    return datetime.now(timezone.utc)


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_directory(path):
    path = Path(path).expanduser().absolute()
    canonical = CANONICAL_STATE_DIR.absolute()
    if path == canonical or canonical in path.parents:
        raise VerificationError("canonical Need Radar state is not an allowed verification path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise VerificationError("verification paths cannot contain symlinks")
        except FileNotFoundError:
            pass
    if path.exists():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise VerificationError("verification directory is not a private user-owned directory")
    else:
        path.mkdir(mode=0o700, parents=True)
    os.chmod(path, 0o700)
    return path


def _write_private_json(path, value):
    path = Path(path)
    content = json.dumps(redact(value), ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _pricing_date(value, today=None):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as error:
        raise VerificationError("pricing check must use UTC YYYY-MM-DD") from error
    current = today or _utc_now().date()
    if parsed.isoformat() != value or parsed != current or PRICE_SNAPSHOT_DATE != current.isoformat():
        raise VerificationError("verify official pricing today and pass today's UTC date")
    return value


def load_api_key(path=CREDENTIAL_FILE):
    path = Path(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise VerificationError("DeepSeek credential file must be user-owned, regular, and mode 0600")
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == "DEEPSEEK_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values.append(value)
    if len(values) != 1 or not values[0] or "\n" in values[0] or "\r" in values[0]:
        raise VerificationError("credential file must contain one non-empty DEEPSEEK_API_KEY")
    return values[0]


def _cost_micro_usd(prompt_tokens, completion_tokens):
    return (
        prompt_tokens * INPUT_PEAK_MICRO_USD_PER_MILLION
        + completion_tokens * OUTPUT_PEAK_MICRO_USD_PER_MILLION
        + 999_999
    ) // 1_000_000


class BudgetStore:
    def __init__(self, directory):
        self.directory = _private_directory(directory)
        self.path = self.directory / "budget.sqlite3"

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        os.chmod(self.path, 0o600)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reserved_micro_usd INTEGER NOT NULL,
                    usage_estimate_micro_usd INTEGER,
                    request_sha256 TEXT NOT NULL,
                    pricing_verified_on TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    http_status INTEGER,
                    evidence_path TEXT,
                    error_code TEXT
                )"""
            )
            stored = connection.execute(
                "SELECT value FROM metadata WHERE key = 'policy_sha256'"
            ).fetchone()
            if stored is None:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('policy_sha256', ?)",
                    (POLICY_SHA256,),
                )
            elif stored[0] != POLICY_SHA256:
                raise VerificationError("verification budget policy changed; reconcile before continuing")
            connection.commit()
            _fsync_directory(self.directory)
        except Exception:
            connection.rollback()
            connection.close()
            raise
        return connection

    def reserve(self, attempt_id, request_sha256, pricing_verified_on):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            unreconciled = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE status IN ('reserved', 'unknown')"
            ).fetchone()[0]
            if unreconciled:
                raise BudgetLimit("an earlier request outcome is unknown; reconcile before dispatch")
            failed = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE status = 'failed'"
            ).fetchone()[0]
            if failed:
                raise BudgetLimit("an earlier request failed; retries are disabled")
            count, usage_estimate, reserved = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(usage_estimate_micro_usd), 0),
                          COALESCE(SUM(reserved_micro_usd), 0) FROM attempts"""
            ).fetchone()
            if count >= MAX_REQUESTS:
                raise BudgetLimit("cumulative request limit reached")
            if usage_estimate + reserved + MAX_REQUEST_RESERVATION_MICRO_USD > BUDGET_LIMIT_MICRO_USD:
                raise BudgetLimit("cumulative spend reservation would exceed $0.05")
            connection.execute(
                """INSERT INTO attempts(
                    attempt_id, status, reserved_micro_usd, usage_estimate_micro_usd,
                    request_sha256, pricing_verified_on, created_at
                ) VALUES (?, 'reserved', ?, NULL, ?, ?, ?)""",
                (
                    attempt_id,
                    MAX_REQUEST_RESERVATION_MICRO_USD,
                    request_sha256,
                    pricing_verified_on,
                    _utc_now().isoformat(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def settle(self, attempt_id, status, *, usage_estimate_micro_usd=None, http_status=None,
               evidence_path=None, error_code=None):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reservation = MAX_REQUEST_RESERVATION_MICRO_USD if status == "unknown" else 0
            cursor = connection.execute(
                """UPDATE attempts SET status = ?, reserved_micro_usd = ?,
                       usage_estimate_micro_usd = ?, http_status = ?, evidence_path = ?, error_code = ?
                   WHERE attempt_id = ? AND status = 'reserved'""",
                (
                    status,
                    reservation,
                    usage_estimate_micro_usd,
                    http_status,
                    str(evidence_path) if evidence_path else None,
                    error_code,
                    attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise VerificationError("reserved attempt could not be reconciled")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def summary(self):
        connection = self._connect()
        try:
            count, usage_estimate, reserved = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(usage_estimate_micro_usd), 0),
                          COALESCE(SUM(reserved_micro_usd), 0) FROM attempts"""
            ).fetchone()
            return {
                "requests_reserved_or_dispatched": count,
                "usage_derived_estimate_micro_usd": usage_estimate,
                "held_unknown_or_in_flight_micro_usd": reserved,
                "account_billed_usd": None,
                "account_billing_evidence": "not queried",
                "budget_limit_micro_usd": BUDGET_LIMIT_MICRO_USD,
            }
        finally:
            connection.close()


def _request_body(prompt):
    payload = {
        "model": MODEL,
        "messages": prompt["messages"],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise VerificationError("synthetic request exceeds the bounded input payload")
    return payload, body


def _safe_usage(value):
    if not isinstance(value, dict):
        return None
    tokens = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        candidate = value.get(key)
        if type(candidate) is not int or candidate < 0:
            return None
        tokens[key] = candidate
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        candidate = value.get(key)
        if type(candidate) is int and candidate >= 0:
            tokens[key] = candidate
    for details_key, allowed in (
        ("prompt_tokens_details", ("cached_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")),
        ("completion_tokens_details", ("reasoning_tokens",)),
    ):
        details = value.get(details_key)
        if isinstance(details, dict):
            tokens[details_key] = {
                key: details[key]
                for key in allowed
                if type(details.get(key)) is int and details[key] >= 0
            }
    if tokens["total_tokens"] != tokens["prompt_tokens"] + tokens["completion_tokens"]:
        return None
    return tokens


def _provider_text(value, api_key):
    if not isinstance(value, str):
        return None
    return redact(value.replace(api_key, "[REDACTED]"))[:4096]


def _response_details(status, headers, body, api_key):
    body_hash = _sha256(body)
    details = {
        "http_status": status,
        "response_bytes": len(body),
        "response_sha256": body_hash,
        "request_id": _provider_text(headers.get("x-request-id"), api_key),
    }
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return details, None
    if not isinstance(decoded, dict):
        return details, None
    usage = _safe_usage(decoded.get("usage"))
    choices = decoded.get("choices")
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    details.update({
        "returned_model": _provider_text(decoded.get("model"), api_key),
        "system_fingerprint": _provider_text(decoded.get("system_fingerprint"), api_key),
        "finish_reason": _provider_text(choice.get("finish_reason"), api_key)
        if isinstance(choice, dict) else None,
        "assistant_role": _provider_text(message.get("role"), api_key)
        if isinstance(message, dict) else None,
        "assistant_content": _provider_text(content, api_key),
        "usage": usage,
    })
    return details, decoded


def _send(body, api_key):
    connection = http.client.HTTPSConnection(HOST, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        connection.request(
            "POST",
            "/chat/completions",
            body=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Connection": "close",
                "Content-Type": "application/json",
            },
        )
        if connection.sock is not None:
            connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
        response = connection.getresponse()
        chunks = []
        total = 0
        while total <= MAX_RESPONSE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("provider response deadline exceeded")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65_536, MAX_RESPONSE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
        headers = {"x-request-id": response.getheader("x-request-id")}
        if len(raw) > MAX_RESPONSE_BYTES:
            raise VerificationError("provider response exceeds the bounded response size")
        return response.status, headers, raw
    finally:
        connection.close()


def _write_replay(run_directory, attempt_id, prompt, request_payload, request_sha256, details,
                  pricing_verified_on, result_status, cost_micro_usd=None, error_code=None):
    replay_path = run_directory / f"{attempt_id}.replay.json"
    response_record = {
        key: details[key]
        for key in (
            "http_status", "response_bytes", "response_sha256", "request_id",
            "returned_model", "system_fingerprint", "finish_reason", "assistant_role",
            "assistant_content", "usage",
        )
        if key in details
    }
    usage = details.get("usage")
    cost = None
    if usage is not None:
        cost = _cost_micro_usd(usage["prompt_tokens"], usage["completion_tokens"])
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "created_at": _utc_now().isoformat(),
        "provider": "deepseek-direct",
        "endpoint": ENDPOINT,
        "requested_model": MODEL,
        "request": request_payload,
        "request_sha256": request_sha256,
        "prompt": prompt,
        "response": response_record,
        "status": result_status,
        "error_code": error_code,
        "pricing": {
            "source": PRICING_SOURCE,
            "price_snapshot_date": PRICE_SNAPSHOT_DATE,
            "verified_on_utc": pricing_verified_on,
            "input_cache_miss_peak_usd_per_million": INPUT_CACHE_MISS_PEAK_USD_PER_MILLION,
            "output_peak_usd_per_million": OUTPUT_PEAK_USD_PER_MILLION,
            "input_peak_micro_usd_per_million": INPUT_PEAK_MICRO_USD_PER_MILLION,
            "output_peak_micro_usd_per_million": OUTPUT_PEAK_MICRO_USD_PER_MILLION,
            "account_billed_usd": None,
            "account_billing_evidence": "not queried",
            "usage_derived_estimated_cost_micro_usd": cost_micro_usd if cost_micro_usd is not None else cost,
            "cost_method": "all input tokens priced as peak cache-miss; output at peak rate",
        },
        "telemetry": {"external_export_enabled": False},
    }
    _write_private_json(replay_path, evidence)
    return replay_path, evidence


def _invoke_once(prompt, api_key, store, run_directory, pricing_verified_on, attempt_id):
    try:
        request_payload, request_body = _request_body(prompt)
    except VerificationError:
        return {"status": "failure", "error": "synthetic_request_limit", "attempted_requests": 0}
    request_sha256 = _sha256(request_body)
    try:
        store.reserve(attempt_id, request_sha256, pricing_verified_on)
    except BudgetLimit as error:
        return {"status": "failure", "error": str(error), "attempted_requests": 0}
    except (VerificationError, OSError, sqlite3.Error):
        return {"status": "failure", "error": "budget_reservation_failed", "attempted_requests": 0}

    try:
        status, headers, raw = _send(request_body, api_key)
        details, _ = _response_details(status, headers, raw, api_key)
    except Exception:
        details = {
            "http_status": None,
            "response_bytes": 0,
            "response_sha256": None,
            "request_id": None,
        }
        error_code = "transport_outcome_unknown"
        replay_status = "unknown"
        replay_path, _ = _write_replay(
            run_directory, attempt_id, prompt, request_payload, request_sha256, details,
            pricing_verified_on, replay_status, error_code=error_code,
        )
        store.settle(
            attempt_id, "unknown", evidence_path=replay_path, error_code=error_code,
        )
        return {
            "status": "failure",
            "error": error_code,
            "usage": None,
            "response": None,
            "attempted_requests": 1,
            "attempt_id": attempt_id,
        }

    usage = details.get("usage")
    usage_valid = usage is not None
    cost = (
        _cost_micro_usd(usage["prompt_tokens"], usage["completion_tokens"])
        if usage_valid else None
    )
    output = details.get("assistant_content")
    parsed_output = None
    output_valid = False
    if isinstance(output, str):
        try:
            parsed_output = json.loads(output)
            output_valid = parsed_output == []
        except json.JSONDecodeError:
            pass
    model_valid = details.get("returned_model") == MODEL
    limits_valid = usage_valid and (
        usage["prompt_tokens"] <= MAX_INPUT_TOKENS
        and usage["completion_tokens"] <= MAX_OUTPUT_TOKENS
    )
    response_valid = (
        status == 200 and model_valid and limits_valid and output_valid
        and details.get("assistant_role") == "assistant"
        and details.get("finish_reason") == "stop"
    )
    if response_valid:
        result_status, error_code, ledger_status = "success", None, "success"
    elif usage_valid:
        result_status, ledger_status = "failed_known_usage", "failed"
        if status != 200:
            error_code = f"http_status_{status}"
        elif not model_valid:
            error_code = "provider_model_mismatch"
        elif not limits_valid:
            error_code = "provider_usage_exceeded_ceiling"
        else:
            error_code = "invalid_or_incomplete_model_response"
    else:
        result_status, error_code, ledger_status = "unknown", "usage_unavailable", "unknown"

    replay_path, _ = _write_replay(
        run_directory, attempt_id, prompt, request_payload, request_sha256, details,
        pricing_verified_on, result_status, cost_micro_usd=cost, error_code=error_code,
    )
    store.settle(
        attempt_id,
        ledger_status,
        usage_estimate_micro_usd=cost if usage_valid else None,
        http_status=status,
        evidence_path=replay_path,
        error_code=error_code,
    )
    normalized_usage = (
        {"requests": 1, "tokens": usage["total_tokens"]} if usage_valid else None
    )
    return {
        "status": "provider_response" if response_valid else "failure",
        "error": error_code,
        "usage": normalized_usage,
        "response": parsed_output,
        "attempted_requests": 1,
        "attempt_id": attempt_id,
        "usage_derived_estimated_cost_micro_usd": cost,
        "account_billed_usd": None,
    }


def run_verification(requests, pricing_verified_on, state_dir=DEFAULT_STATE_DIR,
                     api_key=None, today=None):
    if type(requests) is not int or requests < 1 or requests > MAX_REQUESTS:
        raise VerificationError("requests must be between 1 and 3")
    pricing_verified_on = _pricing_date(pricing_verified_on, today=today)
    api_key = api_key if api_key is not None else load_api_key()
    state_directory = _private_directory(state_dir)
    run_id = uuid.uuid4().hex
    runs_directory = _private_directory(state_directory / "runs")
    run_directory = _private_directory(runs_directory / run_id)
    store = BudgetStore(state_directory)
    tracer = Tracer(
        run_id,
        uuid.uuid4().hex,
        LangfuseBoundary(run_directory / "trace.jsonl", redact),
        JsonlSpanSink(run_directory / "logs.jsonl"),
        redact,
    )
    calls = []
    settings = {
        "provider": "deepseek-direct",
        "model": MODEL,
        "request_limit": 1,
        "max_tokens": MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS,
        "candidate_limit": 1,
        "retry_policy": {"max_attempts": 1},
        "input_token_limit": MAX_INPUT_TOKENS,
    }
    try:
        for _ in range(requests):
            call_id = uuid.uuid4().hex
            with tracer.span(
                "deepseek_model_call",
                inputs={"call_id": call_id, "prompt": SYNTHETIC_PROMPT},
                attributes={
                    "provider": "deepseek-direct",
                    "model": MODEL,
                    "boundary": "direct_https",
                    "retry_policy": "none",
                    "external_export_enabled": False,
                },
            ) as span:
                invocation = lambda prompt: _invoke_once(
                    prompt, api_key, store, run_directory, pricing_verified_on, call_id,
                )
                result, error, artifact, response, _ = execute_extraction(
                    SYNTHETIC_PROMPT,
                    invocation,
                    settings,
                    boundary="deepseek_direct",
                    success_status="provider_response",
                )
                span["attributes"]["result_status"] = result
                span["attributes"]["attempt_id"] = artifact.get("attempt_id")
                if result != "success":
                    span["status"] = "failed"
                    span["error"] = {"type": "VerificationError", "message": error}
                span["output"] = {
                    "status": result,
                    "response": response,
                    "usage": artifact.get("usage"),
                    "error": error,
                    "attempt_id": artifact.get("attempt_id"),
                    "usage_derived_estimated_cost_micro_usd": artifact.get(
                        "usage_derived_estimated_cost_micro_usd"
                    ),
                    "account_billed_usd": None,
                }
            calls.append({
                "status": result,
                "error": error,
                "attempt_id": artifact.get("attempt_id"),
                "attempted_requests": artifact.get("attempted_requests", 0),
                "usage": artifact.get("usage"),
                "usage_derived_estimated_cost_micro_usd": artifact.get(
                    "usage_derived_estimated_cost_micro_usd"
                ),
                "account_billed_usd": None,
            })
            if result != "success":
                break
    finally:
        for path in run_directory.iterdir():
            if path.is_file():
                os.chmod(path, 0o600)

    report = {
        "run_id": run_id,
        "provider": "deepseek-direct",
        "model": MODEL,
        "endpoint": ENDPOINT,
        "synthetic_only": True,
        "status": "success" if calls and all(call["status"] == "success" for call in calls) else "failed",
        "calls": calls,
        "pricing_verified_on_utc": pricing_verified_on,
        "budget": store.summary(),
        "observability": {
            "local_span_count": tracer.span_count,
            "local_spans_written": tracer.exported_span_count,
            "local_export_failures": tracer.export_failures,
        },
        "evidence_directory": str(run_directory),
        "external_telemetry_export": "disabled",
    }
    _write_private_json(run_directory / "run.json", report)
    report["evidence_directory"] = str(run_directory)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify DeepSeek using bounded synthetic-only requests")
    parser.add_argument("--requests", type=int, default=1, help="number of independent requests (1-3)")
    parser.add_argument(
        "--pricing-verified-on",
        required=True,
        metavar="UTC-DATE",
        help="today's UTC date after rechecking official DeepSeek rates",
    )
    arguments = parser.parse_args(argv)
    try:
        report = run_verification(
            arguments.requests,
            arguments.pricing_verified_on,
            state_dir=DEFAULT_STATE_DIR,
        )
    except (VerificationError, OSError, sqlite3.Error, PermissionError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
