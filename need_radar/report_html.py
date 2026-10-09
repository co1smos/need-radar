import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from need_radar.observability import JsonlSpanSink, LangfuseBoundary, Tracer
from need_radar.presentation import RENDERER_VERSION, render_markdown_html


CONFIGURATION = {
    "renderer_version": RENDERER_VERSION,
    "stylesheet": "embedded",
    "network_assets": False,
    "pdf": "deferred",
    "encoding": "utf-8",
}


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_write(path, content):
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _same_file(first, second):
    if first.resolve() == second.resolve():
        return True
    try:
        return first.samefile(second)
    except OSError:
        return False


def _failure_category(error):
    if isinstance(error, UnicodeDecodeError):
        return "invalid_utf8"
    if isinstance(error, OSError):
        return "artifact_write_failed"
    if isinstance(error, (ValueError, TypeError)):
        return "invalid_markdown_or_path"
    return "renderer_failed"


def render_file(input_path, output_path, redact, parent_artifact_id=None):
    source = Path(input_path)
    target = Path(output_path)
    manifest_path = Path(f"{target}.manifest.json")
    if _same_file(source, target) or _same_file(source, manifest_path):
        raise ValueError("input and output artifacts must be distinct")

    raw = source.read_bytes()
    source_hash = sha256(raw)
    run_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
    config_bytes = json.dumps(CONFIGURATION, sort_keys=True).encode("utf-8")
    render_id = sha256(source_hash.encode() + b":" + sha256(config_bytes).encode())
    target.parent.mkdir(parents=True, exist_ok=True)
    trace_dir = target.parent / "presentation"
    trace_dir.mkdir(parents=True, exist_ok=True)
    tracer = Tracer(
        run_id,
        trace_id,
        LangfuseBoundary(trace_dir / "trace.jsonl", redact),
        JsonlSpanSink(trace_dir / "logs.jsonl"),
        redact,
    )

    with tracer.span(
        "report_render",
        inputs={
            "source_sha256": source_hash,
            "source_bytes": len(raw),
            "parent_artifact_id": parent_artifact_id,
        },
        attributes={"render_id": render_id, "configuration": CONFIGURATION},
    ) as span:
        try:
            markdown = redact(raw.decode("utf-8"))
            output = render_markdown_html(markdown, source_hash).encode("utf-8")
            atomic_write(target, output)
            output_hash = sha256(output)
            manifest = {
                "status": "success",
                "run_id": run_id,
                "trace_id": trace_id,
                "render_id": render_id,
                "parent": {
                    "stage": "report",
                    "artifact": source.name,
                    "artifact_id": parent_artifact_id,
                    "sha256": source_hash,
                },
                "output": {"artifact": target.name, "sha256": output_hash, "bytes": len(output)},
                "configuration": CONFIGURATION,
                "validation": {"utf8": "valid", "standalone_assets": "embedded", "content_source": "frozen_markdown"},
                "redaction": {"applied_before_render": True, "coverage": "best_effort"},
            }
            atomic_write(manifest_path, json_bytes(manifest))
            span["attributes"]["result_status"] = "success"
            span["output"] = {
                "status": "success",
                "render_id": render_id,
                "source_sha256": source_hash,
                "output_sha256": output_hash,
            }
            return "success"
        except Exception as error:
            category = _failure_category(error)
            span["status"] = "failed"
            span["attributes"]["result_status"] = "failed"
            span["error"] = {"type": type(error).__name__, "message": category}
            span["output"] = {"status": "failed", "render_id": render_id, "source_sha256": source_hash}
            failure = {
                "status": "failed",
                "run_id": run_id,
                "trace_id": trace_id,
                "render_id": render_id,
                "parent": {
                    "stage": "report",
                    "artifact": source.name,
                    "artifact_id": parent_artifact_id,
                    "sha256": source_hash,
                },
                "output": {"artifact": target.name, "sha256": None},
                "configuration": CONFIGURATION,
                "failure": {"category": category, "error_type": type(error).__name__},
                "redaction": {"applied_before_render": True, "coverage": "best_effort"},
            }
            try:
                atomic_write(manifest_path, json_bytes(failure))
            except OSError:
                pass
            return "failed"


def main():
    parser = argparse.ArgumentParser(description="Render a frozen canonical report to standalone offline HTML.")
    parser.add_argument("--input", required=True, type=Path, help="canonical Markdown report")
    parser.add_argument("--output", required=True, type=Path, help="standalone HTML output")
    arguments = parser.parse_args()
    try:
        from need_radar.__main__ import redact

        status = render_file(arguments.input, arguments.output, redact)
    except (OSError, ValueError):
        print("render failed: input or artifact path is unavailable", file=sys.stderr)
        return 1
    print(f"status={status} output={arguments.output}")
    return 0 if status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
