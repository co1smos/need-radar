# Round 1 - Hermes frontier

Session: `20261006_114451_8a8410`. Skill: `grill-with-docs`.

This is a readable transcription of Hermes' public questions and recommendations, with terminal wrapping and repeated labels removed. It is not a raw terminal log or private reasoning transcript. The underlying Hermes session is retained.

## Experiment contract

**Q1. Immutable comparison unit?** Freeze an ordered acquisition manifest with normalized item IDs, immutable raw references, provenance, timestamps, and content hashes. Both arms consume it. Later edits/comments/enrichment belong to another snapshot.

**Q2. Source/retrieval/extraction boundary?** Source is the enabled universe; retrieval is queries, ordering, windows, pagination, item types, comments and source-side filtering; extraction interprets frozen items. Common normalization, exact deduplication and validation must not encode explicit/latent-friction preferences.

**Q3. Common prefilter?** Remove objectively unusable items, exact duplicates, independently identified spam and bounded resource overflows with reason codes. Do not silently filter by complaint keywords, sentiment, questions, engagement, first-person phrasing or inferred pain before the split.

**Q4. Comparable budgets?** Pin the model/settings, ordered input, context rules, token/request ceilings, schema, candidate cap, evidence allowance, batches and retry policy. Only extraction instructions differ. Equal caps do not mean forcing equal output counts.

## Candidate and evidence contract

**Q5. Candidate schema?** Identity/provenance, affected user, goal, concrete obstacle, consequence/value, scoped intervention hypothesis, snapshot evidence references/excerpts, caveats. Arm metadata must be separable from judge-facing content; extractor confidence is not judge evidence.

**Q6. Evidence sufficiency?** At least one retained excerpt must substantiate the obstacle and user/goal. Frequency, breadth, severity and cost claims need their own support. One strong account can establish friction, not population prevalence. A URL alone is not retained evidence.

**Q7. Meaningful size?** Practical consequence, recurring burden, blocking impact or costly failure, not TAM or monetization. Narrow but severe problems remain eligible; missing breadth is uncertainty, not necessarily automatic rejection.

**Q8. When is a workaround friction?** Evidence must show avoidable effort, failure risk, cognitive/coordination cost or a constrained tradeoff. Multiple steps, a script, or an advanced workflow alone are insufficient. Separate observed behavior from inferred pain.

**Q9. May arms obtain outside context?** No asymmetric enrichment or browsing. Both receive the same retained thread/item/metadata scope. Enrichment belongs to a later retrieval experiment and must precede snapshot freeze.

**Q10. Candidate duplicates?** Preserve raw arm outputs. Use one shared, versioned consolidation procedure with explicit cluster membership and representative provenance. Measure raw and distinct yield; do not silently discard candidates.

## Judge and metrics

**Q11. Fixed eligibility rule?** Same five dimensions: clarity, evidenced pain/value, evidence/size, plausible solvability, scoped intervention. Grounding is a gate. Return dimensions, citations, unsupported-claim flags, uncertainty and eligibility; hide arm labels and keep judge configuration fixed.

**Q12. Judge calibration?** Small arm-blind positives/negatives/edge-case fixtures; preserve ambiguity rather than treating the judge as ground truth. Hermes suggested eventual owner review for ambiguous examples; this suggestion requires reconciliation with the owner's explicit rejection of an in-product personal-feedback workflow.

**Q13. Useful-yield denominator?** Distinct judge-eligible clusters divided by fixed eligible input items, supplemented with cluster precision, grounding failures, abstention, cost/latency per eligible cluster, and cross-arm overlap/unique contribution. Precision alone rewards emitting almost nothing.

**Q14. Small/empty samples?** Empty/insufficient inputs, unequal processing, budget failures, missing evidence or incomplete judging make comparison inconclusive. Report descriptive results; do not manufacture significance from one day. Numerical minimums should follow pilot evidence.

**Q15. Promotion/rollback?** No auto-promotion. Use repeated comparable runs and grounding/cost/latency/reliability guardrails. Keep the previous version available. Future production promotion/tradeoff approval belongs to the owner, not this review.

## Failure isolation and reporting

**Q16. Partial failures?** Serve proceeds when shadow/judge/comparison fails. Never publish shadow automatically when serve fails. Partial arms cannot be compared as complete. Preserve outputs on judge failure. Optional renderer failure retains Markdown. Delivery retries reuse the report rather than re-running extraction.

**Q17. Health states?** Distinguish healthy findings, healthy no findings, source degradation, extraction degradation, unavailable evaluation, report failure, delivery failure and missing scheduled run. HTTP/process success is not proof of healthy data.

**Q18. Authoritative output?** Hermes proposed a structured canonical result plus deterministic Markdown and projections. This needs reconciliation with the owner's selected Markdown-as-canonical-report contract. Rendering and serve delivery must not depend on shadow completion. Quarto/Chromium remain candidates pending preflight.

## State and observability

**Q19. Minimum durable state?** Run/experiment/config IDs, snapshot hashes, normalized items and raw references, exclusions/deduplication, context manifests, prompt templates/resolved inputs, model/usage, raw outputs, validation/judge results, cluster membership, report artifacts, delivery attempts, timestamps/failures/lineage. SQLite plus files; no speculative workflow engine, event bus or vector database.

**Q20. Prompt diagnosis?** Save versioned templates/config, ordered inputs, context/truncation decisions, resolved-prompt artifacts/references and upstream transformations. Do not rely only on logs or claim visibility into opaque third-party internals.

**Q21. One trace for everything?** Separate execution traces connected by stable run/snapshot/candidate/evaluation/report IDs. Avoid a single trace spanning all asynchronous days. Reuse OTel where valuable, and validate actual backend coverage.

**Q22. Retention/redaction?** Retain necessary, permitted evidence/replay text; minimize other data. No credentials/cookies/auth headers/full provider bodies in telemetry. Deterministic redaction; fail closed when rights/terms are unclear. Production retention duration and external telemetry destination require later owner approval.

## Security and dependencies

**Q23. Untrusted source text?** Quote/delimit as evidence, never system instructions. Schema-constrained output and snapshot citation validation. No source-requested tool calls, secret access, automatic URL execution or browsing.

**Q24. AI repair?** Proposal-only: evidence, suspected cause, affected stage, confidence, narrow suggested fix. No automatic production mutation, installations, credentials, schedules or paid retries without authorization. Bounded deterministic transient retries must preserve lineage.

**Q25. Acquisition proof?** Verify exact provider/interface, access/retention terms, platform/item coverage, pagination, time bounds, IDs, edits, ordering, quotas, errors, consistency and VPS behavior. Fixtures prove adapter behavior, not live source access. Provider approval/spend remains an activation gate.

**Q26. Optional integrations?** Direct GitHub/HN APIs only when enabled; Agent-Reach as later measured enrichment/fallback; OpenMagpie only when replacing rather than duplicating substantial ingestion responsibilities.

**Q27. Hermes preflight?** Unattended deterministic scheduling, environment, timeout/overlap, missing-run detection, artifact persistence, exit status, Discord behavior and IDs. Reuse scheduler/gateway unless a verified gap exists. Credentials/channel/time/permissions are later activation inputs.

**Q28. Budget defaults?** Explicit item, call/token, retry, enrichment and rendering limits. No approved dollar cap means no paid acquisition or production LLM schedule. Exhaustion must be visible and must not silently change model/scope/parity.

## Acceptance

**Q29. Fixture coverage?** Grounded explicit pain, unsolvable complaint, real and benign workarounds, recurring decisions, narrow high-impact pain, vague complaints, fabricated size, duplicates, injection, malformed/deleted content, empty/partial acquisition, arm/judge/renderer/delivery failures. Assert properties, not exact prose.

**Q30. Acceptance seams?** Prove identical manifests and one changed dimension, neutral filters, valid evidence, unsupported-claim flags, provenance-preserving dedupe, blind fixed judging, serve failure isolation, health distinctions, backward prompt lineage, no source-triggered actions and no secret leakage.

## Owner-facing issues raised by Hermes

No current product-intent contradiction identified. Potential future approvals: production promotion/tradeoffs, live spend, provider/account permissions, retention/external telemetry, and Discord activation. Proxy will distinguish these from questions that actually block this documentation review.
