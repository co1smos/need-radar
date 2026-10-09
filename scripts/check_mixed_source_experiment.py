import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HERMES_TMPDIR = Path.home() / ".hermes" / "cache" / "scratch"
SOURCE_FIXTURE = ROOT / "fixtures" / "source_results" / "synthetic_reddit_x.json"
DEMO_FIXTURE = ROOT / "fixtures" / "synthetic_demo.json"
SHARED_SETTINGS = {
    "provider": "synthetic_fixture",
    "model": "offline-recorded-response",
    "model_settings": {"fixture_mode": "synthetic"},
    "schema": {
        "version": 1,
        "candidate_fields": ["title", "friction", "evidence"],
        "evidence_fields": ["item_id", "excerpt"],
    },
    "retry_policy": {"max_attempts": 1},
    "evidence_policy": {"scope": "frozen_context", "citation": "exact_excerpt"},
    "request_limit": 1,
    "max_tokens": 64,
    "candidate_limit": 10,
}


def safe_environment():
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(HERMES_TMPDIR),
        "HOME": str(HERMES_TMPDIR),
    }


def assert_offline_guards(environment):
    import network_guard

    network_guard.install()
    try:
        socket.socket()
    except PermissionError:
        pass
    else:
        raise AssertionError("network access was not denied")
    for protected_path in (network_guard.CREDENTIALS_PATH, network_guard.LIVE_STATE_PATH):
        try:
            open(protected_path, "rb")
        except PermissionError:
            continue
        raise AssertionError("protected credentials or live state were accessible")

    probes = (
        "import socket; socket.socket()",
        "open('/home/ubuntu/projects/need-radar/credentials.env', 'rb')",
        "open('/home/ubuntu/.local/state/need-radar/assessment/state.json', 'rb')",
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


def build_serve_fixture():
    fixture = json.loads(SOURCE_FIXTURE.read_text(encoding="utf-8"))
    reddit_comment = fixture["source_results"]["reddit"]["posts"][0]["comments"][0]
    x_post = fixture["source_results"]["x"]["posts"][0]
    x_post["text"] = reddit_comment["body"]
    normalized = fixture["normalization_model"]["response"]["results"]
    reddit_normalized_text = next(
        row["normalized_text"] for row in normalized
        if row["record_id"] == "reddit:reddit-comment-001"
    )
    next(
        row for row in normalized
        if row["record_id"] == "x:100000000000000001"
    )["normalized_text"] = reddit_normalized_text

    fixture["notice"] = (
        "Synthetic offline Reddit+X experiment. The X fixture record intentionally duplicates a Reddit comment; "
        "no source or model was contacted."
    )
    fixture["model"]["usage"] = {"requests": 1, "tokens": 19}
    fixture["experiment"] = {"shared_settings": SHARED_SETTINGS}
    fixture["judge"] = json.loads(DEMO_FIXTURE.read_text(encoding="utf-8"))["judge"]
    fixture["judge"]["response"][0]["evidence_ids"] = ["reddit:reddit-comment-001"]
    for dimension in fixture["judge"]["response"][0]["dimensions"].values():
        dimension["evidence_ids"] = ["reddit:reddit-comment-001"]
    return fixture


def _run(command, environment):
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"command failed: {command[1]}")
    return result


def run(output):
    if not HERMES_TMPDIR.is_dir():
        raise RuntimeError(f"Hermes TMPDIR is unavailable: {HERMES_TMPDIR}")
    environment = safe_environment()
    assert_offline_guards(environment)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    serve_fixture = build_serve_fixture()
    serve_fixture_path = output / "serve-fixture.json"
    serve_fixture_path.write_text(json.dumps(serve_fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    serve_dir = output / "serve"
    _run(
        [sys.executable, "-m", "need_radar", "--fixture", str(serve_fixture_path), "--output", str(serve_dir)],
        environment,
    )

    shadow_candidate = json.loads(json.dumps(serve_fixture["model"]["response"][0]))
    shadow_candidate["friction"] = "A builder repeatedly rebuilds tool context after agent-session resets."
    shadow_fixture = {
        "provider": "synthetic_fixture",
        "experiment": {
            "shared_settings": SHARED_SETTINGS,
            "shadow_model": {
                "status": "synthetic_response",
                "response": [shadow_candidate],
                "usage": {"requests": 1, "tokens": 10},
            },
            "shadow_assessment": {
                "status": "synthetic_response",
                "response": serve_fixture["judge"]["response"],
            },
        },
    }
    shadow_fixture_path = output / "shadow-fixture.json"
    shadow_fixture_path.write_text(json.dumps(shadow_fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shadow_dir = output / "shadow"
    _run(
        [
            sys.executable,
            "-m",
            "need_radar.shadow",
            "--serve-dir",
            str(serve_dir),
            "--fixture",
            str(shadow_fixture_path),
            "--output",
            str(shadow_dir),
        ],
        environment,
    )

    snapshot = json.loads((serve_dir / "snapshot.json").read_text(encoding="utf-8"))
    selection = json.loads((serve_dir / "selection.json").read_text(encoding="utf-8"))
    serve_prompt = json.loads((serve_dir / "prompt.json").read_text(encoding="utf-8"))
    shadow_prompt = json.loads((shadow_dir / "v1" / "prompt.json").read_text(encoding="utf-8"))
    comparison_result = json.loads((shadow_dir / "comparison.json").read_text(encoding="utf-8"))
    from need_radar.source_adapter import validate_canonical_evidence

    if validate_canonical_evidence(snapshot.get("items")):
        raise AssertionError("frozen snapshot is not canonical evidence")
    coverage = snapshot["source_normalization"]["coverage"]
    duplicate_groups = selection["cross_source_duplicate_groups"]
    if coverage["sources"]["reddit"]["status"] != "partial":
        raise AssertionError("Reddit partial fixture was not reported")
    if coverage["sources"]["x"]["status"] != "synthetic_complete":
        raise AssertionError("X fixture coverage was not reported")
    if len(duplicate_groups) != 1 or duplicate_groups[0]["independent_item_count"] != 1:
        raise AssertionError("cross-source duplicate was not consolidated")
    duplicate_identities = {identity["id"] for identity in duplicate_groups[0]["identities"]}
    if duplicate_identities != {"reddit:reddit-comment-001", "x:100000000000000001"}:
        raise AssertionError("cross-source duplicate identity provenance was not preserved")
    if any(not isinstance(identity.get("discovery_origin"), dict) for identity in duplicate_groups[0]["identities"]):
        raise AssertionError("duplicate discovery origins were not preserved")
    if comparison_result["status"] != "complete" or not comparison_result["parity"]["same_context"]:
        raise AssertionError("serve/shadow comparison did not complete on a shared frozen context")
    if comparison_result["source_coverage"] != coverage:
        raise AssertionError("source coverage does not match the frozen snapshot")
    if comparison_result["cross_source_duplicate_groups"] != duplicate_groups:
        raise AssertionError("comparison does not retain duplicate identity provenance")
    if serve_prompt["messages"][1]["content"].encode() != shadow_prompt["messages"][1]["content"].encode():
        raise AssertionError("serve and shadow ordered inputs differ")
    for arm in ("v0", "v1"):
        if comparison_result["assessment_comparison"]["arms"][arm]["verdict_counts"]["NEEDS_EVIDENCE"] != 1:
            raise AssertionError("fixed assessment uncertainty was not preserved")

    report_path = serve_dir / "report.md"
    comparison_path = shadow_dir / "comparison.md"
    report = report_path.read_text(encoding="utf-8")
    comparison_report = comparison_path.read_text(encoding="utf-8")
    if "not independent demand per source" not in report or "live source completeness is unverified" not in comparison_report:
        raise AssertionError("canonical reports omit duplicate or verification limits")
    if "omissions 1" not in report or "failures 0" not in report or "additional omissions unknown" not in report:
        raise AssertionError("serve report omits source-level coverage counts")

    return {
        "status": "passed",
        "evidence_kind": "synthetic_offline",
        "serve_status": "success",
        "shadow_status": comparison_result["status"],
        "source_coverage": coverage["sources"],
        "cross_source_duplicate_groups": len(duplicate_groups),
        "source_provenance_preserved": True,
        "ordered_snapshot_items": len(snapshot["items"]),
        "same_ordered_input": True,
        "assessment_uncertainty_preserved": True,
        "network_requests": 0,
        "provider_calls": 0,
        "serve_report": "serve/report.md",
        "comparison_report": "shadow/comparison.md",
        "verification_limit": "synthetic fixtures only; live provider coverage is unverified",
    }


def main():
    parser = argparse.ArgumentParser(description="Run the synthetic offline Reddit+X evidence comparison.")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        summary = run(arguments.output)
    except (OSError, ValueError, RuntimeError, AssertionError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
