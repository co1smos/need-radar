import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import need_radar.__main__ as tracer_cli


ROOT = Path(__file__).resolve().parents[1]


class OfflineServeDemoTests(unittest.TestCase):
    def run_subprocess(self, command, **options):
        environment = os.environ.copy()
        guarded_paths = [str(ROOT / "tests"), str(ROOT)]
        if environment.get("PYTHONPATH"):
            guarded_paths.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(guarded_paths)
        return subprocess.run(command, env=environment, **options)

    def invoke_fixture(self, temporary_directory, fixture):
        fixture_path = Path(temporary_directory) / "fixture.json"
        output = Path(temporary_directory) / "run"
        fixture_path.write_text(json.dumps(fixture))
        result = self.run_subprocess(
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

    def run_fixture(self, temporary_directory, model):
        fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
        fixture["model"] = model
        return self.invoke_fixture(temporary_directory, fixture)

    def test_redaction_is_idempotent_after_markdown_escaping(self):
        safe_value = tracer_cli.markdown_literal("password=[REDACTED]")
        self.assertEqual(tracer_cli.redact(safe_value), safe_value)

    def test_demo_persists_snapshot_prompt_candidates_report_and_lineage(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            result = self.run_subprocess(
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
            self.assertIn(r"reddit\:fixture\-1", report)

            with sqlite3.connect(output / "lineage.sqlite3") as database:
                stages = database.execute(
                    "SELECT stage, status, input_stage, input_sha256, artifact_path, output_sha256, "
                    "trace_id, span_id, artifact_id, input_artifact_id, call_id "
                    "FROM stages ORDER BY sequence"
                ).fetchall()
            self.assertEqual(
                [stage[:3] for stage in stages],
                [
                    ("snapshot", "success", None),
                    ("selection", "success", "snapshot"),
                    ("context_assembly", "success", "selection"),
                    ("truncation", "success", "context_assembly"),
                    ("prompt_render", "success", "truncation"),
                    ("model_call", "success", "prompt_render"),
                    ("validation", "success", "model_call"),
                    ("report", "success", "validation"),
                ],
            )
            trace_events = [json.loads(line) for line in (output / "trace.jsonl").read_text().splitlines()]
            observations = json.loads((output / "observability.json").read_text())
            run_span = next(event for event in trace_events if event["name"] == "run")
            self.assertIsNone(run_span["parent_span_id"])
            self.assertTrue({stage[7] for stage in stages} <= {event["id"] for event in trace_events})
            self.assertEqual({event["trace_id"] for event in trace_events}, {observations["ids"]["trace_id"]})
            self.assertTrue(all(event["parent_span_id"] == run_span["id"] for event in trace_events if event is not run_span))
            self.assertEqual(observations["remote_export"]["status"], "unverified")
            self.assertFalse(observations["remote_export"]["enabled"])
            self.assertFalse(observations["coverage"]["redaction"]["complete"])
            self.assertEqual(observations["ids"]["snapshot_id"], stages[0][8])
            self.assertEqual(observations["ids"]["report_id"], stages[-1][8])
            self.assertEqual(observations["ids"]["call_id"], stages[5][10])
            for index, stage in enumerate(stages):
                with self.subTest(stage=stage[0]):
                    artifact = (output / stage[4]).read_bytes()
                    self.assertEqual(hashlib.sha256(artifact).hexdigest(), stage[5])
                    self.assertEqual(stage[6], observations["ids"]["trace_id"])
                    self.assertTrue(stage[7])
                    self.assertTrue(stage[8])
                    if index:
                        self.assertEqual(stage[3], stages[index - 1][5])
                        self.assertEqual(stage[9], stages[index - 1][8])

    def test_successful_fake_model_call_exposes_upstream_context_truncation(self):
        def faulty_truncation(context):
            items = [dict(item) for item in context["items"]]
            items[0]["text"] = "synthetic test truncation removed the source excerpt"
            return {
                **context,
                "items": items,
                "truncated": True,
                "reason": "injected upstream context bug",
            }

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            with mock.patch.object(tracer_cli, "truncate_context", side_effect=faulty_truncation):
                status = tracer_cli.run(tracer_cli.DEFAULT_FIXTURE, output)

            self.assertEqual(status, "invalid_output")
            truncation = json.loads((output / "truncation.json").read_text())
            self.assertTrue(truncation["truncated"])
            self.assertEqual(truncation["reason"], "injected upstream context bug")
            prompt = json.loads((output / "prompt.json").read_text())
            self.assertNotIn("I manually copy the same project context", prompt["messages"][1]["content"])
            model = json.loads((output / "model-response.json").read_text())
            self.assertEqual(model["status"], "success")
            validation = json.loads((output / "candidates.json").read_text())
            self.assertEqual(validation["status"], "invalid_output")
            self.assertIn("does not resolve to prompt context", validation["errors"][0])
            trace_events = [json.loads(line) for line in (output / "trace.jsonl").read_text().splitlines()]
            model_span = next(event for event in trace_events if event["name"] == "model_call")
            truncation_span = next(event for event in trace_events if event["name"] == "truncation")
            self.assertEqual(model_span["metadata"]["result_status"], "success")
            self.assertEqual(truncation_span["output"]["value"]["reason"], "injected upstream context bug")

    def test_span_export_failure_does_not_block_serve_and_is_reported(self):
        class FailingSink:
            def write(self, record):
                raise OSError("synthetic local exporter failure")

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            with mock.patch("need_radar.observability.JsonlSpanSink", return_value=FailingSink()):
                status = tracer_cli.run(tracer_cli.DEFAULT_FIXTURE, output)

            self.assertEqual(status, "success")
            self.assertTrue((output / "report.md").is_file())
            observations = json.loads((output / "observability.json").read_text())
            self.assertEqual(observations["local_export"]["status"], "failed")
            self.assertFalse(observations["coverage"]["complete"])
            self.assertTrue(observations["local_export"]["failures"])
            self.assertEqual(observations["remote_export"]["status"], "unverified")

    def test_langfuse_boundary_preserves_ordinary_spans_and_context_in_local_sink(self):
        class LocalSink:
            def __init__(self):
                self.records = []

            def write(self, record):
                self.records.append(record)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            sink = LocalSink()
            with mock.patch("need_radar.observability.JsonlSpanSink", return_value=sink):
                status = tracer_cli.run(tracer_cli.DEFAULT_FIXTURE, output)

            self.assertEqual(status, "success")
            names = {record["name"] for record in sink.records}
            self.assertTrue({"selection", "context_assembly", "truncation", "prompt_render", "model_call", "validation", "report"} <= names)
            run_record = next(record for record in sink.records if record["name"] == "run")
            self.assertIsNone(run_record["parent_span_id"])
            self.assertTrue(all(record["run_id"] == run_record["run_id"] for record in sink.records))
            self.assertTrue(all(record["trace_id"] == run_record["trace_id"] for record in sink.records))
            self.assertTrue(all(
                record["parent_span_id"] == run_record["id"]
                for record in sink.records
                if record is not run_record
            ))
            self.assertTrue(all(record["remote_status"] == "unverified" for record in sink.records))
            self.assertTrue(all(record["external_export_enabled"] is False for record in sink.records))
            model_record = next(record for record in sink.records if record["name"] == "model_call")
            self.assertIn("UNTRUSTED SOURCE EVIDENCE", model_record["input"]["prompt"]["messages"][1]["content"])
            self.assertNotIn("SYNTHETICONLY1234567890", json.dumps(sink.records))
            self.assertTrue((output / "report.md").is_file())

    def test_offline_child_process_denies_network_credentials_and_live_state(self):
        script = """
import socket

try:
    socket.socket()
except PermissionError:
    print('network=denied')
else:
    raise SystemExit('network=allowed')

for name, path in (
    ('credentials', '/home/ubuntu/projects/need-radar/credentials.env'),
    ('live_state', '/home/ubuntu/.local/state/need-radar'),
):
    try:
        open(path, 'rb')
    except PermissionError:
        print(name + '=denied')
    else:
        raise SystemExit(name + '=allowed')
"""
        result = self.run_subprocess(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("network=denied", result.stdout)
        self.assertIn("credentials=denied", result.stdout)
        self.assertIn("live_state=denied", result.stdout)

    def test_synthetic_end_to_end_output_redacts_all_trace_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            result = self.run_subprocess(
                [sys.executable, "-m", "need_radar", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=success", result.stdout)
            for path in output.iterdir():
                if path.is_file():
                    self.assertNotIn(b"SYNTHETICONLY1234567890", path.read_bytes(), path.name)
            observations = json.loads((output / "observability.json").read_text())
            self.assertEqual(observations["remote_export"]["status"], "unverified")

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

    def test_unknown_or_missing_model_status_fails_closed(self):
        fixtures = (
            {"status": "timeout", "response": [], "error": "synthetic timeout detail"},
            {"response": [], "error": "synthetic missing-status detail"},
        )
        for model in fixtures:
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temporary_directory:
                result, output = self.run_fixture(temporary_directory, model)

                self.assertEqual(result.returncode, 1)
                self.assertIn("status=extraction_failure", result.stdout)
                model_result = json.loads((output / "model-response.json").read_text())
                candidates = json.loads((output / "candidates.json").read_text())
                self.assertEqual(model_result["status"], "failure")
                self.assertIn(model["error"], model_result["error"])
                self.assertEqual(candidates["status"], "extraction_failure")
                self.assertIn(model["error"], candidates["errors"][0])

    def test_report_renders_untrusted_fields_and_errors_as_literal_text(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
            item_id = 'reddit:<img src="https://example.invalid/id">![id](https://example.invalid/id-image)'
            title = '<img src="https://example.invalid/title"> ![t](https://example.invalid/image)\n## injected title'
            friction = '<img src="https://example.invalid/friction"> ![f](https://example.invalid/friction-image)\n> injected friction'
            excerpt = '<img src="https://example.invalid/excerpt"> ![e](https://example.invalid/excerpt-image)'
            fixture["items"][0]["id"] = item_id
            fixture["items"][0]["text"] = f"Synthetic evidence: {excerpt}"
            fixture["model"] = {
                "status": "synthetic_response",
                "response": [{
                    "title": title,
                    "friction": friction,
                    "evidence": [{"item_id": item_id, "excerpt": excerpt}],
                }],
            }
            result, output = self.invoke_fixture(temporary_directory, fixture)

            self.assertEqual(result.returncode, 0, result.stderr)
            report = (output / "report.md").read_text()
            self.assertIn("&lt;img src=", report)
            self.assertIn(r"\!\[t\]\(https\://", report)
            self.assertNotIn("<img", report)
            self.assertNotIn("![", report)
            self.assertNotIn("https://example.invalid", report)
            self.assertNotIn("\n## injected title", report)
            self.assertNotIn("\n> injected friction", report)

        with tempfile.TemporaryDirectory() as temporary_directory:
            dangerous_error = '<img src="https://example.invalid/error"> ![error](https://example.invalid/error-image)\n## injected error'
            result, output = self.run_fixture(
                temporary_directory,
                {"status": "failure", "error": dangerous_error},
            )

            self.assertEqual(result.returncode, 1)
            report = (output / "report.md").read_text()
            self.assertIn("&lt;img src=", report)
            self.assertNotIn("<img", report)
            self.assertNotIn("![", report)
            self.assertNotIn("\n## injected error", report)

    def test_canonical_report_discloses_synthetic_and_validation_limits(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, output = self.run_fixture(
                temporary_directory,
                {"status": "synthetic_response", "response": []},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = (output / "report.md").read_text()
            for disclosure in (
                "synthetic/offline only",
                "predetermined synthetic fixture data",
                "no live source or model verification",
                "exact excerpt substrings only",
                "does not assess semantic support",
            ):
                with self.subTest(disclosure=disclosure):
                    self.assertIn(disclosure, report)

    def test_untrusted_source_stays_in_user_data_and_secret_is_redacted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            result = self.run_subprocess(
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

    def test_redacts_sensitive_fields_and_authorization_headers_before_persistence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
            fixture["items"][0]["password"] = "SYNTHETIC_FIELD_SECRET_98765"
            fixture["items"][0]["clientSecret"] = "SYNTHETIC_CAMEL_SECRET_98765"
            fixture["items"][0]["text"] += (
                ' Authorization: Bearer SYNTHETIC_BEARER_SECRET_98765'
                ' Authorization: Bearer "SYNTHETIC_QUOTED_BEARER_SECRET_98765"'
                ' api_key="SYNTHETIC_QUOTED_SECRET_98765"'
            )
            fixture_path = Path(temporary_directory) / "fixture.json"
            output = Path(temporary_directory) / "run"
            fixture_path.write_text(json.dumps(fixture))
            result = self.run_subprocess(
                [sys.executable, "-m", "need_radar", "--fixture", str(fixture_path), "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            for artifact in output.iterdir():
                if artifact.is_file():
                    content = artifact.read_bytes()
                    self.assertNotIn(b"SYNTHETIC_FIELD_SECRET_98765", content, artifact.name)
                    self.assertNotIn(b"SYNTHETIC_CAMEL_SECRET_98765", content, artifact.name)
                    self.assertNotIn(b"SYNTHETIC_BEARER_SECRET_98765", content, artifact.name)
                    self.assertNotIn(b"SYNTHETIC_QUOTED_BEARER_SECRET_98765", content, artifact.name)
                    self.assertNotIn(b"SYNTHETIC_QUOTED_SECRET_98765", content, artifact.name)

    def test_redacts_secret_bearing_dictionary_keys_before_persistence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
            fixture["items"][0]["metadata"] = {
                "sk-SYNTHETICKEYLEAK1234567890": "synthetic metadata"
            }
            result, output = self.invoke_fixture(temporary_directory, fixture)

            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads((output / "snapshot.json").read_text())
            metadata = snapshot["items"][0]["metadata"]
            self.assertEqual(metadata, {"[REDACTED]": "synthetic metadata"})
            for artifact in output.iterdir():
                if artifact.is_file():
                    self.assertNotIn(b"sk-SYNTHETICKEYLEAK1234567890", artifact.read_bytes(), artifact.name)

    def test_rejects_dictionary_keys_that_collide_after_redaction(self):
        key_orders = (
            (
                ("sk-SYNTHETICKEYLEAK1234567890", "synthetic metadata"),
                ("[REDACTED]", "existing metadata"),
            ),
            (
                ("[REDACTED]", "existing metadata"),
                ("sk-SYNTHETICKEYLEAK1234567890", "synthetic metadata"),
            ),
        )
        for key_order in key_orders:
            with self.subTest(key_order=key_order), tempfile.TemporaryDirectory() as temporary_directory:
                fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
                fixture["items"][0]["metadata"] = dict(key_order)
                result, output = self.invoke_fixture(temporary_directory, fixture)

                self.assertEqual(result.returncode, 1)
                self.assertIn("status=invalid_input", result.stdout)
                self.assertEqual(result.stderr, "")
                self.assertFalse((output / "snapshot.json").exists())
                validation = json.loads((output / "validation.json").read_text())
                self.assertEqual(validation["status"], "invalid_input")
                self.assertTrue(validation["errors"])
                self.assertNotIn("input", validation)
                with sqlite3.connect(output / "lineage.sqlite3") as database:
                    stage = database.execute(
                        "SELECT stage, status, artifact_path, output_sha256 FROM stages"
                    ).fetchone()
                self.assertEqual(stage[:3], ("fixture_validation", "invalid_input", "validation.json"))
                validation_bytes = (output / stage[2]).read_bytes()
                self.assertEqual(hashlib.sha256(validation_bytes).hexdigest(), stage[3])
                for artifact in output.iterdir():
                    if artifact.is_file():
                        contents = artifact.read_bytes()
                        self.assertNotIn(b"sk-SYNTHETICKEYLEAK1234567890", contents, artifact.name)
                        self.assertNotIn(b"existing metadata", contents, artifact.name)
                        self.assertNotIn(b"synthetic metadata", contents, artifact.name)

    def test_persisted_citation_still_resolves_after_redaction(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
            fixture["items"][0]["text"] = "password=ACTUAL_SYNTHETIC_SECRET"
            fixture["model"] = {
                "status": "synthetic_response",
                "response": [{
                    "title": "Synthetic finding",
                    "friction": "Synthetic friction",
                    "evidence": [{
                        "item_id": fixture["items"][0]["id"],
                        "excerpt": "password=[REDACTED]",
                    }],
                }],
            }
            result, output = self.invoke_fixture(temporary_directory, fixture)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=success", result.stdout)
            snapshot = json.loads((output / "snapshot.json").read_text())
            candidates = json.loads((output / "candidates.json").read_text())
            citation = candidates["candidates"][0]["evidence"][0]
            retained_text = next(
                item["text"] for item in snapshot["items"] if item["id"] == citation["item_id"]
            )
            self.assertTrue(citation["resolved"])
            self.assertIn(citation["excerpt"], retained_text)
            self.assertEqual(citation["excerpt"], "password=[REDACTED]")
            self.assertIn(r"password=\[REDACTED\]", (output / "report.md").read_text())

    def test_redacted_citation_must_still_resolve_to_frozen_evidence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
            fixture["items"][0]["text"] = "The refresh_token=SYNTHETIC_CONTEXT_VALUE is unavailable."
            fixture["model"] = {
                "status": "synthetic_response",
                "response": [{
                    "title": "Synthetic finding",
                    "friction": "Synthetic friction",
                    "evidence": [{
                        "item_id": fixture["items"][0]["id"],
                        "excerpt": "token=SYNTHETIC_CONTEXT_VALUE",
                    }],
                }],
            }
            result, output = self.invoke_fixture(temporary_directory, fixture)

            self.assertEqual(result.returncode, 1)
            self.assertIn("status=invalid_output", result.stdout)
            snapshot = json.loads((output / "snapshot.json").read_text())
            candidates = json.loads((output / "candidates.json").read_text())
            self.assertEqual(candidates["status"], "invalid_output")
            self.assertEqual(candidates["candidates"], [])
            self.assertTrue(candidates["errors"])
            self.assertNotIn("token=[REDACTED]", snapshot["items"][0]["text"])
            self.assertNotIn(r"token=\[REDACTED\]", (output / "report.md").read_text())
            self.assertIn("refresh_token=SYNTHETIC_CONTEXT_VALUE", snapshot["items"][0]["text"])

    def test_redacts_cookie_headers_and_credentials_in_embedded_json(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
            fixture["items"][0]["text"] += (
                '\nCookie: session=SYNTHETIC_COOKIE_98765; theme=dark'
                '\nEmbedded JSON: {"password": "SYNTHETIC_JSON_PASSWORD_98765", '
                '"cookie": "SYNTHETIC_JSON_COOKIE_98765", '
                '"authorization": "Bearer SYNTHETIC_JSON_AUTH_98765"}'
            )
            result, output = self.invoke_fixture(temporary_directory, fixture)

            self.assertEqual(result.returncode, 0, result.stderr)
            for artifact in output.iterdir():
                if artifact.is_file():
                    content = artifact.read_bytes()
                    self.assertNotIn(b"SYNTHETIC_COOKIE_98765", content, artifact.name)
                    self.assertNotIn(b"SYNTHETIC_JSON_PASSWORD_98765", content, artifact.name)
                    self.assertNotIn(b"SYNTHETIC_JSON_COOKIE_98765", content, artifact.name)
                    self.assertNotIn(b"SYNTHETIC_JSON_AUTH_98765", content, artifact.name)

    def test_distinct_redacted_secret_values_cannot_validate_a_citation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
            fixture["items"][0]["text"] = "password=ACTUAL_SYNTHETIC_SECRET"
            fixture["model"] = {
                "status": "synthetic_response",
                "response": [{
                    "title": "Synthetic unsupported finding",
                    "friction": "Synthetic unsupported friction",
                    "evidence": [{
                        "item_id": fixture["items"][0]["id"],
                        "excerpt": "password=INVENTED_SYNTHETIC_SECRET",
                    }],
                }],
            }
            result, output = self.invoke_fixture(temporary_directory, fixture)

            self.assertEqual(result.returncode, 1)
            self.assertIn("status=invalid_output", result.stdout)
            candidates = json.loads((output / "candidates.json").read_text())
            self.assertEqual(candidates["status"], "invalid_output")
            self.assertEqual(candidates["candidates"], [])
            for artifact in output.iterdir():
                if artifact.is_file():
                    content = artifact.read_bytes()
                    self.assertNotIn(b"ACTUAL_SYNTHETIC_SECRET", content, artifact.name)
                    self.assertNotIn(b"INVENTED_SYNTHETIC_SECRET", content, artifact.name)

    def test_malformed_json_persists_sanitized_failure_lineage(self):
        invalid_inputs = (
            b'{"password":"SYNTHETIC_MALFORMED_SECRET_98765","items":',
            b'{"password":"SYNTHETIC_DECODE_SECRET_98765",\xff',
        )
        for invalid_input in invalid_inputs:
            with self.subTest(invalid_input=invalid_input), tempfile.TemporaryDirectory() as temporary_directory:
                fixture_path = Path(temporary_directory) / "fixture.json"
                output = Path(temporary_directory) / "run"
                fixture_path.write_bytes(invalid_input)
                result = self.run_subprocess(
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

                self.assertEqual(result.returncode, 1)
                self.assertIn("status=invalid_input", result.stdout)
                validation_path = output / "validation.json"
                validation_bytes = validation_path.read_bytes()
                validation = json.loads(validation_bytes)
                self.assertEqual(validation["status"], "invalid_input")
                self.assertTrue(validation["errors"])
                self.assertNotIn(b"SYNTHETIC_MALFORMED_SECRET_98765", validation_bytes)
                self.assertNotIn(b"SYNTHETIC_DECODE_SECRET_98765", validation_bytes)
                for artifact in output.iterdir():
                    if artifact.is_file():
                        content = artifact.read_bytes()
                        self.assertNotIn(b"SYNTHETIC_MALFORMED_SECRET_98765", content, artifact.name)
                        self.assertNotIn(b"SYNTHETIC_DECODE_SECRET_98765", content, artifact.name)
                with sqlite3.connect(output / "lineage.sqlite3") as database:
                    stage = database.execute(
                        "SELECT stage, status, artifact_path, output_sha256 FROM stages"
                    ).fetchone()
                self.assertEqual(stage[:3], ("fixture_validation", "invalid_input", "validation.json"))
                self.assertEqual(hashlib.sha256(validation_bytes).hexdigest(), stage[3])

    def test_prompt_evidence_matches_the_redacted_frozen_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            result = self.run_subprocess(
                [sys.executable, "-m", "need_radar", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads((output / "snapshot.json").read_text())
            prompt = json.loads((output / "prompt.json").read_text())
            embedded_items = json.loads(prompt["messages"][1]["content"].split("\n", 1)[1])
            self.assertEqual(embedded_items, snapshot["items"])

    def test_resolved_system_prompt_contains_target_and_v0_strategy(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "run"
            result = self.run_subprocess(
                [sys.executable, "-m", "need_radar", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = json.loads((output / "prompt.json").read_text())
            self.assertEqual(prompt["config"]["version"], "v0")
            system = prompt["messages"][0]["content"].lower()
            for requirement in ("ai application-layer", "explicit pain", "complaints", "feature requests", "missing capabilities"):
                with self.subTest(requirement=requirement):
                    self.assertIn(requirement, system)

    def test_invalid_fixture_persists_redacted_failure_before_snapshot(self):
        base = json.loads((ROOT / "fixtures" / "synthetic_demo.json").read_text())
        invalid_fixtures = []
        invalid_fixtures.append([])
        null_items = json.loads(json.dumps(base))
        null_items["items"] = None
        invalid_fixtures.append(null_items)
        null_text = json.loads(json.dumps(base))
        null_text["items"][0]["text"] = None
        null_text["items"][0]["password"] = "SYNTHETIC_INVALID_INPUT_SECRET_98765"
        invalid_fixtures.append(null_text)
        invalid_source = json.loads(json.dumps(base))
        invalid_source["items"][0]["source"] = []
        invalid_fixtures.append(invalid_source)
        invalid_id = json.loads(json.dumps(base))
        invalid_id["items"][0]["id"] = None
        invalid_fixtures.append(invalid_id)
        duplicate_id = json.loads(json.dumps(base))
        duplicate_id["items"][1]["id"] = duplicate_id["items"][0]["id"]
        invalid_fixtures.append(duplicate_id)
        empty_id = json.loads(json.dumps(base))
        empty_id["items"][0]["id"] = " "
        invalid_fixtures.append(empty_id)

        for fixture in invalid_fixtures:
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as temporary_directory:
                result, output = self.invoke_fixture(temporary_directory, fixture)

                self.assertEqual(result.returncode, 1)
                self.assertIn("status=invalid_input", result.stdout)
                self.assertFalse((output / "snapshot.json").exists())
                validation = json.loads((output / "validation.json").read_text())
                self.assertEqual(validation["status"], "invalid_input")
                self.assertTrue(validation["errors"])
                with sqlite3.connect(output / "lineage.sqlite3") as database:
                    stage = database.execute(
                        "SELECT stage, status, artifact_path, output_sha256 FROM stages"
                    ).fetchone()
                self.assertEqual(stage[:3], ("fixture_validation", "invalid_input", "validation.json"))
                validation_bytes = (output / stage[2]).read_bytes()
                self.assertEqual(hashlib.sha256(validation_bytes).hexdigest(), stage[3])
                self.assertNotIn(b"SYNTHETIC_INVALID_INPUT_SECRET_98765", validation_bytes)


if __name__ == "__main__":
    unittest.main()
