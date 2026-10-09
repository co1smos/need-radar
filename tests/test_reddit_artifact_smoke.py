import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_reddit_artifact.py"
SCRATCH = os.environ.get("TMPDIR", tempfile.gettempdir())


def guarded_environment():
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["TMPDIR"] = SCRATCH
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "tests"), environment.get("PYTHONPATH", "")]
    )
    return environment


class RedditArtifactSmokeTest(unittest.TestCase):
    def test_synthetic_artifact_and_subprocess_guards(self):
        def edge(post_id, cells, **extra):
            return {"node": {"id": post_id, "cells": cells, **extra}}

        edges = [
            edge(
                "synthetic-post-id",
                [
                    {"__typename": "MetadataCell", "createdAt": "2026-10-08T12:00:00.000000+0000"},
                    {"__typename": "TitleCell", "title": "Synthetic post title"},
                    {"__typename": "PreviewTextCell", "text": "Synthetic post preview"},
                ],
            ),
            edge("synthetic-ad-id", [{"__typename": "AdMetadataCell"}], adPayload=True),
            edge("synthetic-recommendation-id", [{"__typename": "RecommendationCell"}]),
        ]
        artifact = {
            "configuration": {
                "subreddits": ["AI_Agents"],
                "window_start": "2026-10-01T00:00:00Z",
                "window_end": "2026-10-09T00:00:00Z",
            },
            "request": {"parameters": {"subreddit_name": "AI_Agents"}},
            "response": {
                "http_status": 200,
                "body": {"data": {"subredditV3": {"elements": {"edges": edges}}}},
            },
            "billing": {"charged_micro_usd": "1000"},
        }
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp_dir:
            artifact_path = Path(temp_dir) / "synthetic-record.json"
            artifact_path.write_text(json.dumps(artifact))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(artifact_path)],
                capture_output=True,
                text=True,
                env=guarded_environment(),
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["feed_elements"], 3)
            self.assertEqual(summary["posts_in_window"], 1)
            self.assertEqual(summary["ads_excluded"], 1)
            self.assertEqual(summary["recommendations_excluded"], 1)
            self.assertEqual(summary["charge_micro_usd"], 1000)
            self.assertEqual(summary["collector_replay"], "not_run")
            self.assertNotIn("synthetic-post-id", result.stdout)
            self.assertNotIn("Synthetic post preview", result.stdout)

            environment = guarded_environment()
            probes = (
                "import socket; socket.socket()",
                "open('/home/ubuntu/projects/need-radar/credentials.env')",
                "open('/home/ubuntu/.local/state/need-radar/ticket-19/state.json')",
            )
            for probe in probes:
                with self.subTest(probe=probe):
                    result = subprocess.run(
                        [sys.executable, "-c", probe],
                        capture_output=True,
                        text=True,
                        env=environment,
                        check=False,
                        timeout=10,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("disabled in tests", result.stderr)
