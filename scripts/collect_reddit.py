#!/usr/bin/env python3
import argparse
import contextlib
import datetime as dt
import email.utils
import fcntl
import hashlib
import json
import math
import os
import pathlib
import re
import signal
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass


TICKET = "3"
INCIDENT_REF = "need-radar-issue-3:unknown-outcome-request"
STATE_DEFAULT = pathlib.Path("/home/ubuntu/.local/state/need-radar/ticket-3")
APPROVAL_DEFAULT = STATE_DEFAULT / "approval.json"
CREDENTIALS_PATH = pathlib.Path("/home/ubuntu/projects/need-radar/credentials.env")
LIVE_ACK = "ISSUE-3-REDDIT-2026-10-07-USD-0.25-25-SUCCESSFUL"
MAX_SUCCESSFUL_REQUESTS = 25
MAX_SPEND_MICRO_USD = 250_000
MAX_RETRIES = 2
MAX_RETRY_DELAY_SECONDS = 8
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 30
RETENTION_DAYS = 7
MAX_JSON_DEPTH = 64
MAX_URL_DECODE_ROUNDS = 16
MAX_NESTED_URLS = 8
MAX_SANITIZE_DEPTH = 32
MAX_BILLING_HEADER_DIGITS = 18
FEED_ID = "tikhub.x.reddit-app-fetch-subreddit-feed"
COMMENTS_ID = "scrapecreators.x.v1-reddit-post-comments"
FEED_LANGUAGE_DEFAULT = "en-US"
ROUTES = {
    "feed": {
        "id": FEED_ID,
        "provider": "tikhub",
        "billing_unit": "per_success",
        "max_charge_micro_usd": 1_000,
    },
    "comments": {
        "id": COMMENTS_ID,
        "provider": "scrapecreators",
        "billing_unit": "per_call",
        "max_charge_micro_usd": 1_880,
    },
}
ALLOWED_RESPONSE_HEADERS = {
    "content-type",
    "retry-after",
    "x-treg-cache",
    "x-treg-call-id",
    "x-treg-cost-micro",
    "x-treg-fetched-at",
    "x-treg-idempotent-replay",
    "x-treg-original-cost-micro",
    "x-treg-served-via",
    "x-treg-served-by",
}
SENSITIVE_KEYS = re.compile(
    r"(?:^|[_-])(?:access[_-]?token|refresh[_-]?token|token|api[_-]?key|api[_-]?token|key|private[_-]?key|secret[_-]?key|secret|password|passwd|cookie|auth|authorization|credential|signature)(?:$|[_-])",
    re.IGNORECASE,
)
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "key",
    "password",
    "secret",
    "sig",
    "sign",
    "signature",
    "token",
    "x-api-key",
    "x-amz-credential",
    "x-amz-security-token",
    "x-amz-signature",
    "x-goog-credential",
    "x-goog-signature",
    "x-treg-token",
}
SENSITIVE_ASSIGNMENT_PREFIX = re.compile(
    r'''(?ix)
    (?P<bearer>\bbearer\s+)
    |
    (?<![a-z0-9_])(?P<label>
        proxy-authorization|authorization|cookie|
        api[_-]?(?:key|token)|private[_-]?key|secret[_-]?key|
        access[_-]?token|refresh[_-]?token|token|key|secret|
        auth|client[_-]?secret|password|passwd|credential|signature|sig|
        x-amz-(?:credential|security-token|signature)|
        x-goog-(?:credential|signature)|x-treg-token
    )
    (?:\\?["'])?\s*(?:\\?[:=])\s*
    '''
)
EMBEDDED_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
URL_SCHEME = re.compile(r"https?://", re.IGNORECASE)


class CollectorError(Exception):
    def __init__(self, code, status="failed"):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass
class Credentials:
    token: str
    org: str | None


@dataclass
class HTTPResponse:
    status: int
    headers: dict
    body: bytes
    truncated: bool = False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        return None


class UrllibTransport:
    def request(self, url, headers, timeout):
        if urllib.parse.urlsplit(url).hostname != "treg.to" or not url.startswith("https://"):
            raise CollectorError("request_host_rejected", "blocked")
        request = urllib.request.Request(url, headers=headers, method="GET")
        opener = urllib.request.build_opener(NoRedirect())
        with request_deadline(timeout):
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                return HTTPResponse(
                    response.status,
                    dict(response.headers.items()),
                    body[:MAX_RESPONSE_BYTES],
                    len(body) > MAX_RESPONSE_BYTES,
                )


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def timestamp(value):
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


@contextlib.contextmanager
def request_deadline(seconds):
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        raise CollectorError("request_deadline_unavailable", "blocked")
    if any(signal.getitimer(signal.ITIMER_REAL)):
        raise CollectorError("request_deadline_unavailable", "blocked")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def deadline_expired(_signum, _frame):
        raise TimeoutError("request_deadline_exceeded")

    signal.signal(signal.SIGALRM, deadline_expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def parse_datetime(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp_required")
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    try:
        return parsed.astimezone(dt.timezone.utc)
    except (OverflowError, OSError):
        raise ValueError("timestamp_out_of_range") from None


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise CollectorError("private_directory_permissions_required", "blocked")
    return path


def private_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise CollectorError("private_file_permissions_required", "blocked")
    return True


def repository_path(path):
    root = pathlib.Path(__file__).resolve().parents[1]
    resolved = pathlib.Path(path).resolve()
    roots = {root}
    git_path = root / ".git"
    if git_path.is_dir():
        git_dir = git_path
    else:
        try:
            git_pointer = git_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            git_pointer = ""
        if git_pointer.startswith("gitdir:"):
            git_dir = pathlib.Path(git_pointer.partition(":")[2].strip())
            if not git_dir.is_absolute():
                git_dir = root / git_dir
            git_dir = git_dir.resolve()
        else:
            git_dir = None
    if git_dir is not None:
        common_dir_file = git_dir / "commondir"
        try:
            common_dir = (git_dir / common_dir_file.read_text(encoding="utf-8").strip()).resolve()
        except (OSError, UnicodeError):
            common_dir = git_dir
        roots.add(common_dir.parent)
        worktrees = common_dir / "worktrees"
        if worktrees.is_dir():
            for entry in worktrees.iterdir():
                try:
                    gitfile = pathlib.Path((entry / "gitdir").read_text(encoding="utf-8").strip())
                    if not gitfile.is_absolute():
                        gitfile = entry / gitfile
                    gitfile = gitfile.resolve()
                except (OSError, UnicodeError):
                    continue
                roots.add(gitfile.parent)
    return any(resolved == candidate or candidate in resolved.parents for candidate in roots)


def atomic_json(path, value):
    private_directory(path.parent)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        descriptor, temp_name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, path)
            os.chmod(path, 0o600)
            os.fsync(directory_fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)
    finally:
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)


def redact_assignments(value):
    value = decode_escaped_ascii(value)
    parts = []
    offset = 0
    while match := SENSITIVE_ASSIGNMENT_PREFIX.search(value, offset):
        parts.append(value[offset:match.end()])
        label = (match.group("label") or "").lower()
        start = match.end()
        private_key = re.sub(r"[_-]", "", label) == "privatekey"
        begin = re.compile(
            r"\s*-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----",
            re.IGNORECASE,
        ).match(value, start)
        if private_key and begin:
            end_marker = "-----END " + begin.group(1) + "-----"
            marker_end = value.find(end_marker, begin.end())
            offset = len(value) if marker_end < 0 else marker_end + len(end_marker)
            parts.append("[REDACTED]")
            continue
        unterminated_quote = False
        quoted_value = False
        quote_start = start + 1 if value.startswith((r'\"', r"\'"), start) else start
        if quote_start < len(value) and value[quote_start] in "\"'`":
            quoted_value = True
            quote = value[quote_start]
            escaped_quote = quote_start != start
            limit = len(value)
            end = quote_start + 1
            while end < limit:
                if value[end] == "\\":
                    escape_start = end
                    while end < limit and value[end] == "\\":
                        end += 1
                    slash_count = end - escape_start
                    if end < limit and value[end] == quote:
                        if (
                            escaped_quote and slash_count == 1
                            or not escaped_quote and slash_count % 2 == 0
                        ):
                            end += 1
                            break
                        end += 1
                elif value[end] == quote:
                    end += 1
                    break
                else:
                    end += 1
            else:
                end = len(value)
                unterminated_quote = True
        elif label in {"authorization", "proxy-authorization", "cookie"}:
            line_end = value.find("\n", start)
            end = len(value) if line_end < 0 else line_end
        else:
            end = start
            while end < len(value) and value[end] not in "\r\n ,;&<>":
                end += 1
        if label in {"authorization", "proxy-authorization", "cookie"} and not unterminated_quote:
            line_end = value.find("\n", end if quoted_value else start)
            end = len(value) if line_end < 0 else line_end
            while end < len(value):
                next_line = end + 1
                if next_line >= len(value) or value[next_line] not in " \t":
                    break
                line_end = value.find("\n", next_line)
                end = len(value) if line_end < 0 else line_end
        parts.append("[REDACTED]")
        offset = end
    parts.append(value[offset:])
    return "".join(parts)


def redact_quoted_url_userinfo(value):
    parts = []
    offset = 0
    search_offset = 0
    while match := URL_SCHEME.search(value, search_offset):
        start = match.start()
        scheme_end = match.end()
        quote = None
        escaped = False
        quoted_space = False
        at = None
        index = scheme_end
        while index < len(value):
            character = value[index]
            if quote:
                if character.isspace():
                    quoted_space = True
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
            elif character in "\"'":
                quote = character
            elif character == "@":
                at = index
                break
            elif character in "/?#\r\n\t <>" or character.isspace():
                break
            index += 1
        if quoted_space:
            if at is None:
                parts.extend((value[offset:start], "[REDACTED]"))
                return "".join(parts)
            parts.extend((value[offset:start], value[start:scheme_end], "[REDACTED]@"))
            offset = at + 1
            search_offset = offset
        else:
            if index >= len(value):
                break
            search_offset = index + 1
    if not parts:
        return value
    parts.append(value[offset:])
    return "".join(parts)


def safe_string(value, secrets=(), _depth=0):
    if value is None:
        return None
    if _depth > MAX_NESTED_URLS:
        return "[REDACTED]"
    text = str(value).encode("utf-8", errors="replace").decode("utf-8")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    decoded_parts = []
    offset = 0
    for match in EMBEDDED_URL.finditer(text):
        decoded = decode_url_component(text[offset:match.start()])
        if decoded is None:
            return "[REDACTED]"
        decoded_parts.extend((decoded, match.group(0)))
        offset = match.end()
    decoded = decode_url_component(text[offset:])
    if decoded is None:
        return "[REDACTED]"
    decoded_parts.append(decoded)
    text = "".join(decoded_parts)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = redact_assignments(text)
    text = redact_quoted_url_userinfo(text)
    parts = []
    offset = 0
    for match in EMBEDDED_URL.finditer(text):
        parts.append(safe_text(text[offset:match.start()], secrets, _depth + 1))
        parts.append(safe_url(match.group(0), secrets, _depth + 1))
        offset = match.end()
    parts.append(safe_text(text[offset:], secrets, _depth + 1))
    return "".join(part or "" for part in parts)


def safe_text(value, secrets=(), _depth=0):
    if value is None:
        return None
    if _depth > MAX_NESTED_URLS:
        return "[REDACTED]"
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    decoded = decode_url_component(text)
    if decoded is None:
        return "[REDACTED]"
    for secret in secrets:
        if secret:
            decoded = decoded.replace(secret, "[REDACTED]")
    if EMBEDDED_URL.search(decoded):
        return safe_string(decoded, secrets, _depth + 1)
    return redact_assignments(decoded)


def decode_url_component(value):
    for _ in range(MAX_URL_DECODE_ROUNDS):
        decoded = urllib.parse.unquote(value)
        if decoded == value:
            return value
        value = decoded
    if re.search(r"%[0-9a-f]{2}", value, re.IGNORECASE):
        return None
    return value


def decode_escaped_ascii(value):
    return re.sub(
        r"\\u([0-9a-f]{4})",
        lambda match: chr(int(match.group(1), 16)) if int(match.group(1), 16) < 128 else match.group(0),
        value,
        flags=re.IGNORECASE,
    )


def safe_url(value, secrets=(), _depth=0):
    if not isinstance(value, str):
        return value
    if _depth > MAX_NESTED_URLS:
        return "[REDACTED_URL]"
    if any(ord(character) <= 0x20 or ord(character) == 0x7f for character in value):
        return "[REDACTED_URL]"
    if re.search(r"%(?![0-9a-f]{2})", value, re.IGNORECASE):
        return "[REDACTED_URL]"
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return "[REDACTED_URL]"
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        return "[REDACTED_URL]"
    try:
        hostname = decode_url_component(parsed.hostname or "")
        if hostname is None:
            return "[REDACTED_URL]"
        host = urllib.parse.quote(safe_string(hostname, secrets, _depth + 1), safe=".-:")
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        if parsed.port:
            host += ":" + str(parsed.port)
        if parsed.username is not None or parsed.password is not None:
            host = "[REDACTED]@" + host
    except ValueError:
        return "[REDACTED_URL]"
    query = []
    for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        decoded_key = decode_url_component(key)
        decoded_item = decode_url_component(item)
        if decoded_key is None or decoded_item is None:
            return "[REDACTED_URL]"
        if decoded_key.strip().lower() in SENSITIVE_QUERY_KEYS or SENSITIVE_KEYS.search(decoded_key):
            item = "[REDACTED]"
        else:
            if EMBEDDED_URL.search(decoded_item):
                item = "[REDACTED]"
            else:
                item = safe_string(decoded_item, secrets, _depth + 1)
        key = safe_string(decoded_key, secrets, _depth + 1)
        query.append((key, item))
    decoded_path = decode_url_component(parsed.path)
    if decoded_path is None:
        return "[REDACTED_URL]"
    return urllib.parse.urlunsplit((
        parsed.scheme,
        host,
        urllib.parse.quote(
            safe_string(decoded_path, secrets, _depth + 1),
            safe="/:@-._~!$&'()*+,;=",
        ),
        urllib.parse.urlencode(query, doseq=True),
        "[REDACTED]" if parsed.fragment else "",
    ))


def sanitize(value, secrets=(), key="", _depth=0):
    if _depth > MAX_SANITIZE_DEPTH:
        raise ValueError("sanitization_depth_exceeded")
    if SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            original = str(name)
            decoded_name = decode_url_component(original)
            if decoded_name is not None:
                for _ in range(MAX_URL_DECODE_ROUNDS):
                    escaped_name = decode_escaped_ascii(decoded_name)
                    if escaped_name == decoded_name:
                        break
                    decoded_name = decode_url_component(escaped_name)
            invalid_encoding = decoded_name is None or re.search(
                r"%(?![0-9a-f]{2})|\\u[0-9a-f]{4}", decoded_name, re.IGNORECASE
            )
            sensitive_name = not invalid_encoding and SENSITIVE_KEYS.search(decoded_name)
            cleaned = (
                "[REDACTED_KEY]"
                if invalid_encoding or sensitive_name
                else safe_string(decoded_name, secrets)
            )
            unique = cleaned
            index = 2
            while unique in result:
                unique = f"{cleaned}#{index}"
                index += 1
            result[unique] = "[REDACTED]" if invalid_encoding or sensitive_name else sanitize(
                item, secrets, decoded_name, _depth + 1
            )
        return result
    if isinstance(value, list):
        return [sanitize(item, secrets, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        try:
            nested = parse_json(value)
        except json.JSONDecodeError:
            nested = None
        except ValueError:
            return "[REDACTED_INVALID_JSON]"
        if isinstance(nested, (dict, list)):
            return json.dumps(
                sanitize(nested, secrets, _depth=_depth + 1),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        if key.lower() in {"url", "permalink", "link", "uri", "cache_url"} or key.lower().endswith("_url"):
            return safe_url(value, secrets)
        return safe_string(value, secrets)
    return value


def sanitize_body(body, secrets=()):
    text = body.decode("utf-8", errors="replace")
    try:
        value = parse_json(text)
    except json.JSONDecodeError:
        return safe_string(text, secrets)
    except RecursionError:
        return "[REDACTED_INVALID_JSON]"
    except ValueError:
        try:
            value = parse_json(text, parse_int=str)
        except (json.JSONDecodeError, ValueError, RecursionError):
            return "[REDACTED_INVALID_JSON]"
        try:
            return json.dumps(
                sanitize(value, secrets), ensure_ascii=True, sort_keys=True, separators=(",", ":")
            )
        except (RecursionError, ValueError):
            return "[REDACTED_INVALID_JSON]"
    try:
        return sanitize(value, secrets)
    except (RecursionError, ValueError):
        return "[REDACTED_INVALID_JSON]"


def response_headers(headers, secrets=()):
    result = {}
    for key, value in headers.items():
        normalized = str(key).lower()
        if normalized in ALLOWED_RESPONSE_HEADERS:
            if normalized == "x-treg-cost-micro":
                cost = billing_cost(value)
                if cost is None:
                    continue
                value = str(cost)
            result[normalized] = safe_string(value, secrets)
    return result


def billing_cost(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if len(normalized) > MAX_BILLING_HEADER_DIGITS or not re.fullmatch(r"[0-9]+", normalized):
        return None
    try:
        return int(normalized)
    except (ValueError, OverflowError):
        return None


def evidence_present(value):
    return isinstance(value, str) and bool(value.strip())


def verify_item(value, verified_key="verified", evidence_key="evidence"):
    return isinstance(value, dict) and value.get(verified_key) is True and evidence_present(value.get(evidence_key))


def validate_approval(approval, subreddits, start, end):
    errors = []
    if not isinstance(approval, dict):
        return ["approval_manifest_invalid"]
    if approval.get("ticket") != TICKET or approval.get("source") != "reddit":
        errors.append("approval_ticket_or_source_mismatch")
    if not verify_item(approval.get("source_permission")):
        errors.append("source_permission_unverified")
    approved_seeds = approval.get("seeds")
    if not verify_item(approved_seeds) or approved_seeds.get("values") != subreddits:
        errors.append("approved_seed_mismatch_or_unverified")
    approved_window = approval.get("window")
    if (
        not verify_item(approved_window)
        or approved_window.get("start") != start
        or approved_window.get("end") != end
    ):
        errors.append("approved_window_mismatch_or_unverified")
    routes = approval.get("routes")
    if not isinstance(routes, dict):
        routes = {}
    for name, expected in ROUTES.items():
        route = routes.get(name)
        if not isinstance(route, dict):
            errors.append(name + "_route_approval_missing")
            continue
        if route.get("id") != expected["id"]:
            errors.append(name + "_route_id_mismatch")
        for field, evidence in (
            ("schema_verified", "schema_evidence"),
            ("permission_verified", "permission_evidence"),
            ("billing_verified", "billing_evidence"),
            ("max_charge_verified", "max_charge_evidence"),
            ("cost_cap_verified", "cost_cap_evidence"),
        ):
            if route.get(field) is not True or not evidence_present(route.get(evidence)):
                errors.append(name + "_" + field.replace("_verified", "_unverified"))
        if route.get("billing_unit") != expected["billing_unit"]:
            errors.append(name + "_billing_unit_mismatch")
        if route.get("max_charge_micro_usd") != expected["max_charge_micro_usd"]:
            errors.append(name + "_max_charge_mismatch")
    account = approval.get("provider_account")
    if (
        not isinstance(account, dict)
        or account.get("eligibility_verified") is not True
        or not evidence_present(account.get("evidence"))
        or account.get("credential_mode") != "treg_managed"
        or account.get("token_type") not in {"per_org", "identity"}
        or account.get("overflow_disabled") is not True
        or account.get("fallback_disabled") is not True
        or account.get("own_provider_credentials_absent") is not True
    ):
        errors.append("provider_account_or_route_controls_unverified")
    limits = approval.get("limits")
    if (
        not isinstance(limits, dict)
        or limits.get("successful_requests") != MAX_SUCCESSFUL_REQUESTS
        or limits.get("spend_micro_usd") != MAX_SPEND_MICRO_USD
        or not verify_item(limits)
    ):
        errors.append("approved_ticket_limits_mismatch_or_unverified")
    retention = approval.get("retention")
    if (
        not isinstance(retention, dict)
        or retention.get("recording_permitted") is not True
        or type(retention.get("days")) is not int
        or not 1 <= retention["days"] <= RETENTION_DAYS
        or retention.get("derived_removal_verified") is not True
        or not evidence_present(retention.get("evidence"))
    ):
        errors.append("recording_retention_unverified")
    return errors


def load_approval(path):
    if path.is_symlink() or not private_file(path):
        raise CollectorError("private_approval_manifest_required", "blocked")
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, UnicodeError, ValueError):
        raise CollectorError("approval_manifest_unreadable", "blocked") from None


def load_credentials(token_type):
    try:
        info = CREDENTIALS_PATH.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
        ):
            raise ValueError
        values = {}
        for line in CREDENTIALS_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            name = name.strip()
            if name in {"TREG_TOKEN", "TREG_ORG"}:
                if name in values:
                    raise ValueError
                values[name] = value.strip().strip("\"'")
        token = values.get("TREG_TOKEN")
        org = values.get("TREG_ORG") if token_type == "identity" else None
        if not token or (token_type == "identity" and not org):
            raise ValueError
        return Credentials(token, org)
    except (OSError, UnicodeError, ValueError):
        raise CollectorError("approved_runtime_credentials_unavailable", "blocked") from None


def default_state():
    return {
        "version": 1,
        "ticket": TICKET,
        "successful_requests": 0,
        "attempts": 0,
        "spent_micro_usd": 0,
        "reserved_micro_usd": 0,
        "reserved_success_slots": 0,
        "pending": None,
        "limit_breach": False,
        "incident_hold": {"reference": INCIDENT_REF, "status": "unresolved", "outcome": "unknown", "charge_micro_usd": None},
        "reconciliations": [],
    }


def expire_pending_parameters(state, now):
    pending = state.get("pending")
    if not isinstance(pending, dict) or "request_parameters" not in pending:
        return False
    try:
        expires = parse_datetime(pending.get("request_parameters_expires_at"))
    except (TypeError, ValueError):
        expires = now
    if expires <= now:
        del pending["request_parameters"]
        return True
    return False


def load_state(path):
    if not path.exists():
        return default_state(), True
    if path.is_symlink() or not private_file(path):
        raise CollectorError("private_budget_state_required", "blocked")
    try:
        with path.open(encoding="utf-8") as source:
            state = json.load(source)
    except (OSError, UnicodeError, ValueError):
        raise CollectorError("budget_state_unreadable", "blocked") from None
    required = default_state()
    if not isinstance(state, dict) or any(key not in state for key in required if key != "incident_hold"):
        raise CollectorError("budget_state_invalid", "blocked")
    migrated = "incident_hold" not in state
    if migrated:
        state["incident_hold"] = required["incident_hold"]
    if state.get("version") != 1 or state.get("ticket") != TICKET or type(state.get("limit_breach")) is not bool:
        raise CollectorError("budget_state_invalid", "blocked")
    for field in ("successful_requests", "attempts", "spent_micro_usd", "reserved_micro_usd", "reserved_success_slots"):
        if type(state.get(field)) is not int or state[field] < 0:
            raise CollectorError("budget_state_invalid", "blocked")
    if state["successful_requests"] + state["reserved_success_slots"] > MAX_SUCCESSFUL_REQUESTS:
        raise CollectorError("budget_state_exceeds_limit", "blocked")
    if state["spent_micro_usd"] + state["reserved_micro_usd"] > MAX_SPEND_MICRO_USD and state.get("limit_breach") is not True:
        raise CollectorError("budget_state_exceeds_limit", "blocked")
    incident = state.get("incident_hold")
    if (
        not isinstance(incident, dict)
        or incident.get("reference") != INCIDENT_REF
        or incident.get("status") not in {"unresolved", "reconciled"}
    ):
        raise CollectorError("budget_state_incident_hold_invalid", "blocked")
    if incident["status"] == "unresolved" and (
        incident.get("outcome") != "unknown" or incident.get("charge_micro_usd") is not None
    ):
        raise CollectorError("budget_state_incident_hold_invalid", "blocked")
    if not isinstance(state.get("reconciliations"), list):
        raise CollectorError("budget_state_invalid", "blocked")
    pending = state.get("pending")
    if pending is not None:
        if not isinstance(pending, dict):
            raise CollectorError("budget_state_invalid", "blocked")
        pending_reserved = 0 if pending.get("charge_known") and state["reserved_micro_usd"] == 0 else pending.get("reserved_micro_usd")
        if (
            pending.get("route") not in ROUTES
            or type(pending.get("reserved_micro_usd")) is not int
            or pending["reserved_micro_usd"] <= 0
            or pending_reserved != state["reserved_micro_usd"]
            or pending.get("success_slot_reserved") is not (state["reserved_success_slots"] == 1)
            or state["reserved_success_slots"] not in (0, 1)
        ):
            raise CollectorError("budget_state_invalid", "blocked")
    return state, migrated


@contextlib.contextmanager
def locked_state(directory, now=None):
    private_directory(directory)
    lock_path = directory / "state.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        expire_pending_files(directory)
        state_path = directory / "state.json"
        state, migrated = load_state(state_path)
        changed = expire_pending_parameters(state, now or utc_now())
        if migrated or changed:
            atomic_json(state_path, state)
        yield state_path, state
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def require_incident_reconciled(state):
    incident = state.get("incident_hold")
    try:
        parse_datetime(incident.get("reconciled_at"))
    except (AttributeError, TypeError, ValueError):
        raise CollectorError("historical_unknown_outcome_hold", "blocked") from None
    if (
        not isinstance(incident, dict)
        or incident.get("reference") != INCIDENT_REF
        or incident.get("status") != "reconciled"
        or incident.get("outcome") not in {"success", "failed"}
        or type(incident.get("charge_micro_usd")) is not int
        or incident["charge_micro_usd"] < 0
        or not evidence_present(incident.get("evidence_ref"))
    ):
        raise CollectorError("historical_unknown_outcome_hold", "blocked")
    if (
        state["spent_micro_usd"] < incident["charge_micro_usd"]
        or (incident["outcome"] == "success" and state["successful_requests"] == 0)
    ):
        raise CollectorError("historical_incident_not_in_budget_ledger", "blocked")


def cost_text(micro_usd):
    return f"{micro_usd / 1_000_000:.6f}"


def retry_delay(value, fallback, now):
    if value is None:
        return fallback
    text = str(value).strip()
    if re.fullmatch(r"[0-9]+", text):
        normalized = text.lstrip("0") or "0"
        maximum = str(MAX_RETRY_DELAY_SECONDS)
        if len(normalized) > len(maximum) or (
            len(normalized) == len(maximum) and normalized > maximum
        ):
            raise CollectorError("retry_after_exceeds_local_bound", "failed")
        delay = int(normalized)
    else:
        try:
            retry_at = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            raise CollectorError("retry_after_invalid", "failed") from None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=dt.timezone.utc)
        delay = max(0, math.ceil((retry_at - now).total_seconds()))
    if delay > MAX_RETRY_DELAY_SECONDS:
        raise CollectorError("retry_after_exceeds_local_bound", "failed")
    return delay


def header(headers, name):
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return value
    return None


def call_url(route_id, params):
    query = urllib.parse.urlencode(params, doseq=True)
    return "https://treg.to/call/" + route_id + ("?" + query if query else "")


def parse_json(body, *, parse_int=int):
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    if not isinstance(text, str):
        raise TypeError("json_text_required")
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("json_nesting_limit_exceeded")
        elif character in "]}":
            depth = max(0, depth - 1)
    try:
        return json.loads(text, parse_int=parse_int)
    except RecursionError:
        raise ValueError("json_nesting_limit_exceeded") from None


def feed_data(payload):
    if not isinstance(payload, dict):
        raise ValueError("feed_body_not_object")
    if "code" in payload and payload["code"] not in (0, 200, "0", "200"):
        raise ValueError("feed_api_error")
    data = payload.get("data")
    for _ in range(2):
        if isinstance(data, str):
            data = parse_json(data)
    if not isinstance(data, dict):
        raise ValueError("feed_data_schema_unknown")
    elements = data.get("subredditV3", {}).get("elements") if isinstance(data.get("subredditV3"), dict) else None
    if not isinstance(elements, dict) or not isinstance(elements.get("edges"), list):
        raise ValueError("feed_elements_schema_unknown")
    edges = elements["edges"]
    posts = []
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("node"), dict):
            raise ValueError("feed_edge_schema_unknown")
        posts.append(edge["node"])
    page = elements.get("pageInfo")
    if not isinstance(page, dict):
        return posts, None, None
    return posts, page.get("hasNextPage"), page.get("endCursor")


def feed_post_times(posts):
    try:
        return [post_time(post) for post in posts]
    except ValueError:
        raise ValueError("feed_response_payload_invalid") from None


def acquisition_succeeded(route_name, status, payload):
    if not 200 <= status < 300:
        return False
    if isinstance(payload, dict):
        if route_name == "feed" and "code" in payload:
            return payload["code"] in (0, 200, "0", "200")
        if route_name == "comments" and payload.get("success") is False:
            return False
    return True


def post_url(post, secrets, subreddit):
    for key in ("url", "permalink", "link", "post_url"):
        value = post.get(key)
        if isinstance(value, str) and value:
            try:
                parsed = urllib.parse.urlsplit(value)
                port = parsed.port
                decoded_path = urllib.parse.unquote(parsed.path, errors="strict")
            except ValueError:
                continue
            except UnicodeDecodeError:
                continue
            permalink = re.fullmatch(r"/r/([^/]+)/comments/([^/]+)(?:/[^/]*)?/?", parsed.path, re.IGNORECASE)
            decoded_permalink = re.fullmatch(
                r"/r/([^/]+)/comments/([^/]+)(?:/[^/]*)?/?", decoded_path, re.IGNORECASE
            )
            if (
                parsed.scheme.lower() == "https"
                and parsed.hostname in {"reddit.com", "www.reddit.com", "old.reddit.com"}
                and parsed.username is None
                and parsed.password is None
                and port in (None, 443)
                and permalink
                and re.fullmatch(r"[0-9a-z]+", permalink.group(2), re.ASCII | re.IGNORECASE)
                and decoded_permalink
                and decoded_permalink.group(2) == permalink.group(2)
                and not re.search(r"%[0-9a-f]{2}", decoded_path, re.IGNORECASE)
                and "\\" not in decoded_path
                and not any(segment in {".", ".."} for segment in decoded_path.split("/"))
                and permalink.group(1).casefold() == subreddit.casefold()
            ):
                sanitized = urllib.parse.urlsplit(safe_url(value, secrets))
                safe_permalink = re.fullmatch(
                    r"/r/([^/]+)/comments/([^/]+)(?:/[^/]*)?/?", sanitized.path, re.IGNORECASE
                )
                if (
                    sanitized.path == parsed.path
                    and safe_permalink
                    and safe_permalink.group(1).casefold() == subreddit.casefold()
                ):
                    return urllib.parse.urlunsplit(("https", "www.reddit.com", sanitized.path, "", ""))
    return None


def post_time(post):
    for key in ("createdAt", "created_at_iso", "created_utc", "timestamp"):
        value = post.get(key)
        if isinstance(value, (int, float)):
            try:
                if not math.isfinite(value):
                    raise ValueError
                return dt.datetime.fromtimestamp(value, dt.timezone.utc)
            except (OverflowError, OSError, ValueError):
                raise ValueError("post_timestamp_invalid") from None
        if isinstance(value, str):
            try:
                return parse_datetime(value)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
    return None


def cursors_in_comments(value):
    result = []
    if isinstance(value, dict):
        more = value.get("more")
        if isinstance(more, dict) and more.get("has_more") is True:
            cursor = more.get("cursor") or more.get("next_cursor")
            if isinstance(cursor, str) and cursor:
                result.append(cursor)
        for key, child in value.items():
            if key != "more":
                result.extend(cursors_in_comments(child))
    elif isinstance(value, list):
        for item in value:
            result.extend(cursors_in_comments(item))
    return result


def original_cursors(route_name, body):
    try:
        payload = parse_json(body)
        if route_name == "feed":
            return feed_data(payload)[2]
        validate_comment_body(payload)
        return cursors_in_comments(payload)
    except (UnicodeError, TypeError, ValueError, RecursionError):
        return None


def comment_pagination_incomplete(value):
    if isinstance(value, dict):
        more = value.get("more")
        if isinstance(more, dict) and more.get("has_more") is True:
            if not isinstance(more.get("cursor") or more.get("next_cursor"), str):
                return True
        return any(
            comment_pagination_incomplete(child)
            for key, child in value.items()
            if key != "more"
        )
    if isinstance(value, list):
        return any(comment_pagination_incomplete(item) for item in value)
    return False


def validate_comment_body(payload):
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("comments_response_not_success")
    if not isinstance(payload.get("comments"), list):
        raise ValueError("comments_schema_unknown")

    def validate_more(more):
        if (
            not isinstance(more, dict)
            or not isinstance(more.get("has_more"), bool)
            or (more.get("cursor") is not None and not isinstance(more.get("cursor"), str))
            or (more.get("next_cursor") is not None and not isinstance(more.get("next_cursor"), str))
        ):
            raise ValueError("comments_schema_unknown")

    def validate_items(items, depth=0):
        if depth > 32:
            raise ValueError("comments_schema_unknown")
        for comment in items:
            if (
                not isinstance(comment, dict)
                or not isinstance(comment.get("id"), str)
                or not comment["id"]
                or not isinstance(comment.get("body"), str)
            ):
                raise ValueError("comments_schema_unknown")
            if "replies" in comment:
                replies = comment["replies"]
                if not isinstance(replies, dict) or not isinstance(replies.get("items"), list):
                    raise ValueError("comments_schema_unknown")
                validate_items(replies["items"], depth + 1)
                if "more" in replies:
                    validate_more(replies["more"])

    validate_items(payload["comments"])
    if "more" in payload:
        validate_more(payload["more"])
    return payload["comments"]


def valid_recording_envelope(record, filename):
    if not isinstance(record, dict):
        return False
    if any(
        not isinstance(record.get(field), str)
        for field in ("record_id", "evidence_kind", "run_id", "trace_id", "span_id", "approval_sha256")
    ):
        return False
    route = next((route for route in ROUTES.values() if route["id"] == record.get("endpoint")), None)
    validation_errors = record.get("validation_errors", [])
    return bool(
        re.fullmatch(r"[0-9a-f]{32}", str(record.get("record_id", "")))
        and filename == "record-" + record["record_id"] + ".json"
        and record.get("ticket") == TICKET
        and record.get("evidence_kind") in {"synthetic_offline", "live_provider_response"}
        and re.fullmatch(r"[0-9a-f]{32}", str(record.get("run_id", "")))
        and re.fullmatch(r"[0-9a-f]{32}", str(record.get("trace_id", "")))
        and re.fullmatch(r"[0-9a-f]{16}", str(record.get("span_id", "")))
        and record.get("stage") == "reddit_acquisition"
        and route is not None
        and record.get("provider") == route["provider"]
        and re.fullmatch(r"[0-9a-f]{64}", str(record.get("approval_sha256", "")))
        and isinstance(record.get("configuration"), dict)
        and isinstance(record.get("request"), dict)
        and record["request"].get("method") == "GET"
        and evidence_present(record["request"].get("url"))
        and isinstance(record["request"].get("parameters"), dict)
        and type(record["request"].get("request_max_cost_micro_usd")) is int
        and 0 < record["request"]["request_max_cost_micro_usd"] <= route["max_charge_micro_usd"]
        and isinstance(record.get("response"), dict)
        and type(record["response"].get("truncated")) is bool
        and isinstance(record["response"].get("headers"), dict)
        and type(record.get("processing_complete")) is bool
        and isinstance(record.get("billing"), dict)
        and isinstance(record.get("counts"), dict)
        and (
            record.get("error") is None
            or isinstance(record.get("error"), str)
            and re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", record["error"])
        )
        and isinstance(validation_errors, list)
        and all(isinstance(error, str) and re.fullmatch(r"[a-z0-9_]+", error) for error in validation_errors)
        and (
            record.get("parent_record_id") is None
            or re.fullmatch(r"record-[0-9a-f]{32}\.json", str(record.get("parent_record_id", "")))
        )
    )


def recording_file(recordings_dir, record):
    record_id = "record-" + record["record_id"] + ".json"
    atomic_json(recordings_dir / record_id, record)
    return record_id


def record_timestamp(record):
    if not isinstance(record, dict):
        return None
    try:
        return parse_datetime(record.get("recorded_at"))
    except (TypeError, ValueError):
        return None


def record_expiry(record):
    if not isinstance(record, dict):
        return None
    try:
        recorded = parse_datetime(record.get("recorded_at"))
        expires = parse_datetime(record.get("expires_at"))
        retention_days = record.get("retention_days")
        if type(retention_days) is not int or not 1 <= retention_days <= RETENTION_DAYS:
            return None
        maximum_expiry = recorded + dt.timedelta(days=retention_days)
        if record.get("source_expires_at") is not None:
            source_expires = parse_datetime(record["source_expires_at"])
            if expires != source_expires or expires > maximum_expiry:
                return None
        elif expires != maximum_expiry:
            return None
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return expires


def expire_pending_files(directory):
    removed = 0
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        for path in directory.glob(".pending-*"):
            info = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(info.st_mode):
                raise CollectorError("recording_temporary_file_type_rejected", "blocked")
            if info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise CollectorError("private_recording_permissions_required", "blocked")
            path.unlink()
            removed += 1
    finally:
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)
    return removed


def expire_recordings(recordings_dir, now):
    if not recordings_dir.exists():
        return 0
    if recordings_dir.is_symlink():
        raise CollectorError("private_recording_directory_required", "blocked")
    private_directory(recordings_dir)
    removed = 0
    for path in recordings_dir.glob("*.json"):
        if path.is_symlink():
            raise CollectorError("recording_symlink_rejected", "blocked")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise CollectorError("recording_file_type_rejected", "blocked")
        if info.st_mode & 0o077:
            raise CollectorError("private_recording_permissions_required", "blocked")
        try:
            with path.open(encoding="utf-8") as source:
                record = json.load(source)
        except (OSError, UnicodeError, ValueError, RecursionError):
            record = {}
        expires = record_expiry(record)
        if expires is None or expires <= now:
            path.unlink()
            removed += 1
    return removed + expire_pending_files(recordings_dir)


class Collector:
    def __init__(self, args, approval, transport, credentials, clock, sleeper, synthetic):
        self.args = args
        self.approval = approval
        self.transport = transport
        self.credentials = credentials
        self.clock = clock
        self.sleeper = sleeper
        self.synthetic = synthetic
        self.retention_days = approval["retention"]["days"]
        self.approval_sha256 = hashlib.sha256(
            json.dumps(approval, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.request_attempts = 0
        self.run_id = uuid.uuid4().hex
        self.trace_id = uuid.uuid4().hex
        self.previous_record = None
        self.earliest_source_expiry = None
        self.recordings_dir = pathlib.Path(args.recordings_dir or pathlib.Path(args.state_dir) / "recordings")
        self.counts = {
            "feed_requests": 0,
            "comment_requests": 0,
            "feed_pages": 0,
            "comment_pages": 0,
            "posts_seen": 0,
            "posts_in_window": 0,
            "posts_missing_timestamp": 0,
            "comments_seen": 0,
            "duplicate_posts": 0,
            "record_ids": [],
            "errors": [],
            "pending_cursors": [],
            "observed_timestamps": [],
        }
        self.feed_ok = False
        self.comments_ok = True
        self.coverage_reasons = ["bounded NEW-feed pages do not establish complete source or time-window coverage"]

    def result(self, status, state=None, errors=None):
        errors = errors if errors is not None else self.counts["errors"]
        timestamps = self.counts["observed_timestamps"]
        coverage_status = "failed" if status == "failed" else "blocked" if status == "blocked" else "partial"
        report = {
            "ticket": TICKET,
            "source": "reddit",
            "status": status,
            "evidence_kind": "synthetic_offline" if self.synthetic else "live_provider_response",
            "network_requests": 0 if self.synthetic else self.request_attempts,
            "acquisition_attempts": self.request_attempts,
            "approval_sha256": self.approval_sha256,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "requested": {
                "subreddits": self.args.subreddit,
                "window_start": self.args.window_start,
                "window_end": self.args.window_end,
                "sort": "NEW",
                "feed_language": {
                    "mode": "provider_default",
                    "value": FEED_LANGUAGE_DEFAULT,
                    "owner_approved": False,
                },
                "routes": {name: route["id"] for name, route in ROUTES.items()},
                "max_feed_pages_per_subreddit": self.args.max_feed_pages,
                "max_comment_pages_per_post": self.args.max_comment_pages,
                "max_posts": self.args.max_posts,
                "max_retries": MAX_RETRIES,
                "credential_mode": self.approval["provider_account"]["credential_mode"],
                "token_type": self.approval["provider_account"]["token_type"],
            },
            "counts": {key: value for key, value in self.counts.items() if key != "record_ids"},
            "record_ids": self.counts["record_ids"],
            "observed_timestamp_range": {
                "minimum": min(timestamps) if timestamps else None,
                "maximum": max(timestamps) if timestamps else None,
            },
            "capabilities": {
                "feed_schema": {"status": "supported" if self.feed_ok else coverage_status, "evidence": "synthetic fixture parsed" if self.feed_ok and self.synthetic else "verified provider response parsed" if self.feed_ok else "no valid feed page established"},
                "comment_schema": {"status": "supported" if self.comments_ok and self.counts["comment_requests"] else "partial" if self.comments_ok else "failed", "evidence": "comment response schema parsed" if self.comments_ok and self.counts["comment_requests"] else "no valid comment response established"},
                "source_coverage": {"status": coverage_status, "evidence": "; ".join(self.coverage_reasons)},
                "spending_control": {
                    "status": "blocked" if state and (state.get("pending") or state.get("limit_breach")) else "partial" if self.synthetic else "supported",
                    "evidence": "synthetic transport verified reservations; provider enforcement not exercised" if self.synthetic else "persistent reservation and documented Treg request ceiling",
                },
                "recordings": {"status": "supported" if self.counts["record_ids"] else "blocked", "evidence": "sanitized private response recordings" if self.counts["record_ids"] else "no response recording"},
            },
            "validation": {
                "feed_schema": "valid" if self.feed_ok else "not_established",
                "comment_schema": "valid" if self.comments_ok and self.counts["comment_requests"] else "not_established",
                "errors": errors,
            },
            "errors": errors,
        }
        if state is not None:
            report["budget"] = {
                "successful_requests": state["successful_requests"],
                "attempts": state["attempts"],
                "spent_micro_usd": state["spent_micro_usd"],
                "reserved_micro_usd": state["reserved_micro_usd"],
                "successful_limit": MAX_SUCCESSFUL_REQUESTS,
                "spend_limit_micro_usd": MAX_SPEND_MICRO_USD,
            }
        report = sanitize(report, [self.credentials.token])
        artifact_name = "run-" + self.run_id + ".json"
        report["artifact"] = artifact_name
        now = self.clock()
        expires = min(
            now + dt.timedelta(days=self.retention_days),
            self.earliest_source_expiry or now + dt.timedelta(days=self.retention_days),
        )
        persisted = {
            **report,
            "stage": "reddit_acquisition_output",
            "recorded_at": timestamp(now),
            "expires_at": timestamp(expires),
            "retention_days": self.retention_days,
        }
        if self.earliest_source_expiry is not None:
            persisted["source_expires_at"] = timestamp(self.earliest_source_expiry)
        try:
            atomic_json(self.recordings_dir / artifact_name, persisted)
        except (OSError, CollectorError):
            raise CollectorError("run_report_persistence_failed", "blocked") from None
        return report

    def save_record(self, route_name, params, response=None, error=None, reservation=None, api_counts=None, request_headers=None):
        route = ROUTES[route_name]
        now = self.clock()
        expires = now + dt.timedelta(days=self.retention_days)
        self.earliest_source_expiry = min(self.earliest_source_expiry or expires, expires)
        body = sanitize_body(response.body, [self.credentials.token]) if response is not None else None
        headers = response_headers(response.headers, [self.credentials.token]) if response is not None else {}
        call_id = header(response.headers, "X-Treg-Call-Id") if response is not None else None
        charged_micro_usd = billing_cost(header(response.headers, "X-Treg-Cost-Micro")) if response else None
        record = {
            "record_id": uuid.uuid4().hex,
            "ticket": TICKET,
            "evidence_kind": "synthetic_offline" if self.synthetic else "live_provider_response",
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "span_id": uuid.uuid4().hex[:16],
            "parent_record_id": self.previous_record,
            "approval_sha256": self.approval_sha256,
            "stage": "reddit_acquisition",
            "provider": route["provider"],
            "endpoint": route["id"],
            "recorded_at": timestamp(now),
            "expires_at": timestamp(expires),
            "retention_days": self.retention_days,
            "configuration": {
                "subreddits": self.args.subreddit,
                "window_start": self.args.window_start,
                "window_end": self.args.window_end,
                "sort": "NEW",
                "feed_language": {
                    "mode": "provider_default",
                    "value": FEED_LANGUAGE_DEFAULT,
                    "owner_approved": False,
                },
                "max_feed_pages_per_subreddit": self.args.max_feed_pages,
                "max_comment_pages_per_post": self.args.max_comment_pages,
                "max_posts": self.args.max_posts,
                "max_retries": MAX_RETRIES,
                "credential_mode": self.approval["provider_account"]["credential_mode"],
                "token_type": self.approval["provider_account"]["token_type"],
                "successful_request_limit": MAX_SUCCESSFUL_REQUESTS,
                "spend_limit_micro_usd": MAX_SPEND_MICRO_USD,
            },
            "request": {
                "method": "GET",
                "url": safe_url(call_url(route["id"], params), [self.credentials.token]),
                "parameters": sanitize(params, [self.credentials.token]),
                "headers": {
                    str(key).lower(): safe_string(value, [self.credentials.token])
                    for key, value in (request_headers or {}).items()
                    if str(key).lower() not in {"x-treg-token", "x-treg-org"}
                },
                "request_max_cost_micro_usd": reservation,
                "attempt": api_counts.get("attempt") if api_counts else None,
                "sort": "NEW" if route_name == "feed" else None,
            },
            "response": {
                "http_status": response.status if response else None,
                "headers": headers,
                "body": body,
                "truncated": response.truncated if response else False,
            },
            "processing_complete": error is not None,
            "billing": {
                "call_id": safe_string(call_id, [self.credentials.token]) if call_id else None,
                "charged_micro_usd": str(charged_micro_usd) if charged_micro_usd is not None else None,
                "charge_status": "reported" if charged_micro_usd is not None else "unknown",
                "unit": route["billing_unit"],
                "provider_reported_charge": body.get("credits_charged") if isinstance(body, dict) else None,
            },
            "counts": sanitize(api_counts or {}),
            "error": error,
        "request_parameters_sha256": hashlib.sha256(
            json.dumps(sanitize(params, [self.credentials.token]), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        }
        record_id = recording_file(self.recordings_dir, record)
        self.counts["record_ids"].append(record_id)
        self.previous_record = record_id
        return record_id, body, headers

    def annotate_record_validation(self, code):
        if not self.counts["record_ids"]:
            return
        path = self.recordings_dir / self.counts["record_ids"][-1]
        try:
            with path.open(encoding="utf-8") as source:
                record = json.load(source)
        except (OSError, UnicodeError, ValueError):
            raise CollectorError("record_validation_persistence_failed", "blocked") from None
        errors = record.setdefault("validation_errors", [])
        if not isinstance(errors, list):
            raise CollectorError("record_validation_persistence_failed", "blocked")
        if code not in errors:
            errors.append(code)
        record["processing_complete"] = True
        try:
            atomic_json(path, record)
        except (OSError, CollectorError):
            raise CollectorError("record_validation_persistence_failed", "blocked") from None

    def annotate_record_error(self, code):
        if not self.counts["record_ids"]:
            return
        path = self.recordings_dir / self.counts["record_ids"][-1]
        try:
            with path.open(encoding="utf-8") as source:
                record = json.load(source)
            record["error"] = code
            record["processing_complete"] = True
            if code == "billing_amount_unknown_reconciliation_required":
                record["billing"]["charged_micro_usd"] = None
                record["billing"]["charge_status"] = "unknown"
                record["response"]["headers"].pop("x-treg-cost-micro", None)
            atomic_json(path, record)
        except (OSError, UnicodeError, ValueError, CollectorError):
            raise CollectorError("record_error_persistence_failed", "blocked") from None

    def mark_record_processing_complete(self):
        if not self.counts["record_ids"]:
            return
        path = self.recordings_dir / self.counts["record_ids"][-1]
        try:
            with path.open(encoding="utf-8") as source:
                record = json.load(source)
            record["processing_complete"] = True
            atomic_json(path, record)
        except (OSError, UnicodeError, ValueError, CollectorError):
            raise CollectorError("record_processing_persistence_failed", "blocked") from None

    def validate_cursors(self, cursors):
        for cursor in cursors:
            if not isinstance(cursor, str):
                self.coverage_reasons.append("response pagination cursor is not valid text")
                self.annotate_record_validation("response_cursor_not_utf8")
                raise CollectorError("response_cursor_not_utf8", "failed")
            try:
                cursor.encode("utf-8")
            except UnicodeEncodeError:
                self.coverage_reasons.append("response pagination cursor is not valid UTF-8")
                self.annotate_record_validation("response_cursor_not_utf8")
                raise CollectorError("response_cursor_not_utf8", "failed") from None

    def fail_record(self, code, status="failed"):
        self.annotate_record_error(code)
        raise CollectorError(code, status)

    def reserve(self, state_path, state, route_name, params):
        if state.get("limit_breach"):
            raise CollectorError("persistent_budget_limit_breach", "blocked")
        if state.get("pending") is not None:
            raise CollectorError("unreconciled_request_pauses_acquisition", "blocked")
        if state["successful_requests"] + state["reserved_success_slots"] >= MAX_SUCCESSFUL_REQUESTS:
            raise CollectorError("successful_request_limit_reached", "blocked")
        available = MAX_SPEND_MICRO_USD - state["spent_micro_usd"] - state["reserved_micro_usd"]
        reservation = min(ROUTES[route_name]["max_charge_micro_usd"], available)
        if reservation <= 0:
            raise CollectorError("spend_limit_reached", "blocked")
        state["attempts"] += 1
        state["reserved_micro_usd"] += reservation
        state["reserved_success_slots"] += 1
        state["pending"] = {
            "attempt": state["attempts"],
            "route": route_name,
            "endpoint": ROUTES[route_name]["id"],
            "reserved_micro_usd": reservation,
            "success_slot_reserved": True,
            "started_at": timestamp(self.clock()),
            "request_parameters_expires_at": timestamp(self.clock() + dt.timedelta(days=self.retention_days)),
            "request_parameters": sanitize(params, [self.credentials.token]),
            "request_parameters_sha256": hashlib.sha256(
                json.dumps(sanitize(params, [self.credentials.token]), sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }
        atomic_json(state_path, state)
        return reservation

    def settle(self, state_path, state, response, reservation, successful):
        pending = state["pending"]
        status = response.status
        if successful:
            state["successful_requests"] += 1
        state["reserved_success_slots"] -= 1
        call_id = header(response.headers, "X-Treg-Call-Id")
        cost = billing_cost(header(response.headers, "X-Treg-Cost-Micro"))
        if cost is None:
            pending.update({
                "charge_known": False,
                "call_id": safe_string(call_id, [self.credentials.token]) if call_id else None,
                "http_status": status,
                "success_slot_reserved": False,
            })
            pending["http_success"] = successful
            atomic_json(state_path, state)
            raise CollectorError("billing_amount_unknown_reconciliation_required", "blocked")
        state["spent_micro_usd"] += cost
        if cost > reservation or state["spent_micro_usd"] + state["reserved_micro_usd"] - reservation > MAX_SPEND_MICRO_USD:
            state["limit_breach"] = True
            pending.update({
                "charge_known": True,
                "known_charge_micro_usd": cost,
                "call_id": safe_string(call_id, [self.credentials.token]) if call_id else None,
                "http_status": status,
                "http_success": successful,
                "success_slot_reserved": False,
                "reconciliation_evidence_required": True,
            })
            atomic_json(state_path, state)
            raise CollectorError("provider_cost_ceiling_breach", "failed")
        if not call_id:
            state["reserved_micro_usd"] -= reservation
            pending.update({
                "charge_known": True,
                "known_charge_micro_usd": cost,
                "http_status": status,
                "http_success": successful,
                "success_slot_reserved": False,
                "reconciliation_evidence_required": True,
            })
            atomic_json(state_path, state)
            raise CollectorError("billing_call_id_missing_reconciliation_required", "blocked")
        state["reserved_micro_usd"] -= reservation
        state["pending"] = None
        atomic_json(state_path, state)
        return cost, successful, call_id

    def dispatch(self, state_path, state, route_name, params):
        route = ROUTES[route_name]
        cursor = params.get("after", params.get("cursor"))
        if cursor is not None:
            self.validate_cursors([cursor])
        local_retries = 0
        while True:
            reservation = self.reserve(state_path, state, route_name, params)
            idempotency_key = uuid.uuid4().hex
            headers = {
                "X-Treg-Token": self.credentials.token,
                "X-Treg-Route-Max-Cost": cost_text(reservation),
                "Cache-Control": "no-cache",
                "Accept": "application/json",
                "Idempotency-Key": idempotency_key,
            }
            if self.credentials.org:
                headers["X-Treg-Org"] = self.credentials.org
            self.request_attempts += 1
            try:
                result = self.transport.request(
                    call_url(route["id"], params), headers, REQUEST_TIMEOUT_SECONDS
                )
            except Exception as error:
                message = "request_outcome_unknown:" + type(error).__name__
                self.save_record(
                    route_name, params, error=message, reservation=reservation,
                    api_counts={"attempt": state["attempts"]},
                    request_headers=headers,
                )
                if state["pending"] is not None:
                    state["pending"]["request_error"] = type(error).__name__
                    atomic_json(state_path, state)
                raise CollectorError("request_outcome_unknown_reconciliation_required", "blocked") from None
            cursors = original_cursors(route_name, result.body)
            body = sanitize_body(result.body, [self.credentials.token])
            successful_response = acquisition_succeeded(route_name, result.status, body)
            self.save_record(
                route_name, params, response=result, reservation=reservation,
                api_counts={
                    "attempt": state["attempts"],
                    "response_bytes": len(result.body),
                },
                request_headers=headers,
            )
            try:
                _, successful, _ = self.settle(state_path, state, result, reservation, successful_response)
            except CollectorError as error:
                self.fail_record(error.code, error.status)
            served_via = header(result.headers, "X-Treg-Served-Via")
            cache = header(result.headers, "X-Treg-Cache")
            if served_via and str(served_via).lower().startswith("overflow:"):
                state["limit_breach"] = True
                atomic_json(state_path, state)
                self.fail_record("provider_overflow_fallback_detected")
            if cache and str(cache).lower() == "hit":
                state["limit_breach"] = True
                atomic_json(state_path, state)
                self.fail_record("provider_cache_hit_despite_bypass")
            if result.truncated:
                self.fail_record("response_exceeded_recording_size_limit")
            if result.status in (401, 403):
                self.fail_record("provider_authentication_or_permission_failure")
            if result.status in (408, 425, 429, 500, 502, 503, 504) and local_retries < MAX_RETRIES:
                local_retries += 1
                try:
                    delay = retry_delay(
                        header(result.headers, "Retry-After"),
                        min(2**local_retries, MAX_RETRY_DELAY_SECONDS),
                        self.clock(),
                    )
                except CollectorError as error:
                    self.fail_record(error.code, error.status)
                self.annotate_record_error("transient_retry_response")
                self.sleeper(delay)
                continue
            if result.status in (408, 425, 429, 500, 502, 503, 504):
                self.fail_record("transient_retry_limit_exhausted")
            if not successful:
                if 200 <= result.status < 300:
                    self.fail_record("provider_api_request_failed")
                self.fail_record("provider_http_request_failed")
            return body, cursors

    def feed(self, state_path, state):
        start = parse_datetime(self.args.window_start)
        end = parse_datetime(self.args.window_end)
        for subreddit in self.args.subreddit:
            cursor = None
            seen_cursors = set()
            for _ in range(self.args.max_feed_pages):
                params = {"subreddit_name": subreddit, "sort": "NEW", "need_format": "false"}
                if cursor:
                    params["after"] = cursor
                body, original_cursor = self.dispatch(state_path, state, "feed", params)
                self.counts["feed_requests"] += 1
                try:
                    posts, has_next, next_cursor = feed_data(body)
                except (TypeError, ValueError, json.JSONDecodeError):
                    self.counts["errors"].append("feed_response_schema_invalid")
                    self.coverage_reasons.append("feed response schema validation failed")
                    self.annotate_record_validation("feed_response_schema_invalid")
                    raise CollectorError("feed_response_schema_invalid", "failed") from None
                if isinstance(original_cursor, str):
                    next_cursor = original_cursor
                if isinstance(next_cursor, str):
                    self.validate_cursors([next_cursor])
                elif has_next is True and next_cursor is not None:
                    self.validate_cursors([next_cursor])
                try:
                    post_times = feed_post_times(posts)
                except ValueError:
                    self.counts["errors"].append("feed_response_payload_invalid")
                    self.coverage_reasons.append("feed post contains an invalid numeric timestamp")
                    self.annotate_record_validation("feed_response_payload_invalid")
                    raise CollectorError("feed_response_payload_invalid", "failed") from None
                self.mark_record_processing_complete()
                self.feed_ok = True
                self.counts["feed_pages"] += 1
                self.counts["posts_seen"] += len(posts)
                if not posts:
                    self.coverage_reasons.append("empty feed page does not establish source completeness")
                for post, observed in zip(posts, post_times):
                    if observed is None:
                        self.counts["posts_missing_timestamp"] += 1
                        self.coverage_reasons.append("some feed posts have no verified timestamp field")
                        continue
                    observed_text = timestamp(observed)
                    self.counts["observed_timestamps"].append(observed_text)
                    if not start <= observed < end:
                        continue
                    self.counts["posts_in_window"] += 1
                    url = post_url(post, [self.credentials.token], subreddit)
                    if not url:
                        self.coverage_reasons.append("some in-window posts lack a safe Reddit URL")
                        continue
                    if any(item.get("url") == url for item in getattr(self, "posts", [])):
                        self.counts["duplicate_posts"] += 1
                        continue
                    if not hasattr(self, "posts"):
                        self.posts = []
                    if len(self.posts) < self.args.max_posts:
                        self.posts.append({"url": url, "created_at": observed_text})
                    else:
                        self.coverage_reasons.append("comment post limit reached; additional in-window posts were not comment-enriched")
                if has_next is False:
                    break
                if has_next is not True or not isinstance(next_cursor, str) or not next_cursor:
                    if isinstance(next_cursor, str) and next_cursor:
                        self.counts["pending_cursors"].append({
                            "route": "feed",
                            "subreddit": subreddit,
                            "cursor": next_cursor,
                        })
                    self.coverage_reasons.append("feed pagination state is missing or unrecognized")
                    break
                if next_cursor in seen_cursors:
                    self.coverage_reasons.append("feed returned a repeated cursor; pagination stopped")
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                self.counts["pending_cursors"].append({"route": "feed", "subreddit": subreddit, "cursor": cursor})
                self.coverage_reasons.append("feed page limit reached with a continuation cursor")

    def comments(self, state_path, state):
        posts = getattr(self, "posts", [])[:self.args.max_posts]
        if not posts:
            self.coverage_reasons.append("no posts were selected for comment retrieval")
        for post in posts:
            cursors = [None]
            seen_cursors = set()
            page_count = 0
            while cursors and page_count < self.args.max_comment_pages:
                cursor = cursors.pop(0)
                if cursor in seen_cursors:
                    self.coverage_reasons.append("comment cursor repeated; pagination stopped")
                    continue
                if cursor:
                    seen_cursors.add(cursor)
                params = {"url": post["url"], "trim": "false"}
                if cursor:
                    params["cursor"] = cursor
                body, original_comment_cursors = self.dispatch(state_path, state, "comments", params)
                self.counts["comment_requests"] += 1
                page_count += 1
                try:
                    comments = validate_comment_body(body)
                except (TypeError, ValueError):
                    self.comments_ok = False
                    self.counts["errors"].append("comment_response_schema_invalid")
                    self.annotate_record_validation("comment_response_schema_invalid")
                    raise CollectorError("comment_response_schema_invalid", "failed") from None
                comment_cursors = (
                    original_comment_cursors
                    if isinstance(original_comment_cursors, list)
                    else cursors_in_comments(body)
                )
                self.validate_cursors(comment_cursors)
                self.mark_record_processing_complete()
                self.counts["comment_pages"] += 1
                self.counts["comments_seen"] += len(comments)
                for next_cursor in comment_cursors:
                    if next_cursor not in seen_cursors and next_cursor not in cursors:
                        cursors.append(next_cursor)
                    elif next_cursor in seen_cursors:
                        self.coverage_reasons.append("comment cursor repeated; pagination stopped")
                if comment_pagination_incomplete(body):
                    self.coverage_reasons.append("comment continuation indicated more data without a usable cursor")
            if cursors:
                self.counts["pending_cursors"].extend(
                    {"route": "comments", "url": post["url"], "cursor": cursor}
                    for cursor in cursors
                )
                self.coverage_reasons.append("comment page limit reached with a continuation cursor")

    def run(self, state_path, state):
        private_directory(self.recordings_dir)
        expire_recordings(self.recordings_dir, self.clock())
        try:
            self.feed(state_path, state)
            if self.args.max_comment_pages:
                self.comments(state_path, state)
            else:
                self.coverage_reasons.append("comment retrieval was explicitly disabled for this run")
        except CollectorError as error:
            self.counts["errors"].append(error.code)
            status = error.status
            if error.code in {"provider_authentication_or_permission_failure", "provider_api_request_failed", "provider_http_request_failed", "transient_retry_limit_exhausted", "retry_after_invalid", "retry_after_exceeds_local_bound", "feed_response_schema_invalid", "comment_response_schema_invalid", "provider_cost_ceiling_breach", "provider_overflow_fallback_detected", "provider_cache_hit_despite_bypass"}:
                status = "failed"
            return self.result(status, state)
        status = "partial"
        if not self.counts["feed_requests"]:
            status = "failed"
        return self.result(status, state)


def read_run_args(args):
    errors = []
    if not args.subreddit:
        errors.append("explicit_subreddit_required")
    for subreddit in args.subreddit:
        if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", subreddit):
            errors.append("invalid_subreddit_name")
    if len(args.subreddit) != len(set(args.subreddit)):
        errors.append("duplicate_subreddit_name")
    if not 1 <= args.max_feed_pages <= MAX_SUCCESSFUL_REQUESTS:
        errors.append("feed_page_limit_out_of_bounds")
    if not 0 <= args.max_comment_pages <= MAX_SUCCESSFUL_REQUESTS:
        errors.append("comment_page_limit_out_of_bounds")
    if not 0 <= args.max_posts <= MAX_SUCCESSFUL_REQUESTS:
        errors.append("post_limit_out_of_bounds")
    try:
        start = parse_datetime(args.window_start)
        end = parse_datetime(args.window_end)
        if start >= end:
            errors.append("invalid_time_window")
    except (TypeError, ValueError, OverflowError, OSError):
        errors.append("invalid_time_window")
    return errors


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Bounded Reddit acquisition collector with private offline replay.")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="collect approved Reddit feeds and comments")
    collect.add_argument("--subreddit", action="append", required=True)
    collect.add_argument("--window-start", required=True)
    collect.add_argument("--window-end", required=True)
    collect.add_argument("--max-feed-pages", type=int, required=True)
    collect.add_argument("--max-comment-pages", type=int, required=True)
    collect.add_argument("--max-posts", type=int, required=True)
    collect.add_argument("--approval", default=str(APPROVAL_DEFAULT))
    collect.add_argument("--state-dir", default=str(STATE_DEFAULT))
    collect.add_argument("--recordings-dir")
    collect.add_argument("--allow-live-acquisition", default="")
    collect.add_argument("--plan", action="store_true", help="verify gates without credentials or network")
    replay = commands.add_parser("replay", help="replay private recordings offline")
    replay.add_argument("--recordings-dir", default=str(STATE_DEFAULT / "recordings"))
    replay.add_argument("--state-dir", default=str(STATE_DEFAULT))
    reconcile = commands.add_parser("reconcile", help="settle a persisted unknown request outcome")
    reconcile.add_argument("--state-dir", default=str(STATE_DEFAULT))
    reconcile.add_argument("--call-id", required=True)
    reconcile.add_argument("--charge-micro", required=True, type=int)
    reconcile.add_argument("--request-outcome", choices=("success", "failed"), required=True)
    reconcile.add_argument("--evidence", required=True)
    return parser.parse_args(argv)


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def run_collect(args, transport, credentials_loader, clock, sleeper, synthetic):
    errors = read_run_args(args)
    try:
        approval = load_approval(pathlib.Path(args.approval))
    except CollectorError as error:
        report = {
            "ticket": TICKET,
            "source": "reddit",
            "status": error.status,
            "evidence_kind": "offline_preflight",
            "capabilities": {"live_acquisition": {"status": "blocked", "evidence": error.code}},
            "errors": errors + [error.code],
        }
        print_json(report)
        return 2
    errors.extend(validate_approval(approval, args.subreddit, args.window_start, args.window_end))
    if errors:
        report = {
            "ticket": TICKET,
            "source": "reddit",
            "status": "blocked",
            "evidence_kind": "offline_preflight",
            "capabilities": {"live_acquisition": {"status": "blocked", "evidence": "missing or mismatched verified approvals"}},
            "errors": errors,
        }
        print_json(report)
        return 2
    state_dir = pathlib.Path(args.state_dir)
    recordings_dir = pathlib.Path(args.recordings_dir or state_dir / "recordings")
    if any(repository_path(path) for path in (args.approval, state_dir, recordings_dir)):
        print_json({"ticket": TICKET, "source": "reddit", "status": "blocked", "errors": ["private_artifacts_must_be_outside_repository"]})
        return 2
    if args.plan:
        print_json({
            "ticket": TICKET,
            "source": "reddit",
            "status": "ready_for_operator_review",
            "evidence_kind": "offline_preflight",
            "network_requests": 0,
            "credentials_loaded": False,
            "subreddits": args.subreddit,
            "window": {"start": args.window_start, "end": args.window_end},
            "approval_sha256": hashlib.sha256(
                json.dumps(approval, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "feed_language": {
                "mode": "provider_default",
                "value": FEED_LANGUAGE_DEFAULT,
                "owner_approved": False,
            },
            "limits": {"successful_requests": MAX_SUCCESSFUL_REQUESTS, "spend_usd": "0.25"},
            "retry_bound_per_request": MAX_RETRIES,
            "note": "manifest evidence is an operator assertion; account permission and source rights require external verification",
        })
        return 0
    if not synthetic and (
        state_dir.resolve() != STATE_DEFAULT.resolve()
        or recordings_dir.resolve() != (STATE_DEFAULT / "recordings").resolve()
    ):
        print_json({
            "ticket": TICKET,
            "source": "reddit",
            "status": "blocked",
            "evidence_kind": "offline_preflight",
            "capabilities": {"live_acquisition": {"status": "blocked", "evidence": "live ticket state and recordings must use their persistent defaults"}},
            "errors": ["live_state_paths_must_use_ticket_default"],
        })
        return 2
    if args.allow_live_acquisition != LIVE_ACK or os.environ.get("NEED_RADAR_REDDIT_LIVE_ACK") != LIVE_ACK:
        print_json({
            "ticket": TICKET,
            "source": "reddit",
            "status": "blocked",
            "evidence_kind": "offline_preflight",
            "capabilities": {"live_acquisition": {"status": "blocked", "evidence": "explicit bounded live acknowledgement missing"}},
            "errors": ["live_acknowledgement_missing"],
        })
        return 2
    if synthetic and credentials_loader is None:
        print_json({"ticket": TICKET, "source": "reddit", "status": "blocked", "errors": ["synthetic_transport_requires_test_credentials"], "network_requests": 0})
        return 2
    collector = None
    try:
        with locked_state(state_dir, clock()) as (state_path, state):
            if not synthetic:
                require_incident_reconciled(state)
                if state.get("pending") is not None:
                    raise CollectorError("unreconciled_request_pauses_acquisition", "blocked")
                if state.get("limit_breach"):
                    raise CollectorError("persistent_budget_limit_breach", "blocked")
                if state["successful_requests"] + state["reserved_success_slots"] >= MAX_SUCCESSFUL_REQUESTS:
                    raise CollectorError("successful_request_limit_reached", "blocked")
                if state["spent_micro_usd"] + state["reserved_micro_usd"] >= MAX_SPEND_MICRO_USD:
                    raise CollectorError("spend_limit_reached", "blocked")
            account = approval["provider_account"]
            credentials_loaded = False
            credentials = credentials_loader(account["token_type"])
            credentials_loaded = True
            if not isinstance(credentials, Credentials) or not credentials.token:
                raise CollectorError("runtime_credentials_invalid", "blocked")
            collector = Collector(args, approval, transport, credentials, clock, sleeper, synthetic)
            report = collector.run(state_path, state)
    except CollectorError as error:
        report = {
            "ticket": TICKET,
            "source": "reddit",
            "status": error.status,
            "evidence_kind": "offline_preflight" if collector is None else "synthetic_offline" if synthetic else "live_provider_response",
            "errors": [error.code],
            "network_requests": 0 if collector is None or synthetic else collector.request_attempts,
            "credentials_loaded": locals().get("credentials_loaded", False),
        }
        if collector is not None:
            report.update({
                "run_id": collector.run_id,
                "trace_id": collector.trace_id,
                "acquisition_attempts": collector.request_attempts,
            })
        if error.code in {"historical_unknown_outcome_hold", "historical_incident_not_in_budget_ledger"}:
            report["incident"] = default_state()["incident_hold"]
    print_json(report)
    return 0 if report["status"] in {"supported", "partial"} else 2


def run_replay(args, clock):
    recordings_dir = pathlib.Path(args.recordings_dir)
    if repository_path(recordings_dir):
        print_json({"ticket": TICKET, "source": "reddit", "status": "blocked", "network_requests": 0, "errors": ["private_artifacts_must_be_outside_repository"]})
        return 2
    try:
        removed = expire_recordings(recordings_dir, clock())
        source_records = []
        records = []
        replay_errors = []
        invalid_recordings = 0
        for path in sorted(recordings_dir.glob("record-*.json")):
            if path.is_symlink() or not private_file(path):
                raise CollectorError("private_recording_permissions_required", "blocked")
            with path.open(encoding="utf-8") as source:
                record = json.load(source)
            if record_expiry(record) is None:
                raise CollectorError("recording_retention_metadata_invalid", "blocked")
            source_records.append(record)
            if not valid_recording_envelope(record, path.name):
                invalid_recordings += 1
                replay_errors.append("recording_envelope_invalid")
                continue
            records.append(record)
        if not source_records:
            raise CollectorError("no_unexpired_recordings_available", "blocked")
        replay_counts = {
            "feed_pages": 0,
            "feed_posts": 0,
            "posts_missing_timestamp": 0,
            "empty_feed_pages": 0,
            "comment_pages": 0,
            "comments": 0,
            "validation_failures": 0,
            "request_failures": 0,
            "billing_unknown_requests": 0,
            "invalid_recordings": invalid_recordings,
        }
        replay_counts["validation_failures"] += invalid_recordings
        feed_records = 0
        comment_records = 0
        feed_validation_failures = 0
        comment_validation_failures = 0
        billing_unknown_record_ids = []
        billing_ceiling_breach_record_ids = []
        for record in records:
            endpoint = record["endpoint"]
            if endpoint == FEED_ID:
                feed_records += 1
            else:
                comment_records += 1
            recorded_errors = record.get("validation_errors", [])
            if recorded_errors:
                replay_counts["validation_failures"] += len(recorded_errors)
                replay_errors.extend(recorded_errors)
                if endpoint == FEED_ID:
                    feed_validation_failures += len(recorded_errors)
                else:
                    comment_validation_failures += len(recorded_errors)
            recorded_error = record.get("error")
            processing_incomplete = record["processing_complete"] is not True
            if processing_incomplete:
                replay_counts["validation_failures"] += 1
                if endpoint == FEED_ID:
                    feed_validation_failures += 1
                else:
                    comment_validation_failures += 1
                replay_errors.append("acquisition_processing_incomplete")
            response = record["response"]
            truncated = response["truncated"]
            billing = record["billing"]
            charged = billing.get("charged_micro_usd")
            charged_amount = billing_cost(charged)
            charge_known = charged_amount is not None
            charge_exceeds_ceiling = charge_known and charged_amount > record["request"]["request_max_cost_micro_usd"]
            billing_reference_known = evidence_present(billing.get("call_id"))
            billing_unknown = (
                not charge_known
                or not billing_reference_known
                or processing_incomplete
                or recorded_error in {
                    "billing_amount_unknown_reconciliation_required",
                    "billing_call_id_missing_reconciliation_required",
                }
            )
            if truncated:
                replay_counts["validation_failures"] += 1
                if endpoint == FEED_ID:
                    feed_validation_failures += 1
                else:
                    comment_validation_failures += 1
                replay_errors.append("response_exceeded_recording_size_limit")
            if billing_unknown:
                replay_counts["request_failures"] += 1
                replay_counts["billing_unknown_requests"] += 1
                billing_unknown_record_ids.append(record["record_id"])
                if recorded_error not in {
                    "billing_amount_unknown_reconciliation_required",
                    "billing_call_id_missing_reconciliation_required",
                }:
                    replay_errors.append("billing_evidence_incomplete_reconciliation_required")
            if charge_exceeds_ceiling:
                replay_counts["validation_failures"] += 1
                replay_counts["request_failures"] += 1
                if endpoint == FEED_ID:
                    feed_validation_failures += 1
                else:
                    comment_validation_failures += 1
                billing_ceiling_breach_record_ids.append(record["record_id"])
                replay_errors.append("recorded_charge_exceeds_request_ceiling")
            if recorded_error is not None:
                if (
                    recorded_error != "response_exceeded_recording_size_limit"
                    and not billing_unknown
                    and not charge_exceeds_ceiling
                ):
                    replay_counts["request_failures"] += 1
                if recorded_error == "billing_amount_unknown_reconciliation_required":
                    if not billing_unknown:
                        replay_counts["billing_unknown_requests"] += 1
                if isinstance(recorded_error, str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", recorded_error):
                    replay_errors.append(recorded_error)
                else:
                    replay_errors.append("recorded_request_failed")
            if (
                recorded_errors
                or truncated
                or billing_unknown
                or charge_exceeds_ceiling
                or processing_incomplete
                or recorded_error is not None
            ):
                continue
            status_code = response.get("http_status")
            body = response.get("body")
            if not isinstance(status_code, int) or not 200 <= status_code < 300 or body is None:
                replay_counts["request_failures"] += 1
                recorded_error = record.get("error")
                if isinstance(recorded_error, str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", recorded_error):
                    replay_errors.append(recorded_error)
                else:
                    replay_errors.append("recorded_request_failed")
                continue
            if endpoint == FEED_ID:
                try:
                    posts, _, _ = feed_data(body)
                except (TypeError, ValueError, json.JSONDecodeError):
                    replay_counts["validation_failures"] += 1
                    feed_validation_failures += 1
                    replay_errors.append("feed_response_schema_invalid")
                    continue
                try:
                    post_times = feed_post_times(posts)
                except ValueError:
                    replay_counts["validation_failures"] += 1
                    feed_validation_failures += 1
                    replay_errors.append("feed_response_payload_invalid")
                    continue
                replay_counts["feed_pages"] += 1
                replay_counts["feed_posts"] += len(posts)
                replay_counts["posts_missing_timestamp"] += sum(observed is None for observed in post_times)
                replay_counts["empty_feed_pages"] += not posts
            else:
                try:
                    comments = validate_comment_body(body)
                except (TypeError, ValueError, json.JSONDecodeError):
                    replay_counts["validation_failures"] += 1
                    comment_validation_failures += 1
                    replay_errors.append("comment_response_schema_invalid")
                    continue
                replay_counts["comment_pages"] += 1
                replay_counts["comments"] += len(comments)
        feed_status = (
            "partial" if replay_counts["feed_pages"] and (feed_validation_failures or invalid_recordings)
            else "supported" if replay_counts["feed_pages"]
            else "failed" if feed_records or invalid_recordings
            else "partial"
        )
        comment_status = (
            "partial" if replay_counts["comment_pages"] and (comment_validation_failures or invalid_recordings)
            else "supported" if replay_counts["comment_pages"]
            else "failed" if comment_records
            else "partial"
        )
        live = sum(record.get("evidence_kind") == "live_provider_response" for record in records)
        synthetic = sum(record.get("evidence_kind") == "synthetic_offline" for record in records)
        status = "failed" if not replay_counts["feed_pages"] + replay_counts["comment_pages"] and (
            replay_counts["validation_failures"] or replay_counts["request_failures"]
        ) else "partial"
        report = {
            "ticket": TICKET,
            "source": "reddit",
            "status": status,
            "evidence_kind": "offline_replay",
            "network_requests": 0,
            "recordings_replayed": len(records),
            "expired_recordings_removed": removed,
            "live_recordings": live,
            "synthetic_recordings": synthetic,
            "counts": replay_counts,
            "errors": list(dict.fromkeys(replay_errors)),
            "validation": {
                "feed_schema": feed_status,
                "comment_schema": comment_status,
            },
            "capabilities": {
                "replay": {"status": "failed" if status == "failed" else "supported", "evidence": "no valid recorded pages" if status == "failed" else "read only from unexpired private recordings; no acquisition transport"},
                "feed_schema": {"status": feed_status, "evidence": "replayed stored feed bodies" if replay_counts["feed_pages"] else "no valid recorded feed body"},
                "comment_schema": {"status": comment_status, "evidence": "replayed stored comment bodies" if replay_counts["comment_pages"] else "no valid recorded comment body"},
                "billing_evidence": {"status": "blocked" if replay_counts["billing_unknown_requests"] or billing_ceiling_breach_record_ids else "partial", "evidence": "one or more recordings exceed their reserved request ceiling" if billing_ceiling_breach_record_ids else "one or more recordings lack completed accounting, a verified charge, or a call reference" if replay_counts["billing_unknown_requests"] else "provider-reported charge evidence only; no independent billing reconciliation"},
                "source_coverage": {"status": "partial", "evidence": "recorded responses alone do not establish source completeness"},
            },
            "record_ids": [record["record_id"] for record in records],
            "billing_unknown_record_ids": billing_unknown_record_ids,
            "billing_ceiling_breach_record_ids": billing_ceiling_breach_record_ids,
        }
        report_path = recordings_dir / ("replay-" + uuid.uuid4().hex + ".json")
        now = clock()
        replay_retention_days = min(record["retention_days"] for record in source_records)
        source_expires = min(record_expiry(record) for record in source_records)
        atomic_json(report_path, {
            **report,
            "stage": "reddit_replay_output",
            "recorded_at": timestamp(now),
            "expires_at": timestamp(source_expires),
            "source_expires_at": timestamp(source_expires),
            "retention_days": replay_retention_days,
        })
        report["artifact"] = report_path.name
        print_json(report)
        return 2 if status == "failed" else 0
    except CollectorError as error:
        print_json({
            "ticket": TICKET,
            "source": "reddit",
            "status": error.status,
            "evidence_kind": "offline_replay",
            "network_requests": 0,
            "errors": [error.code],
            "capabilities": {"replay": {"status": "blocked", "evidence": error.code}},
        })
        return 2
    except (OSError, UnicodeError, json.JSONDecodeError):
        print_json({"ticket": TICKET, "source": "reddit", "status": "failed", "evidence_kind": "offline_replay", "network_requests": 0, "errors": ["recording_read_failed"]})
        return 2


def run_reconcile(args, clock):
    if not args.call_id.strip() or not evidence_present(args.evidence) or args.charge_micro < 0:
        print_json({"ticket": TICKET, "source": "reddit", "status": "blocked", "errors": ["reconciliation_input_invalid"]})
        return 2
    if repository_path(args.state_dir):
        print_json({"ticket": TICKET, "source": "reddit", "status": "blocked", "network_requests": 0, "errors": ["private_artifacts_must_be_outside_repository"]})
        return 2
    try:
        with locked_state(pathlib.Path(args.state_dir), clock()) as (state_path, state):
            pending = state.get("pending")
            if not isinstance(pending, dict):
                raise CollectorError("no_pending_request_to_reconcile", "blocked")
            reserved = pending["reserved_micro_usd"]
            if pending.get("call_id") and pending["call_id"] != args.call_id:
                raise CollectorError("reconciled_call_id_does_not_match_response", "failed")
            if isinstance(pending.get("http_success"), bool):
                expected_outcome = "success" if pending["http_success"] else "failed"
                if args.request_outcome != expected_outcome:
                    raise CollectorError("reconciled_outcome_does_not_match_response", "failed")
            if pending.get("charge_known"):
                if pending.get("known_charge_micro_usd") != args.charge_micro:
                    raise CollectorError("reconciled_charge_does_not_match_response", "failed")
                if state["reserved_micro_usd"]:
                    state["reserved_micro_usd"] -= reserved
            else:
                state["spent_micro_usd"] += args.charge_micro
                state["reserved_micro_usd"] -= reserved
            if args.charge_micro > reserved or state["spent_micro_usd"] + state["reserved_micro_usd"] > MAX_SPEND_MICRO_USD:
                state["limit_breach"] = True
            if pending.get("success_slot_reserved"):
                state["reserved_success_slots"] -= 1
                if args.request_outcome == "success":
                    state["successful_requests"] += 1
            state["reconciliations"].append({
                "call_id": safe_string(args.call_id),
                "charge_micro_usd": args.charge_micro,
                "request_outcome": args.request_outcome,
                "evidence": safe_string(args.evidence),
                "reconciled_at": timestamp(clock()),
            })
            state["pending"] = None
            atomic_json(state_path, state)
            print_json({
                "ticket": TICKET,
                "source": "reddit",
                "status": "blocked" if state["limit_breach"] else "supported",
                "evidence_kind": "operator_reconciliation",
                "network_requests": 0,
                "successful_requests": state["successful_requests"],
                "spent_micro_usd": state["spent_micro_usd"],
                "reserved_micro_usd": state["reserved_micro_usd"],
                "limit_breach": state["limit_breach"],
            })
            return 2 if state["limit_breach"] else 0
    except CollectorError as error:
        print_json({"ticket": TICKET, "source": "reddit", "status": error.status, "errors": [error.code], "network_requests": 0})
        return 2


def main(argv=None, transport=None, credentials=None, clock=utc_now, sleeper=time.sleep, credentials_loader=None):
    args = parse_args(argv)
    if args.command == "replay":
        return run_replay(args, clock)
    if args.command == "reconcile":
        return run_reconcile(args, clock)
    synthetic = bool(getattr(transport, "synthetic", False))
    if synthetic and credentials_loader is None and credentials is None:
        print_json({
            "ticket": TICKET,
            "source": "reddit",
            "status": "blocked",
            "network_requests": 0,
            "errors": ["synthetic_transport_requires_test_credentials"],
        })
        return 2
    if credentials_loader is None and synthetic:
        credentials_loader = lambda token_type: credentials
    elif credentials_loader is None:
        credentials_loader = (lambda token_type: credentials) if credentials is not None else load_credentials
    return run_collect(
        args,
        transport or UrllibTransport(),
        credentials_loader,
        clock,
        sleeper,
        synthetic,
    )


if __name__ == "__main__":
    raise SystemExit(main())
