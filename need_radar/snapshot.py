import json
from pathlib import Path


def read_frozen_snapshot(path):
    """Read the ordered item payload exposed to serve and shadow consumers."""
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("version")) is not int
        or snapshot["version"] != 1
        or not isinstance(snapshot.get("items"), list)
        or any(not isinstance(item, dict) for item in snapshot["items"])
    ):
        raise ValueError("unsupported frozen snapshot")
    return {
        key: value
        for key, value in snapshot.items()
        if key != "lineage"
    }
