# Live activation gates

Status: NOT enabled. These are future gates, not unresolved design questions.

The proxy/Hermes interview ended with `FRONTIER_EMPTY`. No current product-design decision needs the owner's answer. Neither this checklist nor the design authorizes implementation or live operation.

## Inputs already supplied (2026-10-06)

Model: **DeepSeek V4.1 Flash**. Discord destination: **`1557157266824634469`**. These no longer need to be requested from the owner. The official model identifier, project-only mapping, and credential handoff are recorded in [runtime-selections.md](runtime-selections.md). No global Hermes settings, secrets, jobs, or channel permissions have been changed.

Treg credentials are needed at the first separately authorized authenticated/live call, not for the architecture review or public catalog inspection. Verify the selected endpoint's credential mode before requesting a provider key. Do not ask for Reddit/X credentials speculatively.

## Owner approval/configuration needed before a live pilot

| Gate | Needed later | Conservative state until supplied |
|---|---|---|
| Paid providers and models | Model is selected; serving provider/account, permitted use, model access, explicit hard spend cap and bounded-pilot authorization remain | No live paid acquisition or scheduled LLM runs |
| Data handling | Allowed source retention, model data path, telemetry destination/region and permitted payload retention | No newly authorized external telemetry export or indefinite raw-data retention |
| Discord and schedule | Channel `1557157266824634469` is supplied; verify Hermes bot access/permissions, then obtain send time and explicit enablement | No schedule or outbound message |
| Serving changes | Explicit later instruction to promote a tested extraction version | v0 remains designated serve; no auto-promotion |

The owner-supplied model and channel are recorded above. Budget numbers, credentials, send time and retention periods are deliberately not invented. Credentials belong in an approved secret store, not these documents. The already requested Hermes design interview is separate from future production workloads.

## Technical preflights AI can handle after authorization

These are factual checks, not questions for the owner to design by hand:

- Identify the exact Reddit/X endpoint and provider. Verify request/response schema, stable source IDs, parent/thread relations, pagination, time bounds, freshness, edits/deletions, quotas, error behavior and metering units.
- Demonstrate permitted live access from the Ubuntu VPS. Fixtures prove adapter behavior only; they do not establish actual access, anti-bot reliability, completeness or billing.
- Pin provider selection. Treg is a candidate, not an automatic fallback/router guarantee. Add OpenMagpie or Agent-Reach only when a measured gap warrants replacement or selective enrichment.
- Verify installed Hermes scheduling details: script path restrictions, pinned model/config, memory/tool isolation, timeouts, overlapping runs, exit-status handling and missed-run detection. CLI flags alone are not runtime proof.
- Verify deterministic and LLM spans share usable lineage, including configured export filters; verify local artifact references, redaction, safe failure behavior and external heartbeat delivery if selected.
- Render one fixture report into standalone HTML and optional PDF; inspect fonts, Chinese text, links, offline assets, page breaks and artifact identity. No custom frontend application is required.
- Validate Discord delivery with a separately approved destination, including retry/idempotency reconciliation and attachment limits. Never use a VPS-local file path as though it were an externally accessible download link.

## Required behavior when a gate fails

Fail closed for spending, credentials, permissions, external exports and posting. Preserve diagnostic state and report the specific missing prerequisite. Do not silently change providers, use personal browser cookies, enable proxy rotation, broaden retrieval, change the experiment model, or substitute shadow output.

Implementation can later use fixtures while these live gates remain closed; implementation itself still requires a separate request.
