import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import network_guard
import need_radar.__main__ as tracer_cli
import need_radar.presentation as presentation
import need_radar.report_html as report_html


ROOT = Path(__file__).resolve().parents[1]
HERMES_TMPDIR = Path("/home/ubuntu/.hermes/cache/scratch")


def offline_environment():
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(HERMES_TMPDIR),
    }


class ReportHtmlTests(unittest.TestCase):
    def test_process_denies_network_credentials_and_canonical_live_state(self):
        network_guard.install()
        with self.assertRaisesRegex(PermissionError, "network access"):
            socket.socket()
        with self.assertRaisesRegex(PermissionError, "credential access"):
            open(network_guard.CREDENTIALS_PATH, "rb")
        with self.assertRaisesRegex(PermissionError, "live state access"):
            open(Path(network_guard.LIVE_STATE_PATH) / "state.json", "rb")

        script = """
import os
import socket

if os.environ.get('TMPDIR') != '/home/ubuntu/.hermes/cache/scratch':
    raise SystemExit('wrong TMPDIR')
if any(marker in name.lower() for name in os.environ for marker in ('secret', 'token', 'credential', 'password', 'api_key')):
    raise SystemExit('credential environment inherited')
try:
    socket.socket()
except PermissionError:
    print('network=denied')
else:
    raise SystemExit('network allowed')
for name, path in (
    ('credentials', '/home/ubuntu/projects/need-radar/credentials.env'),
    ('live_state', '/home/ubuntu/.local/state/need-radar/state.json'),
):
    try:
        open(path, 'rb')
    except PermissionError:
        print(name + '=denied')
    else:
        raise SystemExit(name + ' allowed')
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=offline_environment(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("network=denied", result.stdout)
        self.assertIn("credentials=denied", result.stdout)
        self.assertIn("live_state=denied", result.stdout)

    def test_synthetic_serve_pipeline_renders_standalone_html_with_identity(self):
        with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR) as temporary_directory:
            output = Path(temporary_directory) / "run"
            result = subprocess.run(
                [sys.executable, "-m", "need_radar", "--output", str(output)],
                cwd=ROOT,
                env=offline_environment(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=success", result.stdout)

            markdown = (output / "report.md").read_bytes()
            rendered = (output / "report.html").read_bytes()
            manifest = json.loads((output / "report.html.manifest.json").read_text())
            source_hash = hashlib.sha256(markdown).hexdigest()
            report_stage = next(
                row
                for row in sqlite_rows(output / "lineage.sqlite3")
                if row[0] == "report"
            )

            self.assertIn(f'name="need-radar-source-sha256" content="{source_hash}"'.encode(), rendered)
            self.assertEqual(manifest["parent"]["sha256"], source_hash)
            self.assertEqual(manifest["parent"]["artifact_id"], report_stage[1])
            self.assertEqual(manifest["output"]["sha256"], hashlib.sha256(rendered).hexdigest())
            self.assertTrue(manifest["redaction"]["applied_before_render"])
            self.assertEqual(manifest["redaction"]["coverage"], "best_effort")
            self.assertEqual(hashlib.sha256((output / "report.md").read_bytes()).hexdigest(), source_hash)
            self.assertEqual(rendered.decode(), presentation.render_markdown_html(markdown.decode()))
            self.assertIn(b"Coverage: synthetic fixture inputs only", rendered)
            self.assertIn(b"Assessment: unevaluated in this frozen report", rendered)
            self.assertIn(b"Evidence 1-1", rendered)
            self.assertIn(b'href="#finding-1"', rendered)
            self.assertIn(b'href="#evidence-1-1"', rendered)
            self.assertIn(b'id="finding-1"', rendered)
            self.assertIn(b'id="evidence-1-1"', rendered)
            self.assertNotIn(b"SYNTHETICONLY1234567890", rendered)
            self.assertNotIn(b"<script", rendered.lower())
            self.assertNotIn(b"<img", rendered.lower())
            self.assertNotIn(b"<link", rendered.lower())
            self.assertNotIn(b"<script src=", rendered.lower())
            for artifact in output.rglob("*"):
                if artifact.is_file():
                    self.assertNotIn(b"SYNTHETICONLY1234567890", artifact.read_bytes(), str(artifact))

    def test_frozen_fixture_preserves_links_chinese_and_long_excerpts_safely(self):
        fixture = (ROOT / "fixtures" / "synthetic_report.md").read_text(encoding="utf-8")
        long_excerpt = "长篇合成证据，构建者需要重复整理上下文。" * 160
        markdown = fixture.replace("LONG_EXCERPT", long_excerpt)

        with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR) as temporary_directory:
            source = Path(temporary_directory) / "report.md"
            output = Path(temporary_directory) / "report.html"
            source.write_text(markdown, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "need_radar.report_html", "--input", str(source), "--output", str(output)],
                cwd=ROOT,
                env=offline_environment(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            rendered = output.read_text(encoding="utf-8")
            manifest = json.loads(Path(f"{output}.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["parent"]["sha256"], hashlib.sha256(markdown.encode()).hexdigest())
            self.assertTrue(manifest["redaction"]["applied_before_render"])
            self.assertEqual(manifest["redaction"]["coverage"], "best_effort")
            first_render = output.read_bytes()
            first_hash = hashlib.sha256(first_render).hexdigest()
            self.assertIn("<h2 id=\"summary\">Summary</h2>", rendered)
            self.assertIn("<h2 id=\"coverage\">Coverage</h2>", rendered)
            self.assertIn("<h2 id=\"evaluation\">Evaluation</h2>", rendered)
            self.assertIn('href="#evidence-1-1"', rendered)
            self.assertIn('href="https://example.invalid/r/synthetic/1"', rendered)
            self.assertIn("合成证据", rendered)
            self.assertIn(long_excerpt, rendered)
            self.assertIn("&lt;script&gt;", rendered)
            self.assertIn("&lt;img src=", rendered)
            self.assertIn("[REDACTED]", rendered)
            self.assertNotIn("SYNTHETICONLY1234567890", rendered)
            self.assertNotIn("javascript:", rendered)
            self.assertNotIn("<script", rendered.lower())
            self.assertNotIn("<img", rendered.lower())
            self.assertNotIn("<link", rendered.lower())
            for artifact in Path(temporary_directory).rglob("*"):
                if artifact.is_file() and artifact != source:
                    self.assertNotIn("SYNTHETICONLY1234567890", artifact.read_text(encoding="utf-8"), str(artifact))

            repeated = subprocess.run(
                [sys.executable, "-m", "need_radar.report_html", "--input", str(source), "--output", str(output)],
                cwd=ROOT,
                env=offline_environment(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), first_hash)
            self.assertEqual(source.read_text(encoding="utf-8"), markdown)
            self.assertNotIn("SYNTHETICONLY1234567890", (Path(f"{output}.manifest.json")).read_text())

    def test_unwritable_projection_records_failure_without_changing_source(self):
        with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR) as temporary_directory:
            source = Path(temporary_directory) / "report.md"
            source.write_text("# Frozen report\n\nStatus: synthetic only\n", encoding="utf-8")
            before = source.read_bytes()
            target = Path(temporary_directory) / "existing-directory"
            target.mkdir()

            status = report_html.render_file(source, target, tracer_cli.redact)

            self.assertEqual(status, "failed")
            self.assertEqual(source.read_bytes(), before)
            failure = json.loads(Path(f"{target}.manifest.json").read_text())
            self.assertEqual(failure["failure"]["category"], "artifact_write_failed")
            self.assertEqual(failure["parent"]["sha256"], hashlib.sha256(before).hexdigest())
            failed_trace = json.loads((Path(temporary_directory) / "presentation" / "trace.jsonl").read_text())
            self.assertEqual(failed_trace["error"]["message"], "artifact_write_failed")

    def test_render_failure_keeps_canonical_markdown_and_serve_status(self):
        with tempfile.TemporaryDirectory(dir=HERMES_TMPDIR) as temporary_directory:
            output = Path(temporary_directory) / "run"
            with mock.patch.object(report_html, "render_markdown_html", side_effect=RuntimeError("synthetic renderer failure")):
                status = tracer_cli.run(tracer_cli.DEFAULT_FIXTURE, output)

            self.assertEqual(status, "success")
            self.assertTrue((output / "report.md").is_file())
            self.assertFalse((output / "report.html").exists())
            manifest = json.loads((output / "report.html.manifest.json").read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["failure"]["category"], "renderer_failed")
            self.assertEqual(
                manifest["parent"]["sha256"],
                hashlib.sha256((output / "report.md").read_bytes()).hexdigest(),
            )
            failed_trace = json.loads((output / "presentation" / "trace.jsonl").read_text())
            self.assertEqual(failed_trace["status"], "failed")
            self.assertEqual(failed_trace["error"]["message"], "renderer_failed")
            self.assertNotIn("synthetic renderer failure", json.dumps(failed_trace))


def sqlite_rows(path):
    import sqlite3

    with sqlite3.connect(path) as database:
        return database.execute(
            "SELECT stage, artifact_id, status, input_stage, input_sha256 FROM stages ORDER BY sequence"
        ).fetchall()


if __name__ == "__main__":
    unittest.main()
