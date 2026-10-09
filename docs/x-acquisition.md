# X acquisition (ticket 9)

Status: candidate corrections and synthetic/offline verification only; broader live acquisition remains **UNVERIFIED**. One existing partial X artifact has been independently inspected, but it does not establish live-route authorization, account access, source-use rights, or retention rights. The candidate code path is pinned to Treg's `anyapi.x.search.posts` direct-call route.

## Verified public route facts

Checked 2026-10-07 against the [Treg endpoint catalog](https://treg.to/catalog/endpoints/anyapi.x.search.posts) and [Treg protocol](https://treg.to/llms.txt):

- `POST /call/anyapi.x.search.posts` accepts `query` (required), `cursor`, `limit` (1–50), and `queryType` (`Latest`, `Top`, `Photos`, `Videos`). Search dates are passed inline in the query as `since:YYYY-MM-DD until:YYYY-MM-DD`.
- The catalog documents results under `output.data.items`, with root-level `found`, `provider`, `costUsd`, and `source`; the collector accepts `found` at the root or inside `output` and requires stable post IDs, text, and `createdUtc` timestamps.
- The catalog currently lists USD 0.00075 per successful call. The provider response's `costUsd` can differ; persistent accounting uses only Treg's `X-Treg-Cost-Micro` header and stores provider-reported cost separately.
- Treg documents `X-Treg-Route-Max-Cost` as the hard ceiling for direct `/call/` requests: it rejects a request with HTTP 402 when the reserved charge would exceed the header. Direct calls have no default ceiling. Every attempt sends the ticket's remaining USD allowance as this header.
- Treg may serve a direct endpoint through an overflow relay and identify it with `X-Treg-Served-Via: overflow:<name>`. The collector records the header and stops without accepting the response or requesting another page if overflow occurs. The live `internal_source_policy` gate must verify the account's existing overflow/source policy; changing account settings is outside this authorization.

These are public documentation claims, not verification of this account, source rights, the actual billed result, or provider response behavior. Those remain execution gates. No credential file was read during implementation.

## Limits and evidence

- Ticket 9 state persists at `/home/ubuntu/.local/state/need-radar/ticket-9/`; recordings are stored beneath its `recordings/` directory. State and recordings use private directory/file modes, counters are cumulative, and there is no budget reset option.
- Each request reserves the remaining dollar balance before dispatch. The route cap is sent on every attempt. Every successful HTTP response, including an empty or invalid response, counts toward 25; all reported charges, including failed attempts, count toward USD 0.25. Unknown charge/outcome pauses later acquisition.
- Approval inputs must provide the exact query, date window, route/provider, source policy, and request limits; the collector supplies no retrieval seeds. Every execution gate and an independent review matching the collector SHA-256 are required before credential loading.
- Every recording also retains the approval ID/provider and resolved limits. Gate evidence is required for approved query/window, account eligibility, source rights, route schema, billing, route cost cap, provider limits, retention/removal, and internal source policy.
- Responses are bounded to 1 MiB and a total live request/response deadline, then sanitized before private persistence. Recordings preserve bounded response evidence, approval/policy evidence, collector hash, request/window/cursor, limits, validation/exclusion reasons, actual Treg charge/call ID, served-via metadata, and coverage.
- For old AnyAPI legacy recordings only, the `retention_and_removal` gate must specify `retention_seconds` from 1 through 604800 and `derived_removal_verified: true`. The legacy collector enforces the lesser of this approved source/provider duration and seven days, cleaning expired, malformed, and interrupted pending recordings before collection (including unresolved-attempt returns) and refusing replay after expiry.
- For old AnyAPI legacy recordings, cleanup is invocation-driven; no collector run means expiry-time deletion is not guaranteed. Before legacy activation, an authorized operator must arrange periodic deletion for these recordings and remove retained copies/derived exports under the same or stricter deadline. The legacy collector does not schedule cleanup or manage copies outside its directory.
- Cursor continuation is bounded by the approved page/item/attempt limits, ticket-wide request/spend ceilings, cycle detection, and transient retry limits. A missing cursor is not treated as exhaustion; an explicit null cursor still does not prove complete search coverage. X search/reply samples do not establish complete conversations.
- `createdUtc` is checked against the UTC half-open interval `[since 00:00, until 00:00)`. Out-of-window items are excluded and counted in validation. Provider search completeness, date-boundary semantics, and freshness remain unverified.

## Acceptance and cumulative review checklist

Code-level findings from the implementation reviews are addressed in this candidate; this is synthetic/offline code evidence only.

- [x] Dispatch fails closed unless exact approved inputs, all execution gates, and a current independent review are present; external gate facts still require independent verification before live use.
- [x] Deterministic bounded collection validates IDs, text, timestamps, half-open time windows, pagination, retry bounds, and stop conditions against synthetic responses; live search relevance and provider behavior remain unverified.
- [x] Permitted sanitized response evidence and request/policy/cost provenance are retained privately and replayed offline, with output consistency checked even when billing is unknown.
- [x] Coverage reports distinguish bounded search/reply samples from complete conversation coverage.
- [x] Runnable offline end-to-end checks are documented below; observed result on 2026-10-07: 47 X-slice tests and 66 repository tests passed, and `compileall` completed successfully. Synthetic/offline proof is not live verification.
- [x] Redaction precedes persistence/export; test-process and CLI subprocess network guards prevent live calls during checks.
- [ ] One partial artifact is reviewed, but broader route behavior, relevance, account rights/eligibility, billing rules and cost-cap enforcement, retention terms, and collector replay remain unverified; do not activate.

- [x] Review 1 #1: the reusable collector enforces current review and canonical live state/recording paths; only explicitly marked offline transport can use caller-supplied paths.
- [x] Review 1 #2: tests deny network access in the test process and CLI children; the separate issue #3 incident hold remains documented below.
- [x] Review 1 #3: for old AnyAPI legacy recordings, approved shorter retention is enforced, malformed/interrupted artifacts are cleaned before collection including paused returns, and replay refuses expiry. Cleanup remains invocation-driven; an authorized idle-period deletion process is still required before legacy activation.
- [x] Review 1 #4 and Review 2 #1: signed URLs, fragments, credential-bearing URL fields, and complete colon/assignment Authorization and Cookie payloads are sanitized before persistence.
- [x] Review 1 #5: reservation/state replacement fsyncs the containing directory, including newly created state directories.
- [x] Review 1 #6: bounded sanitized response and exclusion evidence, approval/policy lineage, collector version, and transformation diagnostics are retained privately and replay-validated.
- [x] Review 1 #7: response bytes and total elapsed time are bounded; responses close and failed consumption preserves the reservation.
- [x] Review 1 #8: collection rejects an approved query that sanitization would change.
- [x] Review 1 #9 and Review 2 #5: acquisition failures remain failures when spend or attempt limits stop retry; the summary preserves both the error and limiting stop reason for nonzero CLI status.
- [x] Review 2 #2: replay stdout contains counts and allowlisted reason codes only; source-item evidence stays in the private expiring legacy AnyAPI recording.
- [x] Review 2 #3: offline collection requires a caller-supplied token marked with the `synthetic-` prefix and never loads the production credential file.
- [x] Review 2 #4: transport, billing, and redacted-cursor failures replay with the same recorded validation result.
- [x] Review boundaries #1: replay reconstructs and checks normalized outputs against retained response evidence before returning billing-error precedence, including missing-billing recordings.
- [x] Review boundaries #2: empty and whitespace-only post identifiers are excluded with retained validation evidence; successful HTTP responses still count toward the acquisition ceiling.
- [ ] Review 1 #10 and Review 2 #7 — **activation blocked**: independently verify exact query/window, provider account eligibility and managed credentials, schema and source/retention rights, success/failure/retry billing and enforceable per-request ceiling, overflow/substitution policy, canonical cumulative state and reservations, unknown-outcome reconciliation, and absence of competing collectors. Review the existing partial artifact and obtain any additional authorized evidence needed for relevance, pagination/window behavior, sanitized provenance, and network-denied replay. Approval flags, references, catalog claims, code hashes, synthetic tests, and `network_requests: 0` do not establish these facts.
- [ ] Review 2 #6 — **issue #3 activation hold**: retain the separate issue #3 `docs/reddit-acquisition.md` incident record; authorized provider/account evidence and reconciliation of its actual outcome/charge are still required. No charge or credential exposure is independently established here. This ticket does not edit or clear that hold.
- [ ] Review 1 #3 — **legacy idle deletion procedure**: arrange authorized periodic deletion for old AnyAPI legacy recordings and retained derived copies before legacy activation. Invocation-driven cleanup does not guarantee expiry while idle.

## Offline verification

Run the standard-library synthetic suite with network denial inherited by CLI subprocesses (no provider calls, credential reads, or live-state access):

```sh
TMPDIR="$HOME/.hermes/cache/scratch" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_collect_x.py' -v
```

The test audit hook denies socket/DNS/bind/connect events and blocks credential/live-state paths, including subprocesses. Fixture post IDs/text/timestamps and all provider responses are synthetic; passing tests are not live verification.

An independent read-only review of one existing official-route X artifact on October 8, 2026 found HTTP 200, ten unique IDs with nonempty text and in-window timestamps, and a recorded charge of 50,000 micro-USD (USD 0.05) matching its accounting evidence. A continuation cursor means this is partial first-page coverage, not a complete search. The private artifact was mode 0600 in a mode-0700 directory and is retained until the owner revokes retention; its original expiry is historical metadata only. Never pass this artifact to legacy replay or expiry cleanup. Downstream source-use rights remain unverified. The review parsed stored evidence only: it made no request, performed no collector replay, and changed no files.

Replay one old AnyAPI legacy recording without network access; never use this legacy command on the official-route #9 artifact:

```sh
python3 scripts/collect_x.py replay --recording /private/path/to/recording.json
```

The legacy command reports `network_requests: 0` and refuses/deletes expired recordings. Inspect a non-default state directory without touching live ticket state with:

```sh
python3 scripts/collect_x.py status --state-dir /private/path/to/test-state
```

## Live command for supervisor review only

Do not run until every live prerequisite is independently evidenced and an independent budget/secret review is approved for the exact current collector hash. The seven-day maximum and expiry rules apply only to old AnyAPI legacy recordings; retain the existing official-route #9 artifact privately until the owner revokes retention, with its original expiry as historical metadata only and never as authorization for legacy replay or deletion. This code correction did not inspect credentials, live counters, or provider/account settings and made no live request; the existing partial artifact does not authorize another, and broader acquisition remains **UNVERIFIED**.

The private approval JSON must contain `approval_id`, fixed `provider` (`treg-managed-anyapi`) and `route` (`anyapi.x.search.posts`), exact `query` without inline `since:`/`until:` dates, `query_type`, `window` (`since`/`until` ISO dates), `internal_source_policy`, validated `limits`, and all these gates: `approved_query_window`, `provider_account_eligibility`, `source_access_rights`, `route_schema`, `route_billing`, `route_cost_cap`, `provider_limits`, `retention_and_removal`, `internal_source_policy`. Each gate has shape `{"verified": true, "evidence": "private evidence reference"}`; `retention_and_removal` also requires `retention_seconds` and `derived_removal_verified: true`. These private assertions are not independent proof. Synthetic fixture evidence is never suitable for live approval.

Before dispatch, separately verify the exact approved query/window and retrieval inputs, account eligibility and managed-credential mode, route schema and source/retention rights, success/failure/retry billing, enforceable per-request cost ceiling, overflow/substitution policy, canonical cumulative counters/reservations, unknown outcomes, and competing collectors. Following independent code review, observed live IDs/text/timestamps, relevance, pagination/window behavior, charge reconciliation, sanitized provenance, and network-denied replay remain required. Catalog eligibility, approval flags, code review, and synthetic runs do not establish these facts.

The Need Radar issue #3 `docs/reddit-acquisition.md` record dated October 7, 2026 documents a separate accidental request with unknown billing and a deleted temporary unknown-outcome state. No independent evidence establishes its charge or credential exposure. That issue #3 safety hold remains unresolved for **issue #3** live dispatch. This issue #9 correction does not edit or clear the #3 hold and does not establish any X live facts.

The request-limit values are approval inputs, not hidden defaults: `page_size` 1–50, `max_pages` 1–25, `max_items` 1 through `page_size * max_pages`, `max_attempts_per_page` 1–3, `max_total_attempts` 1 through `max_pages * max_attempts_per_page`, `timeout_seconds` greater than 0 through 300, and `retry_delay_seconds` 0–30. The live timeout is a total wall-clock deadline across request/response; collection fails closed if it cannot safely enforce that bound. Ticket-wide success/spend ceilings remain hard-coded at 25 successful responses and USD 0.25 regardless of these per-run bounds.

The private review JSON must match the current `scripts/collect_x.py` SHA-256 and contain `ticket: 9`, `result: "approved"`, and checks `budget_reservations` and `secret_handling`. A reviewer can calculate the code hash with `sha256sum scripts/collect_x.py`.

After those gates are supplied privately, the bounded command shape is:

```sh
python3 scripts/collect_x.py collect --ticket 9 --approval-file /private/path/ticket-9-approved-input.json --review-file /private/path/ticket-9-independent-review.json
```

It uses the fixed persistent ticket-9 state path and approved credential file at runtime. Missing/stale approvals fail before credentials are loaded. Do not substitute personal cookies, reset state, change providers, broaden queries, or run this command as part of offline verification.
