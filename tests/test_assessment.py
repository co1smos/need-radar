import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from need_radar import assessment


ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path.home() / ".hermes" / "cache" / "scratch"
EVIDENCE_ID = "reddit:synthetic-1"


def candidate(text="A builder repeatedly copies agent context after resets."):
    return {
        "title": "Agent context handoff is manual",
        "friction": "A builder repeatedly copies agent context after resets.",
        "evidence": [{"item_id": EVIDENCE_ID, "excerpt": text}],
    }


def response(verdict="ELIGIBLE", statuses=None, uncertainties=None):
    statuses = statuses or {name: "supported" for name in assessment.DIMENSIONS}
    return {
        "verdict": verdict,
        "reason": "The cited report supports the stated assessment.",
        "evidence_ids": [EVIDENCE_ID],
        "dimensions": {
            name: {
                "status": statuses[name],
                "reason": f"Synthetic reason for {label}.",
                "evidence_ids": [EVIDENCE_ID],
            }
            for name, label in assessment.DIMENSIONS.items()
        },
        "uncertainties": uncertainties or [],
    }


class AssessmentTests(unittest.TestCase):
    def validate(self, judge_response):
        return assessment.validate_assessment(judge_response, {EVIDENCE_ID})

    def assert_candidate_evidence(self, text, title=None, friction=None):
        item = {"id": EVIDENCE_ID, "source": "reddit", "text": text}
        candidate_data = {
            **candidate(text),
            "title": title or "Synthetic evidence-grounded friction",
            "friction": friction or "Synthetic source describes a workflow obstacle.",
        }
        prompt = assessment.build_judge_prompt([candidate_data], [item])
        self.assertIn(text, prompt["messages"][1]["content"])
        self.assertEqual(prompt["config"]["evidence_policy_version"], "cited-frozen-items-v1")

    def test_grounded_explicit_friction_is_eligible(self):
        self.assert_candidate_evidence(
            "I spend 45 minutes rebuilding project context after each coding-agent reset.",
            "Repeated agent setup blocks builder work",
            "A developer repeatedly spends 45 minutes rebuilding context after agent resets.",
        )
        result, errors = self.validate(response())
        self.assertEqual(errors, [])
        self.assertEqual(result["verdict"], "ELIGIBLE")
        self.assertEqual(set(result["dimensions"]), set(assessment.DIMENSIONS))

    def test_substantial_narrow_burden_can_qualify_with_unknown_prevalence(self):
        self.assert_candidate_evidence(
            "After a session reset, I spend 45 minutes rebuilding context and miss my morning deploy window.",
            "Repeated context recovery misses a deployment window",
            "One developer loses 45 minutes and misses a deployment window after a reset.",
        )
        result = response(uncertainties=["Population prevalence is unknown."])
        result["dimensions"]["evidence_size"]["reason"] = (
            "One report documents repeated blocking loss of work; prevalence is not established."
        )
        assessed, errors = self.validate(result)
        self.assertEqual(errors, [])
        self.assertEqual(assessed["verdict"], "ELIGIBLE")
        self.assertIn("Population prevalence is unknown.", assessed["uncertainties"])

    def test_benign_manual_workflow_is_not_eligible_without_burden(self):
        self.assert_candidate_evidence(
            "I copy one short command into my notes; it takes a few seconds and causes no problems.",
            "A short command is copied manually",
            "A benign, quick manual step without stated consequence.",
        )
        statuses = {name: "supported" for name in assessment.DIMENSIONS}
        statuses["pain_value"] = "weak-or-unsupported"
        statuses["evidence_size"] = "weak-or-unsupported"
        assessed, errors = self.validate(response(
            "INELIGIBLE", statuses, ["No consequential burden is described."],
        ))
        self.assertEqual(errors, [])
        self.assertEqual(assessed["verdict"], "INELIGIBLE")
        self.assertIn("Manual steps alone do not establish meaningful pain", assessment.JUDGE_INSTRUCTIONS)

    def test_model_internal_friction_is_out_of_scope(self):
        self.assert_candidate_evidence(
            "My GPU kernel is slower after changing the model's low-level attention implementation.",
            "Low-level attention kernel performance",
            "Model-internal GPU inference optimization is the obstacle.",
        )
        statuses = {name: "supported" for name in assessment.DIMENSIONS}
        statuses["scope_fit"] = "weak-or-unsupported"
        assessed, errors = self.validate(response(
            "INELIGIBLE", statuses, ["The friction concerns model-internal optimization."],
        ))
        self.assertEqual(errors, [])
        self.assertEqual(assessed["verdict"], "INELIGIBLE")
        self.assertIn("Low-level training", assessment.JUDGE_INSTRUCTIONS)

    def test_unsupported_prevalence_stays_unknown_and_needs_evidence(self):
        self.assert_candidate_evidence(
            "I lose most of an hour rebuilding the same agent context after every reset.",
            "Session resets cause substantial repeated context recovery",
            "One report describes repeated recovery effort but says nothing about population size.",
        )
        statuses = {name: "supported" for name in assessment.DIMENSIONS}
        statuses["evidence_size"] = "unknown"
        assessed, errors = self.validate(response(
            "NEEDS_EVIDENCE", statuses, ["Population prevalence is unknown; no broad claim is made."],
        ))
        self.assertEqual(errors, [])
        self.assertEqual(assessed["dimensions"]["evidence_size"]["status"], "unknown")

    def test_broken_grounding_is_rejected(self):
        result = response()
        result["dimensions"]["pain_value"]["evidence_ids"] = ["x:borrowed-evidence"]
        assessed, errors = self.validate(result)
        self.assertIsNone(assessed)
        self.assertTrue(any("outside the frozen candidate context" in error for error in errors))

    def test_consolidation_is_within_arm_and_retains_raw_membership(self):
        first = candidate()
        duplicate = {
            **candidate(),
            "title": "  AGENT CONTEXT HANDOFF IS MANUAL ",
            "friction": "A builder repeatedly copies agent context after resets.",
            "evidence": [{"item_id": "x:synthetic-2", "excerpt": "Synthetic second report."}],
        }
        serve = assessment.consolidate_within_arm("serve", [first, duplicate])
        shadow = assessment.consolidate_within_arm("shadow", [first])
        self.assertEqual(len(serve["clusters"]), 1)
        self.assertEqual(serve["clusters"][0]["member_indices"], [0, 1])
        self.assertEqual(len(serve["clusters"][0]["candidate"]["evidence"]), 2)
        self.assertEqual(serve["raw_candidates"], [first, duplicate])
        self.assertEqual(shadow["arm"], "shadow")
        self.assertEqual(len(shadow["clusters"]), 1)
        self.assertEqual(serve["clusters"][0]["member_indices"], [0, 1])

    def test_judge_prompt_contains_only_allowlisted_candidate_and_cited_frozen_evidence(self):
        item = {
            "id": EVIDENCE_ID,
            "source": "reddit",
            "text": "A builder repeatedly copies agent context after resets.",
        }
        tainted_candidate = {
            **candidate(),
            "arm": "shadow",
            "strategy": "latent-friction",
            "extractor_prompt": "extractor-only secret policy",
            "extractor_confidence": 0.99,
            "cost": 100,
        }
        prompt = assessment.build_judge_prompt(
            [tainted_candidate],
            [item, {"id": "reddit:unused", "source": "reddit", "text": "Not cited."}],
        )
        judge_input = json.dumps(prompt["messages"], sort_keys=True)
        for hidden in ("shadow", "latent-friction", "extractor-only secret policy", "0.99", "100", "Not cited"):
            self.assertNotIn(hidden, judge_input)
        self.assertIn("A builder repeatedly copies agent context after resets.", judge_input)
        self.assertEqual(prompt["config"]["rubric_version"], assessment.RUBRIC_VERSION)
        self.assertEqual(prompt["config"]["invocation_settings"], "not resolved; no provider was invoked")

    def test_both_arms_share_rubric_without_cross_candidate_evidence(self):
        serve_item = {
            "id": EVIDENCE_ID,
            "source": "reddit",
            "text": "Serve arm synthetic evidence only.",
        }
        shadow_item = {
            "id": "x:synthetic-2",
            "source": "x",
            "text": "Shadow arm synthetic evidence only.",
        }
        serve_candidate = candidate(serve_item["text"])
        shadow_candidate = {
            **candidate(shadow_item["text"]),
            "evidence": [{"item_id": shadow_item["id"], "excerpt": shadow_item["text"]}],
        }
        serve_cluster = assessment.consolidate_within_arm("serve", [serve_candidate])["clusters"]
        shadow_cluster = assessment.consolidate_within_arm("shadow", [shadow_candidate])["clusters"]
        serve_prompt = assessment.build_isolated_judge_prompts(serve_cluster, [serve_item])[0]["prompt"]
        shadow_prompt = assessment.build_isolated_judge_prompts(shadow_cluster, [shadow_item])[0]["prompt"]
        self.assertEqual(serve_prompt["config"]["rubric_version"], shadow_prompt["config"]["rubric_version"])
        self.assertEqual(serve_prompt["messages"][0], shadow_prompt["messages"][0])
        self.assertIn("Serve arm synthetic", serve_prompt["messages"][1]["content"])
        self.assertNotIn("Shadow arm synthetic", serve_prompt["messages"][1]["content"])
        self.assertIn("Shadow arm synthetic", shadow_prompt["messages"][1]["content"])
        self.assertNotIn("Serve arm synthetic", shadow_prompt["messages"][1]["content"])

    def test_runnable_end_to_end_check_uses_hermes_tmpdir(self):
        if not SCRATCH.is_dir():
            self.skipTest("Hermes scratch directory is unavailable")
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
            output = Path(temporary) / "assessment-check"
            environment = {
                "TMPDIR": str(SCRATCH),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
            }
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "check_assessment.py"), "--output", str(output)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["evidence_kind"], "synthetic_offline")
        self.assertEqual(summary["verdict"], "NEEDS_EVIDENCE")
        self.assertEqual(summary["report_frozen"], "verified")
        self.assertEqual(summary["network_credential_live_state_guards"], "denied_in_process_and_subprocess")


if __name__ == "__main__":
    unittest.main()
