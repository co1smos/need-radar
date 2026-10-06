# Dependency evidence and validation limits

Checked: 2026-10-06 during a documentation-only review. These are documentation/CLI observations, NOT a completed acquisition or deployment test. No live social-data request, credits purchase, scheduler creation, or dependency installation was performed.

## Installed Hermes (direct DevSpace CLI evidence)

`hermes --version`: v0.21.3 (2026.9.14), upstream 960864fd; Python 3.11.15. Do not upgrade as part of this review.

`hermes --help` confirms `--skills`, `--in`, `--resume`, `--reasoning`, `--pass-session-id`, and `-z` one-shot. The interactive bootstrap loaded `grill-with-docs` and created session `20261006_114451_8a8410`.

`hermes cron create --help` confirms `--no-agent`, `--script`, `--workdir`, `--deliver`, model/provider pinning, and `--paused`. Its help states script paths are under `~/.hermes/scripts/`; default script output is injected into an agent prompt, whereas no-agent mode delivers stdout without an LLM. Therefore a future bridge/wrapper path needs explicit setup, not an assumption that any repo script can be scheduled directly. No cron was created.

## Treg: documented candidate, not approved live provider

Source: https://treg.to/use-cases/search-posts-by-keyword

The page describes Reddit/X access through several providers, provider-specific payloads/pricing, and no built-in scheduling or automatic provider failover. Metering units vary; per-call, per-result, and per-found amounts must not be interchanged. Its reliability figures are vendor-reported, exclude some error classes, and are not a controlled coverage benchmark.

Some wording around Reddit comment-tree access differs from earlier conversation claims. Search returning posts does not prove comment coverage. Inspect the exact chosen endpoint, schema, pagination/time bounds, billing unit, credentials, terms, and VPS responses before selecting it. Do not assume a third-party route guarantees permission, completeness, or anti-bot reliability.

## OpenMagpie: alternative subsystem, not mandatory

Source: https://github.com/obris-dev/openmagpie

The README describes feeds, scheduling, semantic filtering, per-attempt audit and instant/digest webhook delivery. Delivery is at-least-once; receivers deduplicate using the item key. X's route requires a browser session cookie. GitHub and richer provenance/branching capabilities appear in roadmap items, not necessarily shipped features. These facts support its role as an alternative ingestion/monitoring subsystem, not proof it satisfies the experiment or VPS requirements. Do not deploy both it and a custom collector without a measured need.

## Langfuse and deterministic traces

Source: https://langfuse.com/integrations/native/opentelemetry

The documented OTLP endpoint accepts HTTP-based trace export. The SDK's default export filtering focuses on LLM-related spans; non-LLM application spans require deliberate instrumentation/export-filter configuration and verification. Metadata needed for aggregation must propagate to the relevant spans. No sensitive data should be placed in propagated baggage. A successful SDK install does not establish end-to-end lineage, metrics coverage, or missing-run alerting.

Acceptance implication: a synthetic deterministic prompt-construction bug must remain visible before, through, and after the LLM span. Verify retained artifact references and root/child relationships in the actual selected backend; a wrapper cannot expose uninstrumented third-party internals automatically.

## Reporting candidates

Sources:
- https://quarto.org/docs/output-formats/html-basics.html
- https://developer.chrome.com/docs/automation-and-testing/headless

Quarto documents HTML output options; Chrome documents headless rendering. Keep the proposed Markdown -> themed HTML -> print-to-PDF direction, but validate standalone assets, offline rendering, Chinese fonts, evidence links, and print pagination on the VPS. Renderer choice is provisional. No report renderer or PDF was created in this review.

## Still factual preflight work, not owner-intent decisions

- Exact Reddit/X endpoint behavior, credentials, lawful/allowed use and retention, quotas, response IDs, edits/deletions, pagination, freshness, and repeat reliability.
- Actual provider charge accounting and a user-approved hard spend cap.
- Hermes isolation from persistent memory/tools and accurate prompt/model capture for experiments.
- Langfuse export filtering/context propagation and any existing authorized backend/region.
- Optional heartbeat destination, Discord attachment/send behavior, file-size limits, and delivery reconciliation.
- Report renderer availability and offline assets/fonts on the target VPS.
