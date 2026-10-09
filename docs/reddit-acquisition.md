# Reddit Acquisition Slice

## Status and boundary

This is the implementation record for Need Radar issue #3. The collector is deterministic, standard-library-only, and source-specific. It does not install dependencies, use a browser/agent loop, select fallback providers, or invoke downstream LLMs.

Implementation and bounded-acquisition authorization are recorded by the owner. For issue #3, provider/account eligibility, source rights, current billing enforcement, approved communities/window, and deletion enforcement remain unverified. No issue #3 account, credential, canonical state, seed, or recording was inspected or exercised in its offline checks. A separate issue #19 artifact is documented below. Test approvals and credentials are synthetic and stay in disposable temporary directories.

**Safety hold (October 7, 2026; issue #3):** One path-guard test accidentally used the real default HTTP transport with a synthetic placeholder token. It sent one request to Treg, received no billing-cost evidence, and left an unknown outcome in temporary test state that was removed by test cleanup. No owner credential or real ticket state was read or modified. Its actual outcome and charge remain unknown, not zero. The collector preserves this incident as `need-radar-issue-3:unknown-outcome-request` in newly initialized or migrated private ticket state and blocks live acquisition before credential loading. Normal request reconciliation does not clear the historical hold.

**Live status: unverified and blocked; safety hold remains active.** Offline tests cannot establish live route behavior, eligibility, permission, billing, deletion rights, or the status of the earlier attempt. They do not establish zero live attempts or zero cost. The tests deny socket activity and access to real credential and ticket-state paths, including in the help subprocess. The direct-connection regression uses a stub resolver and is stopped before socket creation; no collector transport or HTTP request is run.

**Offline sanitizer update (October 8, 2026; issue #3):** The supported boundary is ordinary Reddit JSON fixtures plus the documented HTML-encoded URI-userinfo, raw-string-style assignment, and YAML literal/folded credential regressions. Source strings, dictionary keys, and URL components use bounded normalization before sensitive classification and known-secret removal: URL/HTML decoding is limited to 16 rounds, input to 8 MiB, nested URL processing to 16 levels, and JSON sanitization to 32 levels. This is not a general Python or YAML parser. If a sensitive value or key is malformed, ambiguous, or exceeds those limits, the affected field is withheld as `[REDACTED_WITHHELD]`; collection and replay record `response_sanitization_failed` and `processing_complete: false` while retaining safe sibling fields and request/accounting lineage. Runtime credentials are not written to source recordings. Valid opaque pagination cursors are passed to the next request unchanged, while persisted copies are sanitized. The historical unknown-charge hold remains active.

This change certifies offline code only for issue #3. The October 7, 2026 incident remains unresolved and its seven-day recording-retention control is unchanged. The separate issue #19 smoke below does not clear that hold or independently verify account/source permissions, approved seeds/window, budget reconciliation, or idle deletion enforcement.

To clear the historical hold in a later, separately authorized activation, the supervisor must use actual provider/account evidence to determine the request outcome and actual charge; update the private canonical state under its ticket lock; add that actual charge to `spent_micro_usd` and increment `successful_requests` only if the evidence establishes success; and update `incident_hold` with the same incident reference, `status: "reconciled"`, the evidence-backed `outcome`, actual integer `charge_micro_usd` (including zero only when proven), an `evidence_ref`, and `reconciled_at`. The collector's ordinary `reconcile` command handles only its own persisted pending request and cannot resolve this historical incident. Do not use an acknowledgement, a free-text claim, or an assumed zero charge. Review the real manifest evidence and establish authorized deletion enforcement before any issue #3 live run; this correction does not perform those steps or mark acquisition complete.

## Cumulative review criteria

This matrix carries forward the initial and resumed independent findings. “Code fixed” means the local regression is implemented; it does not clear a live gate or establish external evidence.

| # | Criterion | Code status | Separate live status |
|---|---|---|---|
| 1 | Persist the unknown-outcome incident hold and block before credentials/dispatch. | Fixed; hold remains mandatory in new and migrated state. | Historical outcome and charge are still unknown and unreconciled. |
| 2 | Redact URL components, embedded and multiply encoded nested URLs, sensitive dictionary keys (including camel-case `clientSecret`), escaped credential assignments, folded authorization/cookie headers, and malformed quoted URL userinfo without leakage or key loss. | Fixed; bounded normalization handles multiply JSON-escaped labels/delimiters, Unicode-escaped labels, HTML-quoted values, non-HTTP URI userinfo, and multiline triple-quoted assignments in persisted collection and legacy-replay regressions. | No live artifacts were inspected. |
| 3 | Inherit earliest source expiry for replay and run summaries. | Fixed; derived expiry cannot extend its source. | Invocation cleanup is not an idle-time deletion guarantee. |
| 4 | Require explicit synthetic credentials and guard tests/subprocesses from live paths/network. | Fixed in the collector and test guards. | No production credential was read. |
| 5 | Restrict comment targets to valid post permalinks in approved communities. | Fixed; post IDs and decoded dot-segments are validated before dispatch/reservation. | Endpoint/provider permissions remain unverified. |
| 6 | Exclude the primary checkout and every associated worktree from private artifact locations. | Fixed for execution from either primary or worker checkout. | No production recording path was accessed. |
| 7 | Remove abandoned atomic-write files and expire retained request parameters without clearing accounting holds. | Fixed in invocation cleanup and state expiry. | An authorized idle-time deletion mechanism remains required. |
| 8 | Persist malformed-payload failures and reject malformed expiry metadata safely. | Fixed; collection and replay share feed timestamp validation, retain overflow-safe expiry cleanup, and preserve recorded validation errors. | No live response was used. |
| 9 | Fail replay when no valid evidence remains and validate replay envelopes and dispatch/accounting outcomes structurally. | Fixed; recordings remain marked incomplete until payload validation finishes; replay blocks interrupted accounting/validation and independently rejects charges above each recorded request ceiling. | Synthetic replay is not live acceptance evidence. |
| 10 | Record verified overcharges and permanently stop spending after a limit breach. | Fixed; actual charge is retained and the breach blocks later dispatch. | No provider billing event was reconciled in this correction. |
| 11 | Reconcile the historical request from actual provider/account evidence. | Not a code-only action; the enforced hold remains active. | Open; supervisor evidence and canonical-state reconciliation are required. |
| 12 | Substantiate manifest claims for approved seeds/window, schemas, eligibility, permissions, billing ceilings, fallback, and retention. | Manifest assertions remain fail-closed but are not treated as proof. | Open; evidence review is required before dispatch. |
| 13 | Establish authorized expiry enforcement and bounded observed end-to-end acquisition/replay proof. | Invocation cleanup is implemented; it does not guarantee deletion while idle. | Open; one partial issue #19 artifact exists, but broader Reddit acquisition and X (#9) remain unverified. |
| 14 | Validate comment entries, nested reply items, and pagination fields before counting or replaying them. | Fixed; malformed structures persist a lineage-linked validation failure, stop collection, and remain failed in replay. | Provider schema and permissions remain unverified. |
| 15 | Bound billing-header parsing and retain an unknown-charge hold for malformed or oversized values. | Fixed; conversion errors become a structured unknown-billing failure, omit malformed raw values from recordings, and preserve the reservation/accounting hold through replay. | No provider billing event was checked. |
| 16 | Preserve exact valid feed/comment cursors for dispatch while sanitizing their recorded copies; reject unencodable cursors before another reservation and preserve the failure in replay; report empty comment continuations as incomplete coverage at every nesting level. | Fixed; valid opaque cursors remain unchanged in dispatch, while feed and nested comment cursors must be UTF-8 encodable. Empty primary cursors use nonempty alternatives; unusable top-level and nested continuations are recorded as incomplete. Invalid cursors mark the preceding response recording and are retained by offline replay without another paid reservation. | No provider cursor was inspected. |
| 17 | Release a settled known-charge reservation when the provider omits its call reference, without clearing the reconciliation hold or double-counting. | Fixed; known spend remains recorded, the monetary reservation is released, and reconciliation clears the hold without adding the charge or success again. | No provider billing event was reconciled. |
| 18 | Bound nested JSON parsing in feed data and preserve malformed evidence as structured collection/replay failure. | Fixed; nested parsing enforces a depth limit and replay retains lineage-linked validation failure instead of raising `RecursionError`. | No live response or recording was used. |
| 19 | Normalize Unicode/percent and JSON-escaped slash encodings before secret replacement and sensitive-key classification; fail closed on residual dictionary-key encodings. | Fixed; persisted regressions cover multiply escaped runtime tokens and dictionary labels, escaped URL delimiters, encoded query labels, over-limit dictionary keys, settled request counts, sanitized failure recordings, and offline replay. | No live credential or provider response was used. |
| 20 | Redact standalone and unterminated PEM private-key blocks, including when replaying legacy recordings. | Fixed; persisted regressions cover complete and unterminated blocks, and replay sanitizes older recordings in place while reporting the transformation count. | No live recording was read or rewritten. |
| 21 | Redact multiply JSON-escaped credential assignments, multiply escaped Unicode labels, and HTML-quoted values with spaces before persistence and replay. | Fixed; offline persisted-artifact regressions check three/four JSON encoding layers, escaped labels, HTML entities, and fail-closed over-limit HTML depth across collection and legacy replay. | No live artifacts were inspected. |
| 22 | Normalize HTML-encoded dictionary/query labels before sensitive-key classification; fully redact quoted credentials with escaped newline, tab, or carriage-return delimiters across nested JSON string encoding. | Fixed; persisted collection/replay checks cover encoded object/query labels, two-layer JSON credentials, and preserved safe trailing text. | No live artifact or provider response was inspected. |
| 23 | Normalize mixed HTML/percent encoding before known-secret replacement and URL classification; fail closed when bounded decoding leaves encoded content. | Fixed; persisted synthetic collection and legacy-replay regressions cover mixed encodings in prose and a URL host, plus over-limit entity depth. | No live artifact or provider response was inspected. |
| 24 | Redact credential-bearing URI userinfo independently of acquisition schemes, including percent-encoded database/cache credentials in ordinary source text. | Fixed; persisted collection and replay regressions cover PostgreSQL, Redis, custom URI schemes, and encoded userinfo. | No live artifact or provider response was inspected. |
| 25 | Redact complete multiline triple-quoted credentials, including unterminated values, without truncating following safe text after a valid delimiter. | Fixed; bounded quote scanning is exercised for single- and double-quote triple delimiters through collection and replay, including safe tails. | No live artifact or provider response was inspected. |

## Issue #3 acceptance checklist

| Criterion | Offline code acceptance | Live/supervisor evidence |
|---|---|---|
| Fail closed on missing route, permission, seed/window, billing, limit, or retention approval. | [x] Manifest preflight validates required claims and evidence references before credentials or requests. | [ ] Review actual evidence; manifest strings alone are not proof. |
| Deterministic bounded feed/comment collection with pagination, bounded retries, and auth/limit stops. | [x] Exercised with synthetic transports and persistent budget tests; no provider fallback or browser loop. | [ ] Endpoint schemas, account eligibility, permissions, and enforceable charges remain unverified. |
| Private sanitized response/provenance recordings and network-free replay. | [x] Persistence, redaction, lineage, expiry, replay validation, and billing uncertainty have offline regressions. | [ ] Confirm provider/source recording and removal rights; establish idle-time deletion enforcement. |
| Honest capability and coverage reporting, including incomplete/truncated/billing-unknown cases. | [x] Reports distinguish supported/partial/failed/blocked and preserve failures without claiming complete acquisition. | [ ] A successful synthetic response is not live acquisition evidence. |
| Runnable end-to-end check with an observed result. | [x] 120 focused Reddit tests and 188 total offline Python tests pass; synthetic feed/comment pagination, bounded sanitization and withholding, accounting, cursor lineage, legacy-recording replay, and collection-to-recording-to-replay run under Hermes-TMPDIR network and protected-path guards. | [ ] No live request was part of this issue #3 offline check; provider/account verification remains outstanding. |
| Observability, provenance, redaction, and untrusted-source handling. | [x] Request inputs, configuration, outputs, billing/error evidence, lineage, and validation remain private and redacted before persistence. | [ ] Live retention terms and observed end-to-end evidence remain supervisor-owned. |

On October 8, 2026, `npm test` passed (24 orchestration tests and 188 offline Python tests), `npm run typecheck:sandcastle-workflow` passed, Python compilation passed, and the public CLI help check passed. The focused Reddit suite passed all 120 tests. The candidate worktree had no `node_modules`; `npm test` used a temporary symlink to the already-installed primary-checkout dependencies, removed at command exit. No packages were installed. `TMPDIR` pointed to Hermes scratch; unittest audit hooks denied socket activity and access to canonical credential/state paths, including subprocesses. All fixture outcomes are synthetic/offline; the unresolved historical request and acquisition authorization gates remain active.

No canonical state or credential files were changed. The separate issue #19 artifact does not reconcile the issue #3 historical incident or establish its remaining live prerequisites.

## Separate issue #19 read-only smoke (October 8, 2026)

The already-completed Reddit request returned HTTP 200, cost 1,000 micro-USD, and contained eight feed elements: six posts with IDs, titles, preview text, and `MetadataCell.createdAt` timestamps in-window, one advertisement, and one recommendation. The response record SHA-256 is `08a752616c8a96f5ad8d0563f1e0019f595847ec8150ab221611d9e0c5e301c4`. The original `run-89957b16f66a438993541740d6447c8a.json` remains unchanged and reports zero posts in-window and eight missing timestamps because the issue #3 collector does not map these cell fields. Do not rerun acquisition or overwrite that report.

The separate `scripts/check_reddit_artifact.py` check reads only an explicit private record path; it is not a production parser and does not invoke collector replay. Reproduce against the existing record with:

```sh
TMPDIR="$HOME/.hermes/cache/scratch" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests \
  python3 scripts/check_reddit_artifact.py /private/path/to/record-b480f8c036d6455e8d23756b343bca15.json
```

Run its one synthetic subprocess check with:

```sh
TMPDIR="$HOME/.hermes/cache/scratch" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests \
  python3 -m unittest discover -s tests -p 'test_reddit_artifact_smoke.py' -v
```

The test subprocess guard denies network, credential, and canonical-state access. The supplied record is private and owner-revocable with no automatic expiry recorded; downstream source-use rights remain unverified. This smoke does not authorize another live request or change collector/schema controls.

## Public route evidence

Public provider and Treg documentation was fetched on October 7, 2026; none of these checks establishes account eligibility or source/retention rights.

- Treg documents `X-Treg-Route-Max-Cost` as a hard per-call ceiling on direct `/call/` requests. If the reserve exceeds the header, Treg refuses with HTTP 402 and no charge; direct calls have no default ceiling. The collector persists a reservation before dispatch and sends the lesser of the route's approved maximum charge and remaining ticket balance. [Treg API documentation](https://treg.to/llms.txt)
- The Treg catalog currently lists TikHub Reddit feed at USD 0.001 per successful call and ScrapeCreators comments at USD 0.00188 per call. The catalog rate metadata says it was checked July 28, 2026, so it is not current account-billing evidence. A private manifest must attest route-specific billing and enforceable maximum-charge evidence before live use. [Feed catalog entry](https://treg.to/catalog/endpoints/tikhub.x.reddit-app-fetch-subreddit-feed) · [Comments catalog entry](https://treg.to/catalog/endpoints/scrapecreators.x.v1-reddit-post-comments)
- TikHub documents `subreddit_name`, `sort`, `after`, and `need_format`; `NEW` is sent for bounded recent-feed retrieval. Its documented default response language is `en-US`; the collector omits a language parameter, records this provider default as **not owner-approved**, and does not present it as an owner preference. [TikHub feed specification](https://docs.tikhub.io/369454694e0.md)
- ScrapeCreators documents GET `/v1/reddit/post/comments`, one opaque cursor per request, nested reply cursors, and `trim`. The collector sends one returned cursor at a time and requests the untrimmed response. [ScrapeCreators comments specification](https://docs.scrapecreators.com/v1/reddit/post/comments.md)

The feed catalog's sample is truncated and its exact response field mapping has not been confirmed against a permitted live response. The comments documentation provides examples, but successful HTTP or empty responses do not establish complete comment-tree coverage. Runtime schema failures are recorded and stop the slice visibly.

## Runtime controls

- Live ticket state and recordings are pinned outside the primary checkout and its worktrees at `/home/ubuntu/.local/state/need-radar/ticket-3/`; live overrides are blocked so a custom path cannot reset a ticket's cumulative counters or retention. `state.json` and `state.lock` persist across runs; a legacy state lacking the incident field is migrated without changing its counters and remains held. There is no budget reset or `--new-run` option. Synthetic tests may use isolated temporary paths.
- The ticket stops at the first of 25 successful acquisition responses or USD 0.25 cumulative charge. Feed pages, comment pages, pagination and successful retries all count; failed responses do not count as successes, but any reported charge counts. Attempts are counted separately.
- Before every request, the collector locks state, reserves one success slot and that route's verified maximum charge (or the smaller remaining balance), saves the reservation, and sends it as `X-Treg-Route-Max-Cost`. Unknown charge or transport outcome keeps the reservation pending and blocks later calls until local reconciliation. Authentication/permission failures stop immediately. Transient responses get at most two retries, each separately reserved and accounted.
- Request limits are shared across feed and comments. The required explicit `--max-feed-pages`, `--max-comment-pages`, and `--max-posts` arguments provide tighter per-run bounds; no communities or time windows are defaults. The CLI requires each seed and UTC window to match the private manifest exactly.
- Recordings, run reports, and replay reports stay outside the repository in private directories (`0700`) and files (`0600`). Bodies are sanitized before persistence, including credential-shaped fields, known runtime token values, signed URL parameters, and URL fragments. Only allowlisted response headers are retained. Retrieved text is untrusted data, never instructions.
- Each request record stores provider, endpoint, sanitized parameters, request ceiling, attempt, HTTP status, response byte count/body, allowlisted billing headers/call ID, provider charge evidence, cursor-bearing inputs/outputs, timestamp, and run/trace/span lineage. A private run report links all records and stores resolved inputs, counts, validation, errors, coverage reasons, and budget state.
- Recordings expire at their approved retention boundary (up to seven days). Run and replay summaries inherit the earliest contributing source expiry and cannot extend it. Expired or invalid recordings and abandoned private `.pending-*` files are removed on collector/replay invocation; atomic writes and cleanup share a directory lock so an active temporary write is not removed. Pending request parameters are removed at their recorded expiry while counters, reservations, and incident holds remain. Cleanup is invocation-driven and does not guarantee deletion while idle. Authorized deletion enforcement covering recordings, derived copies, and crash leftovers is still required before live retention begins. Copies outside the managed directory are not deleted by this collector.
- Offline replay reads only private, unexpired `record-*.json` recordings, emits a private report, and has no acquisition transport. Missing recordings block; they never trigger a network fallback.

## Approval manifest

The default manifest is `/home/ubuntu/.local/state/need-radar/ticket-3/approval.json`; it must be private and outside the repository. No manifest is created by this implementation. Before a live call, the manifest must contain verified evidence for:

- Ticket/source identity, source permission, exact CLI seed list, and exact UTC window.
- Both fixed endpoint IDs, each endpoint's schema/permission/billing unit, its maximum charge, and Treg cost-ceiling enforcement.
- Treg account eligibility, managed-credential mode and token type, disabled overflow/fallback, and absence of provider-owned credentials.
- Ticket ceilings of 25 successful calls and USD 0.25, plus seven-day permitted recording retention and derived-artifact removal.

The collector validates these exact manifest fields: `source_permission`; `seeds`; `window`; `routes.feed` and `routes.comments` (each with `id`, `schema_verified`/`schema_evidence`, `permission_verified`/`permission_evidence`, `billing_verified`/`billing_unit`/`billing_evidence`, `max_charge_verified`/`max_charge_micro_usd`/`max_charge_evidence`, and `cost_cap_verified`/`cost_cap_evidence`); `provider_account` (`eligibility_verified`, `credential_mode`, `token_type`, `overflow_disabled`, `fallback_disabled`, `own_provider_credentials_absent`, `evidence`); `limits` (`verified`, `successful_requests`, `spend_micro_usd`, `evidence`); and `retention` (`recording_permitted`, `days`, `derived_removal_verified`, `evidence`). Every verification flag must be true and every evidence value nonempty. These booleans and strings are assertions, not proof: a supervisor must review actual evidence for approved communities/window, endpoint schemas and pagination, account eligibility, provider/source rights, billing unit and enforceable per-request ceiling, disabled overflow/fallback, and retention/removal rights. The local two-retry policy is an implementation bound, not an owner-approved ticket limit.

Missing or mismatched manifest fields block before credentials load. `collect --plan` checks the manifest and prints resolved seed/window/language/limit inputs without reading credentials or sending requests. Plan success means only that the recorded assertions are present; it does not independently prove them. Live calls have a 30-second wall-clock deadline enforced synchronously; environments without a main-thread deadline or with an existing process alarm fail closed rather than spawning a request thread.

## Commands

Offline checks use only synthetic fixtures and temporary state:

```sh
set -e
export TMPDIR=/home/ubuntu/.hermes/cache/scratch
export PYTHONDONTWRITEBYTECODE=1
if [ ! -e node_modules ]; then
  test -d /home/ubuntu/projects/need-radar/node_modules
  ln -s /home/ubuntu/projects/need-radar/node_modules node_modules
  trap 'rm node_modules' EXIT
fi
npm test
python3 -m unittest discover -s tests -p 'test_collect_reddit.py' -v
npm run typecheck:sandcastle-workflow
PYTHONPYCACHEPREFIX="$TMPDIR/issue-3-pycache" python3 -m py_compile scripts/collect_reddit.py tests/test_collect_reddit.py
python3 scripts/collect_reddit.py --help
```

The test-process socket and live-path guards are active in the Python test commands. Public CLI help and replay subprocesses inherit offline guards; the replay subprocess uses a synthetic recording and asserts zero network requests. Synthetic collection requires an explicitly injected test credential loader; tests do not fall back to the production credential file. The direct-connection regression uses a stub resolver and asserts the guard blocks before a socket is created; it does not run the collector transport or make a real connection. If this worktree has no `node_modules`, the conditional above reuses the already-installed primary-checkout dependency tree for `npm test` and removes the temporary symlink; it does not install packages.

**Offline result (October 8, 2026; candidate worktree):** `npm test` passed (22 orchestrator tests and 188 offline Python tests); the focused Reddit suite passed 120 tests; `npm run typecheck:sandcastle-workflow` and Python compilation passed. The guarded public replay subprocess returned a Reddit replay report with `network_requests: 0`. These checks used synthetic fixtures and temporary state; they are not integration-checkout or live-acquisition evidence. The controller must rerun integration checks before merge.

There is intentionally no safe runnable live command in this checkout: the historical incident remains unresolved and external prerequisites remain unverified. Only after the supervisor completes the evidence-backed state reconciliation above, reviews all actual manifest evidence, and establishes authorized retention enforcement may a later activation review consider this bounded command shape; replace each bracketed value only with its approved input:

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
