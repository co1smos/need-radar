# Offline Reddit+X evidence experiment

Run the combined synthetic Reddit+X serve/shadow comparison without provider access:

```sh
output="$(mktemp -d /home/ubuntu/.hermes/cache/scratch/need-radar-issue-11.XXXXXX)/run"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. TMPDIR=/home/ubuntu/.hermes/cache/scratch \
  python3 scripts/check_mixed_source_experiment.py --output "$output"
```

The runner uses recorded normalized fixture responses, then invokes the existing serve and shadow CLIs with the same frozen ordered evidence and fixed assessment rubric. It saves the canonical serve report at `serve/report.md`, the assessed comparison at `shadow/comparison.md`, and their lineage/trace artifacts under the output directory. The X fixture intentionally repeats one Reddit comment; selection retains both source-qualified identities and discovery origins while counting the duplicate as one independent evidence item.

Per-source coverage distinguishes fixture inputs, normalized returns, observed omissions, failures, partial responses, and missing or empty sources. Partial, missing, and failed sources also mark any additional omissions as unknown; a missing source is not reported as an empty successful response. Replaying only the normalized evidence records cannot reconstruct this acquisition-coverage metadata. The report is limited to synthetic fixture coverage; it does not establish live source completeness, provider permission, or market demand. Network, credential-file, and canonical live-state access are denied in the runner and subprocesses.
