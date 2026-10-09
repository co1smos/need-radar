# Need Radar Ralph operator

Ralph's tracked controller sources are installed under `tools/ralph/src/` from `co1smos/ralph-orchestrator` revision `95d4b313f0bc5e3b23d725f74f0cdee3e5d4fa24`. Need Radar keeps its project-specific role prompts and model routing; reusable skills live in the repository-root `skills/` directory. New Ralph receipts and its lock are under `.ralph/`; historical `.sandcastle/runs/` and Sandcastle worktrees are preserved.

## Run the eligible frontier

Read `skills/ralph-afk-operator/SKILL.md` and `AGENTS.md`. From the `main` checkout, under Herdr, verify no other Ralph controller owns this repository. The default is **full-frontier** mode: select current open `ready-for-agent` tickets whose native/declaration blockers are closed. Do **not** supply `--issue` unless the owner explicitly requests a single-ticket run. Sandcastle supplies worktree isolation only; the retired `.sandcastle/` controller must not be restarted.

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

The current GitHub ticket's fixed `## Acceptance criteria` and its explicit authorization gates define scope; preserve nonblocking `followups` in receipts. A `ready-for-agent` label is scheduling state, not authorization for live side effects. Offline tickets must deny network, credential, and canonical live-state access in tests and subprocesses and use Hermes `TMPDIR`. Issues #9 (X) and #19 (Reddit) are reserved for separate, explicitly owner-authorized sessions. Live acquisition, external exports, scheduling, and Discord delivery require their own execution, permission, budget, and evidence gates. Never assume unknown charges are zero, clear safety holds, or invent live evidence to unblock another ticket.

Historical issue #3 is complete and is **not** a scope restriction for future full-frontier runs. Preserve its historical receipts in `.sandcastle/runs/` and retain all existing branches and worktrees.

## Inspect results

Inspect `.ralph/runs/<run-id>/run.json`, each `iteration-*/plan.json` and `settled.json`, `iteration-*/issue-*/result.json` or `needs-triage.json`, reviewer receipts, and merger receipts. Historical runs remain under `.sandcastle/runs/`. Confirm GitHub issue states and remote Git HEAD; an orchestrator `complete` message alone does not prove ticket acceptance.

The old 15-minute read-only safety-audit cron is a separate legacy checker, not an AFK worker controller. Do not rely on it for ticket scheduling or restart; the new AFK operator owns run monitoring and stops any monitoring it creates when done.
