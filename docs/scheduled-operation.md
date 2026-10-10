# Bounded Hermes operation preparation

Issue #15 prepares a repository-owned bridge for Hermes Cron; Hermes remains the only scheduler. `scripts/hermes/need-radar-cycle.sh` calls the existing fixture-backed source normalizer, serve pipeline, shadow runner, and fake Discord transport. `scripts/hermes/need-radar-missed-check.sh` is an independent watchdog entry point. Both require an explicit private config at `.need-radar/scheduled.json`; the directory is Git-ignored, and a missing/disabled config does not start work. Keep the config local and uncommitted, and keep state DB, run artifacts, and private fixtures outside the repository. All fixture inputs are redacted before being copied into a run directory.

The config requires `enabled`, interval `schedule` (`interval_seconds`, `anchor_at`, `grace_seconds`), hard `budget` (`requests`, `tokens`), `timeout_seconds`, `state_db`, `output_root`, `serve_fixture`, and `shadow_fixture`. Budgets aggregate the source normalizer, serve extraction, serve judge and shadow extraction fixture usage before any pipeline process starts; missing or malformed planned usage fails before execution, and incomplete observed usage fails the operation as `budget_unverified` while preserving valid serve artifacts. The watcher writes its own record even if the main job has no record, checks report and lineage hashes when one claims success, and links the records by scheduled slot. An advisory lock rejects overlapping runs. Shadow, judging, rendering and fake delivery remain nonblocking for an already valid canonical serve report.

Run the offline proof with Hermes scratch as `TMPDIR`:

```sh
TMPDIR="$HOME/.hermes/cache/scratch" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. \
  python3 scripts/check_scheduled_operation.py
```

The check exercises fixture-backed Reddit+X normalization, fixture model outputs, fake delivery, aggregate request/token budgets, incomplete usage evidence, overlap, a terminated timeout, command failure, an independent missed-run check before any main run, linked records, corrupted output after a zero exit, a synthetic redaction marker, and judge/shadow failures in one cycle. A second complete recorded cycle injects a renderer failure through the existing CLI function seam and verifies valid canonical serve Markdown remains available. Socket, credential-file and canonical live-state access are denied in the check process and Python subprocesses. Results are synthetic/offline, not source, model, Hermes scheduling, or Discord verification.

## Hermes capability and activation boundary

Read-only installed CLI help on October 9, 2026 showed `hermes cron create --script`, `--no-agent`, `--paused`, and a script path restricted to `~/.hermes/scripts/`; `hermes cron pause JOB_ID`, `resume JOB_ID`, and `list --all` are available. The offline check repeats harmless help probes. It does not list the current profile's jobs, copy/install scripts into Hermes home, create even a paused job, or pause/resume any actual job. Hermes runtime overlap, timeout, missed-run and script-isolation behavior therefore remains unverified.

Before any pilot, the owner must separately authorize a concrete schedule, job IDs, permissions, model/source execution and budgets. Stage these wrappers under Hermes's required script directory only at that point, set `enabled` only in the private config after all gates pass, and use local-only Hermes delivery for the runner's diagnostic stdout. No live schedule or external side effect is enabled by this ticket.

The tested disable procedure uses only the two concrete job IDs (main and watchdog):

```sh
scripts/disable_need_radar_cron.sh MAIN_JOB_ID WATCHDOG_JOB_ID
hermes cron list --all
```

The script pauses both IDs and lists all jobs so an operator can confirm each is paused; it never stops the whole Hermes scheduler. The offline check substitutes a temporary fake `hermes` executable and verifies both requested IDs are paused while an unrelated fake job remains active. To restore a deliberately paused pair after the issue is resolved, resume those same IDs explicitly with `hermes cron resume MAIN_JOB_ID` and `hermes cron resume WATCHDOG_JOB_ID`. Neither procedure was run against the real profile.

External source terms, live endpoint/model compatibility, spend, credentials, delivery permissions, real schedule behavior and external retention remain separate unverified gates. No private recordings were used in this slice.
