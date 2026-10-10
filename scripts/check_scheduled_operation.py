import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from unittest import mock


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
    paths = [str(ROOT / "tests"), str(ROOT)]
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join(paths),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(HERMES_TMPDIR),
    }


def assert_denied(action):
    try:
        action()
    except PermissionError:
        return
    raise AssertionError("offline guard allowed a protected operation")


def verify_guards():
    import network_guard

    network_guard.install()
    assert_denied(socket.socket)
    assert_denied(lambda: open(network_guard.CREDENTIALS_PATH, "rb"))
    assert_denied(lambda: open(network_guard.LIVE_STATE_PATH + "/assessment/state.json", "rb"))
    environment = safe_environment()
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
            timeout=10,
            check=False,
        )
        if result.returncode == 0 or "disabled in tests" not in result.stderr:
            raise AssertionError("offline subprocess guard did not deny a protected operation")


def verify_hermes_capabilities():
    hermes = shutil.which("hermes")
    if not hermes:
        return "not_installed"
    create = subprocess.run([hermes, "cron", "create", "--help"], capture_output=True, text=True, timeout=20)
    pause = subprocess.run([hermes, "cron", "pause", "--help"], capture_output=True, text=True, timeout=20)
    if create.returncode or pause.returncode:
        raise AssertionError("read-only Hermes cron help probe failed")
    if not all(token in create.stdout for token in ("--script", "--no-agent", "--paused")) or "job_id" not in pause.stdout:
        raise AssertionError("installed Hermes lacks the inspected script, paused-create, or pause interface")
    return "read_only_help_verified"


def verify_hermes_wrappers(directory):
    disabled_config = directory / "disabled.json"
    disabled_config.write_text(json.dumps({"version": 1, "enabled": False}), encoding="utf-8")
    environment = safe_environment()
    environment["NEED_RADAR_PROJECT_ROOT"] = str(ROOT)
    environment["NEED_RADAR_CONFIG"] = str(disabled_config)
    for wrapper in ("need-radar-cycle.sh", "need-radar-missed-check.sh"):
        result = subprocess.run(
            [str(ROOT / "scripts" / "hermes" / wrapper)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode or json.loads(result.stdout).get("status") != "disabled":
            raise AssertionError(f"Hermes wrapper failed its disabled-config probe: {wrapper}")


def build_fixtures(directory, faulty=False, secret_marker=None):
    directory.mkdir(parents=True, exist_ok=True)
    source = json.loads(SOURCE_FIXTURE.read_text(encoding="utf-8"))
    demo = json.loads(DEMO_FIXTURE.read_text(encoding="utf-8"))
    source["normalization_model"]["usage"] = {"requests": 1, "tokens": 6}
    source["model"]["usage"] = {"requests": 1, "tokens": 19}
    source["experiment"] = {"shared_settings": SHARED_SETTINGS}
    evidence_id = "reddit:reddit-comment-001"
    judge = copy.deepcopy(demo["judge"])
    judge["usage"] = {"requests": 1, "tokens": 13}
    judge["response"][0]["evidence_ids"] = [evidence_id]
    for dimension in judge["response"][0]["dimensions"].values():
        dimension["evidence_ids"] = [evidence_id]
    if faulty:
        judge["status"] = "failure"
        judge["error"] = "synthetic judge unavailable"
    source["judge"] = judge
    if secret_marker:
        source["source_results"]["reddit"]["posts"][0]["selftext"] += f" api_key={secret_marker}"
    shadow_candidate = copy.deepcopy(source["model"]["response"][0])
    shadow_candidate["friction"] = "A builder repeats context setup after agent-session resets."
    shadow_model = {
        "status": "synthetic_response",
        "response": [shadow_candidate],
        "usage": {"requests": 1, "tokens": 9},
    }
    if faulty:
        shadow_model["status"] = "failure"
        shadow_model["error"] = "synthetic shadow failure"
    shadow = {
        "provider": "synthetic_fixture",
        "experiment": {
            "shared_settings": SHARED_SETTINGS,
            "shadow_model": shadow_model,
            "shadow_assessment": {
                "status": "synthetic_response",
                "response": judge["response"],
            },
        },
    }
    serve_path, shadow_path = directory / "serve-fixture.json", directory / "shadow-fixture.json"
    serve_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shadow_path.write_text(json.dumps(shadow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return serve_path, shadow_path


def make_config(directory, serve_path, shadow_path, *, requests=4, tokens=64, state_name="schedule.sqlite3"):
    config_path = directory / f"{state_name}.json"
    config = {
        "version": 1,
        "enabled": True,
        "schedule": {
            "interval_seconds": 3600,
            "anchor_at": "2026-10-07T00:00:00Z",
            "grace_seconds": 0,
        },
        "budget": {"requests": requests, "tokens": tokens},
        "timeout_seconds": 30,
        "state_db": f"{state_name}",
        "output_root": f"{state_name}.runs",
        "serve_fixture": str(serve_path),
        "shadow_fixture": str(shadow_path),
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def invoke(environment, command, config_path, scheduled_for, *, expect_success=True):
    result = subprocess.run(
        [sys.executable, "-m", "need_radar.scheduled", command, "--config", str(config_path), "--scheduled-for", scheduled_for],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if expect_success and result.returncode:
        raise RuntimeError(result.stderr or result.stdout or f"scheduled {command} failed")
    summary = json.loads(result.stdout)
    return result, summary


def verify_disable_procedure(directory):
    bin_directory = directory / "bin"
    bin_directory.mkdir()
    log_path = directory / "hermes-probe.log"
    state_path = directory / "hermes-jobs.json"
    state_path.write_text(json.dumps({
        "main-job": "active", "watchdog-job": "active", "unrelated-job": "active",
    }), encoding="utf-8")
    hermes_stub = bin_directory / "hermes"
    hermes_stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "state_path = os.environ['HERMES_PROBE_STATE']\n"
        "state = json.load(open(state_path))\n"
        "with open(os.environ['HERMES_PROBE_LOG'], 'a') as log: log.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['cron', 'pause']:\n"
        "    state[sys.argv[3]] = 'paused'\n"
        "    json.dump(state, open(state_path, 'w'))\n"
        "elif sys.argv[1:3] == ['cron', 'list']:\n"
        "    print(json.dumps(state, sort_keys=True))\n",
        encoding="utf-8",
    )
    hermes_stub.chmod(0o755)
    environment = safe_environment()
    environment["PATH"] = str(bin_directory) + os.pathsep + environment["PATH"]
    environment["HERMES_PROBE_LOG"] = str(log_path)
    environment["HERMES_PROBE_STATE"] = str(state_path)
    result = subprocess.run(
        [str(ROOT / "scripts" / "disable_need_radar_cron.sh"), "main-job", "watchdog-job"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode or log_path.read_text().splitlines() != [
        "cron pause main-job", "cron pause watchdog-job", "cron list --all",
    ]:
        raise AssertionError("disable procedure did not pause and verify both Hermes jobs")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state != {"main-job": "paused", "watchdog-job": "paused", "unrelated-job": "active"}:
        raise AssertionError("disable procedure changed jobs outside the Need Radar pair")


def main():
    if not HERMES_TMPDIR.is_dir() or Path(os.environ.get("TMPDIR", "")).resolve() != HERMES_TMPDIR.resolve():
        raise RuntimeError("TMPDIR must point to the existing Hermes scratch directory")
    verify_guards()
    hermes_capabilities = verify_hermes_capabilities()
    with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR, prefix="need-radar-scheduled-") as temporary:
        root = Path(temporary)
        verify_hermes_wrappers(root)
        secret_marker = "sk-SCHEDULEFIXTURESECRET123456789"
        serve_fixture, shadow_fixture = build_fixtures(root, secret_marker=secret_marker)
        config_path = make_config(root, serve_fixture, shadow_fixture)
        slot = "2026-10-08T00:00:00Z"

        _, missed = invoke(safe_environment(), "missed", config_path, slot)
        if missed["status"] != "missed":
            raise AssertionError("independent watchdog did not detect an absent main run")

        _, completed = invoke(safe_environment(), "run", config_path, slot)
        if completed["status"] != "success":
            raise AssertionError(json.dumps(completed, sort_keys=True))
        run_directory = root / "schedule.sqlite3.runs" / completed["record_id"]
        receipt = json.loads((run_directory / "run-record.json").read_text(encoding="utf-8"))
        if receipt["budget"]["planned"] != {"requests": 4, "tokens": 47}:
            raise AssertionError("aggregate run budget did not sum all recorded model boundaries")
        if receipt["budget"]["used"] != receipt["budget"]["planned"]:
            raise AssertionError("observed fixture usage differs from planned shared usage")
        serve_report_ref = receipt["children"]["serve"]["report"]
        delivery_ref = receipt["children"]["fake_delivery"]["artifact"]
        if (
            serve_report_ref["artifact_id"] != delivery_ref["input_artifact_id"]
            or delivery_ref["stage"] != "discord_delivery"
            or receipt["children"]["shadow"]["artifact"]["stage"] != "comparison"
        ):
            raise AssertionError("scheduled child artifacts do not preserve report lineage")
        if receipt["stages"]["shadow"] != "complete" or receipt["stages"]["fake_delivery"] not in {
            "delivered", "delivered_reconciled",
        }:
            raise AssertionError(f"complete offline cycle stage status mismatch: {receipt['stages']!r}")
        if receipt["coverage"]["provider_compatibility"] != "not verified":
            raise AssertionError("fixture result overstated provider verification")
        for artifact in run_directory.rglob("*"):
            if artifact.is_file() and secret_marker.encode() in artifact.read_bytes():
                raise AssertionError("synthetic secret marker leaked into a persisted run artifact")

        with sqlite3.connect(root / "schedule.sqlite3") as database:
            linked_records = database.execute(
                "SELECT kind, status, linked_record_id FROM run_records WHERE scheduled_for = ? ORDER BY kind",
                (slot,),
            ).fetchall()
        if len(linked_records) != 2 or any(not record[2] for record in linked_records):
            raise AssertionError("late run and independent missed record were not retained and linked")

        fault_serve, fault_shadow = build_fixtures(root / "fault-fixtures", faulty=True)
        fault_config = make_config(root, fault_serve, fault_shadow, state_name="fault.sqlite3")
        _, faulty = invoke(safe_environment(), "run", fault_config, "2026-10-08T01:00:00Z")
        if faulty["status"] != "success" or not {
            "serve_judge_unavailable", "shadow_incomplete",
        } <= set(faulty["warnings"]):
            raise AssertionError("optional judge or shadow failure blocked serve")
        faulty_directory = root / "fault.sqlite3.runs" / faulty["record_id"]
        faulty_receipt = json.loads((faulty_directory / "run-record.json").read_text(encoding="utf-8"))
        if not (faulty_directory / "serve" / "report.md").is_file():
            raise AssertionError("valid canonical serve report was lost after optional failures")
        if faulty_receipt["stages"]["judge"] != "unavailable" or faulty_receipt["stages"]["render"] != "success":
            raise AssertionError("judge fault was not recorded independently of valid serve output")

        render_config = make_config(root, serve_fixture, shadow_fixture, state_name="render.sqlite3")
        scheduled_module = __import__("need_radar.scheduled", fromlist=["run_cycle"])
        tracer_cli = __import__("need_radar.__main__", fromlist=["run"])
        run_command = scheduled_module._run_command

        def run_with_renderer_failure(command, timeout, environment):
            if command[1:3] == ["-m", "need_radar"]:
                with mock.patch.object(
                    tracer_cli, "render_file", side_effect=OSError("synthetic renderer failure"),
                ):
                    status = tracer_cli.run(Path(command[4]), Path(command[6]))
                return subprocess.CompletedProcess(command, 0 if status in {"success", "no_findings"} else 1, "", "")
            return run_command(command, timeout, environment)

        with mock.patch.object(scheduled_module, "_run_command", side_effect=run_with_renderer_failure):
            render_failure = scheduled_module.run_cycle(
                scheduled_module._read_config(render_config), "2026-10-08T01:00:00Z",
            )
        render_directory = root / "render.sqlite3.runs" / render_failure["record_id"]
        render_receipt = json.loads((render_directory / "run-record.json").read_text(encoding="utf-8"))
        render_spans = [
            json.loads(line)
            for line in (render_directory / "serve" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        if (
            render_failure["status"] != "success"
            or render_receipt["stages"]["render"] != "failed"
            or not (render_directory / "serve" / "report.md").is_file()
            or (render_directory / "serve" / "report.html").exists()
            or not any(span["name"] == "html_render_failure" for span in render_spans)
        ):
            raise AssertionError("renderer failure blocked or obscured the canonical serve report")

        usage_config = make_config(root, serve_fixture, shadow_fixture, state_name="usage-evidence.sqlite3")
        observed_usage = scheduled_module._actual_usage

        def omit_shadow_usage(serve_directory, shadow_directory=None):
            return [
                item for item in observed_usage(serve_directory, shadow_directory)
                if item["stage"] != "shadow_extraction"
            ]

        with mock.patch.object(scheduled_module, "_actual_usage", side_effect=omit_shadow_usage):
            usage_unverified = scheduled_module.run_cycle(
                scheduled_module._read_config(usage_config), "2026-10-08T02:00:00Z",
            )
        usage_directory = root / "usage-evidence.sqlite3.runs" / usage_unverified["record_id"]
        usage_receipt = json.loads((usage_directory / "run-record.json").read_text(encoding="utf-8"))
        if (
            usage_unverified["status"] != "budget_unverified"
            or usage_receipt["coverage"]["budget"]["complete"]
            or usage_receipt["stages"]["serve"] != "success"
            or not (usage_directory / "serve" / "report.md").is_file()
        ):
            raise AssertionError("incomplete aggregate usage evidence reported success or discarded valid serve output")

        request_limited = make_config(root, serve_fixture, shadow_fixture, requests=3, state_name="request-limit.sqlite3")
        _, request_exceeded = invoke(safe_environment(), "run", request_limited, "2026-10-08T02:00:00Z", expect_success=False)
        if request_exceeded["status"] != "budget_exceeded":
            raise AssertionError("shared request budget did not reject combined serve/shadow use")
        token_limited = make_config(root, serve_fixture, shadow_fixture, tokens=46, state_name="token-limit.sqlite3")
        _, token_exceeded = invoke(safe_environment(), "run", token_limited, "2026-10-08T03:00:00Z", expect_success=False)
        if token_exceeded["status"] != "budget_exceeded":
            raise AssertionError("shared token budget did not reject combined serve/shadow use")

        invalid_fixture = root / "invalid.json"
        invalid_fixture.write_text("{bad json", encoding="utf-8")
        failure_config = make_config(root, invalid_fixture, shadow_fixture, state_name="failure.sqlite3")
        _, failed = invoke(safe_environment(), "run", failure_config, "2026-10-08T04:00:00Z", expect_success=False)
        if failed["status"] != "failed":
            raise AssertionError("invalid fixture failure was not recorded")

        overlap_config = make_config(root, serve_fixture, shadow_fixture, state_name="overlap.sqlite3")
        lock_path = root / "overlap.sqlite3.lock"
        with lock_path.open("w") as held_lock:
            fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _, overlapped = invoke(safe_environment(), "run", overlap_config, "2026-10-08T05:00:00Z")
            fcntl.flock(held_lock, fcntl.LOCK_UN)
        if overlapped["status"] != "overlap":
            raise AssertionError("overlapping operation was not rejected")

        scheduled_module = __import__("need_radar.scheduled", fromlist=["_run_command"])
        timeout = scheduled_module._run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            0.05,
            safe_environment(),
        )
        if timeout is not None:
            raise AssertionError("scheduled child timeout did not terminate the process")
        timeout_config = make_config(root, serve_fixture, shadow_fixture, state_name="timeout.sqlite3")
        with mock.patch.object(scheduled_module, "_run_command", return_value=None):
            timeout_result = scheduled_module.run_cycle(
                scheduled_module._read_config(timeout_config), "2026-10-08T06:00:00Z",
            )
        if timeout_result["status"] != "timeout":
            raise AssertionError("scheduled run did not record its timeout")

        command_failure_config = make_config(root, serve_fixture, shadow_fixture, state_name="command-failure.sqlite3")
        failed_process = subprocess.CompletedProcess(["fixture"], 2, "", "synthetic command failure")
        with mock.patch.object(scheduled_module, "_run_command", return_value=failed_process):
            command_failure = scheduled_module.run_cycle(
                scheduled_module._read_config(command_failure_config), "2026-10-08T07:00:00Z",
            )
        if command_failure["status"] != "serve_failed":
            raise AssertionError("scheduled child failure was not recorded")

        serve_report = run_directory / "serve" / "report.md"
        serve_report.write_text(serve_report.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        try:
            scheduled_module._accept_serve_exit(0, run_directory / "serve")
        except ValueError:
            bad_data_rejected = True
        else:
            bad_data_rejected = False
        if not bad_data_rejected:
            raise AssertionError("zero child exit status hid corrupted output data")
        _, data_check = invoke(safe_environment(), "missed", config_path, slot)
        if data_check["status"] != "bad_data":
            raise AssertionError("independent monitor accepted a successful exit with corrupt artifacts")

        verify_disable_procedure(root)
        print(json.dumps({
            "status": "passed",
            "evidence_kind": "synthetic_offline",
            "hermes_cron_capabilities": hermes_capabilities,
            "network_credential_live_state_guards": "denied_in_process_and_subprocess",
            "network_requests": 0,
            "complete_cycle": "passed; fixture collectors, fixture models, fake delivery",
            "aggregate_budget": "requests_and_tokens_enforced_across_normalization_serve_judge_shadow",
            "overlap_timeout_failure": "passed",
            "independent_missed_run_and_linked_records": "passed",
            "zero_exit_with_bad_data": "rejected_by_artifact_lineage_check",
            "optional_branch_faults": "judge_shadow_and_renderer_preserved_valid_serve",
            "incomplete_usage_evidence": "failed_closed_without_discarding_serve_report",
            "disable_procedure": "both Hermes jobs paused and listed using a harmless stub",
            "recurring_schedule": "not created or enabled",
            "live_source_model_delivery_verification": "not performed",
            "redaction": "synthetic secret marker absent from run artifacts",
        }, sort_keys=True))


if __name__ == "__main__":
    main()
