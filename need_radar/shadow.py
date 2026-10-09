import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

from need_radar.__main__ import (
    JsonlSpanSink,
    LangfuseBoundary,
    Tracer,
    assemble_context,
    digest,
    execute_extraction,
    json_bytes,
    make_prompt,
    persist_stage,
    redact,
    resolve_shared_settings,
    select_items,
    truncate_context,
    validate_response,
)


def _read_artifact(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw, redact({key: value for key, value in raw.items() if key != "lineage"})


def _write_summary(output, tracer, ids):
    summary = {
        "ids": ids,
        "local_export": {
            "status": "failed" if tracer.export_failures else "success",
            "span_count": tracer.span_count,
            "exported_span_count": tracer.exported_span_count,
            "failures": tracer.export_failures,
        },
        "remote_export": {
            "status": "unverified",
            "enabled": False,
            "destination_authorized": False,
            "data_policy_authorized": False,
            "verification": "not_attempted",
        },
        "coverage": {
            "complete": not tracer.export_failures,
            "redaction": {
                "applied_before_persistence_and_export": True,
                "coverage": "best_effort",
                "complete": False,
            },
        },
    }
    (output / "observability.json").write_bytes(json_bytes(redact(summary)))


def _run_v1(
    model_fixture, context, settings, truncation_meta, output, database, tracer, run_id, trace_id,
    not_run_reason=None,
):
    (output / "v1").mkdir(parents=True, exist_ok=True)
    call_id = uuid.uuid4().hex
    prompt = make_prompt(context["items"], version="v1", shared_settings=settings)
    with tracer.span(
        "v1_prompt_render",
        inputs={"truncation_id": truncation_meta["artifact_id"], "context": context},
        attributes={"arm": "v1", "instruction_version": "v1"},
    ) as span:
        prompt_meta = persist_stage(
            output, database, run_id, trace_id, 1, "v1_prompt_render", "success",
            Path("v1/prompt.json"), prompt, span, truncation_meta, call_id,
            {"instruction_version": "v1", "shared_settings": settings},
        )

    with tracer.span(
        "v1_model_call",
        inputs={"call_id": call_id, "prompt": prompt},
        attributes={"arm": "v1", "call_id": call_id, "boundary": "synthetic_fixture"},
    ) as span:
        if not_run_reason:
            model_status, model_error, response, candidate_limit_applied = (
                "not_run_parity_failure", not_run_reason, None, False
            )
            model_artifact = {
                "boundary": "synthetic_fixture",
                "execution_seam": None,
                "invoked": False,
                "attempted_requests": 0,
                "prompt_sha256": digest(json_bytes(prompt)),
                "status": model_status,
                "reported_status": None,
                "response": None,
                "usage": None,
                "resolved_shared_settings": settings,
                "candidate_count": None,
                "error": model_error,
                "candidate_limit_applied": False,
                "submitted_candidate_count": None,
            }
        else:
            model_status, model_error, model_artifact, response, candidate_limit_applied = execute_extraction(
                prompt, lambda _prompt: model_fixture, settings
            )
        model_artifact["call_id"] = call_id
        model_artifact["arm"] = "v1"
        span["attributes"]["result_status"] = model_status
        model_meta = persist_stage(
            output, database, run_id, trace_id, 2, "v1_model_call", model_status,
            Path("v1/model-response.json"), model_artifact, span, prompt_meta, call_id,
            {"boundary": "synthetic_fixture", "shared_settings": settings},
        )

    with tracer.span(
        "v1_validation",
        inputs={"call_id": call_id, "response": response, "prompt_items": context["items"]},
        attributes={"arm": "v1"},
    ) as span:
        if model_status != "success":
            status = "extraction_failure" if model_status == "failure" else model_status
            candidates, errors = [], [model_error or "model call failed"]
        else:
            status, candidates, errors = validate_response(response, context["items"])
        candidates, errors = redact(candidates), redact(errors)
        validation = {
            "status": status,
            "candidates": candidates,
            "errors": errors,
            "candidate_limit_applied": candidate_limit_applied,
            "submitted_candidate_count": len(response) if isinstance(response, list) else 0,
        }
        span["attributes"]["result_status"] = status
        validation_meta = persist_stage(
            output, database, run_id, trace_id, 3, "v1_validation", status,
            Path("v1/candidates.json"), validation, span, model_meta, call_id,
            {"error_count": len(errors), "candidate_limit_applied": candidate_limit_applied},
        )
    return {
        "status": status,
        "execution_seam": model_artifact.get("execution_seam"),
        "resolved_shared_settings": model_artifact.get("resolved_shared_settings"),
        "candidate_count": len(candidates),
        "candidate_limit_applied": candidate_limit_applied,
        "resource_usage": model_artifact.get("usage"),
        "prompt": prompt_meta,
        "model_call": model_meta,
        "validation": validation_meta,
    }, prompt


def run(serve_dir, fixture_path, output):
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    raw_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture = redact(raw_fixture)
    if not isinstance(fixture, dict) or fixture.get("provider") != "synthetic_fixture":
        raise ValueError("shadow runner supports synthetic_fixture input only")
    experiment = fixture.get("experiment")
    settings, setting_errors = resolve_shared_settings(fixture)
    raw_experiment = raw_fixture.get("experiment") if isinstance(raw_fixture, dict) else None
    shadow_model = raw_experiment.get("shadow_model") if isinstance(raw_experiment, dict) else None
    if setting_errors or not isinstance(shadow_model, dict):
        raise ValueError("invalid shadow fixture: " + "; ".join(setting_errors or ["shadow_model must be an object"]))

    paths = {
        name: serve_dir / filename
        for name, filename in {
            "snapshot": "snapshot.json",
            "selection": "selection.json",
            "context": "context.json",
            "truncation": "truncation.json",
            "v0_prompt": "prompt.json",
            "v0_response": "model-response.json",
            "v0_candidates": "candidates.json",
            "report": "report.md",
        }.items()
    }
    artifacts = {name: _read_artifact(path) for name, path in paths.items() if path.suffix == ".json"}
    snapshot_raw, snapshot = artifacts["snapshot"]
    selection_raw, selection = artifacts["selection"]
    context_raw, assembled_context = artifacts["context"]
    truncation_raw, context = artifacts["truncation"]
    v0_prompt_raw, v0_prompt = artifacts["v0_prompt"]
    v0_response_raw, v0_response = artifacts["v0_response"]
    v0_candidates_raw, v0_candidates = artifacts["v0_candidates"]
    if not isinstance(snapshot.get("items"), list) or not isinstance(context.get("items"), list):
        raise ValueError("serve artifacts do not contain a frozen snapshot and context")

    parity_errors = []
    lineage = {
        name: value.get("lineage", {})
        for name, value in {
            "snapshot": snapshot_raw,
            "selection": selection_raw,
            "context": context_raw,
            "truncation": truncation_raw,
            "v0_prompt": v0_prompt_raw,
            "v0_response": v0_response_raw,
            "v0_candidates": v0_candidates_raw,
        }.items()
    }
    expected_stages = {
        "snapshot": "snapshot",
        "selection": "selection",
        "context": "context_assembly",
        "truncation": "truncation",
        "v0_prompt": "prompt_render",
        "v0_response": "model_call",
        "v0_candidates": "validation",
    }
    if any(lineage[name].get("stage") != stage for name, stage in expected_stages.items()):
        parity_errors.append("serve stage lineage is incomplete")
    for child, parent in (
        ("selection", "snapshot"),
        ("context", "selection"),
        ("truncation", "context"),
        ("v0_prompt", "truncation"),
        ("v0_response", "v0_prompt"),
        ("v0_candidates", "v0_response"),
    ):
        if lineage[child].get("input_artifact_id") != lineage[parent].get("artifact_id"):
            parity_errors.append(f"serve lineage edge {parent}->{child} is invalid")

    expected_selection = select_items(snapshot["items"])
    expected_assembled_context = assemble_context(expected_selection)
    expected_context = truncate_context(expected_assembled_context)
    selection_matches = selection.get("items") == expected_selection
    context_matches = assembled_context == expected_assembled_context and context == expected_context
    if not selection_matches:
        parity_errors.append("serve selection differs from the frozen snapshot")
    if not context_matches:
        parity_errors.append("serve context differs from deterministic frozen-input assembly")

    expected_v0_prompt = make_prompt(context["items"], version="v0", shared_settings=settings)
    v0_prompt_matches = (
        v0_prompt.get("config") == expected_v0_prompt["config"]
        and v0_prompt.get("messages") == expected_v0_prompt["messages"]
    )
    if not v0_prompt_matches:
        parity_errors.append("v0 prompt differs from the resolved shared configuration or frozen context")
    v0_settings_match = v0_response.get("resolved_shared_settings") == settings
    if not v0_settings_match:
        parity_errors.append("v0 model call shared settings differ")
    v0_seam_matches = v0_response.get("execution_seam") == "execute_extraction"
    if not v0_seam_matches:
        parity_errors.append("v0 did not use the shared extraction execution seam")
    v0_call_prompt_matches = v0_response.get("prompt_sha256") == digest(json_bytes(v0_prompt))
    if not v0_call_prompt_matches:
        parity_errors.append("v0 model call prompt hash does not match")
    usage = v0_response.get("usage")
    if not isinstance(usage, dict):
        parity_errors.append("v0 resource usage is missing")
    elif (
        type(usage.get("requests")) is not int
        or usage["requests"] != 1
        or usage["requests"] > settings["request_limit"]
        or type(usage.get("tokens")) is not int
        or usage["tokens"] > settings["max_tokens"]
    ):
        parity_errors.append("v0 resource usage exceeds or does not prove the configured ceilings")
    submitted_count = v0_response.get("submitted_candidate_count")
    if type(submitted_count) is not int:
        parity_errors.append("v0 submitted candidate count is missing")
    elif submitted_count > settings["candidate_limit"]:
        parity_errors.append("v0 submitted candidates exceed the configured ceiling")

    v1_prompt = make_prompt(context["items"], version="v1", shared_settings=settings)
    v0_config = {key: value for key, value in v0_prompt.get("config", {}).items() if key not in {"version", "instruction"}}
    v1_config = {key: value for key, value in v1_prompt["config"].items() if key not in {"version", "instruction"}}
    same_context = (
        assembled_context.get("item_ids") == context.get("item_ids")
        and assembled_context.get("items") == context.get("items")
        and v0_prompt.get("messages", [None, None])[1] == v1_prompt["messages"][1]
    )
    only_instruction_differs = (
        v0_config == v1_config
        and v0_prompt.get("config", {}).get("goal") == v1_prompt["config"]["goal"]
        and v0_prompt.get("config", {}).get("instruction") != v1_prompt["config"]["instruction"]
    )
    if not same_context:
        parity_errors.append("v0 and v1 ordered context bytes differ")
    if not only_instruction_differs:
        parity_errors.append("v0 and v1 resolved configurations differ beyond extraction instructions")
    pre_run_parity_errors = []
    if not selection_matches or not context_matches or not same_context:
        pre_run_parity_errors.append("frozen input context parity is invalid")
    if not v0_prompt_matches or not only_instruction_differs or not v0_call_prompt_matches:
        pre_run_parity_errors.append("extraction prompt parity is invalid")
    if not v0_settings_match or not v0_seam_matches:
        pre_run_parity_errors.append("shared extraction settings or execution seam parity is invalid")
    serve_report_hash = digest(paths["report"].read_bytes())
    serve_refs = {
        name: {
            "path": f"{serve_dir.name}/{paths[name].name}",
            "sha256": digest(paths[name].read_bytes()),
            "stage": lineage[name].get("stage"),
            "artifact_id": lineage[name].get("artifact_id"),
            "trace_id": lineage[name].get("trace_id"),
            "input_artifact_id": lineage[name].get("input_artifact_id"),
        }
        for name in ("snapshot", "selection", "context", "truncation", "v0_prompt", "v0_response", "v0_candidates")
    }
    truncation_meta = {
        "stage": "truncation",
        "artifact_id": lineage["truncation"].get("artifact_id"),
        "sha256": serve_refs["truncation"]["sha256"],
    }
    context_hash = digest(json_bytes(context))
    snapshot_hash = digest(json_bytes(snapshot["items"]))

    output.mkdir(parents=True, exist_ok=True)
    run_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
    ids = {"run_id": run_id, "trace_id": trace_id}
    tracer = Tracer(
        run_id,
        trace_id,
        LangfuseBoundary(output / "trace.jsonl", redact),
        JsonlSpanSink(output / "logs.jsonl"),
        redact,
    )
    result = None
    with sqlite3.connect(output / "lineage.sqlite3") as database:
        database.execute(
            "CREATE TABLE stages (sequence INTEGER, run_id TEXT, stage TEXT, status TEXT, input_stage TEXT, input_sha256 TEXT, artifact_path TEXT, output_sha256 TEXT, details TEXT, trace_id TEXT, span_id TEXT, artifact_id TEXT, input_artifact_id TEXT, call_id TEXT, PRIMARY KEY (run_id, stage))"
        )
        try:
            with tracer.span("shadow_run", inputs={"serve_artifacts": serve_refs, "shared_settings": settings}) as root_span:
                v1_result, v1_prompt = _run_v1(
                    shadow_model, context, settings, truncation_meta, output,
                    database, tracer, run_id, trace_id,
                    not_run_reason="; ".join(pre_run_parity_errors) if pre_run_parity_errors else None,
                )
                settings_identical = (
                    v0_response.get("resolved_shared_settings") == settings
                    and v1_result["resolved_shared_settings"] == settings
                )
                if not settings_identical:
                    parity_errors.append("resolved shared settings differ between arms")
                prompt_parity = (
                    v0_config == {key: value for key, value in v1_prompt["config"].items() if key not in {"version", "instruction"}}
                    and v0_prompt["messages"][1] == v1_prompt["messages"][1]
                    and v0_prompt["config"].get("goal") == v1_prompt["config"]["goal"]
                )
                if not prompt_parity:
                    parity_errors.append("resolved prompt parity check failed")
                same_execution_seam = (
                    v1_result["execution_seam"] == v0_response.get("execution_seam") == "execute_extraction"
                )
                if v1_result["status"] != "not_run_parity_failure" and not same_execution_seam:
                    parity_errors.append("v0 and v1 did not use the shared extraction execution seam")
                report_unchanged = digest(paths["report"].read_bytes()) == serve_report_hash
                if not report_unchanged:
                    parity_errors.append("serve report changed during shadow execution")
                complete = (
                    not parity_errors
                    and v0_candidates.get("status") in {"success", "no_findings"}
                    and v1_result["status"] in {"success", "no_findings"}
                )
                incomplete_reasons = list(parity_errors)
                if v0_candidates.get("status") not in {"success", "no_findings"}:
                    incomplete_reasons.append(f"v0 serve outcome is {v0_candidates.get('status', 'unknown')}")
                if v1_result["status"] not in {"success", "no_findings"}:
                    incomplete_reasons.append(f"v1 shadow outcome is {v1_result['status']}")
                result = {
                    "status": "complete" if complete else "incomplete",
                    "evidence_kind": "synthetic_offline",
                    "verification_limit": "synthetic fixture only; no live source or provider verification",
                    "arms": {
                        "v0": {
                            "status": v0_candidates.get("status", "unknown"),
                            "candidate_count": len(v0_candidates.get("candidates", [])),
                            "resource_usage": usage,
                            "artifacts": serve_refs,
                        },
                        "v1": v1_result,
                    },
                    "parity": {
                        "ordered_snapshot_sha256": snapshot_hash,
                        "context_sha256": context_hash,
                        "shared_settings_sha256": digest(json_bytes(settings)),
                        "same_execution_seam": same_execution_seam,
                        "same_context": same_context,
                        "shared_settings_identical": settings_identical,
                        "only_extraction_instruction_differs": only_instruction_differs and prompt_parity,
                        "errors": parity_errors,
                    },
                    "incomplete_reasons": incomplete_reasons,
                    "resource_ceilings": settings,
                    "serve_report_sha256": serve_report_hash,
                    "serve_report_unchanged": report_unchanged,
                    "publication": "disabled",
                    "serve_substitution": False,
                    "browsing": False,
                    "evidence_enrichment": False,
                    "judge": "not_run",
                }
                root_span["attributes"]["result_status"] = result["status"]
                with tracer.span("comparison", inputs={"arms": result["arms"]}, attributes={"result_status": result["status"]}) as span:
                    comparison_meta = persist_stage(
                        output, database, run_id, trace_id, 4, "comparison", result["status"],
                        Path("comparison.json"), result, span,
                        input_artifact=v1_result["validation"],
                        details={"parity_errors": parity_errors},
                    )
                ids["comparison_id"] = comparison_meta["artifact_id"]
                result["artifact"] = comparison_meta
        finally:
            _write_summary(output, tracer, ids)
    report_after = digest(paths["report"].read_bytes())
    if serve_report_hash != report_after:
        return "incomplete"
    return result["status"]


def main():
    parser = argparse.ArgumentParser(description="Run v1 shadow against the frozen context from a completed v0 serve run.")
    parser.add_argument("--serve-dir", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        status = run(arguments.serve_dir, arguments.fixture, arguments.output)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error) as error:
        print(f"error: {redact(str(error))}", file=sys.stderr)
        return 1
    print(f"status={status} output={arguments.output}")
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
