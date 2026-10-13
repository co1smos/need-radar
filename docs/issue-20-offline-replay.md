# Issue 20: Genuine source recording validation

**Status: blocked (October 13, 2026).** The approved local replay cannot continue until the applicable upstream rights for the Reddit recording are resolved. The X recording is also unavailable in its documented private location. This is not a finding that either provider has denied permission, and it is not legal certification.

## Authorization and artifacts

- `docs/decisions.md` decision 75, `docs/owner-brief.md` and the current #20 issue body establish owner authorization for strictly local #9/#19 adapter use without repeat consent. No revocation is recorded in the repository or current issue context reviewed.
- The #19 private artifact metadata records owner-revocable retention (`owner_authorized_until_revoked: true`) and downstream rights as `unverified`. One genuine sanitized Reddit response recording is present in the owner-private, Git-ignored #19 artifact directory. No source text, IDs, file names, or raw payload were included in this report.
- The #9 documented `recordings/` directory is absent in this environment. The private ticket-9 metadata available locally does not contain a source response envelope. The prior #9 artifact summary does not substitute for a recording in this offline replay.
- A metadata-only check counted two JSON files under the #19 private directory, one of which is a response recording, and no JSON response recording in the documented #9 `recordings/` directory.

## Partial Reddit format check

Before the terms review was complete, one deterministic schema-only inspection of the #19 response observed an HTTP 200, non-truncated response. The body is nested under `data.subredditV3.elements.edges[]` (8 entries); each edge has a `node` with heterogeneous `cells[]`, including typed `title`, `text`, and `createdAt` fields. `elements.pageInfo.endCursor` is present. No comments/replies were present in the inspected feed response. No post text, IDs, or source values are recorded here.

The same inspected body was passed once to the existing adapter with a local empty fake-normalization response and network, credential, and live-state access denied. The adapter prepared zero canonical records: the actual body lacks its expected `partial`, `origin`, and `posts[]` envelope fields. No source IDs, timestamps, discovery origin, reply relationships, or coverage flags were validated. This confirms a concrete incompatibility for #4/follow-up, not successful provider compatibility. No further Reddit replay or parsing was performed after the terms question was identified.

## Rights check and stop

Current public terms checked October 13, 2026:

- [Treg Terms of Service](https://treg.to/terms), last updated July 22, 2026, §5 says calls through its hosted service remain subject to the upstream provider's terms, and Treg is not a party to that upstream relationship.
- [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms), §§2.4–2.5, describe a limited, revocable license for content use in the approved app use case and state that no other rights are granted or implied. Whether these terms govern the Treg/TikHub route, whether the upstream route is separately authorized, and whether this private adapter replay is within an applicable license are `unverified`.
- [X Developer Policy](https://docs.x.com/developer-terms/policy) requires stored X content to be kept current and deleted or modified when its state changes. With no X recording available, freshness and route applicability could not be checked; rights remain `unverified`.

These public terms make route and use applicability a concrete unresolved rights question; they do not establish a prohibition on this replay or permission for it. Owner authorization alone does not resolve upstream rights. No further recording parsing, adapter replay, or derived-content persistence should occur before the route/use permission is evidenced or the owner resolves the rights question. Do not acquire new data, send source content to a remote model, export or publish content, or treat this as clearing #16.

## Verification and remaining work

- `TMPDIR="$HOME/.hermes/cache/scratch" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_source_adapter.py' -q` passed (8 tests). These existing checks use synthetic inputs and fake model outputs; they exercise the frozen-snapshot/v0 path and deny network, credentials, and live state in the test process and child processes.
- Before the rights stop, one local guarded adapter probe of the Reddit response returned zero canonical records. It emitted schema/error summaries only. This incomplete probe is not acceptance evidence for provider compatibility.
- No genuine two-source replay, canonical snapshot, or v0 report was completed. Keep #20 open until upstream route/use rights are resolved, the Reddit response can be replayed within the applicable scope, and the authentic #9 X recording is available for its own retention and terms check.
