#!/usr/bin/env python3
"""Bounded, recorded X search collector for Need Radar ticket 9."""

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import urllib.error
import urllib.request
import uuid


TICKET = 9
ROUTE = "anyapi.x.search.posts"
ENDPOINT = "https://treg.to/call/anyapi.x.search.posts"
DEFAULT_STATE_DIR = Path("/home/ubuntu/.local/state/need-radar/ticket-9")
CREDENTIALS_FILE = Path("/home/ubuntu/projects/need-radar/credentials.env")
BUDGET_MICRO_USD = 250_000
SUCCESS_LIMIT = 25
RETENTION_SECONDS = 7 * 24 * 60 * 60
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}
REQUIRED_GATES = (
    "approved_query_window",
    "provider_account_eligibility",
    "source_access_rights",
    "route_schema",
    "route_billing",
    "route_cost_cap",
    "provider_limits",
    "retention_and_removal",
    "internal_source_policy",
)
REQUIRED_REVIEW_CHECKS = {"budget_reservations", "secret_handling"}
TOKEN_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|token)"
    r"([\"']?\s*[=:]\s*)(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s&,;]+)"
)


class CollectorError(Exception):
    pass


def _safe_mkdir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _private_json(path, value):
    _safe_mkdir(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _new_state():
    return {
        "schema_version": 1,
        "ticket": TICKET,
        "successful_requests": 0,
        "charged_micro_usd": 0,
        "attempt_count": 0,
        "attempts": [],
    }


def load_state(state_dir):
    path = Path(state_dir) / "state.json"
    if not path.exists():
        return _new_state()
    try:
        metadata = path.stat()
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise CollectorError("ticket state permissions are unsafe")
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CollectorError("ticket state is unreadable; acquisition paused") from None
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 1
        or state.get("ticket") != TICKET
        or not isinstance(state.get("attempts"), list)
    ):
        raise CollectorError("ticket state is invalid; acquisition paused")
    counters = ("successful_requests", "charged_micro_usd", "attempt_count")
    if any(
        isinstance(state.get(key), bool)
        or not isinstance(state.get(key), int)
        or state[key] < 0
        for key in counters
    ):
        raise CollectorError("ticket state is invalid; acquisition paused")
    if state["attempt_count"] != len(state["attempts"]):
        raise CollectorError("ticket state is invalid; acquisition paused")
    allowed_statuses = {"reserved", "unknown", "cap_violation", "success", "failed"}
    attempt_charges = []
    successful_responses = 0
    for attempt in state["attempts"]:
        if not isinstance(attempt, dict) or attempt.get("status") not in allowed_statuses:
            raise CollectorError("ticket state is invalid; acquisition paused")
        charge = attempt.get("cost_micro_usd", 0)
        if isinstance(charge, bool) or not isinstance(charge, int) or charge < 0:
            raise CollectorError("ticket state is invalid; acquisition paused")
        attempt_charges.append(charge)
        status = attempt.get("http_status")
        if isinstance(status, int) and not isinstance(status, bool) and 200 <= status < 300:
            successful_responses += 1
    if sum(attempt_charges) != state["charged_micro_usd"] or successful_responses != state["successful_requests"]:
        raise CollectorError("ticket state is invalid; acquisition paused")
    return state


def _save_state(state_dir, state):
    _private_json(Path(state_dir) / "state.json", state)


@contextmanager
def _ticket_lock(state_dir):
    state_dir = Path(state_dir)
    _safe_mkdir(state_dir)
    lock_path = state_dir / "collector.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _date_epoch(value):
    return datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone.utc).timestamp()


def validate_approval(inputs):
    if not isinstance(inputs, dict):
        raise CollectorError("approved input record is invalid")
    if not all(isinstance(inputs.get(key), str) and inputs[key].strip() for key in ("approval_id", "provider", "route", "query", "query_type", "internal_source_policy")):
        raise CollectorError("approved input record is incomplete")
    if inputs["route"] != ROUTE or inputs["provider"] != "treg-managed-anyapi":
        raise CollectorError("approved provider route does not match this collector")
    if re.search(r"\b(?:since|until):\d{4}-\d{2}-\d{2}", inputs["query"], re.I):
        raise CollectorError("query must not duplicate the approved time window")
    if inputs["query_type"] not in {"Latest", "Top", "Photos", "Videos"}:
        raise CollectorError("approved query type is invalid")
    window = inputs.get("window")
    if not isinstance(window, dict):
        raise CollectorError("approved input record has no time window")
    try:
        since = date.fromisoformat(window["since"])
        until = date.fromisoformat(window["until"])
    except (KeyError, TypeError, ValueError):
        raise CollectorError("approved time window is invalid") from None
    if since >= until:
        raise CollectorError("approved time window is invalid")

    limits = inputs.get("limits")
    if not isinstance(limits, dict):
        raise CollectorError("approved request limits are missing")
    required_limits = (
        "page_size",
        "max_pages",
        "max_items",
        "max_attempts_per_page",
        "max_total_attempts",
        "timeout_seconds",
        "retry_delay_seconds",
    )
    if any(key not in limits for key in required_limits):
        raise CollectorError("approved request limits are incomplete")
    try:
        page_size = limits["page_size"]
        max_pages = limits["max_pages"]
        max_items = limits["max_items"]
        max_attempts = limits["max_attempts_per_page"]
        max_total_attempts = limits["max_total_attempts"]
        timeout = limits["timeout_seconds"]
        retry_delay = limits["retry_delay_seconds"]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (page_size, max_pages, max_items, max_attempts, max_total_attempts)):
            raise ValueError
        if not 1 <= page_size <= 50 or not 1 <= max_pages <= SUCCESS_LIMIT:
            raise ValueError
        if (
            min(max_items, max_attempts, max_total_attempts) < 1
            or max_attempts > 3
            or max_attempts > max_total_attempts
            or max_items > page_size * max_pages
            or max_total_attempts > max_pages * max_attempts
        ):
            raise ValueError
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError
        if isinstance(retry_delay, bool) or not isinstance(retry_delay, (int, float)) or not 0 <= retry_delay <= 30:
            raise ValueError
    except (TypeError, ValueError):
        raise CollectorError("approved request limits are invalid") from None

    gates = inputs.get("gates")
    if not isinstance(gates, dict):
        raise CollectorError("execution gates are missing")
    for gate in REQUIRED_GATES:
        evidence = gates.get(gate)
        if not isinstance(evidence, dict) or evidence.get("verified") is not True or not isinstance(evidence.get("evidence"), str) or not evidence["evidence"].strip():
            raise CollectorError("execution gate is not verified: " + gate)
    return inputs


def _script_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def validate_review(review):
    if not isinstance(review, dict) or review.get("ticket") != TICKET or review.get("result") != "approved":
        raise CollectorError("independent budget/secret review is missing or not approved")
    if review.get("collector_sha256") != _script_sha256():
        raise CollectorError("independent review does not match this collector version")
    checks = review.get("checks")
    if not isinstance(checks, list) or not REQUIRED_REVIEW_CHECKS.issubset(set(checks)):
        raise CollectorError("independent review is missing required control checks")


def _read_json_file(path, description):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CollectorError(description + " is missing or unreadable") from None


def _load_credentials():
    try:
        metadata = CREDENTIALS_FILE.stat()
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise CollectorError("credential file permissions are unsafe")
        values = {}
        for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip("\"'")
            if name in {"TREG_TOKEN", "TREG_ORG"}:
                if name in values:
                    raise CollectorError("credential file contains duplicate entries")
                values[name] = value
    except CollectorError:
        raise
    except OSError:
        raise CollectorError("approved credential file is unavailable") from None
    if not values.get("TREG_TOKEN"):
        raise CollectorError("Treg token is missing")
    return values["TREG_TOKEN"], values.get("TREG_ORG")


def _redact(value, secrets=()):
    if not isinstance(value, str):
        return value
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = TOKEN_PATTERN.sub("Bearer [REDACTED]", value)
    return KEY_VALUE_SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)


def _safe_header(headers, name, secrets):
    value = headers.get(name)
    return _redact(str(value), secrets) if value is not None else None


def _micro_header(headers):
    raw = headers.get("X-Treg-Cost-Micro")
    if raw is None or not re.fullmatch(r"\d+", str(raw).strip()):
        return None
    return int(raw)


def _safe_response_item(item, secrets, start_epoch, end_epoch):
    if not isinstance(item, dict):
        return None, "item_not_object"
    item_id = item.get("id")
    text = item.get("text")
    created = item.get("createdUtc")
    if not isinstance(item_id, (str, int)) or isinstance(item_id, bool) or not isinstance(text, str):
        return None, "missing_id_or_text"
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return None, "missing_timestamp"
    if not start_epoch <= created < end_epoch:
        return None, "out_of_window"
    safe = {
        "id": _redact(str(item_id), secrets),
        "text": _redact(text, secrets),
        "createdUtc": created,
    }
    for key in ("url", "conversationId", "inReplyToId"):
        if isinstance(item.get(key), (str, int)) and not isinstance(item.get(key), bool):
            safe[key] = _redact(str(item[key]), secrets)
    if isinstance(item.get("isReply"), bool):
        safe["isReply"] = item["isReply"]
    return safe, None


def _extract_response(body, inputs, secrets, requested_limit, expected_source_id=None):
    if not isinstance(body, dict):
        return None, {"errors": ["response_not_object"]}
    root = body.get("result", body)
    if not isinstance(root, dict):
        return None, {"errors": ["result_not_object"]}
    output = root.get("output", root)
    if not isinstance(output, dict):
        return None, {"errors": ["output_not_object"]}
    data = output.get("data", output)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None, {"errors": ["items_missing_or_invalid"]}
    if len(data["items"]) > requested_limit:
        return None, {"errors": ["response_page_limit_exceeded"]}
    found = output.get("found")
    if not isinstance(found, bool):
        found = root.get("found")
    if not isinstance(found, bool):
        return None, {"errors": ["found_flag_missing_or_invalid"]}
    source = root.get("source")
    source_id = source.get("id") if isinstance(source, dict) else None
    provider = root.get("provider")
    if not isinstance(provider, str) or provider.casefold() != "anyapi":
        return None, {"errors": ["provider_identity_mismatch"]}
    if not isinstance(source_id, str) or not source_id:
        return None, {"errors": ["served_source_identity_missing"]}
    if expected_source_id is not None and source_id != expected_source_id:
        return None, {
            "errors": ["served_source_changed"],
            "served_source_id": _redact(source_id, secrets),
        }

    start_epoch = _date_epoch(inputs["window"]["since"])
    end_epoch = _date_epoch(inputs["window"]["until"])
    safe_items = []
    errors = []
    out_of_window = 0
    invalid_items = 0
    for item in data["items"]:
        safe_item, error = _safe_response_item(item, secrets, start_epoch, end_epoch)
        if error == "out_of_window":
            out_of_window += 1
        elif error:
            invalid_items += 1
            errors.append(error)
        elif safe_item:
            safe_items.append(safe_item)
    cursor_present = "nextCursor" in data
    cursor = data.get("nextCursor") if cursor_present else None
    if cursor is not None and not isinstance(cursor, str):
        return None, {"errors": ["next_cursor_invalid"]}
    if cursor == "":
        return None, {"errors": ["next_cursor_invalid"]}
    next_cursor = _redact(cursor, secrets) if isinstance(cursor, str) else None
    if isinstance(cursor, str) and next_cursor != cursor:
        return None, {"errors": ["next_cursor_redacted"]}
    source_name = source.get("name") if isinstance(source, dict) else None
    reported_cost = root.get("costUsd")
    response = {
        "provider": _redact(provider, secrets) if isinstance(provider, str) else None,
        "source": {"id": _redact(source_id, secrets), "name": _redact(source_name, secrets) if isinstance(source_name, str) else None},
        "found": found,
        "items": safe_items,
        "next_cursor": next_cursor,
        "next_cursor_present": cursor_present,
        "provider_reported_cost_usd": reported_cost if isinstance(reported_cost, (int, float)) and not isinstance(reported_cost, bool) else None,
        "replayed": root.get("replayed") if isinstance(root.get("replayed"), bool) else None,
        "reason": _redact(output.get("reason", root.get("reason")), secrets)
        if isinstance(output.get("reason", root.get("reason")), str)
        else None,
    }
    validation = {
        "errors": errors,
        "invalid_item_count": invalid_items,
        "out_of_window_count": out_of_window,
        "evidence_completeness": "validated_items" if safe_items else "empty_success",
    }
    return response, validation


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _safe_recording(recording_dir, record):
    recording_id = uuid.uuid4().hex
    record["recording_id"] = recording_id
    record["recorded_at_epoch"] = int(time.time())
    record["expires_at_epoch"] = record["recorded_at_epoch"] + RETENTION_SECONDS
    path = Path(recording_dir) / (recording_id + ".json")
    _private_json(path, record)
    return recording_id


def _update_recording(recording_dir, record):
    path = Path(recording_dir) / (record["recording_id"] + ".json")
    _private_json(path, record)


def _clean_expired(recording_dir, now=None):
    now = int(time.time() if now is None else now)
    _safe_mkdir(Path(recording_dir))
    for path in Path(recording_dir).glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            recorded_at = record.get("recorded_at_epoch") if isinstance(record, dict) else None
            expires_at = record.get("expires_at_epoch") if isinstance(record, dict) else None
            expired = (
                not isinstance(recorded_at, int)
                or isinstance(recorded_at, bool)
                or not isinstance(expires_at, int)
                or isinstance(expires_at, bool)
                or expires_at != recorded_at + RETENTION_SECONDS
                or now >= expires_at
            )
            if expired:
                path.unlink()
        except (OSError, json.JSONDecodeError):
            continue


def replay_recording(path, now=None):
    path = Path(path)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CollectorError("recording is missing or unreadable") from None
    if mode & 0o077:
        raise CollectorError("recording permissions are unsafe")
    current = int(time.time() if now is None else now)
    recorded_at = record.get("recorded_at_epoch") if isinstance(record, dict) else None
    expires_at = record.get("expires_at_epoch") if isinstance(record, dict) else None
    if (
        not isinstance(recorded_at, int)
        or isinstance(recorded_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or expires_at != recorded_at + RETENTION_SECONDS
        or current >= expires_at
        or recorded_at > current + 60
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise CollectorError("recording expired or has invalid retention metadata")
    record["source"] = "offline_recording"
    return record


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _response, _code, _message, _headers, _new_url):
        return None


def _http_request(url, data, headers, timeout):
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        return error


def _attempt_record(attempt_id, request, max_cost):
    return {
        "id": attempt_id,
        "status": "reserved",
        "reserved_micro_usd": max_cost,
        "request_sha256": _hash(json.dumps(request, sort_keys=True).encode()),
        "cursor_sha256": _hash(request["cursor"].encode()) if request.get("cursor") else None,
        "created_at_epoch": int(time.time()),
    }


def _summary(state, stop_reason, run_id, pages, item_count, pagination):
    return {
        "ticket": TICKET,
        "run_id": run_id,
        "successful_requests": state["successful_requests"],
        "charged_micro_usd": state["charged_micro_usd"],
        "attempt_count": state["attempt_count"],
        "pages_this_run": pages,
        "items_this_run": item_count,
        "stop_reason": stop_reason,
        "coverage": {
            "pagination": pagination,
            "conversation": "search/reply samples only; complete conversation coverage not established",
        },
    }


def collect(inputs, state_dir=DEFAULT_STATE_DIR, recording_dir=None, token=None, org=None, transport=None, credential_loader=None):
    inputs = validate_approval(inputs)
    state_dir = Path(state_dir)
    recording_dir = Path(recording_dir) if recording_dir else state_dir / "recordings"
    request_transport = transport or _http_request
    run_id = uuid.uuid4().hex
    pages = 0
    item_count = 0
    seen_ids = set()
    seen_cursors = set()
    parent_span = None
    selected_source_id = None

    with _ticket_lock(state_dir):
        state = load_state(state_dir)
        if any(attempt.get("status") in {"reserved", "unknown"} for attempt in state["attempts"]):
            return _summary(state, "unreconciled_attempt", run_id, pages, item_count, "paused on unknown outcome")
        _clean_expired(recording_dir)
        if state["successful_requests"] >= SUCCESS_LIMIT:
            return _summary(state, "success_limit", run_id, pages, item_count, "success limit reached")
        if state["charged_micro_usd"] >= BUDGET_MICRO_USD:
            return _summary(state, "spend_limit", run_id, pages, item_count, "spend limit reached")
        if state["attempt_count"] >= inputs["limits"]["max_total_attempts"]:
            return _summary(state, "attempt_limit", run_id, pages, item_count, "approved total-attempt limit reached")
        if token is None:
            if credential_loader is None:
                credential_loader = _load_credentials
            token, org = credential_loader()
        if not isinstance(token, str) or not token:
            raise CollectorError("Treg token is missing")

        cursor = None
        query = _redact(
            inputs["query"].strip()
            + " since:" + inputs["window"]["since"]
            + " until:" + inputs["window"]["until"],
            (token, org),
        )
        for _page_index in range(inputs["limits"]["max_pages"]):
            if state["successful_requests"] >= SUCCESS_LIMIT:
                return _summary(state, "success_limit", run_id, pages, item_count, "incomplete at successful-request limit")
            if state["charged_micro_usd"] >= BUDGET_MICRO_USD:
                return _summary(state, "spend_limit", run_id, pages, item_count, "incomplete at spend limit")
            if item_count >= inputs["limits"]["max_items"]:
                return _summary(state, "item_limit", run_id, pages, item_count, "incomplete at item limit")
            if state["attempt_count"] >= inputs["limits"]["max_total_attempts"]:
                return _summary(state, "attempt_limit", run_id, pages, item_count, "incomplete at approved total-attempt limit")

            page_limit = min(inputs["limits"]["page_size"], inputs["limits"]["max_items"] - item_count)
            request_data = {"query": query, "queryType": inputs["query_type"], "limit": page_limit}
            if cursor is not None:
                request_data["cursor"] = cursor
            request_provenance = {
                "method": "POST",
                "endpoint": ENDPOINT,
                "query": query,
                "query_type": inputs["query_type"],
                "window": dict(inputs["window"]),
                "cursor": cursor,
                "limit": page_limit,
                "page_number": pages + 1,
            }
            completed_page = False
            for _attempt_index in range(inputs["limits"]["max_attempts_per_page"]):
                if state["attempt_count"] >= inputs["limits"]["max_total_attempts"]:
                    return _summary(state, "attempt_limit", run_id, pages, item_count, "incomplete at approved total-attempt limit")
                available = BUDGET_MICRO_USD - state["charged_micro_usd"]
                if available <= 0:
                    return _summary(state, "spend_limit", run_id, pages, item_count, "incomplete at spend limit")
                attempt_id = uuid.uuid4().hex
                max_cost = available
                attempt = _attempt_record(attempt_id, request_provenance, max_cost)
                attempt["span_id"] = attempt_id
                attempt["parent_span_id"] = parent_span
                state["attempt_count"] += 1
                state["attempts"].append(attempt)
                _save_state(state_dir, state)

                headers = {
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                    "Content-Type": "application/json",
                    "Idempotency-Key": attempt_id,
                    "User-Agent": "NeedRadar-ticket-9/1",
                    "X-Treg-Route-Max-Cost": f"{max_cost / 1_000_000:.6f}",
                    "X-Treg-Token": token,
                }
                if org:
                    headers["X-Treg-Org"] = org
                try:
                    response_obj = request_transport(
                        ENDPOINT,
                        json.dumps(request_data, separators=(",", ":")).encode(),
                        headers,
                        inputs["limits"]["timeout_seconds"],
                    )
                    status = getattr(response_obj, "status", getattr(response_obj, "code", None))
                    response_headers = response_obj.headers
                    response_bytes = response_obj.read()
                except Exception:
                    attempt["status"] = "unknown"
                    attempt["failure"] = "transport outcome unknown"
                    attempt["reserved_micro_usd"] = max_cost
                    _safe_recording(recording_dir, {
                        "schema_version": 1,
                        "ticket": TICKET,
                        "run_id": run_id,
                        "span_id": attempt_id,
                        "parent_span_id": parent_span,
                        "approval_id": _redact(inputs["approval_id"], (token, org)),
                        "approved_provider": inputs["provider"],
                        "provider_route": ROUTE,
                        "resolved_limits": dict(inputs["limits"]),
                        "request": request_provenance,
                        "request_headers": {key: value for key, value in headers.items() if key != "X-Treg-Token" and key != "X-Treg-Org"},
                        "response": {"http_status": None, "failure": "transport outcome unknown"},
                        "billing": {"charged_micro_usd": None, "reserved_micro_usd": max_cost},
                        "validation": {"errors": ["unknown_transport_outcome"]},
                        "coverage": {"pagination": "unknown", "conversation": "search/reply samples only; complete conversation coverage not established"},
                    })
                    _save_state(state_dir, state)
                    return _summary(state, "unknown_charge_or_outcome", run_id, pages, item_count, "paused on unknown transport outcome")

                if status is None:
                    attempt["status"] = "unknown"
                    attempt["failure"] = "response status missing"
                    _save_state(state_dir, state)
                    return _summary(state, "unknown_charge_or_outcome", run_id, pages, item_count, "paused on unknown response")
                cost = _micro_header(response_headers)
                call_id = _safe_header(response_headers, "X-Treg-Call-Id", (token, org))
                try:
                    body = json.loads(response_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = None
                response_hash = _hash(response_bytes)
                response_data = None
                validation = {"errors": ["http_error"], "evidence_completeness": "not_validated"}
                if 200 <= status < 300 and body is not None:
                    response_data, validation = _extract_response(
                        body, inputs, (token, org), page_limit, selected_source_id
                    )
                elif 200 <= status < 300:
                    validation = {"errors": ["invalid_json"], "evidence_completeness": "invalid_response"}
                served_via = _safe_header(response_headers, "X-Treg-Served-Via", (token, org))
                provider_overflow = isinstance(served_via, str) and served_via.startswith("overflow:")
                if provider_overflow:
                    response_data = None
                    validation = {"errors": ["provider_overflow"]}

                safe_body = response_data if response_data is not None else None
                provider_reported_cost = None
                if isinstance(body, dict):
                    root = body.get("result", body)
                    if isinstance(root, dict) and isinstance(root.get("costUsd"), (int, float)):
                        provider_reported_cost = root["costUsd"]
                page_record = {
                    "schema_version": 1,
                    "ticket": TICKET,
                    "run_id": run_id,
                    "span_id": attempt_id,
                    "parent_span_id": parent_span,
                    "approval_id": _redact(inputs["approval_id"], (token, org)),
                    "approved_provider": inputs["provider"],
                    "provider_route": ROUTE,
                    "resolved_limits": dict(inputs["limits"]),
                    "request": request_provenance,
                    "request_headers": {key: value for key, value in headers.items() if key not in {"X-Treg-Token", "X-Treg-Org"}},
                    "response": {
                        "http_status": status,
                        "body_sha256": response_hash,
                        "body": safe_body,
                        "provider_reported_cost_usd": provider_reported_cost,
                    },
                    "billing": {
                        "header": "X-Treg-Cost-Micro",
                        "call_id": call_id,
                        "charged_micro_usd": cost,
                        "reserved_micro_usd": max_cost,
                        "idempotency_replay": _safe_header(response_headers, "X-Treg-Idempotent-Replay", (token, org)),
                        "served_via": served_via,
                        "cache": _safe_header(response_headers, "X-Treg-Cache", (token, org)),
                        "fetched_at": _safe_header(response_headers, "X-Treg-Fetched-At", (token, org)),
                        "age": _safe_header(response_headers, "X-Treg-Age", (token, org)),
                    },
                    "validation": validation,
                    "coverage": {"pagination": "pending", "conversation": "search/reply samples only; complete conversation coverage not established"},
                }
                recording_id = _safe_recording(recording_dir, page_record)
                attempt["recording_id"] = recording_id
                attempt["http_status"] = status
                attempt["call_id"] = call_id
                attempt["response_sha256"] = response_hash
                if cost is None:
                    attempt["status"] = "unknown"
                    attempt["failure"] = "billing header missing or invalid"
                    attempt["reserved_micro_usd"] = max_cost
                    page_record["validation"] = {"errors": ["billing_header_missing_or_invalid"]}
                    page_record["coverage"]["pagination"] = "unknown because billing could not be reconciled"
                    _update_recording(recording_dir, page_record)
                    if 200 <= status < 300:
                        state["successful_requests"] += 1
                        pages += 1
                    _save_state(state_dir, state)
                    return _summary(state, "unknown_charge_or_outcome", run_id, pages, item_count, "paused on unknown charge")

                attempt["cost_micro_usd"] = cost
                attempt["reserved_micro_usd"] = 0
                state["charged_micro_usd"] += cost
                if 200 <= status < 300:
                    state["successful_requests"] += 1
                    pages += 1
                if cost > max_cost:
                    attempt["status"] = "cap_violation"
                    _save_state(state_dir, state)
                    return _summary(state, "reported_cost_exceeds_reservation", run_id, pages, item_count, "paused after reported cap violation")
                attempt["status"] = "success" if 200 <= status < 300 else "failed"
                _save_state(state_dir, state)

                if provider_overflow:
                    page_record["coverage"]["pagination"] = "not continued after Treg overflow response"
                    _update_recording(recording_dir, page_record)
                    return _summary(state, "provider_overflow", run_id, pages, item_count, "stopped after Treg overflow response")
                if status in {401, 403}:
                    page_record["coverage"]["pagination"] = "not continued after authentication/permission failure"
                    _update_recording(recording_dir, page_record)
                    return _summary(state, "authentication_failure", run_id, pages, item_count, "incomplete after authentication/permission failure")
                if not 200 <= status < 300:
                    if status in TRANSIENT_STATUSES and _attempt_index + 1 < inputs["limits"]["max_attempts_per_page"]:
                        if state["charged_micro_usd"] >= BUDGET_MICRO_USD:
                            page_record["coverage"]["pagination"] = "not continued after spend limit on transient failure"
                            _update_recording(recording_dir, page_record)
                            return _summary(state, "spend_limit", run_id, pages, item_count, "incomplete after charged transient failure")
                        delay = inputs["limits"]["retry_delay_seconds"]
                        if delay:
                            time.sleep(delay)
                        page_record["coverage"]["pagination"] = "bounded transient retry scheduled"
                        _update_recording(recording_dir, page_record)
                        continue
                    page_record["coverage"]["pagination"] = "not continued after request failure"
                    _update_recording(recording_dir, page_record)
                    return _summary(state, "request_failure", run_id, pages, item_count, "incomplete after request failure")
                if response_data is None:
                    page_record["coverage"]["pagination"] = "not continued after response validation failure"
                    _update_recording(recording_dir, page_record)
                    errors = validation.get("errors", [])
                    stop_reason = (
                        "provider_overflow"
                        if "provider_overflow" in errors
                        else "provider_page_limit_exceeded"
                        if "response_page_limit_exceeded" in errors
                        else "provider_source_changed"
                        if "served_source_changed" in errors
                        else "response_validation_failure"
                    )
                    return _summary(state, stop_reason, run_id, pages, item_count, "incomplete after response validation failure")
                if validation.get("errors"):
                    page_record["coverage"]["pagination"] = "not continued after response validation failure"
                    _update_recording(recording_dir, page_record)
                    return _summary(state, "response_validation_failure", run_id, pages, item_count, "incomplete after response validation failure")
                source_id = response_data["source"]["id"]
                if selected_source_id is None:
                    selected_source_id = source_id
                if response_data["next_cursor"]:
                    if response_data["next_cursor"] in seen_cursors:
                        page_record["coverage"]["pagination"] = "cursor repeated; pagination stopped"
                        _update_recording(recording_dir, page_record)
                        return _summary(state, "cursor_cycle", run_id, pages, item_count, "incomplete because cursor repeated")
                    seen_cursors.add(response_data["next_cursor"])
                for item in response_data["items"]:
                    if item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        item_count += 1
                if state["successful_requests"] >= SUCCESS_LIMIT:
                    page_record["coverage"]["pagination"] = "stopped at successful-request ceiling; further pagination not requested"
                    _update_recording(recording_dir, page_record)
                    return _summary(state, "success_limit", run_id, pages, item_count, "incomplete at successful-request limit")
                if state["charged_micro_usd"] >= BUDGET_MICRO_USD:
                    page_record["coverage"]["pagination"] = "stopped at spend ceiling; further pagination not requested"
                    _update_recording(recording_dir, page_record)
                    return _summary(state, "spend_limit", run_id, pages, item_count, "incomplete at spend limit")
                if not response_data["next_cursor"]:
                    if not response_data["next_cursor_present"]:
                        page_record["coverage"]["pagination"] = "cursor field missing; absence does not establish exhaustion"
                        _update_recording(recording_dir, page_record)
                        return _summary(state, "cursor_missing", run_id, pages, item_count, page_record["coverage"]["pagination"])
                    if not response_data["items"]:
                        page_record["coverage"]["pagination"] = "empty result with explicit null cursor; source completeness not established"
                        _update_recording(recording_dir, page_record)
                        return _summary(state, "empty_results", run_id, pages, item_count, "empty result; source completeness not established")
                    page_record["coverage"]["pagination"] = "provider cursor ended; search completeness not established"
                    _update_recording(recording_dir, page_record)
                    return _summary(state, "provider_no_next_cursor", run_id, pages, item_count, "provider cursor ended; search completeness not established")
                page_record["coverage"]["pagination"] = "next cursor recorded; continuation available in this run"
                _update_recording(recording_dir, page_record)
                cursor = response_data["next_cursor"]
                parent_span = attempt_id
                completed_page = True
                break
            if not completed_page:
                return _summary(state, "retry_limit", run_id, pages, item_count, "incomplete after bounded transient retries")

        return _summary(state, "page_limit", run_id, pages, item_count, "incomplete at approved page limit")


def _public_summary(record):
    response = record.get("response", {})
    body = response.get("body") if isinstance(response, dict) else None
    return {
        "source": "offline_recording",
        "recording_id": record.get("recording_id"),
        "http_status": response.get("http_status") if isinstance(response, dict) else None,
        "item_count": len(body.get("items", [])) if isinstance(body, dict) and isinstance(body.get("items"), list) else 0,
        "validation": record.get("validation"),
        "coverage": record.get("coverage"),
        "network_requests": 0,
    }


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help="perform gated ticket-9 acquisition")
    collect_parser.add_argument("--ticket", required=True, choices=[str(TICKET)])
    collect_parser.add_argument("--approval-file")
    collect_parser.add_argument("--review-file")
    replay_parser = commands.add_parser("replay", help="replay one existing recording offline")
    replay_parser.add_argument("--recording", required=True, type=Path)
    status_parser = commands.add_parser("status", help="show the persistent ticket-9 budget")
    status_parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            if not args.approval_file:
                raise CollectorError("approved input record is required; no request sent")
            if not args.review_file:
                raise CollectorError("independent budget/secret review is required; no request sent")
            inputs = validate_approval(_read_json_file(args.approval_file, "approved input record"))
            validate_review(_read_json_file(args.review_file, "independent review"))
            result = collect(inputs, credential_loader=_load_credentials)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "replay":
            record = replay_recording(args.recording)
            print(json.dumps(_public_summary(record), sort_keys=True))
            return 0
        if args.command == "status":
            state = load_state(args.state_dir)
            print(json.dumps({
                "ticket": TICKET,
                "successful_requests": state["successful_requests"],
                "success_limit": SUCCESS_LIMIT,
                "charged_micro_usd": state["charged_micro_usd"],
                "spend_limit_micro_usd": BUDGET_MICRO_USD,
                "attempt_count": state["attempt_count"],
                "unreconciled_attempts": sum(attempt.get("status") in {"reserved", "unknown"} for attempt in state["attempts"]),
            }, sort_keys=True))
            return 0
    except CollectorError as error:
        parser.exit(2, "collect_x: " + str(error) + "\n")
    except (OSError, ValueError, TypeError):
        parser.exit(2, "collect_x: operation failed safely; inspect private state and recording files\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
