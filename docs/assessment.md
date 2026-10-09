# Offline candidate assessment

The fixed evaluator is implemented in `need_radar/assessment.py`. Its rubric is versioned as `fixed-rubric-v1`; each candidate receives the same five dimensions, grounding requirements, uncertainty rules, and `ELIGIBLE`, `NEEDS_EVIDENCE`, or `INELIGIBLE` verdict contract. Within-arm consolidation is limited to candidates with the same whitespace-normalized, case-folded title and friction. `consolidation.json` retains raw candidates, evidence membership, and the arm; serve and shadow consolidation calls are separate.

Each consolidated candidate receives its own judge prompt. The prompt contains only allowlisted candidate text and the candidate's cited frozen evidence; arm, strategy, extractor instructions/confidence, and costs are excluded. The rubric and context policy are shared between arms. Judge output evidence IDs must resolve within that candidate's evidence set. Semantic support is still a judge determination, not established by exact citation resolution.

The current CLI path is **synthetic/offline only**. A fixture may include a predetermined `judge` response. It does not call DeepSeek or any other provider: the selected model ID is recorded, while provider and invocation settings remain unresolved. Assessment artifacts are persisted after the canonical `report.md`, linked to the snapshot, candidate validation, consolidation, judge response, and report hashes. Judge, prompt, or validation failure records an unavailable/invalid assessment without changing serve status or rewriting the report. A non-empty run directory cannot be rerun in place.

Run the end-to-end proof from an empty output directory under Hermes scratch:

```sh
TMPDIR=/home/ubuntu/.hermes/cache/scratch \
PYTHONPATH=tests:. PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/check_assessment.py \
  --output /home/ubuntu/.hermes/cache/scratch/need-radar-assessment-check
```

The check denies network access and canonical credential/live-state reads in the process and subprocesses, verifies redaction and artifact hashes, and reports the fixture verdict. Its success is synthetic/offline evidence only, not live judge, source-coverage, provider, or deployment verification.
