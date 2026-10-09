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
from need_radar.__main__ import DEFAULT_FIXTURE, run


def denied(action):
    try:
        action()
    except PermissionError:
        return
    raise AssertionError("offline guard allowed a protected operation")


def verify_guards(scratch):
    network_guard.install()
    denied(socket.socket)
    denied(lambda: open(network_guard.CREDENTIALS_PATH, "rb"))
    denied(lambda: open(network_guard.LIVE_STATE_PATH + "/assessment/state.json", "rb"))
    environment = {
        "TMPDIR": str(scratch),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
    }
    probes = (
        "import socket; socket.socket()",
        f"open({network_guard.CREDENTIALS_PATH!r}, 'rb')",
        f"open({(network_guard.LIVE_STATE_PATH + '/assessment/state.json')!r}, 'rb')",
    )
    for probe in probes:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0 or "disabled in tests" not in result.stderr:
            raise AssertionError("subprocess offline guard did not deny a protected operation")


def verify_artifacts(output):
    with sqlite3.connect(output / "lineage.sqlite3") as database:
        stages = database.execute(
            "SELECT stage, artifact_path, output_sha256, artifact_id, input_artifact_id "
            "FROM stages ORDER BY sequence"
        ).fetchall()
    by_stage = {row[0]: row for row in stages}
    if not {"report", "consolidation", "judge_prompt", "judge_call", "assessment"} <= set(by_stage):
        raise AssertionError("assessment stages are incomplete")
    for _, relative_path, expected_hash, _, _ in stages:
        artifact = output / relative_path
        if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_hash:
            raise AssertionError("stage artifact does not match its lineage hash")
    if by_stage["assessment"][4] != by_stage["judge_call"][3]:
        raise AssertionError("assessment does not link to the judge response")
    if not stages.index(by_stage["report"]) < stages.index(by_stage["consolidation"]):
        raise AssertionError("assessment branch did not start after report freeze")
    prompt_artifact = json.loads((output / "judge-prompt.json").read_text())
    assessment = json.loads((output / "assessment.json").read_text())
    for artifact_key, stage_name in (
        ("candidate_validation", "validation"),
        ("snapshot", "snapshot"),
        ("consolidation", "consolidation"),
        ("frozen_report", "report"),
    ):
        for linked in (prompt_artifact["source_artifacts"][artifact_key], assessment["source_artifacts"][artifact_key]):
            if linked["artifact_id"] != by_stage[stage_name][3] or linked["sha256"] != by_stage[stage_name][2]:
                raise AssertionError(f"{artifact_key} lineage reference does not resolve")
    report = output / by_stage["report"][1]
    if hashlib.sha256(report.read_bytes()).hexdigest() != assessment["frozen_report"]["sha256"]:
        raise AssertionError("assessment changed or mislinked the canonical report")
    for artifact in output.rglob("*"):
        if artifact.is_file() and b"SYNTHETICONLY1234567890" in artifact.read_bytes():
            raise AssertionError("synthetic secret marker leaked into an artifact")
    return assessment


def main():
    parser = argparse.ArgumentParser(description="Verify the synthetic, offline assessment slice.")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    scratch = Path(os.environ.get("TMPDIR", ""))
    expected_scratch = Path.home() / ".hermes" / "cache" / "scratch"
    if not scratch.is_dir() or scratch.resolve() != expected_scratch.resolve():
        raise RuntimeError("TMPDIR must be the existing Hermes cache scratch directory")
    try:
        arguments.output.resolve().relative_to(scratch.resolve())
    except ValueError as error:
        raise RuntimeError("check output must stay under Hermes TMPDIR") from error
    if arguments.output.exists() and any(arguments.output.iterdir()):
        raise ValueError("output directory must be empty")
    arguments.output.mkdir(parents=True, exist_ok=True)
    verify_guards(scratch)
    status = run(DEFAULT_FIXTURE, arguments.output)
    if status != "success":
        raise AssertionError(f"synthetic serve report failed: {status}")
    assessment = verify_artifacts(arguments.output)
    if assessment["status"] != "success" or not assessment["assessments"]:
        raise AssertionError("synthetic judge response did not produce an assessment")
    prompts = json.loads((arguments.output / "judge-prompt.json").read_text())["prompts"]
    if len(prompts) != len(assessment["assessments"]):
        raise AssertionError("judge prompts were not isolated per consolidated candidate")
    if any(len(json.loads(prompt["prompt"]["messages"][1]["content"].split("\n", 1)[1])) != 1 for prompt in prompts):
        raise AssertionError("a judge prompt contains more than one candidate")
    verdict = assessment["assessments"][0]["verdict"]
    if verdict != "NEEDS_EVIDENCE":
        raise AssertionError("synthetic uncertainty fixture produced an unexpected verdict")
    print(json.dumps({
        "status": "passed",
        "evidence_kind": "synthetic_offline",
        "verdict": verdict,
        "report_frozen": "verified",
        "artifact_lineage_hashes": "verified",
        "network_credential_live_state_guards": "denied_in_process_and_subprocess",
        "network_requests": 0,
        "live_verification": "not performed",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
