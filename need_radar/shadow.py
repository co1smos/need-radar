import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

from need_radar import assessment, comparison
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


def _validate_serve_assessment(artifacts, snapshot_items, stage_refs):
    references = {
        "candidate_validation": "v0_candidates",
        "snapshot": "snapshot",
        "consolidation": "v0_consolidation",
        "frozen_report": "report",
    }
    errors = []
    if any(name not in artifacts for name in (
        "v0_consolidation", "v0_judge_prompt", "v0_judge_response", "v0_assessment",
    )):
        return ["serve assessment artifacts are missing"]
    raw_assessment, scored = artifacts["v0_assessment"]
    raw_consolidation, consolidation = artifacts["v0_consolidation"]
    raw_judge_response, judge_response = artifacts["v0_judge_response"]
    scored_sources = scored.get("source_artifacts")
    scored_sources = scored_sources if isinstance(scored_sources, dict) else {}
    for source_name, artifact_name in references.items():
        reference = scored_sources.get(source_name, {})
        expected = stage_refs.get(source_name, {})
        if reference.get("artifact_id") != expected.get("artifact_id") or reference.get("sha256") != expected.get("sha256"):
            errors.append(f"serve assessment {source_name} lineage does not resolve")
    if raw_assessment.get("lineage", {}).get("input_artifact_id") != raw_judge_response.get("lineage", {}).get("artifact_id"):
        errors.append("serve assessment does not descend from its judge response")
    if judge_response.get("status") != "success":
        errors.append("serve judge response did not succeed")
    if raw_consolidation.get("lineage", {}).get("input_artifact_id") != artifacts["v0_candidates"][0].get("lineage", {}).get("artifact_id"):
        errors.append("serve consolidation does not descend from candidate validation")
    coverage = scored.get("coverage")
    if scored.get("status") != "success" or not isinstance(coverage, dict) or not coverage.get("fixture_assessment_complete"):
        errors.append("serve judging is incomplete")
    if scored.get("rubric_version") != assessment.RUBRIC_VERSION or scored.get("prompt_version") != assessment.PROMPT_VERSION:
        errors.append("serve rubric or judge prompt version differs")
    try:
        prompts = assessment.build_isolated_judge_prompts(consolidation.get("clusters", []), snapshot_items)
    except (KeyError, TypeError, ValueError):
        prompts = None
        errors.append("serve assessment evidence does not resolve to the frozen snapshot")
    if prompts != artifacts["v0_judge_prompt"][1].get("prompts"):
        errors.append("serve judge prompts differ from the frozen candidate evidence")
    if not errors:
        try:
            rows = scored.get("assessments")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("assessment rows are malformed")
            responses = [{key: value for key, value in row.items() if key != "cluster_id"} for row in rows]
            if responses != judge_response.get("response"):
                raise ValueError("assessments differ from the recorded judge response")
            validated = assessment.validate_assessments(responses, consolidation["clusters"])
            if validated["status"] != "success" or validated["assessments"] != rows:
                raise ValueError("assessment grounding is invalid")
        except (KeyError, TypeError, ValueError):
            errors.append("serve assessment has incomplete or invalid grounding")
    return errors


def _run_v1_assessment(
    raw_judge, v1_result, context, snapshot_ref, report_ref, output,
    database, tracer, run_id, trace_id, sequence,
):
    (output / "v1").mkdir(parents=True, exist_ok=True)
    _, validation = _read_artifact(output / "v1" / "candidates.json")
    candidates = validation.get("candidates", [])
    consolidation = assessment.consolidate_within_arm("shadow", candidates)
    with tracer.span("v1_consolidation", inputs={"validation_id": v1_result["validation"]["artifact_id"]}) as span:
        consolidation_meta = persist_stage(
            output, database, run_id, trace_id, sequence, "v1_consolidation", "success",
            Path("v1/consolidation.json"), consolidation, span, v1_result["validation"],
            details={"cluster_count": len(consolidation["clusters"])},
        )
    prompt_errors = []
    try:
        prompts = assessment.build_isolated_judge_prompts(consolidation["clusters"], context["items"])
    except (KeyError, TypeError, ValueError):
        prompts = None
        prompt_errors.append("judge evidence does not resolve to the frozen context")
    prompt_value = {
        "prompts": prompts,
        "source_artifacts": {
            "candidate_validation": v1_result["validation"],
            "snapshot": snapshot_ref,
            "consolidation": consolidation_meta,
            "frozen_report": report_ref,
        },
    }
    with tracer.span("v1_judge_prompt", inputs={"source_artifacts": prompt_value["source_artifacts"]}) as span:
        prompt_meta = persist_stage(
            output, database, run_id, trace_id, sequence + 1, "v1_judge_prompt",
            "success" if prompts is not None else "failed", Path("v1/judge-prompt.json"),
            prompt_value, span, consolidation_meta, details={"errors": prompt_errors},
        )
    judge_input = redact(raw_judge) if isinstance(raw_judge, dict) else None
    response = judge_input.get("response") if isinstance(judge_input, dict) else None
    judge_status = "success" if prompts is not None and isinstance(judge_input, dict) and judge_input.get("status") == "synthetic_response" else "unavailable"
    judge_error = None if judge_status == "success" else "pre-assessed shadow fixture is missing or unsupported"
    judge_id = uuid.uuid4().hex
    judge_value = {
        "boundary": "synthetic_fixture_pre_assessed_input",
        "provider_invoked": False,
        "status": judge_status,
        "response": response,
        "error": judge_error,
    }
    with tracer.span("v1_assessment_input", inputs={"prompts": prompts}, attributes={"provider_invoked": False}) as span:
        if judge_status != "success":
            span["status"] = "failed"
        judge_meta = persist_stage(
            output, database, run_id, trace_id, sequence + 2, "v1_assessment_input", judge_status,
            Path("v1/assessment-input.json"), judge_value, span, prompt_meta, judge_id,
            {"provider_invoked": False},
        )
    if judge_status == "success":
        result = assessment.validate_assessments(response, consolidation["clusters"])
    else:
        result = {"status": "unavailable", "assessments": [], "errors": [judge_error]}
    result.update({
        "rubric_version": assessment.RUBRIC_VERSION,
        "prompt_version": assessment.PROMPT_VERSION,
        "source_artifacts": prompt_value["source_artifacts"],
        "coverage": {
            "assessment_complete": result["status"] == "success",
            "candidate_count": len(consolidation["clusters"]),
            "kind": "synthetic_pre_assessed_input",
            "live_verification": "not performed",
        },
        "provider_invoked": False,
    })
    with tracer.span("v1_assessment", inputs={"input_artifact_id": judge_meta["artifact_id"], "result": result}) as span:
        if result["status"] != "success":
            span["status"] = "failed"
        assessment_meta = persist_stage(
            output, database, run_id, trace_id, sequence + 3, "v1_assessment", result["status"],
            Path("v1/assessment.json"), result, span, judge_meta, judge_id,
            {"candidate_count": len(consolidation["clusters"])},
        )
    return consolidation, result, {"consolidation": consolidation_meta, "assessment": assessment_meta}, prompts


def _stage_duration(trace_path, artifact_id):
    if not trace_path.is_file():
        return None
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            span = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (span.get("output") or {}).get("artifact_id") == artifact_id:
            return span.get("duration_ms")
    return None


def _comparison_arm(status, snapshot_items, processed_item_ids, processing_complete, settings, validation, consolidation, scored, usage, refs, latency):
    judge_configuration = assessment.build_judge_prompt(
        [{"title": "", "friction": "", "evidence": []}], [],
    )["config"]
    return {
        "status": status,
        "snapshot_items": snapshot_items,
        "processed_item_ids": processed_item_ids,
        "processing_complete": processing_complete,
        "shared_configuration": {
            "extraction": settings,
            "judge": judge_configuration,
        },
        "budget_limits": {
            "requests": settings["request_limit"],
            "tokens": settings["max_tokens"],
            "candidate_limit": settings["candidate_limit"],
        },
        "resource_usage": {
            "requests": usage.get("requests") if isinstance(usage, dict) else None,
            "tokens": usage.get("tokens") if isinstance(usage, dict) else None,
            "cost_usd": usage.get("cost_usd") if isinstance(usage, dict) else None,
            "latency_ms": latency,
        },
        "validation": validation,
        "consolidation": consolidation,
        "assessment": scored,
        "artifact_refs": refs,
    }


def _serve_stage_refs(serve_dir, paths):
    stage_by_name = {
        "snapshot": "snapshot",
        "candidate_validation": "validation",
        "consolidation": "consolidation",
        "judge_prompt": "judge_prompt",
        "judge_call": "judge_call",
        "assessment": "assessment",
        "frozen_report": "report",
    }
    try:
        with sqlite3.connect(serve_dir / "lineage.sqlite3") as database:
            records = {
                row[0]: {"sha256": row[1], "artifact_id": row[2], "status": row[3]}
                for row in database.execute("SELECT stage, output_sha256, artifact_id, status FROM stages")
            }
    except sqlite3.Error:
        return {}, ["serve lineage database is missing or invalid"]
    refs, errors = {}, []
    for ref_name, stage_name in stage_by_name.items():
        record = records.get(stage_name)
        path_name = {
            "snapshot": "snapshot",
            "candidate_validation": "v0_candidates",
            "consolidation": "v0_consolidation",
            "judge_prompt": "v0_judge_prompt",
            "judge_call": "v0_judge_response",
            "assessment": "v0_assessment",
            "frozen_report": "report",
        }[ref_name]
        path = paths[path_name]
        if not record or not path.is_file() or digest(path.read_bytes()) != record["sha256"]:
            errors.append(f"serve {stage_name} artifact does not match its lineage record")
            continue
        if path.suffix == ".json":
            artifact = json.loads(path.read_text(encoding="utf-8"))
            if artifact.get("lineage", {}).get("artifact_id") != record["artifact_id"]:
                errors.append(f"serve {stage_name} artifact ID does not match its lineage record")
        refs[ref_name] = {
            "stage": stage_name,
            "artifact_id": record["artifact_id"],
            "sha256": record["sha256"],
            "status": record["status"],
        }
    return refs, errors


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
    shadow_assessment = experiment.get("shadow_assessment") if isinstance(experiment, dict) else None
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
            "v0_consolidation": "consolidation.json",
            "v0_judge_prompt": "judge-prompt.json",
            "v0_judge_response": "judge-response.json",
            "v0_assessment": "assessment.json",
            "report": "report.md",
        }.items()
    }
    artifacts = {name: _read_artifact(path) for name, path in paths.items() if path.suffix == ".json" and path.is_file()}
    for name in ("v0_consolidation", "v0_judge_prompt", "v0_judge_response", "v0_assessment"):
        artifacts.setdefault(name, ({}, {}))
    snapshot_raw, snapshot = artifacts["snapshot"]
    selection_raw, selection = artifacts["selection"]
    context_raw, assembled_context = artifacts["context"]
    truncation_raw, context = artifacts["truncation"]
    v0_prompt_raw, v0_prompt = artifacts["v0_prompt"]
    v0_response_raw, v0_response = artifacts["v0_response"]
    v0_candidates_raw, v0_candidates = artifacts["v0_candidates"]
    v0_consolidation_raw, v0_consolidation = artifacts["v0_consolidation"]
    v0_judge_prompt_raw, v0_judge_prompt = artifacts["v0_judge_prompt"]
    v0_judge_response_raw, v0_judge_response = artifacts["v0_judge_response"]
    v0_assessment_raw, v0_assessment = artifacts["v0_assessment"]
    if not isinstance(snapshot.get("items"), list) or not isinstance(context.get("items"), list):
        raise ValueError("serve artifacts do not contain a frozen snapshot and context")

    parity_errors = []
    serve_stage_refs, serve_stage_errors = _serve_stage_refs(serve_dir, paths)
    assessment_precondition_errors = serve_stage_errors + _validate_serve_assessment(
        artifacts, snapshot["items"], serve_stage_refs,
    )
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
            "v0_consolidation": v0_consolidation_raw,
            "v0_judge_prompt": v0_judge_prompt_raw,
            "v0_judge_response": v0_judge_response_raw,
            "v0_assessment": v0_assessment_raw,
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
        "v0_consolidation": "consolidation",
        "v0_judge_prompt": "judge_prompt",
        "v0_judge_response": "judge_call",
        "v0_assessment": "assessment",
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
        ("v0_consolidation", "v0_candidates"),
        ("v0_judge_prompt", "v0_consolidation"),
        ("v0_judge_response", "v0_judge_prompt"),
        ("v0_assessment", "v0_judge_response"),
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
        or usage["tokens"] < 0
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
                v1_consolidation, v1_assessment, v1_assessment_refs, _ = _run_v1_assessment(
                    shadow_assessment,
                    v1_result,
                    context,
                    serve_stage_refs.get("snapshot", {}),
                    serve_stage_refs.get("frozen_report", {}),
                    output,
                    database,
                    tracer,
                    run_id,
                    trace_id,
                    4,
                )
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
                assessment_reasons = [*assessment_precondition_errors, *parity_errors]
                if v0_candidates.get("status") not in {"success", "no_findings"}:
                    assessment_reasons.append(f"v0 serve outcome is {v0_candidates.get('status', 'unknown')}")
                if v1_result["status"] not in {"success", "no_findings"}:
                    assessment_reasons.append(f"v1 shadow outcome is {v1_result['status']}")
                if not isinstance(snapshot.get("items"), list) or not snapshot["items"]:
                    assessment_reasons.append("common frozen input is empty")
                v0_validation = {
                    **v0_candidates,
                    "candidate_count": len(v0_candidates.get("candidates", [])),
                }
                v1_validation_raw, v1_validation = _read_artifact(output / "v1" / "candidates.json")
                v1_validation["candidate_count"] = len(v1_validation.get("candidates", []))
                v0_usage = v0_response.get("usage")
                v1_usage = v1_result.get("resource_usage")
                v0_arm = _comparison_arm(
                    v0_candidates.get("status"), snapshot["items"],
                    context.get("item_ids", []),
                    not context.get("truncated", False) and context_matches,
                    settings,
                    v0_validation, v0_consolidation, v0_assessment, v0_usage,
                    {
                        key: serve_stage_refs.get(key, {})
                        for key in ("candidate_validation", "snapshot", "consolidation", "frozen_report")
                    },
                    _stage_duration(serve_dir / "trace.jsonl", lineage["v0_response"].get("artifact_id")),
                )
                v1_arm = _comparison_arm(
                    v1_result.get("status"), snapshot["items"],
                    context.get("item_ids", []),
                    not context.get("truncated", False) and context_matches,
                    settings,
                    v1_validation, v1_consolidation, v1_assessment, v1_usage,
                    {
                        "candidate_validation": v1_result["validation"],
                        "snapshot": serve_stage_refs.get("snapshot", {}),
                        "consolidation": v1_assessment_refs["consolidation"],
                        "assessment": v1_assessment_refs["assessment"],
                        "frozen_report": serve_stage_refs.get("frozen_report", {}),
                    },
                    _stage_duration(output / "trace.jsonl", v1_result["model_call"]["artifact_id"]),
                )
                assessment_comparison = (
                    comparison.compare_assessed_arms(v0_arm, v1_arm)
                    if not assessment_reasons
                    else {
                        "status": "inconclusive",
                        "reasons": list(dict.fromkeys(assessment_reasons)),
                        "matching": None,
                        "arms": None,
                        "qualification": "not comparable; not market truth or global recall",
                        "promotion_recommendation": "none",
                    }
                )
                assessment_report = comparison.render_markdown(assessment_comparison)
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
                    "assessment_comparison": assessment_comparison,
                    "assessment_comparison_report_sha256": digest(assessment_report.encode()),
                    "assessment_artifacts": {
                        "v0": {
                            "consolidation": serve_stage_refs.get("consolidation"),
                            "assessment": serve_stage_refs.get("assessment"),
                        },
                        "v1": v1_assessment_refs,
                    },
                    "resource_ceilings": settings,
                    "serve_report_sha256": serve_report_hash,
                    "serve_report_unchanged": report_unchanged,
                    "publication": "disabled",
                    "serve_substitution": False,
                    "browsing": False,
                    "evidence_enrichment": False,
                    "judge": "pre-assessed fixture only; no provider invoked",
                }
                root_span["attributes"]["result_status"] = result["status"]
                with tracer.span(
                    "assessment_comparison_report",
                    inputs={"assessment_comparison": assessment_comparison},
                    attributes={"result_status": assessment_comparison["status"]},
                ) as span:
                    report_meta = persist_stage(
                        output, database, run_id, trace_id, 8, "assessment_comparison_report", "success",
                        Path("comparison.md"), assessment_report, span, v1_assessment_refs["assessment"],
                    )
                with tracer.span("comparison", inputs={"arms": result["arms"]}, attributes={"result_status": result["status"]}) as span:
                    result["assessment_comparison_report_sha256"] = report_meta["sha256"]
                    comparison_meta = persist_stage(
                        output, database, run_id, trace_id, 9, "comparison", result["status"],
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
