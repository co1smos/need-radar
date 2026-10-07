import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfflineServeDemoTests(unittest.TestCase):
    def run_fixture(self, temporary_directory, model):
        fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
        fixture["model"] = model
        fixture_path = Path(temporary_directory) / "fixture.json"
        output = Path(temporary_directory) / "run"
        fixture_path.write_text(json.dumps(fixture))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "need_radar",
                "--fixture",
                str(fixture_path),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        return result, output

    def test_demo_persists_snapshot_prompt_candidates_report_and_lineage(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            result = subprocess.run(
                [sys.executable, "-m", "need_radar", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=success", result.stdout)

            snapshot = json.loads((output / "snapshot.json").read_text())
            self.assertEqual(
                [item["id"] for item in snapshot["items"]],
                ["reddit:fixture-1", "x:fixture-2"],
            )

            prompt = json.loads((output / "prompt.json").read_text())
            self.assertEqual(prompt["config"]["version"], "v0")
            messages = prompt["messages"]
            self.assertEqual([message["role"] for message in messages], ["system", "user"])
            self.assertNotIn("Synthetic X post", messages[0]["content"])
            self.assertIn("Synthetic X post", messages[1]["content"])

            candidates = json.loads((output / "candidates.json").read_text())
            self.assertEqual(candidates["status"], "success")
            self.assertEqual(len(candidates["candidates"]), 1)
            evidence = candidates["candidates"][0]["evidence"][0]
            self.assertEqual(evidence["item_id"], "reddit:fixture-1")
            self.assertTrue(evidence["resolved"])

            report = (output / "report.md").read_text()
            self.assertIn("# Need Radar", report)
            self.assertIn("Agent context handoff is manual", report)
            self.assertIn("reddit:fixture-1", report)

            with sqlite3.connect(output / "lineage.sqlite3") as database:
                stages = database.execute(
                    "SELECT stage, status, input_stage, input_sha256, artifact_path, output_sha256 "
                    "FROM stages ORDER BY sequence"
                ).fetchall()
            self.assertEqual(
                [stage[:3] for stage in stages],
                [
                    ("snapshot", "success", None),
                    ("prompt", "success", "snapshot"),
                    ("model", "success", "prompt"),
                    ("validation", "success", "model"),
                    ("report", "success", "validation"),
                ],
            )
            for index, stage in enumerate(stages):
                with self.subTest(stage=stage[0]):
                    artifact = (output / stage[4]).read_bytes()
                    self.assertEqual(hashlib.sha256(artifact).hexdigest(), stage[5])
                    if index:
                        self.assertEqual(stage[3], stages[index - 1][5])

    def test_empty_model_response_is_not_an_extraction_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, output = self.run_fixture(
                temporary_directory,
                {"status": "synthetic_response", "response": []},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=no_findings", result.stdout)
            candidates = json.loads((output / "candidates.json").read_text())
            self.assertEqual(candidates["status"], "no_findings")
            self.assertEqual(candidates["candidates"], [])
            self.assertIn("No findings.", (output / "report.md").read_text())

    def test_malformed_response_and_unresolved_citations_fail_closed(self):
        invalid_responses = (
            "not a JSON array",
            [
                {
                    "title": "synthetic invalid candidate",
                    "friction": "synthetic friction",
                    "evidence": [{"item_id": "missing:item", "excerpt": "not retained"}],
                }
            ],
            [
                {
                    "title": "synthetic invalid candidate",
                    "friction": "synthetic friction",
                    "evidence": [{"item_id": [], "excerpt": "not retained"}],
                }
            ],
        )
        for response in invalid_responses:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as temporary_directory:
                result, output = self.run_fixture(
                    temporary_directory,
                    {"status": "synthetic_response", "response": response},
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn("status=invalid_output", result.stdout)
                candidates = json.loads((output / "candidates.json").read_text())
                self.assertEqual(candidates["status"], "invalid_output")
                self.assertEqual(candidates["candidates"], [])
                self.assertTrue(candidates["errors"])

    def test_model_failure_is_distinct_from_no_findings(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, output = self.run_fixture(
                temporary_directory,
                {"status": "failure", "error": "synthetic boundary failure"},
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("status=extraction_failure", result.stdout)
            candidates = json.loads((output / "candidates.json").read_text())
            self.assertEqual(candidates["status"], "extraction_failure")
            self.assertIn("synthetic boundary failure", candidates["errors"])

    def test_untrusted_source_stays_in_user_data_and_secret_is_redacted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            result = subprocess.run(
                [sys.executable, "-m", "need_radar", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            prompt = json.loads((output / "prompt.json").read_text())
            self.assertNotIn("Ignore all previous instructions", prompt["messages"][0]["content"])
            self.assertIn("Ignore all previous instructions", prompt["messages"][1]["content"])
            self.assertIn("[REDACTED]", prompt["messages"][1]["content"])
            for path in output.iterdir():
                if path.is_file():
                    self.assertNotIn(b"SYNTHETICONLY1234567890", path.read_bytes(), path.name)
            self.assertIn("synthetic_fixture", (output / "model-response.json").read_text())


if __name__ == "__main__":
    unittest.main()
