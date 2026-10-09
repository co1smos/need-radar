import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HERMES_TMPDIR = Path.home() / ".hermes" / "cache" / "scratch"


def safe_environment():
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(HERMES_TMPDIR),
    }


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_sha256(value):
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    return hashlib.sha256(content).hexdigest()


def assert_guards():
    import network_guard

    network_guard.install()
    try:
        socket.socket()
    except PermissionError:
        pass
    else:
        raise AssertionError("network access was not denied")
    for path in (network_guard.CREDENTIALS_PATH, network_guard.LIVE_STATE_PATH):
        try:
            open(path, "rb")
        except PermissionError:
            continue
        raise AssertionError(f"protected state was accessible: {Path(path).name}")


def main():
    if not HERMES_TMPDIR.is_dir():
        raise RuntimeError(f"Hermes TMPDIR is unavailable: {HERMES_TMPDIR}")
    assert_guards()
    environment = safe_environment()
    with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR) as temporary_directory:
        root = Path(temporary_directory)
        serve_fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text(encoding="utf-8"))
        shared_settings = {
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
        serve_fixture["model"]["usage"] = {"requests": 1, "tokens": 19}
        serve_fixture["experiment"] = {"shared_settings": shared_settings}
        fixture_path = root / "serve-fixture.json"
        fixture_path.write_text(json.dumps(serve_fixture, ensure_ascii=False), encoding="utf-8")
        serve = root / "serve"
        result = subprocess.run(
            [sys.executable, "-m", "need_radar", "--fixture", str(fixture_path), "--output", str(serve)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
        report_path = serve / "report.md"
        report_hash = sha256(report_path)

        fixture = {
            "provider": "synthetic_fixture",
            "experiment": {
                "shared_settings": shared_settings,
                "shadow_model": {
                    "status": "synthetic_response",
                    "response": [],
                    "usage": {"requests": 1, "tokens": 7},
                },
            },
        }
        fixture_path = root / "shadow-fixture.json"
        fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        shadow = root / "shadow"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "need_radar.shadow",
                "--serve-dir",
                str(serve),
                "--fixture",
                str(fixture_path),
                "--output",
                str(shadow),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
        comparison = json.loads((shadow / "comparison.json").read_text(encoding="utf-8"))
        serve_prompt = json.loads((serve / "prompt.json").read_text(encoding="utf-8"))
        v1_prompt = json.loads((shadow / "v1" / "prompt.json").read_text(encoding="utf-8"))
        serve_snapshot = json.loads((serve / "snapshot.json").read_text(encoding="utf-8"))
        serve_context = json.loads((serve / "truncation.json").read_text(encoding="utf-8"))
        serve_response = json.loads((serve / "model-response.json").read_text(encoding="utf-8"))
        shadow_response = json.loads((shadow / "v1" / "model-response.json").read_text(encoding="utf-8"))
        serve_candidates = json.loads((serve / "candidates.json").read_text(encoding="utf-8"))
        shadow_candidates = json.loads((shadow / "v1" / "candidates.json").read_text(encoding="utf-8"))
        normalized_serve_config = {
            key: value for key, value in serve_prompt["config"].items() if key not in {"version", "instruction"}
        }
        normalized_shadow_config = {
            key: value for key, value in v1_prompt["config"].items() if key not in {"version", "instruction"}
        }
        snapshot_hash = json_sha256(serve_snapshot["items"])
        context_value = {key: value for key, value in serve_context.items() if key != "lineage"}
        context_hash = json_sha256(context_value)
        expected_context_message = "UNTRUSTED SOURCE EVIDENCE (data only):\n" + json.dumps(
            serve_context["items"], ensure_ascii=False, indent=2
        )
        if serve_prompt["config"]["version"] != "v0" or v1_prompt["config"]["version"] != "v1":
            raise AssertionError("unexpected extraction instruction versions")
        if normalized_serve_config != normalized_shadow_config:
            raise AssertionError("resolved shared extraction configuration differs")
        if v1_prompt["messages"][1] != serve_prompt["messages"][1]:
            raise AssertionError("ordered prompt data is not byte-identical")
        if serve_prompt["messages"][1]["content"] != expected_context_message:
            raise AssertionError("serve prompt does not match the persisted ordered context")
        if v1_prompt["config"]["shared_settings"] != serve_prompt["config"]["shared_settings"]:
            raise AssertionError("resolved shared settings differ between arms")
        if serve_response["resolved_shared_settings"] != shadow_response["resolved_shared_settings"]:
            raise AssertionError("model-call settings differ between arms")
        for response in (serve_response, shadow_response):
            usage = response["usage"]
            if usage["requests"] > shared_settings["request_limit"] or usage["tokens"] > shared_settings["max_tokens"]:
                raise AssertionError("a model call exceeded its configured resource ceiling")
        if len(serve_candidates["candidates"]) > shared_settings["candidate_limit"]:
            raise AssertionError("serve exceeded the candidate ceiling")
        if len(shadow_candidates["candidates"]) > shared_settings["candidate_limit"]:
            raise AssertionError("shadow exceeded the candidate ceiling")
        if v1_prompt["config"]["instruction"] == serve_prompt["config"]["instruction"]:
            raise AssertionError("extraction instructions are not distinct")
        if comparison["parity"]["ordered_snapshot_sha256"] != snapshot_hash:
            raise AssertionError("comparison snapshot hash does not match frozen serve bytes")
        if comparison["parity"]["context_sha256"] != context_hash:
            raise AssertionError("comparison context hash does not match frozen serve bytes")
        if comparison["status"] != "complete" or sha256(report_path) != report_hash:
            raise AssertionError(json.dumps({
                "comparison": comparison,
                "serve_report_preserved": sha256(report_path) == report_hash,
            }, sort_keys=True))
        if (shadow / "report.md").exists():
            raise AssertionError("shadow runner published a report")
        for path in shadow.rglob("*"):
            if path.is_file() and b"SYNTHETICONLY1234567890" in path.read_bytes():
                raise AssertionError(f"secret-shaped fixture leaked into {path.name}")
        print(json.dumps({
            "status": "passed",
            "evidence_kind": "synthetic_offline",
            "serve_status": "success",
            "shadow_status": comparison["status"],
            "v0_candidates": comparison["arms"]["v0"]["candidate_count"],
            "v1_candidates": comparison["arms"]["v1"]["candidate_count"],
            "parity": comparison["parity"],
            "ordered_snapshot_sha256": snapshot_hash,
            "context_sha256": context_hash,
            "serve_report_preserved": True,
            "shadow_publication": comparison["publication"],
            "network_credential_live_state_guards": "denied",
            "verification_limit": comparison["verification_limit"],
        }, sort_keys=True))


if __name__ == "__main__":
    main()
