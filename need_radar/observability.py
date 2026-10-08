from contextvars import ContextVar
from datetime import datetime, timezone
import json
import time
import uuid


ACTIVE_SPAN = ContextVar("need_radar_active_span", default=None)


class JsonlSpanSink:
    def __init__(self, path):
        self.path = path

    def write(self, record):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


class LangfuseBoundary:
    def __init__(self, path, redact):
        self.sink = JsonlSpanSink(path)
        self.redact = redact

    def export(self, span):
        self.sink.write({
            "event": "span",
            "id": span["span_id"],
            "run_id": span["run_id"],
            "trace_id": span["trace_id"],
            "parent_span_id": span["parent_span_id"],
            "name": span["name"],
            "start_time": span["start_time"],
            "end_time": span["end_time"],
            "duration_ms": span["duration_ms"],
            "status": span["status"],
            "input": self.redact(span["input"]),
            "output": self.redact(span["output"]),
            "metadata": self.redact(span["attributes"]),
            "error": self.redact(span["error"]),
            "remote_status": "unverified",
            "external_export_enabled": False,
        })


class Tracer:
    def __init__(self, run_id, trace_id, boundary, redact):
        self.run_id = run_id
        self.trace_id = trace_id
        self.boundary = boundary
        self.redact = redact
        self.span_count = 0
        self.exported_span_count = 0
        self.export_failures = []

    def span(self, name, inputs=None, attributes=None):
        parent = ACTIVE_SPAN.get()
        parent_span_id = None
        if parent and parent[0] == self.trace_id:
            parent_span_id = parent[1]
        return _Span(self, name, parent_span_id, inputs or {}, attributes or {})


class _Span:
    def __init__(self, tracer, name, parent_span_id, inputs, attributes):
        self.tracer = tracer
        self.name = name
        self.parent_span_id = parent_span_id
        self.record = {
            "run_id": tracer.run_id,
            "trace_id": tracer.trace_id,
            "span_id": uuid.uuid4().hex,
            "parent_span_id": parent_span_id,
            "name": name,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "duration_ms": None,
            "status": "success",
            "input": tracer.redact(inputs),
            "output": None,
            "attributes": tracer.redact(attributes),
            "error": None,
        }
        self.started = time.monotonic()

    def __enter__(self):
        self.token = ACTIVE_SPAN.set((self.tracer.trace_id, self.record["span_id"]))
        self.tracer.span_count += 1
        return self.record

    def __exit__(self, error_type, error, traceback):
        if error is not None:
            self.record["status"] = "failed"
            self.record["error"] = {
                "type": error_type.__name__,
                "message": self.tracer.redact(str(error)),
            }
        self.record["end_time"] = datetime.now(timezone.utc).isoformat()
        self.record["duration_ms"] = round((time.monotonic() - self.started) * 1000, 3)
        try:
            self.tracer.boundary.export(self.record)
        except Exception as export_error:
            self.tracer.export_failures.append({
                "span_id": self.record["span_id"],
                "span": self.name,
                "error_type": type(export_error).__name__,
                "message": self.tracer.redact(str(export_error)),
            })
        else:
            self.tracer.exported_span_count += 1
        finally:
            ACTIVE_SPAN.reset(self.token)
        return False
