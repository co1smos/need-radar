# Synthetic evidence identity and replay

`need_radar.evidence` defines a stable identity as `source:native_id` and a content version as the SHA-256 of normalized text. Exact re-imports merge unique discovery origins. An edit retains its prior version, while the latest version enters the next snapshot. This bounded proof uses only `fixtures/evidence_replay.json` and the existing report path.

Run from the repository root with Hermes scratch and test guards enabled:

```sh
out="$(mktemp -d "$HOME/.hermes/cache/scratch/need-radar-evidence.XXXXXX")"
TMPDIR="$HOME/.hermes/cache/scratch" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 scripts/check_evidence_replay.py --output "$out"
```

Observed result on October 9, 2026: passed with two source-qualified identities, three retained content versions after one edit, two independent items in the edited snapshot, two Reddit discovery origins, verified artifact-lineage hashes, stable replayed snapshot item bytes and report bytes, and network/credential/live-state access denied in the check process and subprocesses. The runner reports `network_requests: 0`. All evidence is synthetic and does not verify live sources or provider formats. Retention and derived-artifact erasure were not tested and remain separately gated.
