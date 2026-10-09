import math

from need_radar import assessment


def _match_key(candidate):
    return tuple(" ".join(candidate[key].casefold().split()) for key in ("title", "friction"))


def _validate_arm(arm, name):
    errors = []
    if arm.get("status") not in {"success", "no_findings"}:
        errors.append(f"{name} arm failed")
    items = arm.get("snapshot_items")
    if not isinstance(items, list) or not items:
        errors.append(f"{name} input is empty or missing")
        items = []
    item_ids = [item.get("id") for item in items if isinstance(item, dict)]
    if (
        len(item_ids) != len(items)
        or any(not isinstance(item_id, str) or not item_id for item_id in item_ids)
        or len(set(item_ids)) != len(item_ids)
    ):
        errors.append(f"{name} snapshot item IDs are invalid")
    processed_ids = arm.get("processed_item_ids")
    if (
        arm.get("processing_complete") is not True
        or not isinstance(processed_ids, list)
        or not processed_ids
        or any(not isinstance(item_id, str) or not item_id for item_id in processed_ids)
        or len(set(processed_ids)) != len(processed_ids)
        or any(item_id not in item_ids for item_id in processed_ids)
    ):
        errors.append(f"{name} processing coverage is incomplete")

    usage = arm.get("resource_usage")
    limits = arm.get("budget_limits")
    if not isinstance(usage, dict) or not isinstance(limits, dict):
        errors.append(f"{name} resource usage or budget limits are missing")
    else:
        for key in ("requests", "tokens"):
            if (
                type(usage.get(key)) is not int or usage[key] < 0
                or type(limits.get(key)) is not int or limits[key] < 0
                or usage[key] > limits[key]
            ):
                errors.append(f"{name} {key} usage is invalid or exceeds its budget")
        validation = arm.get("validation")
        candidate_count = validation.get("candidate_count") if isinstance(validation, dict) else None
        candidate_limit = limits.get("candidate_limit")
        if (
            type(candidate_count) is not int or candidate_count < 0
            or type(candidate_limit) is not int or candidate_count > candidate_limit
        ):
            errors.append(f"{name} candidate count is unknown or exceeds its budget")
        cost_limit = limits.get("cost_usd")
        cost = usage.get("cost_usd")
        if cost_limit is not None and (
            type(cost_limit) not in {int, float} or cost_limit < 0
            or type(cost) not in {int, float} or not math.isfinite(cost) or cost < 0 or cost > cost_limit
        ):
            errors.append(f"{name} cost is unknown or exceeds its budget")
        for key in ("cost_usd", "latency_ms"):
            value = usage.get(key)
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                errors.append(f"{name} {key} usage is invalid")

    validation = arm.get("validation")
    consolidation = arm.get("consolidation")
    scored = arm.get("assessment")
    if not isinstance(validation, dict) or validation.get("status") not in {"success", "no_findings"}:
        errors.append(f"{name} candidate validation is incomplete")
    if not isinstance(consolidation, dict) or not isinstance(scored, dict):
        errors.append(f"{name} scored candidate artifacts are missing")
        return errors
    candidates = consolidation.get("raw_candidates")
    clusters = consolidation.get("clusters")
    rows = scored.get("assessments")
    if not isinstance(candidates, list) or validation.get("candidates") != candidates:
        errors.append(f"{name} consolidation differs from validated candidates")
    if not isinstance(clusters, list) or not isinstance(rows, list) or len(clusters) != len(rows):
        errors.append(f"{name} judging coverage is incomplete")
    if scored.get("status") != "success":
        errors.append(f"{name} judging failed or is incomplete")
    coverage = scored.get("coverage")
    if not isinstance(coverage, dict) or not (
        coverage.get("fixture_assessment_complete") is True
        or coverage.get("assessment_complete") is True
    ):
        errors.append(f"{name} assessment coverage is incomplete")
    shared_configuration = arm.get("shared_configuration")
    judge_config = shared_configuration.get("judge", {}) if isinstance(shared_configuration, dict) else {}
    judge_config = judge_config if isinstance(judge_config, dict) else {}
    if scored.get("rubric_version") != judge_config.get("rubric_version"):
        errors.append(f"{name} assessment rubric differs from resolved judge configuration")
    if scored.get("prompt_version") != judge_config.get("prompt_version"):
        errors.append(f"{name} assessment prompt differs from resolved judge configuration")

    refs = arm.get("artifact_refs", {})
    sources = scored.get("source_artifacts", {})
    for source_name in ("candidate_validation", "snapshot", "consolidation", "frozen_report"):
        source_ref = sources.get(source_name, {})
        actual_ref = refs.get(source_name, {})
        if (
            not actual_ref.get("artifact_id")
            or not isinstance(actual_ref.get("sha256"), str)
            or source_ref.get("artifact_id") != actual_ref.get("artifact_id")
            or source_ref.get("sha256") != actual_ref.get("sha256")
        ):
            errors.append(f"{name} {source_name} lineage does not resolve")
    if not errors:
        try:
            assessment.build_isolated_judge_prompts(clusters, items)
            responses = [{key: value for key, value in row.items() if key != "cluster_id"} for row in rows]
            validated = assessment.validate_assessments(responses, clusters)
            if validated["status"] != "success" or validated["assessments"] != rows:
                errors.append(f"{name} assessment has missing or invalid evidence")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{name} assessment evidence is missing from the frozen snapshot")
    return errors


def _matches(serve, shadow):
    arms = {"v0": serve, "v1": shadow}
    scored = {
        name: {row["cluster_id"]: row for row in arm["assessment"]["assessments"]}
        for name, arm in arms.items()
    }
    keys = {
        name: {cluster["cluster_id"]: _match_key(cluster["candidate"]) for cluster in arm["consolidation"]["clusters"]}
        for name, arm in arms.items()
    }
    exact_pairs = [
        (serve_id, shadow_id)
        for serve_id, serve_key in keys["v0"].items()
        for shadow_id, shadow_key in keys["v1"].items()
        if serve_key == shadow_key
    ]
    uncertain = [
        {
            "v0_cluster_id": serve_id,
            "v1_cluster_id": shadow_id,
            "reason": "normalized title or friction matches but the full candidate key differs",
        }
        for serve_id, serve_key in keys["v0"].items()
        for shadow_id, shadow_key in keys["v1"].items()
        if serve_key != shadow_key and (
            (serve_key[0] and serve_key[0] == shadow_key[0])
            or (serve_key[1] and serve_key[1] == shadow_key[1])
        )
    ]
    exact_ids = {
        "v0": {serve_id for serve_id, _ in exact_pairs},
        "v1": {shadow_id for _, shadow_id in exact_pairs},
    }
    uncertain_ids = {
        "v0": {row["v0_cluster_id"] for row in uncertain},
        "v1": {row["v1_cluster_id"] for row in uncertain},
    }
    eligible_pairs = [
        (serve_id, shadow_id)
        for serve_id, shadow_id in exact_pairs
        if scored["v0"][serve_id]["verdict"] == scored["v1"][shadow_id]["verdict"] == "ELIGIBLE"
    ]
    eligible_exact_ids = {
        "v0": {serve_id for serve_id, _ in eligible_pairs},
        "v1": {shadow_id for _, shadow_id in eligible_pairs},
    }
    eligible_uncertain_ids = {"v0": set(), "v1": set()}
    for row in uncertain:
        serve_id, shadow_id = row["v0_cluster_id"], row["v1_cluster_id"]
        if scored["v0"][serve_id]["verdict"] == scored["v1"][shadow_id]["verdict"] == "ELIGIBLE":
            eligible_uncertain_ids["v0"].add(serve_id)
            eligible_uncertain_ids["v1"].add(shadow_id)
    return {
        "exact_overlap_count": len(exact_pairs),
        "unique_contribution": {
            name: sum(cluster_id not in exact_ids[name] and cluster_id not in uncertain_ids[name] for cluster_id in scored[name])
            for name in arms
        },
        "eligible_overlap_count": len(eligible_pairs),
        "eligible_unique_contribution": {
            name: sum(
                row["verdict"] == "ELIGIBLE"
                and cluster_id not in eligible_exact_ids[name]
                and cluster_id not in eligible_uncertain_ids[name]
                for cluster_id, row in scored[name].items()
            )
            for name in arms
        },
        "assessment_disagreement_count": sum(
            scored["v0"][serve_id]["verdict"] != scored["v1"][shadow_id]["verdict"]
            for serve_id, shadow_id in exact_pairs
        ),
        "uncertain_matches": uncertain,
        "uncertain_eligible_count": {
            name: sum(scored[name][cluster_id]["verdict"] == "ELIGIBLE" for cluster_id in cluster_ids)
            for name, cluster_ids in uncertain_ids.items()
        },
        "policy": "exact whitespace-normalized, case-folded title and friction only, after both arms are assessed",
    }


def _arm_metrics(arm, common_input_count):
    rows = arm["assessment"]["assessments"]
    counts = {verdict: sum(row["verdict"] == verdict for row in rows) for verdict in assessment.VERDICTS}
    dimensions = {
        dimension: {
            status: sum(row["dimensions"][dimension]["status"] == status for row in rows)
            for status in assessment.DIMENSION_STATUSES
        }
        for dimension in assessment.DIMENSIONS
    }
    eligible = counts["ELIGIBLE"]
    emitted = len(arm["consolidation"]["clusters"])
    resources = arm["resource_usage"]
    return {
        "raw_emitted_count": len(arm["consolidation"]["raw_candidates"]),
        "consolidated_emitted_count": emitted,
        "assessed_count": len(rows),
        "eligible_count": eligible,
        "common_input_yield": {"numerator": eligible, "denominator": common_input_count, "rate": eligible / common_input_count},
        "emitted_precision": {
            "numerator": eligible,
            "denominator": emitted,
            "rate": eligible / emitted if emitted else None,
            "denominator_kind": "consolidated emitted candidates with completed assessments",
        },
        "verdict_counts": counts,
        "grounded_assessment_count": sum(bool(row["evidence_ids"]) for row in rows),
        "unknown_dimension_count": sum(values["unknown"] for values in dimensions.values()),
        "dimension_status_counts": dimensions,
        "abstention_count": counts["NEEDS_EVIDENCE"],
        "resources": {
            "requests": resources["requests"],
            "tokens": resources["tokens"],
            "cost_usd": resources.get("cost_usd"),
            "latency_ms": resources.get("latency_ms"),
            "cost_per_eligible_usd": "n/a" if eligible == 0 else resources.get("cost_usd") / eligible if resources.get("cost_usd") is not None else None,
            "cost_note": "unknown is not treated as zero" if resources.get("cost_usd") is None else None,
        },
    }


def compare_assessed_arms(serve, shadow):
    reasons = _validate_arm(serve, "serve") + _validate_arm(shadow, "shadow")
    if serve.get("snapshot_items") != shadow.get("snapshot_items"):
        reasons.append("ordered frozen snapshots differ")
    if serve.get("processed_item_ids") != shadow.get("processed_item_ids"):
        reasons.append("common processed input sets differ")
    if serve.get("shared_configuration") != shadow.get("shared_configuration"):
        reasons.append("resolved shared extraction or judge configuration differs")
    if serve.get("budget_limits") != shadow.get("budget_limits"):
        reasons.append("budget ceilings differ between arms")
    if reasons:
        return {
            "status": "inconclusive",
            "reasons": list(dict.fromkeys(reasons)),
            "matching": None,
            "arms": None,
            "qualification": "not comparable; not market truth or global recall",
            "promotion_recommendation": "none",
        }
    common_input_count = len(serve["processed_item_ids"])
    return {
        "status": "descriptive",
        "common_input_count": common_input_count,
        "arms": {
            "v0": _arm_metrics(serve, common_input_count),
            "v1": _arm_metrics(shadow, common_input_count),
        },
        "matching": _matches(serve, shadow),
        "qualification": "judge-qualified descriptive comparison; not market truth or global recall",
        "promotion_recommendation": "none; one run does not justify promotion",
    }


def render_markdown(result):
    lines = [
        "# Assessed extraction-arm comparison", "", f"Status: **{result['status']}**", "",
        result["qualification"], "",
        "This is a judge-qualified description, not market truth or global recall. One run does not justify promotion.", "",
    ]
    source_coverage = result.get("source_coverage")
    if isinstance(source_coverage, dict):
        lines.extend(["## Source coverage", ""])
        for source, source_result in source_coverage.get("sources", {}).items():
            omission_note = ", additional omissions unknown" if source_result["omissions_unknown"] else ""
            lines.append(
                f"- {source}: {source_result['status']}; synthetic inputs {source_result['input_count']}, "
                f"normalized returns {source_result['returned_count']}, omissions {source_result['omitted_count']}, "
                f"failures {source_result['failure_count']}{omission_note}."
            )
        lines.extend([
            "",
            "Synthetic fixture coverage only; live source completeness is unverified.",
            f"Cross-source duplicate groups consolidated as one independent item each: "
            f"{len(result.get('cross_source_duplicate_groups', []))}.",
            "",
        ])
    if result["status"] == "inconclusive":
        lines.extend(["## Inconclusive reasons", "", *(f"- {reason}" for reason in result["reasons"])])
        return "\n".join(lines) + "\n"
    lines.extend([
        f"Common processed input denominator: {result['common_input_count']}.", "",
        "| Arm | Raw emitted | Assessed | Eligible | Common-input yield | Emitted precision | Grounded | Abstentions | Unknown dimensions |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for key, label in (("v0", "Serve"), ("v1", "Shadow")):
        arm = result["arms"][key]
        precision = arm["emitted_precision"]["rate"]
        lines.append(
            f"| {label} | {arm['raw_emitted_count']} | {arm['assessed_count']} | {arm['eligible_count']} "
            f"| {arm['common_input_yield']['rate']:.4f} | {precision if precision is not None else 'n/a'} "
            f"| {arm['grounded_assessment_count']}/{arm['assessed_count']} | {arm['abstention_count']} | {arm['unknown_dimension_count']} |"
        )
    match = result["matching"]
    lines.extend([
        "", "## Matching", "", match["policy"],
        f"Exact overlap: {match['exact_overlap_count']}; eligible overlap: {match['eligible_overlap_count']}.",
        f"Unique contribution: serve {match['unique_contribution']['v0']}, shadow {match['unique_contribution']['v1']}; "
        f"eligible unique contribution: serve {match['eligible_unique_contribution']['v0']}, "
        f"shadow {match['eligible_unique_contribution']['v1']}.",
        f"Uncertain potential matches: {len(match['uncertain_matches'])}.", "", "## Resources", "",
    ])
    for key, label in (("v0", "Serve"), ("v1", "Shadow")):
        resource = result["arms"][key]["resources"]
        cost_per_eligible = resource["cost_per_eligible_usd"]
        lines.append(
            f"- {label} extraction fixture usage: {resource['requests']} requests, "
            f"{resource['tokens']} tokens; judge-provider usage is not included because the judge is a pre-assessed fixture. "
            f"cost {resource['cost_usd'] if resource['cost_usd'] is not None else 'unknown'}, "
            f"latency {resource['latency_ms'] if resource['latency_ms'] is not None else 'unknown'} ms, "
            f"cost per eligible {cost_per_eligible if cost_per_eligible is not None else 'unknown'}."
        )
    if match["uncertain_matches"]:
        lines.extend(["", "## Uncertain matches", ""])
        lines.extend(
            f"- `{row['v0_cluster_id']}` ↔ `{row['v1_cluster_id']}`: {row['reason']}."
            for row in match["uncertain_matches"]
        )
    return "\n".join(lines) + "\n"
