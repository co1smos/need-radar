# Need Radar Ralph operator

Synced from `/home/ubuntu/projects/ralph-orchestrator` commit `16efb26`: criterion-scoped review and AFK operator Skill. Controller, core, schemas and tests are upstream copies. Need Radar adds offline safety instructions to phase prompts and retains its pinned dependencies. The old controller and cumulative review behavior are replaced.

Load `.agents/skills/ralph-afk-operator/SKILL.md`. Operate from main inside Herdr; exactly one orchestrator, ONLY issue 3:

    npm test
    npm run typecheck:sandcastle-workflow
    npm run ralph:check -- --issue 3 --max-parallel 1 --implementer-harness codex --implementer-model gpt-6-luna --implementer-effort max --reviewer-harness codex --reviewer-model gpt-6-astra --reviewer-effort medium --merger-harness codex --merger-model gpt-6-luna --merger-effort high

Start with identical flags using `npm run ralph --`. Default focused/final/integration command is `npm test`, covering controller tests and network-denied Python tests. Preserve Hermes TMPDIR. Worker phases cannot access credentials/live state, acquire data, install, schedule, or export. Only the merger may perform authorized Git/GitHub integration. Direct host execution is not a security sandbox.

Fixed acceptance criteria become AC1 etc. Review only pending criteria, correct failures, then a fresh final reviewer checks all original criteria and material regressions once. Final failure reopens affected criteria. Merge/push/close after approval and deterministic gates. Closing offline #3 does not close live follow-up work or clear incident holds.

Artifacts: `.sandcastle/runs/<run-id>/run.json`, `iteration-1/issue-3/review-state.json`, round receipts/prompts/tests, `result.json`, `iteration-1/settled.json`, merger receipts and integration results. Read actual processes and artifacts, not badges. Controller `complete` alone is not success: inspect settled results, merger receipt, remote SHA and issue state.

Old issue-3 run explicitly stopped. Interrupted round 21 is preserved remotely as `archive/issue-3-round-21-wip` (4a06567); its new test fails. Main keeps last committed Reddit baseline 0209a54, reviewed offline X baseline 8c94caa, and portability fixes at 7291093. Neither baseline establishes live acceptance. Do not cherry-pick WIP blindly.
