# X acquisition (ticket 9)

Status: candidate corrections and synthetic/offline verification only; live acquisition remains **UNVERIFIED**. No live X request has been made. The candidate code path is pinned to Treg's `anyapi.x.search.posts` direct-call route; this implementation choice is not live-route authorization, and public route eligibility is not proof of account access, source-use rights, or retention rights.

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
- The `retention_and_removal` gate must specify `retention_seconds` from 1 through 604800 and `derived_removal_verified: true`. The collector enforces the lesser of this approved source/provider duration and seven days, cleaning expired, malformed, and interrupted pending recordings before collection (including unresolved-attempt returns) and refusing replay after expiry.
- Cleanup is invocation-driven; no collector run means expiry-time deletion is not guaranteed. Before activation, an authorized operator must arrange periodic deletion for this recordings directory and remove retained copies/derived exports under the same or stricter deadline. The collector does not schedule cleanup or manage copies outside its directory.
- Cursor continuation is bounded by the approved page/item/attempt limits, ticket-wide request/spend ceilings, cycle detection, and transient retry limits. A missing cursor is not treated as exhaustion; an explicit null cursor still does not prove complete search coverage. X search/reply samples do not establish complete conversations.
- `createdUtc` is checked against the UTC half-open interval `[since 00:00, until 00:00)`. Out-of-window items are excluded and counted in validation. Provider search completeness, date-boundary semantics, and freshness remain unverified.

## Offline verification

Run the standard-library synthetic suite with network denial inherited by CLI subprocesses (no provider calls, credential reads, or live-state access):

```sh
PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_collect_x.py' -v
```

The test audit hook denies socket/DNS/bind/connect events and blocks credential/live-state paths, including subprocesses. Fixture post IDs/text/timestamps and all provider responses are synthetic; passing tests are not live verification.

Replay one private recording without network access:

```sh
python3 scripts/collect_x.py replay --recording /private/path/to/recording.json
```

The command reports `network_requests: 0` and refuses/deletes expired recordings. Inspect a non-default state directory without touching live ticket state with:

```sh
python3 scripts/collect_x.py status --state-dir /private/path/to/test-state
```

## Live command for supervisor review only

Do not run until every live prerequisite is independently evidenced and an independent budget/secret review is approved for the exact current collector hash. The owner-approved maximum is seven-day private retention, subject to stricter provider/source terms. This correction did not inspect credentials, live counters, or provider/account settings and made no live request; acquisition remains **UNVERIFIED**.

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
