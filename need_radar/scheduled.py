import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone

from need_radar.__main__ import digest, json_bytes, redact


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
MAIN_USAGE_ARTIFACTS = (
    ("source_normalization", "source-normalization-response.json"),
    ("serve_extraction", "model-response.json"),
    ("serve_judge", "judge-response.json"),
)


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("schedule timestamps must be ISO-8601 strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("schedule timestamps must include a timezone")
    return parsed.astimezone(UTC).replace(microsecond=0)


def _timestamp(value):
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("unsupported scheduled-operation configuration")
    if config.get("enabled") is not True:
        return {"version": 1, "enabled": False}
    schedule = config.get("schedule")
    budget = config.get("budget")
    if not isinstance(schedule, dict) or not isinstance(budget, dict):
        raise ValueError("configuration requires schedule and budget objects")
    for key in ("interval_seconds", "grace_seconds"):
        if type(schedule.get(key)) is not int or schedule[key] < 0:
            raise ValueError(f"schedule {key} must be a non-negative integer")
    if schedule["interval_seconds"] == 0:
        raise ValueError("schedule interval_seconds must be positive")
    _utc(schedule.get("anchor_at", ""))
    for key in ("requests", "tokens"):
        if type(budget.get(key)) is not int or budget[key] < 0:
            raise ValueError(f"budget {key} must be a non-negative integer")
    if (
        type(config.get("timeout_seconds")) not in {int, float}
        or not math.isfinite(config["timeout_seconds"])
        or config["timeout_seconds"] <= 0
    ):
        raise ValueError("timeout_seconds must be positive")
    mode = config.get("mode", "fixture")
    if mode not in {"fixture", "product"}:
        raise ValueError("operation mode must be fixture or product")
    required_paths = ("state_db", "output_root")
    required_paths += ("serve_fixture", "shadow_fixture") if mode == "fixture" else ("activation_file", "product_budget_db")
    if any(not isinstance(config.get(key), str) or not config[key] for key in required_paths):
        raise ValueError("configuration paths are incomplete for the selected operation mode")
    if mode == "product":
        from need_radar.product import MAX_AGGREGATE_MODEL_TOKENS, MAX_TOTAL_REQUESTS, PRODUCT_BUDGET_DB

        configured_budget_path = Path(config["product_budget_db"]).expanduser()
        if configured_budget_path != PRODUCT_BUDGET_DB:
            raise ValueError("product_budget_db must use the persistent canonical product ledger")
        if budget != {"requests": MAX_TOTAL_REQUESTS, "tokens": MAX_AGGREGATE_MODEL_TOKENS}:
            raise ValueError("product budget must use the fixed request and token ceilings")
        if config["timeout_seconds"] > 300:
            raise ValueError("product timeout_seconds must not exceed 300")
    resolved = dict(config)
    for key in required_paths:
        value = Path(config[key]).expanduser()
        if key == "product_budget_db":
            resolved[key] = value.expanduser()
            continue
        resolved[key] = (path.parent / value).resolve() if not value.is_absolute() else value.resolve()
        if key in {"state_db", "output_root", "product_budget_db", "activation_file"} and resolved[key].is_relative_to(ROOT):
            raise ValueError(f"{key} must be outside the repository")
    resolved["schedule"] = {
        "interval_seconds": schedule["interval_seconds"],
        "anchor_at": _timestamp(_utc(schedule["anchor_at"])),
        "grace_seconds": schedule["grace_seconds"],
    }
    resolved["budget"] = {"requests": budget["requests"], "tokens": budget["tokens"]}
    resolved["timeout_seconds"] = float(config["timeout_seconds"])
    resolved["mode"] = mode
    resolved["config_path"] = path
    return resolved


def _scheduled_for(config, now=None):
    now = now or datetime.now(UTC)
    schedule = config["schedule"]
    anchor = _utc(schedule["anchor_at"])
    if now < anchor:
        raise ValueError("current time precedes the configured schedule anchor")
    interval = schedule["interval_seconds"]
    elapsed = int((now - anchor).total_seconds())
    return _timestamp(anchor + timedelta(seconds=(elapsed // interval) * interval))


def _validate_slot(config, value):
    slot = _utc(value)
    anchor = _utc(config["schedule"]["anchor_at"])
    elapsed = (slot - anchor).total_seconds()
    if elapsed < 0 or elapsed % config["schedule"]["interval_seconds"] != 0:
        raise ValueError("scheduled_for must align with the configured interval")
    return _timestamp(slot)


def _ensure_hermes_tmpdir():
    expected = (Path.home() / ".hermes" / "cache" / "scratch").resolve()
    supplied = Path(os.environ.get("TMPDIR", expected)).resolve()
    if supplied != expected or not supplied.is_dir():
        raise ValueError("TMPDIR must be the existing Hermes scratch directory")
    return str(supplied)


def _database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS run_records ("
        "record_id TEXT PRIMARY KEY, kind TEXT NOT NULL, scheduled_for TEXT NOT NULL, "
        "status TEXT NOT NULL, linked_record_id TEXT, created_at TEXT NOT NULL, "
        "finished_at TEXT, output_path TEXT, record_sha256 TEXT, details TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS run_records_slot ON run_records(kind, scheduled_for, created_at)"
    )
    connection.commit()
    return connection


def _insert_record(database, record_id, kind, scheduled_for, status, details, output_path=None, linked_record_id=None):
    database.execute(
        "INSERT INTO run_records VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?)",
        (
            record_id,
            kind,
            scheduled_for,
            status,
            linked_record_id,
            _timestamp(datetime.now(UTC)),
            str(output_path) if output_path else None,
            json.dumps(redact(details), ensure_ascii=False),
        ),
    )
    database.commit()


def _lock(database_path):
    handle = open(database_path.with_suffix(database_path.suffix + ".lock"), "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _child_environment(hermes_tmpdir):
    pythonpath = [str(ROOT)]
    pythonpath.extend(path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join(dict.fromkeys(pythonpath)),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": hermes_tmpdir,
    }
    return environment


def _run_command(command, timeout, environment):
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=max(timeout, 0.001))
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        return None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _process_evidence(child):
    return {
        "exit_code": child.returncode,
        "stdout_sha256": digest((child.stdout or "").encode("utf-8")),
        "stderr_sha256": digest((child.stderr or "").encode("utf-8")),
    }


def _file_reference(path):
    return {
        "path": path.name,
        "sha256": digest(path.read_bytes()) if path.is_file() and not path.is_symlink() else None,
    }


def _fixture(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("offline fixtures must contain a JSON object")
    return redact(value)


def _usage(value, name):
    usage = value.get("usage") if isinstance(value, dict) else None
    if not isinstance(usage, dict):
        raise ValueError(f"fixture usage is missing for {name}")
    requests, tokens = usage.get("requests"), usage.get("tokens")
    if type(requests) is not int or requests < 0 or type(tokens) is not int or tokens < 0:
        raise ValueError(f"fixture usage is invalid for {name}")
    return {"stage": name, "requests": requests, "tokens": tokens}


def _planned_usage(serve, shadow):
    entries = []
    if "source_results" in serve:
        entries.append(_usage(serve.get("normalization_model"), "source_normalization"))
    entries.append(_usage(serve.get("model"), "serve_extraction"))
    if "judge" in serve:
        entries.append(_usage(serve["judge"], "serve_judge"))
    experiment = shadow.get("experiment")
    if not isinstance(experiment, dict):
        raise ValueError("shadow fixture requires an experiment object")
    entries.append(_usage(experiment.get("shadow_model"), "shadow_extraction"))
    return entries


def _sum_usage(entries):
    return {
        "requests": sum(item["requests"] for item in entries),
        "tokens": sum(item["tokens"] for item in entries),
    }


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json_bytes(redact(value))
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary.name, path)
    return digest(content)


def _verify_artifacts(run_directory):
    database_path = run_directory / "lineage.sqlite3"
    report_path = run_directory / "report.md"
    candidates_path = run_directory / "candidates.json"
    if not database_path.is_file() or not report_path.is_file() or not candidates_path.is_file():
        raise ValueError("serve output is missing required artifacts")
    with sqlite3.connect(database_path) as database:
        rows = database.execute(
            "SELECT stage, status, artifact_path, output_sha256, artifact_id, input_artifact_id, run_id FROM stages"
        ).fetchall()
    if not rows:
        raise ValueError("serve lineage has no run record")
    run_ids = {row[6] for row in rows}
    stages = {row[0]: row for row in rows}
    if len(run_ids) != 1 or not {"validation", "report"} <= stages.keys():
        raise ValueError("serve lineage is incomplete or spans multiple runs")
    if stages["validation"][1] not in {"success", "no_findings"} or stages["report"][1] != "success":
        raise ValueError("serve validation or canonical report stage failed")
    if stages["validation"][2] != "candidates.json" or stages["report"][2] != "report.md":
        raise ValueError("serve lineage points to unexpected canonical artifact paths")
    if stages["report"][5] != stages["validation"][4]:
        raise ValueError("canonical report does not descend from validated candidates")
    for _, _, relative_path, expected_hash, _, _, _ in rows:
        relative_artifact = Path(relative_path)
        artifact = run_directory / relative_artifact
        if (
            relative_artifact.is_absolute()
            or ".." in relative_artifact.parts
            or artifact.is_symlink()
            or not artifact.resolve().is_relative_to(run_directory.resolve())
            or not artifact.is_file()
            or digest(artifact.read_bytes()) != expected_hash
        ):
            raise ValueError("serve artifact failed lineage verification")
        if artifact.suffix in {".json", ".md", ".html"}:
            content = artifact.read_text(encoding="utf-8")
            if redact(content) != content:
                raise ValueError("serve artifact contains secret-shaped content")
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    if candidates.get("status") not in {"success", "no_findings"}:
        raise ValueError("serve candidates failed validation")
    if candidates.get("lineage", {}).get("artifact_id") != stages["validation"][4]:
        raise ValueError("candidate artifact ID does not match serve lineage")
    if digest(report_path.read_bytes()) != stages["report"][3]:
        raise ValueError("canonical report hash does not match serve lineage")
    if not report_path.read_text(encoding="utf-8").startswith("# Need Radar"):
        raise ValueError("canonical serve report is malformed")
    for artifact in run_directory.rglob("*"):
        if artifact.is_file() and artifact != database_path:
            try:
                content = artifact.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if redact(content) != content:
                raise ValueError("serve run contains secret-shaped persisted content")
    return {
        "run_id": next(iter(run_ids)),
        "report": {
            "stage": "report",
            "artifact_id": stages["report"][4],
            "sha256": digest(report_path.read_bytes()),
            "input_artifact_id": stages["report"][5],
        },
        "validation": {
            "stage": "validation",
            "artifact_id": stages["validation"][4],
            "sha256": stages["validation"][3],
        },
    }


def _artifact_reference(path):
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    lineage = value.get("lineage")
    if not isinstance(lineage, dict) or not lineage.get("artifact_id"):
        return None
    return {
        "run_id": lineage.get("run_id"),
        "trace_id": lineage.get("trace_id"),
        "stage": lineage.get("stage"),
        "artifact_id": lineage["artifact_id"],
        "input_artifact_id": lineage.get("input_artifact_id"),
        "input_sha256": lineage.get("input_sha256"),
        "sha256": digest(path.read_bytes()),
    }


def _accept_serve_exit(returncode, run_directory):
    if returncode != 0:
        raise ValueError("serve command did not exit successfully")
    return _verify_artifacts(run_directory)


def _actual_usage(run_directory, shadow_directory=None):
    entries = []
    for name, relative in MAIN_USAGE_ARTIFACTS:
        path = run_directory / relative
        if not path.is_file():
            if name == "source_normalization" and not (run_directory / "source-normalization-prompt.json").exists():
                continue
            if name == "serve_judge" and not (run_directory / "judge-prompt.json").exists():
                continue
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value.get("usage"), dict):
            entries.append(_usage(value, name))
    if shadow_directory:
        path = shadow_directory / "v1" / "model-response.json"
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value.get("usage"), dict):
                entries.append(_usage(value, "shadow_extraction"))
    return entries


def _optional_statuses(serve_directory, shadow_status, delivery_status):
    assessment_path = serve_directory / "assessment.json"
    assessment_status = "not_run"
    if assessment_path.is_file():
        assessment_status = json.loads(assessment_path.read_text(encoding="utf-8")).get("status", "unknown")
    render_status = "success" if (serve_directory / "report.html").is_file() else "failed"
    warnings = []
    if assessment_status != "success":
        warnings.append("serve_judge_unavailable")
    if render_status != "success":
        warnings.append("html_render_unavailable")
    if shadow_status != "complete":
        warnings.append("shadow_incomplete")
    if delivery_status not in {"delivered", "delivered_reconciled"}:
        warnings.append("fake_delivery_incomplete")
    return {
        "judge": assessment_status,
        "render": render_status,
        "shadow": shadow_status,
        "fake_delivery": delivery_status,
        "warnings": warnings,
    }


def _product_health_status(health):
    serve_ok = health.get("serve_status") in {"success", "no_findings"}
    if not serve_ok or health.get("source_status") not in {"bounded", "partial"}:
        return "failed"
    complete = (
        health.get("source_status") == "bounded"
        and not health.get("source_errors")
        and health.get("serve_judge_status") in {"success", "not_required_no_candidates"}
        and health.get("visual_render_status") == "success"
        and health.get("delivery_status") in {"delivered", "delivered_reconciled"}
        and health.get("shadow_status") == "complete"
        and health.get("shadow_comparison_status") in {"complete", "success"}
    )
    return "complete" if complete else "degraded"


def _verify_product_health(receipt_path, recorded):
    child = recorded.get("children", {}).get("product")
    if not isinstance(child, dict):
        return None
    output_path = Path(child.get("output_path", ""))
    reference = child.get("health_artifact")
    if (
        output_path.is_symlink()
        or output_path.resolve() != receipt_path.parent.resolve()
        or not isinstance(reference, dict)
        or reference.get("path") != "product-health.json"
    ):
        raise ValueError("product health artifact reference is invalid")
    health_path = output_path / "product-health.json"
    if health_path.is_symlink() or not health_path.is_file() or digest(health_path.read_bytes()) != reference.get("sha256"):
        raise ValueError("product health artifact is missing or corrupt")
    health = json.loads(health_path.read_text(encoding="utf-8"))
    health_status = health.get("health_status")
    health_state = health.get("status")
    if (
        health.get("run_id") != recorded.get("record_id")
        or health_status not in {"complete", "degraded", "failed"}
        or health_status != recorded.get("product_health_status")
        or health_status != child.get("health_status")
        or _product_health_status(health) != health_status
        or (health_status == "complete" and health_state != "success")
        or (health_status == "degraded" and health_state != "degraded")
        or (health_status == "failed" and health_state not in {"failed", "blocked", "serve_failed"})
    ):
        raise ValueError("product health status does not match its required stage evidence")
    expected_run_status = {"complete": "success", "degraded": "degraded", "failed": "failed"}[health_status]
    if recorded.get("status") != expected_run_status:
        raise ValueError("scheduled result status does not match product health")
    if health.get("serve_status") in {"success", "no_findings"}:
        serve_output = recorded.get("children", {}).get("serve", {}).get("output_path")
        if not serve_output:
            raise ValueError("successful serve output is missing from the product receipt")
        verified = _verify_artifacts(Path(serve_output))
        if verified != {
            key: recorded["children"]["serve"][key]
            for key in ("run_id", "report", "validation")
        }:
            raise ValueError("serve artifact evidence does not match the product receipt")
        candidates_path = Path(serve_output) / "candidates.json"
        candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
        candidates_present = bool(candidates.get("candidates"))
        if candidates_present == (health.get("serve_judge_status") == "not_required_no_candidates"):
            raise ValueError("assessment requirement does not match validated candidates")
    lineage = health.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError("product health lineage is missing")
    acquisition_ref = lineage.get("acquisition")
    if isinstance(acquisition_ref, dict):
        if acquisition_ref.get("path") != "acquisition.json":
            raise ValueError("acquisition artifact path is invalid")
        acquisition_path = output_path / acquisition_ref["path"]
        if (
            acquisition_path.is_symlink()
            or not acquisition_path.is_file()
            or digest(acquisition_path.read_bytes()) != acquisition_ref.get("sha256")
        ):
            raise ValueError("acquisition artifact is missing or corrupt")
        acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
        if acquisition.get("coverage", {}).get("source_errors") != health.get("source_errors"):
            raise ValueError("source errors do not match acquisition evidence")
        if health.get("source_status") == "failed" and (
            set(health.get("source_errors", {})) != {"reddit", "x"}
            or any(acquisition.get("source_results", {}).get(source, {}).get("posts") for source in ("reddit", "x"))
            or health.get("artifacts", {}).get("model_calls")
            or health.get("delivery_status") != "not_run"
        ):
            raise ValueError("total acquisition failure continued into product execution")
    elif health.get("source_status") not in {"not_run"}:
        raise ValueError("product receipt is missing acquisition lineage")
    shadow_ref = lineage.get("shadow")
    if isinstance(shadow_ref, dict):
        if shadow_ref.get("path") != "shadow/comparison.json":
            raise ValueError("shadow comparison artifact path is invalid")
        comparison_path = output_path / shadow_ref["path"]
        if (
            comparison_path.is_symlink()
            or not comparison_path.is_file()
            or digest(comparison_path.read_bytes()) != shadow_ref.get("sha256")
        ):
            raise ValueError("shadow comparison artifact is corrupt")
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        if comparison.get("status") != health.get("shadow_comparison_status"):
            raise ValueError("shadow status does not match comparison evidence")
    elif health.get("shadow_status") == "complete" or health.get("shadow_comparison_status") in {"complete", "success"}:
        raise ValueError("complete shadow is missing comparison lineage")
    delivery_ref = lineage.get("delivery")
    if isinstance(delivery_ref, dict):
        if delivery_ref.get("path") != "serve/discord-delivery.json":
            raise ValueError("delivery artifact path is invalid")
        delivery_path = output_path / delivery_ref["path"]
        if (
            delivery_path.is_symlink()
            or not delivery_path.is_file()
            or digest(delivery_path.read_bytes()) != delivery_ref.get("sha256")
        ):
            raise ValueError("delivery artifact is corrupt")
        delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
        if delivery.get("status") != health["delivery_status"]:
            raise ValueError("delivery status does not match delivery artifact")
    elif health.get("delivery_status") in {"delivered", "delivered_reconciled"}:
        raise ValueError("successful delivery is missing artifact lineage")
    assessment_ref = lineage.get("assessment")
    if isinstance(assessment_ref, dict):
        if assessment_ref.get("path") != "serve/assessment.json":
            raise ValueError("assessment artifact path is invalid")
        assessment_path = output_path / assessment_ref["path"]
        if (
            assessment_path.is_symlink()
            or not assessment_path.is_file()
            or digest(assessment_path.read_bytes()) != assessment_ref.get("sha256")
        ):
            raise ValueError("assessment artifact is corrupt")
        assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
        if assessment.get("status") != health.get("serve_judge_status"):
            raise ValueError("assessment status does not match assessment artifact")
    elif health.get("serve_judge_status") not in {"not_required_no_candidates", "not_run"}:
        raise ValueError("assessment outcome is missing artifact evidence")
    if health.get("visual_render_status") == "success":
        render_path = output_path / "serve" / "report.html"
        if render_path.is_symlink() or not render_path.is_file():
            raise ValueError("successful render artifact is missing")
    return health_status


def run_cycle(config, scheduled_for=None, boundaries=None):
    if config.get("enabled") is not True:
        return {"record_id": None, "status": "disabled", "evidence_kind": "synthetic_offline"}
    hermes_tmpdir = _ensure_hermes_tmpdir()
    slot = _validate_slot(config, scheduled_for) if scheduled_for else _scheduled_for(config)
    database_path = Path(config["state_db"])
    output_root = Path(config["output_root"])
    if config.get("mode", "fixture") == "product":
        from need_radar.product import _private_dir

        _private_dir(database_path.parent)
        _private_dir(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    record_id = uuid.uuid4().hex
    output_directory = output_root / record_id
    record_path = output_directory / "run-record.json"
    database = _database(database_path)
    lock = _lock(database_path)
    configuration = {
        "schedule": config["schedule"],
        "budget": config["budget"],
        "timeout_seconds": config["timeout_seconds"],
        "provider_invoked": False,
        "source_acquisition_invoked": False,
        "real_delivery_invoked": False,
    }
    details = {"configuration": configuration, "output_path": str(output_directory)}
    _insert_record(database, record_id, "scheduled_run", slot, "running" if lock else "overlap", details, output_directory)
    if not lock:
        database.execute(
            "UPDATE run_records SET finished_at = ? WHERE record_id = ?",
            (_timestamp(datetime.now(UTC)), record_id),
        )
        database.commit()
        database.close()
        return {"record_id": record_id, "status": "overlap", "evidence_kind": "synthetic_offline"}

    started_at = datetime.now(UTC)
    result = {
        "schema_version": 1,
        "record_id": record_id,
        "scheduled_for": slot,
        "started_at": _timestamp(started_at),
        "status": "failed",
        "evidence_kind": "synthetic_offline",
        "configuration": configuration,
        "inputs": {},
        "children": {},
        "budget": {"limits": config["budget"], "planned": {}, "used": {}},
        "stages": {},
        "warnings": [],
        "coverage": {
            "source_coverage": "fixture_only; live completeness unverified",
            "provider_compatibility": "not verified",
            "redaction": {"applied_before_persistence": True, "coverage": "best_effort", "complete": False},
        },
    }
    linked_watchdog = database.execute(
        "SELECT record_id FROM run_records WHERE kind = 'missed_check' AND scheduled_for = ? "
        "AND status = 'missed' ORDER BY created_at DESC LIMIT 1",
        (slot,),
    ).fetchone()
    if linked_watchdog:
        result["linked_watchdog_record_id"] = linked_watchdog[0]
        database.execute(
            "UPDATE run_records SET linked_record_id = ? WHERE record_id = ?",
            (record_id, linked_watchdog[0]),
        )
        database.execute(
            "UPDATE run_records SET linked_record_id = ? WHERE record_id = ?",
            (linked_watchdog[0], record_id),
        )
        database.commit()
    if config.get("mode", "fixture") == "product":
        try:
            from need_radar.product import run_product_cycle

            output_directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            operation = run_product_cycle(
                config, output_directory, record_id, slot, boundaries=boundaries,
            )
            health_path = output_directory / "product-health.json"
            health_artifact = _file_reference(health_path)
            health_status = operation.get("health_status", "failed")
            try:
                health = json.loads(health_path.read_text(encoding="utf-8"))
                if health.get("health_status") != health_status:
                    health_status = "failed"
            except (OSError, ValueError, json.JSONDecodeError):
                health_status = "failed"
            scheduled_status = {"complete": "success", "degraded": "degraded", "failed": "failed"}.get(health_status, "failed")
            result.update({
                "status": scheduled_status,
                "product_health_status": health_status,
                "evidence_kind": operation.get("evidence_kind", "synthetic_injected_boundary"),
                "stages": {
                    "acquisition": operation.get("source_status", "not_run"),
                    "serve": operation.get("serve_status", "not_run"),
                    "assessment": operation.get("serve_judge_status", "not_run"),
                    "render": operation.get("visual_render_status", "not_run"),
                    "discord_delivery": operation.get("delivery_status", "not_run"),
                    "shadow": operation.get("shadow_status", "not_run"),
                    "comparison": operation.get("shadow_comparison_status", "not_run"),
                },
                "budget": operation.get("budget"),
                "coverage": {
                    "source_coverage": operation.get("source_status", "not_run"),
                    "source_errors": operation.get("source_errors", {}),
                },
                "children": {"product": {
                    "output_path": str(output_directory), "stage": operation.get("stage"),
                    "health_status": health_status, "health_artifact": health_artifact,
                }},
                "warnings": ([operation["error"]] if operation.get("error") else []) + operation.get("warnings", []),
            })
            dispatches = operation.get("dispatches", {})
            authentic = operation.get("evidence_kind") == "authentic_live"
            result["configuration"].update({
                "provider_invoked": authentic and dispatches.get("model", 0) > 0,
                "source_acquisition_invoked": authentic and dispatches.get("source", 0) > 0,
                "real_delivery_invoked": authentic and dispatches.get("discord", 0) > 0,
                "injected_boundary_dispatches": dispatches if operation.get("evidence_kind") == "synthetic_injected_boundary" else {},
            })
            if operation.get("serve_status") in {"success", "no_findings"}:
                serve_ref = _accept_serve_exit(0, output_directory / "serve")
                result["children"]["serve"] = {**serve_ref, "output_path": str(output_directory / "serve")}
            return _finish(database, lock, record_path, result)
        except Exception as error:
            result["status"] = "failed"
            result["error"] = type(error).__name__
            return _finish(database, lock, record_path, result)
    try:
        output_directory.mkdir(parents=True, exist_ok=False)
        serve_fixture = _fixture(config["serve_fixture"])
        shadow_fixture = _fixture(config["shadow_fixture"])
        serve_bytes, shadow_bytes = json_bytes(serve_fixture), json_bytes(shadow_fixture)
        serve_fixture_path = output_directory / "serve-fixture.json"
        shadow_fixture_path = output_directory / "shadow-fixture.json"
        serve_fixture_path.write_bytes(serve_bytes)
        shadow_fixture_path.write_bytes(shadow_bytes)
        result["inputs"] = {
            "serve_fixture_sha256": digest(serve_bytes),
            "shadow_fixture_sha256": digest(shadow_bytes),
            "fixture_inputs_redacted": True,
        }
        planned = _planned_usage(serve_fixture, shadow_fixture)
        planned_total = _sum_usage(planned)
        result["budget"].update({"planned_calls": planned, "planned": planned_total})
        if (
            planned_total["requests"] > config["budget"]["requests"]
            or planned_total["tokens"] > config["budget"]["tokens"]
        ):
            result["status"] = "budget_exceeded"
            result["error"] = "aggregate fixture usage exceeds the configured run budget"
            return _finish(database, lock, record_path, result)

        environment = _child_environment(hermes_tmpdir)
        deadline = time.monotonic() + config["timeout_seconds"]
        serve_directory = output_directory / "serve"
        serve_command = [
            sys.executable, "-m", "need_radar", "--fixture", str(serve_fixture_path), "--output", str(serve_directory),
        ]
        child = _run_command(serve_command, deadline - time.monotonic(), environment)
        if child is None:
            result["status"] = "timeout"
            result["error"] = "serve stage exceeded the operation deadline"
            return _finish(database, lock, record_path, result)
        if child.returncode != 0:
            result["status"] = "serve_failed"
            result["error"] = "serve command failed"
            result["children"]["serve"] = _process_evidence(child)
            return _finish(database, lock, record_path, result)
        serve_ref = _accept_serve_exit(child.returncode, serve_directory)
        result["children"]["serve"] = {
            **serve_ref,
            "output_path": str(serve_directory),
            "trace_sha256": digest((serve_directory / "trace.jsonl").read_bytes()),
            "logs_sha256": digest((serve_directory / "logs.jsonl").read_bytes()),
        }
        result["stages"]["serve"] = "success"

        delivery_command = [
            sys.executable, "-m", "need_radar.discord_delivery", "--run-dir", str(serve_directory),
        ]
        try:
            child = _run_command(delivery_command, deadline - time.monotonic(), environment)
            if child is None:
                delivery_status = "timeout"
            elif child.returncode != 0:
                delivery_status = "failed"
                result["children"]["fake_delivery"] = {
                    "status": delivery_status,
                    **_process_evidence(child),
                }
            else:
                delivery_path = serve_directory / "discord-delivery.json"
                delivery_status = json.loads(delivery_path.read_text(encoding="utf-8")).get("status", "failed")
                _verify_artifacts(serve_directory)
                result["children"]["fake_delivery"] = {
                    "status": delivery_status,
                    "artifact": _artifact_reference(delivery_path),
                }
        except Exception:
            delivery_status = "failed"
            result["children"]["fake_delivery"] = {
                "status": delivery_status,
                "artifact": _file_reference(serve_directory / "discord-delivery.json"),
            }
        result["stages"]["fake_delivery"] = delivery_status
        shadow_directory = output_directory / "shadow"
        shadow_command = [
            sys.executable, "-m", "need_radar.shadow", "--serve-dir", str(serve_directory),
            "--fixture", str(shadow_fixture_path), "--output", str(shadow_directory),
        ]
        try:
            child = _run_command(shadow_command, deadline - time.monotonic(), environment)
            if child is None:
                shadow_status = "timeout"
            elif child.returncode != 0:
                shadow_status = "failed"
                result["children"]["shadow"] = {
                    "status": shadow_status,
                    **_process_evidence(child),
                    "artifact": _file_reference(shadow_directory / "comparison.json"),
                }
            else:
                comparison_path = shadow_directory / "comparison.json"
                shadow_status = json.loads(comparison_path.read_text(encoding="utf-8")).get("status", "failed") if comparison_path.is_file() else "failed"
                result["children"]["shadow"] = {
                    "status": shadow_status,
                    "artifact": _artifact_reference(comparison_path),
                }
        except Exception:
            shadow_status = "failed"
            result["children"]["shadow"] = {
                "status": shadow_status,
                "artifact": _file_reference(shadow_directory / "comparison.json"),
            }
        try:
            optional_stages = _optional_statuses(serve_directory, shadow_status, delivery_status)
        except Exception:
            optional_stages = {
                "judge": "unavailable",
                "render": "unavailable",
                "shadow": shadow_status,
                "fake_delivery": delivery_status,
                "warnings": ["optional_stage_record_unavailable"],
            }
        result["stages"].update(optional_stages)
        result["warnings"] = result["stages"]["warnings"]
        try:
            actual = _actual_usage(serve_directory, shadow_directory)
            actual_total = _sum_usage(actual)
            result["budget"].update({"actual_calls": actual, "used": actual_total})
        except Exception:
            actual_total = None
            result["budget"]["usage_evidence"] = "incomplete"
            result["warnings"].append("actual_usage_evidence_incomplete")
        planned_by_stage = {item["stage"]: item for item in planned}
        actual_by_stage = {item["stage"]: item for item in result["budget"].get("actual_calls", [])}
        budget_complete = planned_by_stage == actual_by_stage
        result["coverage"]["budget"] = {
            "evidence_kind": "recorded_fixture_usage",
            "complete": budget_complete,
            "live_spend_verified": False,
        }
        if not budget_complete:
            result["warnings"].append("planned_and_observed_fixture_usage_differ")
        if actual_total and (
            actual_total["requests"] > config["budget"]["requests"]
            or actual_total["tokens"] > config["budget"]["tokens"]
        ):
            result["warnings"].append("aggregate_budget_exceeded_after_execution")
            result["status"] = "budget_violation"
        elif not budget_complete:
            result["status"] = "budget_unverified"
        else:
            result["status"] = "success" if result["stages"]["serve"] == "success" else "failed"
        return _finish(database, lock, record_path, result)
    except Exception as error:
        result["status"] = "failed"
        result["error"] = type(error).__name__
        return _finish(database, lock, record_path, result)


def _finish(database, lock, record_path, result):
    result["finished_at"] = _timestamp(datetime.now(UTC))
    record_hash = _write_json(record_path, result)
    database.execute(
        "UPDATE run_records SET status = ?, finished_at = ?, record_sha256 = ?, details = ? WHERE record_id = ?",
        (
            result["status"],
            result["finished_at"],
            record_hash,
            json.dumps(redact({"warnings": result.get("warnings", []), "budget": result.get("budget", {})})),
            result["record_id"],
        ),
    )
    database.commit()
    database.close()
    if lock:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return {
        "record_id": result["record_id"],
        "status": result["status"],
        "product_health_status": result.get("product_health_status"),
        "source_status": result.get("coverage", {}).get("source_coverage"),
        "source_errors": result.get("coverage", {}).get("source_errors", {}),
        "stages": result.get("stages"),
        "product_health_artifact": result.get("children", {}).get("product", {}).get("health_artifact"),
        "evidence_kind": result["evidence_kind"],
        "warnings": result.get("warnings", []),
        "record_sha256": record_hash,
    }


def check_missed(config, scheduled_for=None, observed_at=None):
    _ensure_hermes_tmpdir()
    observed = observed_at or datetime.now(UTC)
    if scheduled_for:
        slot = _validate_slot(config, scheduled_for)
    else:
        anchor = _utc(config["schedule"]["anchor_at"])
        latest_due = observed - timedelta(seconds=config["schedule"]["grace_seconds"])
        slot = _scheduled_for(config, latest_due if latest_due >= anchor else observed)
    due_at = _utc(slot) + timedelta(seconds=config["schedule"]["grace_seconds"])
    database = _database(Path(config["state_db"]))
    product_health_status = None
    if observed < due_at:
        status = "not_due"
        main_record = None
    else:
        main_record = database.execute(
            "SELECT record_id, status, output_path, record_sha256, created_at, details FROM run_records "
            "WHERE kind = 'scheduled_run' AND scheduled_for = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (slot,),
        ).fetchone()
        if main_record is None:
            status = "missed"
        elif main_record[1] == "running":
            try:
                run_details = json.loads(main_record[5])
                timeout = run_details["configuration"]["timeout_seconds"]
                stale_after = timeout + config["schedule"]["grace_seconds"]
                status = "failed" if (observed - _utc(main_record[4])).total_seconds() > stale_after else "running"
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                status = "bad_data"
        elif main_record[1] == "overlap":
            status = "overlap"
        elif main_record[1] in {"success", "degraded", "failed"}:
            status = "observed" if main_record[1] == "success" else main_record[1]
            try:
                receipt = Path(main_record[2]) / "run-record.json"
                if not receipt.is_file() or digest(receipt.read_bytes()) != main_record[3]:
                    raise ValueError("run receipt is missing or corrupt")
                recorded = json.loads(receipt.read_text(encoding="utf-8"))
                if recorded.get("status") != main_record[1]:
                    raise ValueError("run receipt status does not match scheduled health")
                product_health_status = _verify_product_health(receipt, recorded)
                if product_health_status is None:
                    if recorded.get("status") == "success":
                        serve_output = recorded.get("children", {}).get("serve", {}).get("output_path")
                        if not serve_output:
                            raise ValueError("run receipt has no successful serve output")
                        _verify_artifacts(Path(serve_output))
                    elif main_record[1] != "failed":
                        raise ValueError("run receipt has no product health evidence")
                elif product_health_status == "complete":
                    status = "observed"
            except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, json.JSONDecodeError):
                status = "bad_data"
        else:
            status = "failed"
    record_id = uuid.uuid4().hex
    details = {
        "observed_at": _timestamp(observed),
        "grace_seconds": config["schedule"]["grace_seconds"],
        "independent_entrypoint": True,
        "main_record_id": main_record[0] if main_record else None,
        "main_status": main_record[1] if main_record else None,
        "product_health_status": product_health_status if main_record else None,
        "evidence_kind": "local_state_check",
    }
    _insert_record(
        database, record_id, "missed_check", slot, status, details,
        linked_record_id=main_record[0] if main_record else None,
    )
    database.execute(
        "UPDATE run_records SET finished_at = ? WHERE record_id = ?",
        (_timestamp(datetime.now(UTC)), record_id),
    )
    database.commit()
    if main_record:
        database.execute(
            "UPDATE run_records SET linked_record_id = ? WHERE record_id = ? AND linked_record_id IS NULL",
            (record_id, main_record[0]),
        )
        database.commit()
    database.close()
    return {
        "record_id": record_id, "status": status, "scheduled_for": slot,
        "linked_record_id": details["main_record_id"],
        "product_health_status": product_health_status if main_record else None,
    }


def main(argv=None, boundaries=None):
    parser = argparse.ArgumentParser(description="Run or monitor a bounded Need Radar operation under Hermes Cron.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "missed"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True, type=Path)
        command.add_argument("--scheduled-for")
    arguments = parser.parse_args(argv)
    try:
        config = _read_config(arguments.config)
        if not config.get("enabled"):
            result = {"record_id": None, "status": "disabled", "evidence_kind": "synthetic_offline"}
        else:
            result = run_cycle(config, arguments.scheduled_for, boundaries=boundaries) if arguments.command == "run" else check_missed(
                config, arguments.scheduled_for,
            )
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(redact(result), sort_keys=True))
    return 0 if result["status"] in {"disabled", "success", "observed", "running", "missed", "not_due", "overlap", "bad_data"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
