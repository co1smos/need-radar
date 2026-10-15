# Bounded product cycle

The product mode is a one-shot Hermes operation. It is disabled in the checked-in example and does not create a Hermes job. Copy both JSON examples to the named private locations, keep the activation manifest disabled until the independent #16 checks are complete, and protect them with mode `0600`:

```sh
install -D -m 600 config/product-cycle.example.json ~/.config/need-radar/product-cycle.json
install -D -m 600 config/product-activation.example.json ~/.config/need-radar/product-activation.json
```

The cycle config must retain the fixed canonical aggregate ledger path and fixed request/token limits. Its output, scheduler database, and activation manifest must remain outside the repository. Real dispatch requires every activation gate to be verified with evidence. An enabled config alone does not satisfy those gates.

Runtime credentials use the existing Treg and DeepSeek loaders plus `DISCORD_BOT_TOKEN` from `~/.config/need-radar/discord.env`; do not put secrets in either JSON file.

Use the existing Hermes run and missed-run entry points with `NEED_RADAR_CONFIG` set to the private cycle config:

```sh
NEED_RADAR_CONFIG="$HOME/.config/need-radar/product-cycle.json" scripts/hermes/need-radar-cycle.sh
NEED_RADAR_CONFIG="$HOME/.config/need-radar/product-cycle.json" scripts/hermes/need-radar-missed-check.sh
```

The one-shot ledger persists reservations and refuses a second cycle after any cycle has been claimed. Do not delete or replace it to retry. The external cleanup entry point removes only expired, private product-run directories; it never removes the scheduler record or aggregate accounting ledger:

```sh
NEED_RADAR_OUTPUT_ROOT="$HOME/.local/state/need-radar/product-cycle/runs" scripts/hermes/need-radar-product-cleanup.sh
```

Cleanup expires local run data at its recorded deadline. Without `--discord`, Discord is reported as `not_requested` and any known removal identity stays in the private receipt for later reconciliation. For an explicit early removal, or to remove Discord copies during expiry cleanup, add `--run-id <32-character-run-id> --discord` or `--discord` respectively:

```sh
NEED_RADAR_OUTPUT_ROOT="$HOME/.local/state/need-radar/product-cycle/runs" \
  scripts/hermes/need-radar-product-cleanup.sh --run-id "$RUN_ID" --discord
NEED_RADAR_OUTPUT_ROOT="$HOME/.local/state/need-radar/product-cycle/runs" \
  scripts/hermes/need-radar-product-cleanup.sh --discord
```

The first command removes only that validated pilot run before expiry; the second expires eligible runs and requests one verified Discord deletion per run. Discord deletion checks the saved run, channel, bot author, message ID and delivery nonce, then makes at most one DELETE request. A timeout or permission denial is reported as unresolved, never as deleted. A minimal content-free receipt in `.product-removals/` retains the delivery identity for a later explicit invocation; unresolved receipts are kept for reconciliation. Local and Discord results are reported separately. Source text, reports, and derived run copies are removed locally. Provider-held copies cannot be inspected or deleted by this application and remain unverified.

The Hermes idle-time contract is to invoke cleanup only; it does not start a product cycle. If a separately authorized schedule is later chosen, install the wrapper under Hermes's required script directory and create a no-agent job in a paused state before explicitly activating it:

```sh
install -D -m 700 scripts/hermes/need-radar-product-cleanup.sh \
  "$HOME/.hermes/scripts/need-radar-product-cleanup.sh"
hermes cron create "every 24h" --name need-radar-product-cleanup \
  --script "$HOME/.hermes/scripts/need-radar-product-cleanup.sh" \
  --no-agent --deliver local --paused \
  --paused-reason "Awaiting explicit cleanup schedule authorization"
```

No Hermes job is created by this ticket. Expiry-only cleanup does not call Discord; `--discord` is an explicit per-invocation gate. Actual Discord deletion and schedule creation or activation require their own authorization. To disable before a run, keep `enabled` false in the operation config or activation manifest. To disable after a run, pause only the specific Hermes run/watchdog jobs using the repository's existing disable procedure; do not remove unrelated jobs. Code and example changes can be reverted with `git revert <candidate-commit>`; local private outputs and the aggregate ledger need separate, deliberate retention handling.

## Remaining #16 checks

Before enabling a real one-shot run, verify and retain evidence for: (1) current Reddit and X Treg request/response schemas, provider identity, first-page behavior, cache/overflow behavior, and cost headers; (2) account eligibility and current credentials without placing them in config; (3) source/provider rights for collection, internal model processing, and seven-day retention; (4) source billing caps and the existing Reddit/X unresolved-hold state; (5) current DeepSeek model availability, input/output pricing, data-use terms, and the account-level billing view; (6) Discord channel send permission, bot token scope, message attachment limits, and read-back visibility; and (7) operation-lock, watchdog, and cleanup readiness. The local injected proof verifies only request construction and response handling. It does not verify provider access, charges, third-party rights, schedule execution, or Discord delivery.

## Offline verification

The focused run passed 50 tests with Hermes `TMPDIR`, including a subprocess run of the injected product cycle with network, credential-file, and canonical-live-state access denied. The cleanup wrapper is also tested in a subprocess without starting a product cycle. `npm test` passed 34 Ralph tests and 318 offline Python tests. `npm run typecheck:sandcastle-workflow` passed. These checks use synthetic HTTP/service responses and do not establish live access, billing, source rights, or Discord delivery.
