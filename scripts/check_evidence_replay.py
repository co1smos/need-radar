import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import network_guard
from need_radar import evidence
from need_radar.__main__ import json_bytes, redact, run


def fixture_for(items):
    reddit_item = next(item for item in items if item["source"] == "reddit")
    return {
        "notice": "Synthetic normalized evidence replay; no live verification.",
        "items": items,
        "model": {
            "status": "synthetic_response",
            "response": [{
                "title": "Agent context must be rebuilt",
                "friction": "Builders manually rebuild agent context after session resets.",
                "evidence": [{
                    "item_id": reddit_item["id"],
                    "excerpt": reddit_item["text"].split(": ", 1)[1],
                }],
            }],
        },
    }


def write_json(path, value):
    path.write_bytes(json_bytes(value))
    return path


def identity_version_count(ledger):
    return sum(len(versions) for versions in ledger["versions"].values())


def denied(action):
    try:
        action()
    except PermissionError:
        return
    raise AssertionError("offline guard allowed a protected operation")


def verify_guards(scratch):
    network_guard.install()
    denied(lambda: socket.socket())
    denied(lambda: open(network_guard.CREDENTIALS_PATH, "rb"))
    denied(lambda: open(network_guard.LIVE_STATE_PATH + "/ticket-5/state.json", "rb"))
    environment = {
        "TMPDIR": str(scratch),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
    }
    probes = (
        "import socket; socket.socket()",
        f"open({network_guard.CREDENTIALS_PATH!r}, 'rb')",
        f"open({(network_guard.LIVE_STATE_PATH + '/ticket-5/state.json')!r}, 'rb')",
    )
    for probe in probes:
        result = subprocess.run(
            [sys.executable, "-c", probe], env=environment,
            capture_output=True, text=True, check=False, timeout=10,
        )
        if result.returncode == 0 or "disabled in tests" not in result.stderr:
            raise AssertionError("subprocess offline guard did not deny a protected operation")


def verify_stage_artifacts(output):
    with sqlite3.connect(output / "lineage.sqlite3") as database:
        stages = database.execute(
            "SELECT stage, artifact_path, output_sha256 FROM stages ORDER BY sequence"
        ).fetchall()
    if not stages or stages[-1][0] != "report":
        raise AssertionError("report path did not complete")
    for _, relative_path, expected_hash in stages:
        artifact = output / relative_path
        if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_hash:
            raise AssertionError("report stage artifact does not match its lineage hash")


def main():
    parser = argparse.ArgumentParser(description="Prove synthetic evidence identity and report replay.")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    scratch = Path(os.environ.get("TMPDIR", ""))
    if not scratch.is_dir():
        raise RuntimeError("TMPDIR must point to the existing Hermes scratch directory")
    if arguments.output.exists() and any(arguments.output.iterdir()):
        raise ValueError("output directory must be empty")
    arguments.output.mkdir(parents=True, exist_ok=True)
    verify_guards(scratch)

    fixture = redact(json.loads((ROOT / "fixtures" / "evidence_replay.json").read_text(encoding="utf-8")))
    ledger = {}
    initial_items = evidence.import_normalized_items(ledger, fixture["initial_import"])
    if len(ledger["contents"]) != 1 or identity_version_count(ledger) != 3 or len(initial_items) != 3:
        raise AssertionError("exact cross-identity content was not stored once with distinct identities")
    if [item["id"] for item in initial_items] != [
        "reddit:alias-42", "reddit:shared-42", "x:shared-42",
    ]:
        raise AssertionError("source-qualified identities did not distinguish Reddit and X")
    if len(initial_items[0]["discovery_origins"]) != 3:
        raise AssertionError("discovery origins were not preserved")

    ledger_path = write_json(arguments.output / "evidence-ledger.json", ledger)
    ledger = json.loads(ledger_path.read_bytes())
    initial_ledger_bytes = ledger_path.read_bytes()
    initial_input = write_json(arguments.output / "snapshot-1-input.json", fixture_for(initial_items))
    first_output = arguments.output / "snapshot-1"
    if run(initial_input, first_output) != "success":
        raise AssertionError("initial synthetic report did not succeed")
    verify_stage_artifacts(first_output)
    initial_selection = json.loads((first_output / "selection.json").read_bytes())
    if len(initial_items) != 3 or len(initial_selection["items"]) != 1:
        raise AssertionError("identical content inflated extraction demand")
    if [item["id"] for item in initial_selection["items"][0]["identity_versions"]] != [
        "reddit:alias-42", "reddit:shared-42", "x:shared-42",
    ]:
        raise AssertionError("selection did not preserve all source-qualified identity versions")
    first_snapshot_bytes = (first_output / "snapshot.json").read_bytes()
    if [item["id"] for item in json.loads(first_snapshot_bytes)["items"]] != [
        "reddit:alias-42", "reddit:shared-42", "x:shared-42",
    ]:
        raise AssertionError("snapshot did not preserve separate source-qualified identities")
    first_item_bytes = json_bytes(json.loads(first_snapshot_bytes)["items"])
    first_report_bytes = (first_output / "report.md").read_bytes()

    repeated_items = evidence.import_normalized_items(ledger, fixture["initial_import"])
    if len(ledger["contents"]) != 1 or identity_version_count(ledger) != 3 or repeated_items != initial_items:
        raise AssertionError("identical re-import inflated evidence or demand")
    write_json(ledger_path, ledger)
    if ledger_path.read_bytes() != initial_ledger_bytes:
        raise AssertionError("persisted exact re-import was not byte-identical")

    edited_items = evidence.import_normalized_items(ledger, fixture["edited_import"])
    if len(ledger["contents"]) != 2 or identity_version_count(ledger) != 4 or len(edited_items) != 3:
        raise AssertionError("edit did not create a version without inflating independent demand")
    prior_content = next(item for item in edited_items if "Synthetic Reddit evidence:" in item["text"])
    if [item["id"] for item in prior_content["identity_versions"]] != [
        "reddit:alias-42", "reddit:shared-42", "x:shared-42",
    ]:
        raise AssertionError("editing one duplicate lost its prior identity or origin provenance")
    edited_input = write_json(arguments.output / "snapshot-2-input.json", fixture_for(edited_items))
    edited_output = arguments.output / "snapshot-2"
    if run(edited_input, edited_output) != "success":
        raise AssertionError("edited synthetic report did not succeed")
    verify_stage_artifacts(edited_output)
    edited_selection = json.loads((edited_output / "selection.json").read_bytes())
    if len(edited_items) != 3 or len(edited_selection["items"]) != 2:
        raise AssertionError("edited content did not create one additional demand item")
    prior_selection = next(
        item for item in edited_selection["items"] if "Synthetic Reddit evidence:" in item["text"]
    )
    if [item["id"] for item in prior_selection["identity_versions"]] != [
        "reddit:alias-42", "reddit:shared-42", "x:shared-42",
    ]:
        raise AssertionError("report selection lost provenance when an identity was edited")
    write_json(ledger_path, ledger)

    frozen_input = write_json(
        arguments.output / "snapshot-1-replay-input.json",
        fixture_for(json.loads(first_snapshot_bytes)["items"]),
    )
    replay_output = arguments.output / "snapshot-1-replay"
    if run(frozen_input, replay_output) != "success":
        raise AssertionError("original synthetic report replay did not succeed")
    replay_snapshot = json.loads((replay_output / "snapshot.json").read_bytes())
    if json_bytes(replay_snapshot["items"]) != first_item_bytes:
        raise AssertionError("replayed snapshot changed ordered evidence bytes")
    if (replay_output / "report.md").read_bytes() != first_report_bytes:
        raise AssertionError("replayed report changed original bytes")
    if (first_output / "snapshot.json").read_bytes() != first_snapshot_bytes:
        raise AssertionError("later imports modified the frozen snapshot")
    verify_stage_artifacts(replay_output)

    for artifact in arguments.output.rglob("*"):
        if artifact.is_file() and b"sk-REPLAYFIXTURESECRET" in artifact.read_bytes():
            raise AssertionError("synthetic secret marker leaked into a persisted artifact")
    print(json.dumps({
        "status": "passed",
        "evidence_kind": "synthetic_offline",
        "source_qualified_identities": len(ledger["versions"]),
        "identity_versions": identity_version_count(ledger),
        "content_versions": len(ledger["contents"]),
        "independent_items_before_edit": len(initial_selection["items"]),
        "independent_items_after_edit": len(edited_selection["items"]),
        "discovery_origins_before_edit": len(initial_items[0]["discovery_origins"]),
        "snapshot_and_report_replay": "stable",
        "artifact_lineage_hashes": "verified",
        "network_credential_live_state_guards": "denied_in_process_and_subprocess",
        "network_requests": 0,
        "retention_and_derived_erasure": "not tested; separately gated",
        "verification_limit": "synthetic fixtures do not verify live sources or provider formats",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
