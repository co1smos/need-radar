# Need Radar Ralph operator

Synced from `/home/ubuntu/projects/ralph-orchestrator` master `c49984d6f5c53667585a76f1239eb25a94020fa8`: criterion-scoped materiality review, nonblocking followups, 20-round triage and AFK monitoring cadence. Need Radar retains offline safety instructions, explicit correction authorization, pinned dependencies, and installed-tsx resolution for nested worktree process tests. Root `skills/` contains the upstream skills; the AFK Skill is also mirrored at the existing `.agents/skills/` path.

Load `.agents/skills/ralph-afk-operator/SKILL.md`. Operate from main inside Herdr; exactly one orchestrator, ONLY issue 3:

    npm test
    npm run typecheck:sandcastle-workflow
    npm run ralph:check -- --issue 3 --max-parallel 1 --implementer-harness codex --implementer-model gpt-6-luna --implementer-effort max --reviewer-harness codex --reviewer-model gpt-6-astra --reviewer-effort medium --merger-harness codex --merger-model gpt-6-luna --merger-effort high

Start with identical flags using `npm run ralph --`. Default focused/final/integration command is `npm test`, covering controller tests and network-denied Python tests. Preserve Hermes TMPDIR. Worker phases cannot access credentials/live state, acquire data, install, schedule, or export. Only the merger may perform authorized Git/GitHub integration. Direct host execution is not a security sandbox.

Fixed acceptance criteria become AC1 etc. Review only pending criteria, correct failures, then a fresh final reviewer checks all original criteria and material regressions once. Final failure reopens affected criteria. Merge/push/close after approval and deterministic gates. Closing offline #3 does not close live follow-up work or clear incident holds.

Use the current GitHub ticket contract, not historical recovery notes. Review nonblocking improvements as followups, not expanded criteria. At 20 rounds, inspect needs-triage.json and report the design/implementation mismatch without silently changing criteria. Monitor at 15 minutes, backing off to 30/60/120 minutes only without material progress; progress resets the interval. The current session's injected scope still restricts the operator to #3; a full-frontier launch requires refreshed higher-priority project context, not merely editing this file. Separate interactive Hermes tabs for #9/#19 inspect their own live gates and must not start another Ralph controller or mutate shared main.

Artifacts: `.sandcastle/runs/<run-id>/run.json`, `iteration-1/issue-3/review-state.json`, round receipts/prompts/tests, `result.json`, `iteration-1/settled.json`, merger receipts and integration results. Read actual processes and artifacts, not badges. Controller `complete` alone is not success: inspect settled results, merger receipt, remote SHA and issue state.

Old issue-3 run explicitly stopped. Interrupted round 21 is preserved remotely as `archive/issue-3-round-21-wip` (4a06567); its new test fails. Main keeps last committed Reddit baseline 0209a54, reviewed offline X baseline 8c94caa, and portability fixes at 7291093. Neither baseline establishes live acceptance. Do not cherry-pick WIP blindly.
