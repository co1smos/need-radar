#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

from network_guard import CREDENTIALS_PATH, LIVE_STATE_PATH, install as install_offline_guards

install_offline_guards()
sys.path.insert(0, str(ROOT))

from need_radar.snapshot import read_frozen_snapshot
from need_radar.__main__ import redact
from need_radar.source_adapter import (
    normalization_prompt,
    prepare_source_results,
    source_results_from_recordings,
    validate_canonical_evidence,
)


MAX_INPUT_BYTES = 16 * 1024 * 1024


class ReplayFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReplayFailure(message)


def _private(path, expected_mode):
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        not path.is_symlink()
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == expected_mode
        and (stat.S_ISDIR(info.st_mode) if expected_mode == 0o700 else stat.S_ISREG(info.st_mode))
    )


def _read_recording(path):
    require(_private(path.parent, 0o700), "recording directory is not private")
    require(_private(path, 0o600), "recording file is not private")
    content = path.read_bytes()
    require(len(content) <= MAX_INPUT_BYTES, "recording exceeds the local read limit")
    return json.loads(content), hashlib.sha256(content).hexdigest()


def _read_x_retention_policy(folder, recording_name, recording_hash, recording):
    policies = []
    for path in folder.glob("*.json"):
        if not _private(path, 0o600) or path.stat().st_size > 64 * 1024:
            continue
        try:
            policy = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(policy, dict):
            continue
        historical = policy.get("historical_metadata_unchanged")
        if not isinstance(historical, dict):
            continue
        if (
            policy.get("ticket") == 9
            and policy.get("original_record") == recording_name
            and policy.get("original_sha256") == recording_hash
            and str(policy.get("owner_policy", "")).startswith("retain_until_owner_revokes")
            and "adapter" in str(policy.get("purpose", "")).lower()
            and historical.get("expires_at_epoch") == recording.get("expires_at_epoch")
            and policy.get("revoked") is not True
        ):
            policies.append(policy)
    require(len(policies) == 1, "X owner retention evidence is missing or ambiguous")
    return policies[0]


def _verify_retention(reddit, x_recording, x_policy):
    retention = reddit.get("retention") if isinstance(reddit, dict) else None
    require(
        isinstance(reddit, dict)
        and isinstance(retention, dict)
        and isinstance(x_recording, dict)
        and isinstance(x_policy, dict)
        and
        reddit.get("evidence_kind") == "live_provider_response"
        and reddit.get("retention_mode") == "owner_revocable"
        and isinstance(reddit.get("response"), dict)
        and reddit["response"].get("http_status") == 200
        and reddit.get("processing_complete") is True
        and reddit.get("expires_at") is None
        and reddit.get("retention_days") is None
        and retention.get("owner_authorized_until_revoked") is True
        and retention.get("private_access_owner_only") is True,
        "Reddit recording retention or owner authorization is not valid",
    )
    expires = x_recording.get("expires_at_epoch")
    recorded = x_recording.get("recorded_at_epoch")
    seconds = x_recording.get("retention_seconds")
    require(
        x_recording.get("ticket") == 9
        and x_recording.get("artifact_type") == "authentic_sanitized_response"
        and x_recording.get("http_status") == 200
        and type(recorded) is int
        and type(expires) is int
        and type(seconds) is int
        and expires == recorded + seconds
        and "seven-day" in str(x_policy.get("supersedes", "")).lower()
        and "adapter" in str(x_policy.get("supersedes", "")).lower()
        and x_policy.get("privacy") == "private directory 0700, files 0600, gitignored; no credential headers",
        "X recording does not match its owner retention policy",
    )


def _profile(rows, depth=0):
    if depth >= 4 or not rows:
        return {"count": len(rows)}
    keys = sorted({key for row in rows if isinstance(row, dict) for key in row})
    fields = {}
    for key in keys:
        values = [row[key] for row in rows if isinstance(row, dict) and key in row]
        types = {}
        for value in values:
            kind = (
                "null" if value is None else "boolean" if isinstance(value, bool)
                else "number" if isinstance(value, (int, float)) else "string" if isinstance(value, str)
                else "array" if isinstance(value, list) else "object" if isinstance(value, dict)
                else type(value).__name__
            )
            types[kind] = types.get(kind, 0) + 1
        entry = {"present": len(values), "types": types}
        objects = [value for value in values if isinstance(value, dict)]
        if objects:
            entry["children"] = _profile(objects, depth + 1)
        arrays = [item for value in values if isinstance(value, list) for item in value if isinstance(item, dict)]
        if arrays:
            entry["array_items"] = _profile(arrays, depth + 1)
        fields[key] = entry
    return {"count": len(rows), "fields": fields}


def _provider_shapes(reddit, x_recording):
    body = reddit["response"]["body"]
    elements = body["data"]["subredditV3"]["elements"]
    edges = elements["edges"]
    node_types = {}
    cell_types = {}
    cell_rows = {}
    for edge in edges:
        node = edge.get("node", {}) if isinstance(edge, dict) else {}
        kind = node.get("__typename", "<missing>")
        node_types[kind] = node_types.get(kind, 0) + 1
        for cell in node.get("cells", []) if isinstance(node, dict) else []:
            if not isinstance(cell, dict):
                continue
            cell_kind = cell.get("__typename", "<missing>")
            cell_types[cell_kind] = cell_types.get(cell_kind, 0) + 1
            cell_rows.setdefault(cell_kind, []).append(cell)
    x_response = x_recording["response"]
    tweets = x_response["data"]
    reference_types = {}
    for tweet in tweets:
        for reference in tweet.get("referenced_tweets", []) if isinstance(tweet, dict) else []:
            if isinstance(reference, dict):
                kind = reference.get("type", "<missing>")
                reference_types[kind] = reference_types.get(kind, 0) + 1
    return {
        "reddit": {
            "http_status": reddit["response"].get("http_status"),
            "response_fields": sorted(reddit["response"]),
            "body_fields": sorted(body),
            "data_fields": sorted(body["data"]),
            "edge_count": len(edges),
            "node_type_counts": node_types,
            "cell_type_counts": cell_types,
            "cell_field_profiles": {kind: _profile(rows) for kind, rows in sorted(cell_rows.items())},
            "page_info_fields": sorted(elements.get("pageInfo", {})),
            "comment_or_reply_records_supplied": any(
                "comment" in key.lower() or "reply" in key.lower()
                for edge in edges if isinstance(edge, dict)
                for node in [edge.get("node", {})] if isinstance(node, dict)
                for key in node
            ),
        },
        "x": {
            "http_status": x_recording.get("http_status"),
            "response_fields": sorted(x_response),
            "record_count": len(tweets),
            "record_field_profile": _profile(tweets),
            "reference_type_counts": reference_types,
            "next_page_present": bool(x_response.get("meta", {}).get("next_token")),
            "reply_context_count": sum(bool(replies) for replies in (
                [reference for reference in tweet.get("referenced_tweets", [])
                 if isinstance(reference, dict) and reference.get("type") == "replied_to"]
                for tweet in tweets if isinstance(tweet, dict)
            )),
        },
    }


def _assert_offline_guard():
    try:
        socket.socket()
    except PermissionError:
        pass
    else:
        raise ReplayFailure("network guard did not block socket creation")
    for path, mode in ((CREDENTIALS_PATH, "rb"), (LIVE_STATE_PATH + "/ticket-20/state.json", "wb")):
        try:
            open(path, mode)
        except PermissionError:
            pass
        else:
            raise ReplayFailure("protected path guard did not block access")


def _child_environment(hermes_tmp):
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(hermes_tmp),
        "HOME": str(hermes_tmp),
    }


def _secure_tree(path):
    for child in path.rglob("*"):
        require(not child.is_symlink(), "replay output contains a symlink")
        os.chmod(child, 0o700 if child.is_dir() else 0o600)
    os.chmod(path, 0o700)


def _write_private_json(path, value):
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(content).hexdigest()


def _brief_profile(profile):
    return {
        field: {"present": value["present"], "types": value["types"]}
        for field, value in profile.get("fields", {}).items()
    }


def replay(reddit_path, x_path, output_path):
    os.umask(0o077)
    _assert_offline_guard()
    reddit, reddit_hash = _read_recording(reddit_path)
    x_recording, x_hash = _read_recording(x_path)
    x_policy = _read_x_retention_policy(x_path.parent, x_path.name, x_hash, x_recording)
    _verify_retention(reddit, x_recording, x_policy)
    require(
        not output_path.resolve().is_relative_to(ROOT),
        "derived source data must be written outside the Git worktree",
    )
    parent = output_path.parent
    if not parent.exists():
        parent.mkdir(mode=0o700)
    require(_private(parent, 0o700), "replay output parent is not private")
    require(not output_path.exists(), "replay output directory already exists")
    output_path.mkdir(mode=0o700)
    os.chmod(output_path, 0o700)

    source_results = source_results_from_recordings(
        reddit, x_recording, {"reddit": reddit_hash, "x": x_hash}
    )
    source_results = redact(source_results)
    require(redact(source_results) == source_results, "source redaction is not stable")
    prepared = prepare_source_results(source_results)
    require(prepared["records"], "recordings produced no adapter records")
    prompt = normalization_prompt(prepared)
    require(
        '"records": [' in prompt["messages"][1]["content"]
        and sum(prepared["input_counts"].values()) > 0,
        "recorded source content did not reach the shared normalization prompt",
    )
    fake_normalization = {
        "results": [
            {"record_id": record["id"], "normalized_text": "Local fake normalization response"}
            for record in prepared["records"]
            if not record["flags"]["withheld"]
        ]
    }
    fixture = {
        "notice": "Private authentic source recordings; local fake model responses only; no remote calls.",
        "source_results": source_results,
        "normalization_model": {"status": "synthetic_response", "response": fake_normalization},
        "items": [],
        "model": {"status": "synthetic_response", "response": []},
    }
    input_path = output_path / "input.json"
    _write_private_json(input_path, fixture)
    serve_path = output_path / "serve"
    hermes_tmp = Path(os.environ.get("TMPDIR", str(Path.home() / ".hermes/cache/scratch"))).resolve()
    require(
        hermes_tmp == (Path.home() / ".hermes/cache/scratch").resolve(),
        "replay requires the Hermes TMPDIR",
    )
    require(_private(hermes_tmp, 0o700), "Hermes TMPDIR is not private")
    code = """
import network_guard, os, runpy, socket, sys
network_guard.install()
assert os.environ.get('TMPDIR') == sys.argv[1]
assert not any(any(marker in name.lower() for marker in ('secret', 'token', 'credential', 'api_key', 'password')) for name in os.environ)
try: socket.socket()
except PermissionError: pass
else: raise SystemExit('network guard did not block socket creation')
for path, mode in ((network_guard.CREDENTIALS_PATH, 'rb'), (network_guard.LIVE_STATE_PATH + '/ticket-20/state.json', 'wb')):
    try: open(path, mode)
    except PermissionError: pass
    else: raise SystemExit('protected path guard did not block access')
sys.argv = ['need-radar', '--fixture', sys.argv[2], '--output', sys.argv[3]]
runpy.run_module('need_radar', run_name='__main__')
"""
    environment = _child_environment(hermes_tmp)
    child = subprocess.run(
        [sys.executable, "-c", code, str(hermes_tmp), str(input_path), str(serve_path)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    require(child.returncode == 0, f"offline serve replay failed with exit {child.returncode}")
    _secure_tree(serve_path)

    snapshot = read_frozen_snapshot(serve_path / "snapshot.json")
    errors = validate_canonical_evidence(snapshot["items"])
    require(not errors, "frozen snapshot failed canonical evidence validation")
    require(snapshot["source_normalization"]["status"] == "partial", "recorded coverage was mislabeled complete")
    require(
        all(snapshot["source_normalization"]["coverage"]["sources"][source]["status"] == "partial"
            for source in ("reddit", "x")),
        "per-source partial coverage was not preserved",
    )
    require(len(snapshot["items"]) == len(prepared["records"]), "adapter changed or omitted identified records")
    expected_id_times = {
        (record["source"], record["source_id"]): record["timestamp"]
        for record in prepared["records"]
    }
    actual_id_times = {
        (item["source"], item["source_id"]): item["timestamp"]
        for item in snapshot["items"]
    }
    require(actual_id_times == expected_id_times, "source identity or timestamp changed")
    require(
        all(item["flags"]["partial_coverage"] is True for item in snapshot["items"]),
        "source pagination coverage was not preserved",
    )
    require(
        all(item["discovery_origin"].get("recording_sha256") for item in snapshot["items"]),
        "recording provenance was lost",
    )
    require(
        all(item["flags"]["deleted"] is None for item in snapshot["items"]),
        "unknown deletion state was fabricated",
    )
    require(
        not any(item["source"] == "reddit" and item["kind"] == "comment" for item in snapshot["items"]),
        "missing Reddit comments were fabricated",
    )
    require(
        sum(item["source"] == "reddit" for item in snapshot["items"]) == len(
            [item for item in prepared["records"] if item["source"] == "reddit"]
        ),
        "Reddit source-qualified identity count changed",
    )
    require(
        sum(item["source"] == "x" and item["relationships"]["reply_to_id"] is not None
            for item in snapshot["items"])
        == sum(item["source"] == "x" and item["relationships"]["reply_to_id"] is not None
               for item in prepared["records"]),
        "X reply context changed",
    )
    expected_relations = {
        (item["source"], item["source_id"]): item["relationships"]
        for item in prepared["records"]
    }
    actual_relations = {
        (item["source"], item["source_id"]): item["relationships"]
        for item in snapshot["items"]
    }
    require(actual_relations == expected_relations, "thread or reply references changed")
    prompt = json.loads((serve_path / "prompt.json").read_text(encoding="utf-8"))
    prompt_items = json.loads(prompt["messages"][1]["content"].split("\n", 1)[1])
    require(prompt_items, "frozen selected source context did not reach v0 extraction")
    model_response = json.loads((serve_path / "model-response.json").read_text(encoding="utf-8"))
    require(model_response.get("execution_seam") == "execute_extraction", "v0 did not use shared extraction seam")
    require(model_response.get("status") == "success", "local fake v0 response was not accepted")
    report = (serve_path / "report.md").read_text(encoding="utf-8")
    require("private recorded inputs only" in report.lower(), "canonical report mislabels authentic input")
    require("Private recorded replay only" in report, "canonical report omits offline replay boundary")
    require(
        "Model responses use predetermined local fixtures." in report,
        "canonical report does not identify the local fake model response",
    )
    require(
        hashlib.sha256(reddit_path.read_bytes()).hexdigest() == reddit_hash
        and hashlib.sha256(x_path.read_bytes()).hexdigest() == x_hash,
        "source recording changed during replay",
    )

    proof = {
        "status": "passed",
        "evidence_kind": "authentic_private_recording_offline_replay",
        "source_recordings": {
            "reddit": {"sha256": reddit_hash, "unchanged": True, "retention": "owner-revocable; unexpired"},
            "x": {"sha256": x_hash, "unchanged": True, "retention": "owner-retained for adapter use until revoked; old expiry superseded"},
        },
        "rights": {
            "owner_authorization": "confirmed for private adapter replay by existing #9/#19 records and docs/decisions.md decision 75",
            "reddit_provider_route_and_use": "unverified; Reddit Data API Terms §§2.1, 2.4-2.5 do not establish route applicability",
            "x_storage_freshness": "unverified; X Developer Policy requires stored content to track current source state",
            "live_use": "not authorized by this replay",
        },
        "provider_shapes": _provider_shapes(reddit, x_recording),
        "canonical": {
            "item_count": len(snapshot["items"]),
            "source_counts": {
                source: sum(item["source"] == source for item in snapshot["items"])
                for source in ("reddit", "x")
            },
            "validation": "passed",
            "coverage": {
                source: snapshot["source_normalization"]["coverage"]["sources"][source]["status"]
                for source in ("reddit", "x")
            },
            "partial_first_page_preserved": True,
            "comments_or_replies_fabricated": False,
            "timestamps_and_source_qualified_ids_preserved": True,
            "discovery_lineage_preserved": True,
        },
        "normalization": "local fake response through the shared source normalization prompt and adapter",
        "redaction": "shared redactor applied before derived fixture persistence",
        "v0_extraction": {
            "boundary": "local fake response through execute_extraction",
            "frozen_context_item_count": len(prompt_items),
            "result": "no_findings",
        },
        "denials": {
            "network": "verified denied in replay process and serve subprocess",
            "credentials": "verified denied in replay process and serve subprocess",
            "canonical_live_state": "verified denied in replay process and serve subprocess",
            "remote_model_calls": 0,
        },
        "artifact_hashes": {
            "snapshot": hashlib.sha256((serve_path / "snapshot.json").read_bytes()).hexdigest(),
            "canonical_report": hashlib.sha256((serve_path / "report.md").read_bytes()).hexdigest(),
            "v0_prompt": hashlib.sha256((serve_path / "prompt.json").read_bytes()).hexdigest(),
        },
        "gaps": [
            "Reddit recording contains feed pages only; comment and parent-thread fields are absent.",
            "Reddit edited/deleted flags and X deletion state were not supplied and remain unknown.",
            "X recording contains one page with a continuation token; unreturned pages are unknown.",
            "Reddit detailsLink values were not accepted as absolute permalinks, and X supplied no URL field; no URL was synthesized.",
            "X quoted and reposted references are outside the canonical thread/reply fields and remain unrepresented.",
        ],
    }
    _write_private_json(output_path / "proof.json", proof)
    _secure_tree(output_path)
    return proof


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay private #9/#19 recordings through the shared adapter offline.")
    parser.add_argument("--reddit-record", required=True, type=Path)
    parser.add_argument("--x-record", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        proof = replay(args.reddit_record, args.x_record, args.output.resolve())
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ReplayFailure, json.JSONDecodeError):
        print("issue 20 replay failed; private payload details withheld", file=sys.stderr)
        return 1
    summary = {
        key: proof[key]
        for key in (
            "status", "evidence_kind", "rights", "provider_shapes", "canonical",
            "normalization", "v0_extraction", "denials", "gaps",
        )
    }
    reddit_shape = proof["provider_shapes"]["reddit"]
    x_shape = proof["provider_shapes"]["x"]
    summary["provider_shapes"] = {
        "reddit": {
            key: reddit_shape[key]
            for key in ("http_status", "response_fields", "body_fields", "data_fields", "edge_count", "node_type_counts", "cell_type_counts", "page_info_fields", "comment_or_reply_records_supplied")
        },
        "reddit_cell_fields": {
            kind: _brief_profile(profile)
            for kind, profile in reddit_shape["cell_field_profiles"].items()
        },
        "x": {
            key: x_shape[key]
            for key in ("http_status", "response_fields", "record_count", "reference_type_counts", "next_page_present", "reply_context_count")
        },
        "x_record_fields": _brief_profile(x_shape["record_field_profile"]),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
