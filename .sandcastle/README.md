# Need Radar Ralph operator

Ralph was synced from [ralph-orchestrator](https://github.com/co1smos/ralph-orchestrator) `master` at `c49984d`. Need Radar retains project-specific offline safety guards, model routing and nested-worktree `tsx` resolution. The controller is in `.sandcastle/`; reusable skills live in the repository-root `skills/` directory.

## Run the eligible frontier

Read `skills/ralph-afk-operator/SKILL.md` and `AGENTS.md`. From the `main` checkout, under Herdr, verify no other Ralph controller owns this repository. The default is **full-frontier** mode: select current open `ready-for-agent` tickets whose native/declaration blockers are closed. Do **not** supply `--issue` unless the owner explicitly requests a single-ticket run.

```bash
npm test
npm run typecheck:sandcastle-workflow

npm run ralph:check -- \
  --implementer-harness codex --implementer-model gpt-6-luna --implementer-effort max \
  --reviewer-harness codex --reviewer-model gpt-6-astra --reviewer-effort medium \
  --merger-harness codex --merger-model gpt-6-luna --merger-effort high \
  --max-parallel 3

npm run ralph -- \
  --implementer-harness codex --implementer-model gpt-6-luna --implementer-effort max \
  --reviewer-harness codex --reviewer-model gpt-6-astra --reviewer-effort medium \
  --merger-harness codex --merger-model gpt-6-luna --merger-effort high \
  --max-parallel 3
```

Use the Skill for AFK supervision, progress-aware 15 → 30 → 60 → 120 minute monitoring, owned-worker recovery, and 20-round `needs_triage` summaries. The controller owns ticket selection, correction, merge, push and closure. The default focused/final/integration checks are `npm test`; no-sandbox host execution is not a security sandbox.

## Ticket execution boundaries

The GitHub ticket is the delivery contract. Review the fixed `## Acceptance criteria` against its supported scope and allowed safe failures; preserve nonblocking `followups` in receipts. Offline tickets must not access credentials or live provider state and must use guarded fixtures/recordings. Live X (#9) and Reddit (#19) acquisition may proceed only after their **own** explicit source/credential/rights/budget/retention/reconciliation gates pass, with separate oversight in their existing Hermes tabs. The live source tickets are external data prerequisites for adapter #4. Do not clear safety holds or fake live evidence to unblock it.

Historical issue #3 runs and WIP remain available in `.sandcastle/runs/` and archived branches; #3 is already completed and is **not** a scope restriction for future runs.

## Inspect results

Inspect `.sandcastle/runs/<run-id>/run.json`, each `iteration-*/plan.json` and `settled.json`, `iteration-*/issue-*/result.json` or `needs-triage.json`, reviewer receipts, and merger receipts. Confirm GitHub issue states and remote Git HEAD; an orchestrator `complete` message alone does not prove ticket acceptance.

The old 15-minute read-only safety-audit cron is a separate legacy checker, not an AFK worker controller. Do not rely on it for ticket scheduling or restart; the new AFK operator owns run monitoring and stops any monitoring it creates when done.
