# Reddit Acquisition Slice

## Status and boundary

This is the implementation record for Need Radar issue #3. The collector is deterministic, standard-library-only, and source-specific. It does not install dependencies, use a browser/agent loop, select fallback providers, or invoke downstream LLMs.

Implementation and bounded-acquisition authorization are recorded by the owner. Live execution is still blocked until the private approval manifest contains verified source/provider permission, exact account eligibility, approved seeds and UTC window, route schemas/billing/cost ceilings, and recording/removal rights. No real account, credential, seed, or recording was inspected or exercised here. The test approval is synthetic and exists only in temporary directories.

**Safety hold (October 7, 2026):** One path-guard test accidentally used the real default HTTP transport with a synthetic placeholder token. It sent one request to Treg, received no billing-cost evidence, and left an unknown outcome in temporary test state that was removed by test cleanup. No owner credential or real ticket state was read or modified. Treat the attempt's charge as unknown; do not dispatch any live Reddit request until a supervisor checks the Treg call ledger and reconciles this attempt. This was not live verification and is not an acceptance result.

**Live status: unverified; safety hold remains active.** Offline tests cannot establish live route behavior, eligibility, permission, billing, or the status of the earlier attempt. They do not establish zero live attempts or zero cost. The test module now installs a process-wide Python audit hook that fails on socket creation, DNS lookup, bind, connect, and UDP send events. A regression makes a direct connection attempt with DNS stubbed to loopback and confirms the hook stops it before socket creation; no collector transport or HTTP request is run, and the safety hold remains unresolved.

## Public route evidence

Public provider and Treg documentation was fetched on October 7, 2026; none of these checks establishes account eligibility or source/retention rights.

- Treg documents `X-Treg-Route-Max-Cost` as a hard per-call ceiling on direct `/call/` requests. If the reserve exceeds the header, Treg refuses with HTTP 402 and no charge; direct calls have no default ceiling. The collector persists a reservation before dispatch and sends the lesser of the route's approved maximum charge and remaining ticket balance. [Treg API documentation](https://treg.to/llms.txt)
- The Treg catalog currently lists TikHub Reddit feed at USD 0.001 per successful call and ScrapeCreators comments at USD 0.00188 per call. The catalog rate metadata says it was checked July 28, 2026, so it is not current account-billing evidence. A private manifest must attest route-specific billing and enforceable maximum-charge evidence before live use. [Feed catalog entry](https://treg.to/catalog/endpoints/tikhub.x.reddit-app-fetch-subreddit-feed) · [Comments catalog entry](https://treg.to/catalog/endpoints/scrapecreators.x.v1-reddit-post-comments)
- TikHub documents `subreddit_name`, `sort`, `after`, and `need_format`; `NEW` is sent for bounded recent-feed retrieval. Its documented default response language is `en-US`; the collector omits a language parameter, records this provider default as **not owner-approved**, and does not present it as an owner preference. [TikHub feed specification](https://docs.tikhub.io/369454694e0.md)
- ScrapeCreators documents GET `/v1/reddit/post/comments`, one opaque cursor per request, nested reply cursors, and `trim`. The collector sends one returned cursor at a time and requests the untrimmed response. [ScrapeCreators comments specification](https://docs.scrapecreators.com/v1/reddit/post/comments.md)

The feed catalog's sample is truncated and its exact response field mapping has not been confirmed against a permitted live response. The comments documentation provides examples, but successful HTTP or empty responses do not establish complete comment-tree coverage. Runtime schema failures are recorded and stop the slice visibly.

## Runtime controls

- Live ticket state and recordings are pinned outside the repository at `/home/ubuntu/.local/state/need-radar/ticket-3/`; live overrides are blocked so a custom path cannot reset a ticket's cumulative counters or retention. `state.json` and `state.lock` persist across runs; there is no budget reset or `--new-run` option. Synthetic tests may use isolated temporary paths.
- The ticket stops at the first of 25 successful acquisition responses or USD 0.25 cumulative charge. Feed pages, comment pages, pagination and successful retries all count; failed responses do not count as successes, but any reported charge counts. Attempts are counted separately.
- Before every request, the collector locks state, reserves one success slot and that route's verified maximum charge (or the smaller remaining balance), saves the reservation, and sends it as `X-Treg-Route-Max-Cost`. Unknown charge or transport outcome keeps the reservation pending and blocks later calls until local reconciliation. Authentication/permission failures stop immediately. Transient responses get at most two retries, each separately reserved and accounted.
- Request limits are shared across feed and comments. The required explicit `--max-feed-pages`, `--max-comment-pages`, and `--max-posts` arguments provide tighter per-run bounds; no communities or time windows are defaults. The CLI requires each seed and UTC window to match the private manifest exactly.
- Recordings, run reports, and replay reports stay outside the repository in private directories (`0700`) and files (`0600`). Bodies are sanitized before persistence, including credential-shaped fields, known runtime token values, signed URL parameters, and URL fragments. Only allowlisted response headers are retained. Retrieved text is untrusted data, never instructions.
- Each request record stores provider, endpoint, sanitized parameters, request ceiling, attempt, HTTP status, response byte count/body, allowlisted billing headers/call ID, provider charge evidence, cursor-bearing inputs/outputs, timestamp, and run/trace/span lineage. A private run report links all records and stores resolved inputs, counts, validation, errors, coverage reasons, and budget state.
- Every recording/report expires within seven days of its recorded timestamp; a stricter approved provider/source retention value (1–6 days) is applied to recordings and their run/replay summaries. Collection and replay delete expired or invalid-expiry JSON files in the recording directory. Cleanup is invocation-driven: this slice creates no background timer, so a separate authorized retention process is needed if deletion must happen exactly at expiry while the CLI is not run. Any copies made outside that directory remain subject to the same deletion requirement and are not managed by this collector.
- Offline replay reads only private, unexpired `record-*.json` recordings, emits a private report, and has no acquisition transport. Missing recordings block; they never trigger a network fallback.

## Approval manifest

The default manifest is `/home/ubuntu/.local/state/need-radar/ticket-3/approval.json`; it must be private and outside the repository. No manifest is created by this implementation. Before a live call, the manifest must contain verified evidence for:

- Ticket/source identity, source permission, exact CLI seed list, and exact UTC window.
- Both fixed endpoint IDs, each endpoint's schema/permission/billing unit, its maximum charge, and Treg cost-ceiling enforcement.
- Treg account eligibility, managed-credential mode and token type, disabled overflow/fallback, and absence of provider-owned credentials.
- Ticket ceilings of 25 successful calls and USD 0.25, plus seven-day permitted recording retention and derived-artifact removal.

The collector validates these exact manifest fields: `source_permission`; `seeds`; `window`; `routes.feed` and `routes.comments` (each with `id`, `schema_verified`/`schema_evidence`, `permission_verified`/`permission_evidence`, `billing_verified`/`billing_unit`/`billing_evidence`, `max_charge_verified`/`max_charge_micro_usd`/`max_charge_evidence`, and `cost_cap_verified`/`cost_cap_evidence`); `provider_account` (`eligibility_verified`, `credential_mode`, `token_type`, `overflow_disabled`, `fallback_disabled`, `own_provider_credentials_absent`, `evidence`); `limits` (`verified`, `successful_requests`, `spend_micro_usd`, `evidence`); and `retention` (`recording_permitted`, `days`, `derived_removal_verified`, `evidence`). Every verification flag must be true and every evidence value nonempty. The local two-retry policy is an implementation bound, not an owner-approved ticket limit.

Missing, mismatched, or unevidenced fields block before credentials load. `collect --plan` checks the manifest and prints resolved seed/window/language/limit inputs without reading credentials or sending requests. Plan success means only that the recorded assertions are present; it does not independently prove the assertions.

## Commands

Offline checks use only synthetic fixtures and temporary state:

```sh
python3 -m unittest discover -s tests -p 'test_collect_reddit.py' -v
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/collect_reddit.py tests/test_collect_reddit.py
python3 scripts/collect_reddit.py --help
```

The test-process socket guard is active in both unittest commands. The direct-connection regression uses a stub resolver and asserts the guard blocks before a socket is created; it does not run the collector transport or make a real connection.

There is intentionally no safe runnable live command in this checkout: no approved seed/window or verified private approval manifest is available, and the safety hold above is unresolved. After reconciliation and independent review, the supervisor can review this bounded command shape; replace each bracketed value only with its approved input:

```sh
NEED_RADAR_REDDIT_LIVE_ACK=ISSUE-3-REDDIT-2026-10-07-USD-0.25-25-SUCCESSFUL \
python3 scripts/collect_reddit.py collect \
  --subreddit '<approved-subreddit>' \
  --window-start '<approved-UTC-start>' \
  --window-end '<approved-UTC-end>' \
  --max-feed-pages '<approved-feed-page-bound>' \
  --max-comment-pages '<approved-comment-page-bound>' \
  --max-posts '<approved-post-bound>' \
  --approval /home/ubuntu/.local/state/need-radar/ticket-3/approval.json \
  --state-dir /home/ubuntu/.local/state/need-radar/ticket-3 \
  --recordings-dir /home/ubuntu/.local/state/need-radar/ticket-3/recordings \
  --allow-live-acquisition ISSUE-3-REDDIT-2026-10-07-USD-0.25-25-SUCCESSFUL
```

Do not run that template until the private manifest passes `--plan` and the independent review approves the budget, redaction, permissions, and retention controls. A live result, if separately dispatched, must be reported as partial unless its evidence establishes otherwise; an HTTP success or zero rows is not completeness.

## Shared reuse point

This source collector deliberately does not edit the shared CLI or sibling collector. Future authorized integration can reuse the same small control logic: locked per-ticket reservations, the Treg max-cost header, fail-closed reconciliation, sanitize-before-write, private retention, and the run/record lineage envelope. No adapter framework is needed for this slice.
