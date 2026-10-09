#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys


MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


def deny_network(event, arguments):
    if event.startswith("socket."):
        raise PermissionError("network access is disabled")


sys.addaudithook(deny_network)


def parse_timestamp(value):
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    except (TypeError, ValueError) as error:
        raise ValueError("unexpected Reddit cell timestamp") from error


def check_artifact(path):
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds the read limit")
    raw = path.read_bytes()
    record = json.loads(raw)

    if record["response"]["http_status"] != 200:
        raise ValueError("artifact is not an HTTP 200 response")
    configuration = record["configuration"]
    start = dt.datetime.fromisoformat(configuration["window_start"].replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(configuration["window_end"].replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ValueError("artifact window is invalid")

    subreddit = record["request"]["parameters"]["subreddit_name"]
    if subreddit not in configuration["subreddits"]:
        raise ValueError("request subreddit does not match configuration")
    edges = record["response"]["body"]["data"]["subredditV3"]["elements"]["edges"]
    if not isinstance(edges, list):
        raise ValueError("feed edges are invalid")

    seen_ids = set()
    posts_in_window = 0
    posts_outside_window = 0
    ads_excluded = 0
    recommendations_excluded = 0
    for edge in edges:
        node = edge["node"]
        cells = node.get("cells", [])
        if node.get("adPayload") or any(cell.get("__typename") == "AdMetadataCell" for cell in cells):
            ads_excluded += 1
            continue

        metadata = [cell for cell in cells if cell.get("__typename") == "MetadataCell"]
        titles = [cell.get("title") for cell in cells if cell.get("__typename") == "TitleCell"]
        previews = [cell.get("text") for cell in cells if cell.get("__typename") == "PreviewTextCell"]
        if not metadata and not titles:
            recommendations_excluded += 1
            continue
        if not metadata or not any(isinstance(title, str) and title.strip() for title in titles):
            raise ValueError("incomplete Reddit post cells")

        post_id = node.get("id")
        if not isinstance(post_id, str) or not post_id.strip() or post_id in seen_ids:
            raise ValueError("post ID is missing or duplicated")
        if not any(isinstance(preview, str) and preview.strip() for preview in previews):
            raise ValueError("post preview text is missing")
        seen_ids.add(post_id)

        created_at = parse_timestamp(metadata[0].get("createdAt"))
        if start <= created_at <= end:
            posts_in_window += 1
        else:
            posts_outside_window += 1

    charge = record["billing"]["charged_micro_usd"]
    if isinstance(charge, bool):
        raise ValueError("artifact charge is invalid")
    charge = int(charge)
    if charge < 0:
        raise ValueError("artifact charge is invalid")

    return {
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "http_status": 200,
        "network_requests": 0,
        "feed_elements": len(edges),
        "posts_in_window": posts_in_window,
        "posts_outside_window": posts_outside_window,
        "ads_excluded": ads_excluded,
        "recommendations_excluded": recommendations_excluded,
        "charge_micro_usd": charge,
        "collector_replay": "not_run",
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only smoke check for one Reddit response artifact")
    parser.add_argument("artifact", type=Path, help="explicit path to a private response record")
    args = parser.parse_args()
    try:
        result = check_artifact(args.artifact)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        print("Reddit artifact smoke check failed", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
