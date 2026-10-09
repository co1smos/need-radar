import hashlib


def import_normalized_items(ledger, items):
    pending = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"source", "native_id", "text", "discovery_origin"}:
            raise ValueError("normalized evidence requires source, native_id, text, and discovery_origin")
        source = item["source"]
        native_id = item["native_id"]
        text = item["text"]
        origin = item["discovery_origin"]
        if not isinstance(source, str) or source not in {"reddit", "x"}:
            raise ValueError("source must be reddit or x")
        if not isinstance(native_id, str) or not native_id.strip():
            raise ValueError("native_id must be a non-empty string")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if not isinstance(origin, str) or not origin.strip():
            raise ValueError("discovery_origin must be a non-empty string")
        pending.append((source, native_id, text, origin.strip()))

    versions = ledger.setdefault("versions", {})
    current = ledger.setdefault("current", {})
    for source, native_id, text, origin in pending:
        identity = f"{source}:{native_id}"
        content_version = hashlib.sha256(text.encode("utf-8")).hexdigest()
        item_versions = versions.setdefault(identity, {})
        evidence = item_versions.get(content_version)
        if evidence is None:
            evidence = {
                "id": identity,
                "source": source,
                "native_id": native_id,
                "content_version": content_version,
                "text": text,
                "discovery_origins": [],
            }
            item_versions[content_version] = evidence
        evidence["discovery_origins"] = sorted(set(evidence["discovery_origins"]) | {origin})
        current[identity] = content_version

    return [versions[identity][current[identity]] for identity in sorted(current)]
