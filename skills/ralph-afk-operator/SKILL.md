---
name: ralph-afk-operator
description: Start and supervise the repo-local Ralph orchestrator in AFK mode. Use when an agent should execute ready GitHub tickets through the Ralph workflow, choose explicit Codex harness/model/effort routing for implementer/reviewer/merger, monitor the orchestrator and all Herdr execution surfaces it owns, and perform coarse whole-run recovery only when the run is genuinely unhealthy.
---

# Ralph AFK Operator

Operate the repository's Ralph orchestrator. Do not reimplement its scheduler, ticket selection, review loop, or merge policy. In the source repository the controller lives under `src/`; when installed in another repository it lives under `tools/ralph/src/`. Keep generated run artifacts under `.ralph/runs/`, distinct from Sandcastle-managed `.sandcastle/worktrees/`.

## Start

1. Load/use the `herdr` skill before inspecting or controlling Herdr.
2. Work from the target repository and verify `HERDR_ENV=1`.
3. Confirm no Ralph orchestrator already owns the repository. Do not start a second one. Inspect existing `ralph/issue-*` candidate branches from previous runs and preserve committed work. Ralph safely merges an advanced target base into clean candidates when possible and aborts on conflicts; do not force-reset/delete WIP or silently resolve conflicts.
4. Choose the harness, model, and effort explicitly for all three roles. V1 supports `codex` only; `claude-code` and `pi` are reserved startup values for later implementation.
5. Run preflight first, then start the same command without `--preflight`.
   Inspect the role prompt templates for stale single-ticket authorization restrictions before launch; scheduler preflight does not validate prompt scope. Repeated merger rejection after accepted reviews can indicate a stale merger prompt rather than an implementation failure.
6. Keep the orchestrator itself in a visible Herdr terminal. The orchestrator creates visible owned Herdr panes for implementers, reviewers, correction implementers, and mergers.

Example:

```bash
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

Use repository-appropriate `--focused-test`, `--final-test`, and `--integration-test` overrides when the defaults are not valid.

## Authorization without approval loops

Read the standing delegation in `AGENTS.md` before supervising a run. For already-approved tickets, **act within their bounded offline scope, then report**: do not pause for repeated owner consent to edit code, run offline tests, replay previously owner-authorized sanitized private inputs locally, review, or merge accepted candidates through Ralph. #9/#19 already specify owner-authorized retention for adapter use; #20's bounded local verification does not need duplicate owner approval. Inspect the actual issue contract and existing approval record before labeling work blocked.

If a worker claims an authorization blocker, verify the specific missing fact and whether earlier issue approvals already cover it. Investigate readily accessible third-party terms without requiring the owner to supply links. Unknown supplier rights must be labeled **unverified**, never certified or used to justify public/live activation; an explicit prohibition, expired retention, or genuinely missing permission still blocks the affected action. Continue independent work instead of converting one ticket's uncertainty into a global stop.

Notify the owner **after** bounded reversible steps, with commit/issue IDs, tests, data handling, unresolved risks, and exact rollback instructions. Seek approval **before** unapproved spend, new live calls, external model disclosure, publishing/sending, exports, schedule activation, credential/retention changes, destructive operations, or a consequential product decision. Rollback of a Git commit cannot undo irreversible side effects. Preserve Ralph's existing implementer/reviewer/merger authority boundaries.

## Monitor

Inspect the orchestrator terminal and owned Herdr panes on a bounded cadence: 15 minutes initially; after successive checks without material progress, wait 30, then 60, then 120 minutes (cap at 120). A new commit, completed review round, changed acceptance state, or finished ticket resets the next interval to 15 minutes. Process exit or a `needs_triage` result should be handled on the next observation, not left waiting for owner approval.

Treat these as normal and do nothing:
- ticket-local worker/test/review failures while the orchestrator keeps progressing;
- a failed, blocked, or merger-rejected ticket being deferred for the rest of the current run;
- a ticket paused at 20 review rounds while independent tickets continue;
- healthy long-running agent output;
- successful sibling tickets continuing after another ticket fails.

Look for evidence of a genuinely unhealthy run:
- the orchestrator itself is dead or frozen while work remains;
- an owned worker is clearly frozen and indefinitely prevents the current iteration from settling;
- multiple owned Codex sessions show shared 429/quota exhaustion;
- Herdr/process ownership is inconsistent enough that safe progress cannot continue.

Do not infer failure from an idle-looking badge alone. Read terminal output and process/session evidence. A normal `complete_with_failures` or `complete_with_triage` result ends the run and needs an owner-facing summary, **not** an automatic restart. A future run may intentionally reconsider open tickets.

## Recover

Prefer coarse recovery over clever repair in v1.

When the run is genuinely unhealthy:
1. Stop the one owned orchestrator.
2. Stop/close only Herdr surfaces that carry the same `RALPH_RUN_ID`.
3. Confirm the old orchestrator is gone and its repo lock is no longer live.
4. If the evidence suggests quota or a transient provider condition, wait/back off before retrying.
5. Restart exactly one orchestrator with the same explicit harness/model/effort routing.

Do not restart one ticket with a second orchestrator. Do not build phase-level resume logic. Open GitHub issues are intentionally eligible to run again after a whole-run restart. A first round can reuse a prior committed candidate already descended from the new base; it must still pass tests and fresh read-only review. Do not reimplement it blindly or fabricate a no-op commit.

## Triage

When a ticket hits the 20-round review limit, read its `needs-triage.json` and existing review receipts. Summarize repeated failed ACs, follow-ups, and the likely design/implementation mismatch. Send the owner a concise recommendation or save a Markdown review artifact. Do not silently change acceptance criteria, create backlog issues, or wait for human approval; let other ready tickets continue. The paused issue stays open for an owner decision.

## Finish

When the orchestrator exits because no runnable ticket remains:
- verify there are no still-owned active Herdr agents;
- stop any monitoring/cron created for this run;
- report the final outcome, failed/blocked issues and their receipts, meaningful non-blocking follow-ups from ticket results, triage summaries, and any tickets left open. Recommend which follow-ups merit backlog consideration; never create tickets or wait for owner approval.
