# Need Radar reviewed runner

Adapted from the exercised JEV/flight_search_demo Sandcastle controller (JEV setup dbd2f51; flight controller a5287cf). Sandcastle 0.12.0 owns isolated branch/worktree execution; its controller launches visible Codex implementer/reviewer panes via the installed Unsnooze CLI. This is direct host execution, not a security sandbox.

## Setup and invocation

From the repository root, install pinned development dependencies with `npm ci --ignore-scripts`. Existing host Codex/GitHub authentication, Herdr, Unsnooze and Python 3 are prerequisites. Never copy credentials into a worktree or pass secret values in command arguments.

Run these checks before model execution:

    npm run test:sandcastle-workflow
    npm run typecheck:sandcastle-workflow
    SANDCASTLE_MODEL=gpt-6-luna SANDCASTLE_EFFORT=max npm run sandcastle:preflight -- --issue 3 --base-sha <exact-candidate-sha> --branch <candidate-branch>

In a dedicated Herdr issue tab, use the same arguments with `npm run sandcastle --`. `SANDCASTLE_CONTEXT_FILE` supplies a sanitized recovery brief, and `SANDCASTLE_REVIEW_BASE` names the full-diff review base. Existing candidate branches must match the explicit starting SHA. The review model defaults to gpt-6-astra/medium; override explicitly through SANDCASTLE_REVIEW_MODEL/SANDCASTLE_REVIEW_EFFORT. The implementer model/effort are required, not inferred.

`--timeout` specifies the per-phase budget in seconds (default 18000, five hours), including bounded 429 retry/backoff. There is no three-round cap or overall task-duration kill. A deadline/infrastructure failure preserves work and reports blocked; it does not mean accepted. Healthy work can run for hours. If a phase deadline expires while its worker is still active, the controller refuses to close that pane and requires supervisor process reconciliation.

## Acceptance and monitoring

Each issue loops: implement -> controller tests -> fresh independent review. Failed tests and changes_requested feed cumulative findings to another implementation round automatically. Approval requires a matching SHA, distinct verified sessions, zero worker exit, unchanged clean candidate and passing final tests. Malformed receipts, external blockers and infrastructure errors fail closed. Inspect `.sandcastle/runs/<run-id>/state.json`, result.json and round receipts/tests. State writes are atomic; state includes controller PID, current worker pane, phase, round and candidate. Runs/outputs are ignored; source/prompts/schemas/lockfile are tracked.

Herdr badges are only display hints. Verify actual processes in the recorded worker/controller panes and their descendants; receipt presence alone does not prove exit. Finished worker panes close only after a shell-only process check. The supervisor registers per-controller result-or-exit notifications and closes the owned controller tab after collecting results and confirming all children have exited. The runner retains candidate worktrees for supervisor verification rather than entering unbounded close(). A failure may intentionally leave an active child for safe supervised cancellation; never start a duplicate blindly.

The runner makes no GitHub writes and does not merge. The authorized outer supervisor verifies the accepted SHA, merges/pushes and closes only fully satisfied tickets. Current prompts scope #3/#9 to offline code correction. Code acceptance does NOT satisfy live source proof, clear incident/accounting holds, authorize retention scheduling, or enable exports. Both acquisition tickets remain open until their separate live gates are actually satisfied.

## Recovery

Resume from the preserved candidate commit with a fresh unique branch or an explicitly matching existing branch, complete prior findings and the same review base. Never reset counters or discard uncommitted work. Do not use old ad-hoc runners under ~/.hermes/need-radar-supervision for new implementations.
