# Issue 20: Genuine source recording validation

**Status: completed as a strictly local technical replay (October 14, 2026).** The retained Reddit and X recordings were available, private, and within their documented owner retention at replay time. Provider/source rights and live freshness remain `unverified`; this is not legal certification or live-pilot approval.

## Authorization, retention, and terms

- `docs/decisions.md` decision 75 and `docs/owner-brief.md` authorize local adapter use of the retained #9/#19 recordings until owner revocation. No revocation was present in the current issue context or inspected private retention records.
- Both artifact directories were mode `0700`; both source files were mode `0600`. The X response is an `authentic_sanitized_response`; its retained owner policy says keep for adapter use until revoked and supersedes the old seven-day automatic deletion. The response metadata was also still unexpired. The Reddit response is a completed live-provider recording with `owner_authorized_until_revoked: true`, private owner-only access, and no expiry.
- `/artifacts/` is Git-ignored. The replay and its derived serve artifacts are stored privately under `/home/ubuntu/projects/need-radar/artifacts/ticket-20/` with directories `0700` and files `0600`.
- Public terms checked October 14, 2026: [Treg Terms of Service](https://treg.to/terms), §5, assigns upstream API limits, quotas, billing, and enforcement to the upstream provider. [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms), §§2.1 and 2.4–2.5, describe a revocable API license, limit User Content use to the approved App use case, and address removals. Whether those terms apply to this recorded Treg/TikHub route and private adapter replay is `unverified`. [X Developer Policy](https://docs.x.com/developer-terms/policy), “Content compliance,” requires stored X content to track the current source state and be updated or removed when it changes. Recording freshness was not checked with a live request and remains `unverified`.
- No provider/source terms reviewed establish a prohibition on this narrowly scoped local replay; none certify permission for other use. No provider requests or remote model calls were made. #16’s rights, freshness, and activation gates remain unchanged.

## Recorded provider formats and mapping

The deterministic structural inspection emitted field names, types, and counts only; no source text or IDs were printed. The reusable evidence is `artifacts/ticket-20/replay-20261014-validated/proof.json`.

| Source | Observed response | Canonical mapping and coverage |
| --- | --- | --- |
| Reddit | HTTP 200, not truncated; `response.body.data.subredditV3.elements.edges[]` contained 8 edges: 6 usable post entries, one recommendation unit, and one ad. Usable cells included `TitleCell.title`, `PreviewTextCell.text`, and `MetadataCell.createdAt`/`detailsLink`; `pageInfo.endCursor` was present. | Decoded provider post IDs, title/preview text, timestamps, request provenance, and recording hash produced 6 canonical posts. Coverage is `partial`; no comment text or parent-thread references were supplied. No permalink passed the absolute Reddit URL check. Edited/deleted state remains unknown. |
| X | HTTP 200; `response.data[]` had 10 records with `id`, `created_at`, `text`, `conversation_id`, and `edit_history_tweet_ids`; one record also had `article.title`. Five `referenced_tweets` entries comprised two `replied_to`, one `quoted`, and two `retweeted`; `meta.next_token` was present. | All 10 canonical records retained IDs, timestamps, thread IDs, two reply links, article title, request provenance, and recording hash. Coverage is `partial`; the next page is unknown. Deletion state remains unknown, and no URL field was supplied. Quote/repost references are not represented in the current canonical relationship fields. |

Both recordings yielded 16 canonical records and passed `validate_canonical_evidence`. All records retained source-qualified identity, supplied timestamps and discovery lineage. The local normalization fake returned only a fixed placeholder through the shared source-normalization prompt and adapter. The v0 serve run loaded the frozen snapshot, passed all 16 selected items to `execute_extraction`, and accepted a local fake `[]` response; the canonical report says `no_findings` and contains no source text. That result is only an execution-seam check.

The adapter correctly reports partial coverage rather than completeness. Reddit coverage reports 6 inputs, 6 normalized returns, zero omitted records, and 18 field warnings (edited status, deleted status, and a canonical URL are unavailable for each post). X coverage reports 10 inputs, 10 normalized returns, zero omitted records, and 20 warnings (deleted status and URL unavailable for each record). These warnings are preserved in the private `source-normalization.json`; they do not fabricate values or erase otherwise valid records. Future requirements to preserve these URLs or X quote/repost edges should be handled as a scoped #4 follow-up.

## Offline guards and reproducibility

The replay process and serve subprocess both verified socket creation, credential-path access, and canonical live-state access were denied. The child inherited only a clean environment with Hermes `TMPDIR`; it used the existing CLI and its frozen snapshot, prompt, model-response, and report stages. Input hashes were checked unchanged after replay. The proof records zero remote calls and the report/snapshot/prompt hashes.

Verification: `TMPDIR=/home/ubuntu/.hermes/cache/scratch npm test` passed (278 tests), and `npm run typecheck:sandcastle-workflow` passed. The focused adapter suite passed 10 tests; the three date-sensitive subprocess checks also passed with deterministic test-only clocks.

Run from the repository root, using the retained paths supplied in the #9/#19 handoff:

```sh
TMPDIR=/home/ubuntu/.hermes/cache/scratch PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests \
python3 scripts/replay_issue20.py \
  --reddit-record "$REDDIT_RECORD" \
  --x-record "$X_RECORD" \
  --output "/home/ubuntu/projects/need-radar/artifacts/ticket-20/replay-$(date -u +%Y%m%dT%H%M%SZ)"
```

Set `REDDIT_RECORD` and `X_RECORD` in the local shell to the corresponding owner-private #19 and #9 response files before running the command; their filenames are intentionally omitted from tracked documentation.

The script refuses inputs without the matching owner-retention evidence or private permissions, writes only to a private directory outside the worktree, and prints structural evidence without post text or source IDs. Recheck the current owner issue/retention state before each replay; provider rights, X content freshness, complete pagination, Reddit comment enrichment, URLs, and quote/repost relationship support remain follow-up or live-gate questions.
