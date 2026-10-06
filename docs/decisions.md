# Need Radar decision record

Status: compact finalized record for interview session `20261006_114451_8a8410`.
This is not an implementation, deployment, purchasing, or activation authorization.

## Authority labels

- **Owner-approved**: fixed product intent or operating constraint from [owner-brief.md](owner-brief.md).
- **Proxy default**: conservative, reversible detail settled in [grill/02-proxy-answers.md](grill/02-proxy-answers.md).
- **Deferred activation**: live fact, permission, credential, spend, retention, destination, or promotion choice reserved for later.

The complete design is [design.md](design.md).
Limited dependency observations are in [evidence-notes.md](evidence-notes.md).

## Product and scope

1. **Owner-approved — Product objective.** Discover concrete, valuable, meaningfully sized, evidenced, solvable friction in AI development or learning, with a reasonably scoped intervention.
2. **Owner-approved — Size meaning.** Meaningful practical burden matters; venture scale, market size, monetization, novelty, and willingness to pay are not required or inferred.
3. **Owner-approved — No personalization.** No owner-interest recommender, personal taste score, or continuous personal-feedback feature belongs in the system.
4. **Owner-approved — Simplicity.** Reuse existing tools, prefer deterministic execution, and avoid speculative infrastructure.
5. **Owner-approved — Review boundary.** This finalization creates design records only; implementation and live operation require separate authorization.

## Acquisition and dependencies

6. **Proxy default — Minimal acquisition path.** Select one deterministic acquisition path before adding integrations.
7. **Proxy default — Treg status.** Treg is preferred to investigate but remains tentative, unvalidated, and non-mandatory.
8. **Proxy default — Alternatives.** OpenMagpie is an alternative subsystem; Agent-Reach is optional enrichment/fallback; direct GitHub/HN APIs are source-specific later options.
9. **Proxy default — Storage.** Begin with SQLite plus filesystem artifacts; no workflow engine, event bus, vector database, ontology, or separate analytics platform.
10. **Owner-approved — Hermes preference.** Prefer Hermes for future scheduling and Discord entry rather than recreating its framework, subject to runtime verification.
11. **Deferred activation — Provider approval.** Exact endpoint behavior, terms, authorization, retention rights, credentials, quotas, billing, completeness, and VPS reliability remain fail-closed preflights.

## Experiment contract

12. **Owner-approved — One dimension.** A comparison changes exactly one of source, retrieval, or extraction.
13. **Owner-approved — First experiment.** The initial comparison changes extraction only: explicit-friction serve versus latent-friction shadow.
14. **Owner-approved — Initial extraction comparison.** Both arms share sources, retrieval, snapshot, model/settings except extraction instructions, schema, budgets, evaluator, and report constraints. Later source/retrieval experiments intentionally may change retrieved inputs while keeping downstream behavior fixed; they retain each arm's snapshot.
15. **Proxy default — Frozen input.** Compare the same ordered normalized item bytes and manifest, not mutable IDs alone; late edits and arrivals belong to later snapshots.
16. **Proxy default — Neutral prefilter.** Shared processing is limited to lossless normalization, exact dedupe, objective invalid records, allowlisted metadata spam flags, and equal resource ceilings.
17. **Proxy default — No semantic suppression.** Complaint, workaround, sentiment, engagement, and inferred-friction filters are forbidden in the common prefilter.
18. **Proxy default — Candidate cap.** `K=10` submitted candidates per arm per snapshot is an initial configurable default, not an owner mandate; fewer or zero are valid.
19. **Proxy default — Equal resources.** Calls, tokens, batching, context, truncation, retry policy, and evidence scope remain equal; no arm-specific browsing or enrichment.

## Evidence, dedupe, and judge

20. **Owner-approved — Evidence grounding.** A finding must identify a concrete user, goal, obstacle, evidence, and bounded intervention hypothesis.
21. **Proxy default — Claim discipline.** Recurrence, breadth, impact, willingness to pay, and workaround burden require supporting evidence; unknown remains unknown.
22. **Proxy default — Latent friction.** Manual steps or scripts alone are insufficient; evidence must show burden, risk, repeated effort, cognitive load, coordination cost, or constrained tradeoff.
23. **Proxy default — Identity minimization.** Do not resolve cross-platform real-world identities or build user profiles.
24. **Proxy default — Within-arm dedupe.** Consolidate candidates separately within each arm before judging, preserving raw candidates and membership.
25. **Proxy default — Cross-arm matching.** Match arms only after scoring to report overlap and unique contribution; never lend one arm the other's evidence.
26. **Owner-approved — Fixed evaluator.** Both arms use the same versioned rubric, judge model/settings, evidence policy, and grounding rules.
27. **Proxy default — Arm blindness.** Hide arm label, strategy name, extractor prompt/confidence, cost, and version name from the judge.
28. **Proxy default — Verdicts.** Use `ELIGIBLE`, `NEEDS_EVIDENCE`, and `INELIGIBLE`; do not create a weighted pseudo-precise opportunity score.
29. **Proxy default — Five dimensions.** Assess friction clarity, evidenced pain/value, evidence and meaningful size, plausible solvability, and bounded scope fit.
30. **Proxy default — Judge limits.** Preserve uncertainty and disagreement; the judge is not market truth and does not create an owner-labeling loop.

## Metrics and change control

31. **Proxy default — Primary metric.** Report unique judge-eligible frictions per common frozen input count under equal budgets.
32. **Proxy default — Supporting metrics.** Preserve absolute eligible count, emitted precision, grounding/unknown rates, abstention, cost, latency, coverage, overlap, and unique contribution.
33. **Proxy default — Honest language.** Call outputs judge-qualified, not proven market truth; do not call observed yield global recall.
34. **Proxy default — Zero denominator.** When none qualify, cost per eligible result is `n/a` while actual spend remains reported.
35. **Proxy default — Inconclusive cases.** Unequal processing, missing evidence, configuration drift, incomplete judging, budget failure, prohibited fallback, or arm failure invalidate comparison.
36. **Proxy default — Repetition.** Use repeated comparable snapshots before a strategy recommendation; do not claim significance from one daily run.
37. **Proxy default / deferred — No auto-promotion.** Serving never changes automatically; a future explicit instruction is required, with the prior pinned version retained for deliberate rollback.
38. **Deferred activation — Tradeoff thresholds.** Promotion thresholds and accepted cost/quality tradeoffs are chosen only in the future promotion request.

## Reporting and failure isolation

39. **Proxy default — Serve independence.** Shadow, judge, comparison, HTML, and PDF failures must not block a valid serve report.
40. **Proxy default — No substitution.** Shadow never automatically replaces failed serve; a serve failure may produce a status-only notice, not stale findings.
41. **Owner-approved — Canonical report.** `report.md` is the FINAL canonical human-facing report.
42. **Proxy default — One truth.** Candidate/evaluation structures are intermediate data; HTML, PDF, and Discord are deterministic projections of frozen Markdown.
43. **Proxy default — No rewrite.** Presentation adds no LLM beautification, re-reasoning, or independently authored summary.
44. **Proxy default — Revisions.** Every report revision has its own ID/hash; late evaluation cannot silently alter a sent report.
45. **Proxy default — Rendering.** HTML is the first visual projection; PDF is optional and cannot block Markdown. Quarto/Chromium remain provisional pending fixture validation.
46. **Proxy default — Delivery retry.** Reuse the same report and payload; reconcile ambiguous outcomes rather than claiming exactly-once delivery.

## State, observability, and security

47. **Owner-approved — End-to-end diagnosis.** Observability covers deterministic transformations and LLM calls, not only process exits or model spans.
48. **Proxy default — Prompt provenance.** Retain template, resolved configuration, ordered inputs, context assembly, truncation, redacted resolved prompt or reconstructible content, output, and validation.
49. **Proxy default — Trace topology.** Use separate linked run/trace records for acquisition, analysis, evaluation, reporting, and delivery; do not invent one long-lived asynchronous trace.
50. **Proxy default — Minimum telemetry.** Use structured logs/counts, stable lineage IDs, artifact references, and independent missed-run detection; verify normal-span export before relying on Langfuse/OpenTelemetry.
51. **Proxy default / safety constraint — Untrusted content.** Retrieved text is quoted data, never instructions; it cannot trigger tools, browsing, URL execution, secrets, or mutations.
52. **Proxy default / safety constraint — Secret boundary.** No credentials, cookies, authorization headers, tokens, or secrets in artifacts, reports, logs, traces, or baggage.
53. **Proxy default — Repair boundary.** Repairs are evidence-backed proposals only; no automatic deployment, installation, credential rotation, schedule change, paid retry, stealth, or bypass behavior.
54. **Proxy default — Rights over replay.** Approved deletion or rights requests may make replay unavailable; snapshot immutability does not override removal obligations.

## Deferred live activation

55. **Deferred activation — Spend.** No live acquisition, extractor, judge, or telemetry spending until the owner approves hard caps.
56. **Deferred activation — Data path.** Provider permission, model data handling, source retention duration, and telemetry destination/region remain unset and fail closed.
57. **Deferred activation — Scheduling.** Hermes runtime isolation, script paths, timeouts, overlap, tracing, missed-run detection, and actual schedule require preflight and approval.
58. **Deferred activation — Delivery.** Discord destination, send time, permissions, attachment limits, reconciliation behavior, and explicit enablement remain unset.
59. **Deferred activation — Rendering environment.** VPS renderer availability, offline assets, Chinese fonts, links, and pagination require a narrow check.
60. **Deferred activation — Promotion.** Any serving-strategy change remains an explicit owner action after comparable evidence is reviewed.

## Acceptance and blocker status

61. **Proxy default — Compact acceptance.** Fixtures cover parity, neutral filtering, evidence grounding, dedupe leakage, arm metadata leakage, size claims, empty/partial runs, budget exhaustion/fallback, prompt bugs, injection, report isolation, revision, delivery ambiguity, and redaction.
62. **Proxy default — Fixture limits.** Fixtures prove contracts and error handling, not live permission, access, completeness, anti-bot resistance, reliability, or billing.
63. **Proxy default — Live facts.** Factual preflights are performed only under separate authorization and do not reopen settled product design.
64. **Settled — Current frontier.** The grill frontier is empty.
65. **Settled — Owner blockers.** There are zero current owner design blockers.
66. **Settled — Activation is separate.** Unset activation inputs remain fail-closed future gates, not blockers to this finalized documentation.

## Owner update after the grill (2026-10-06)

67. **Owner-approved — Model.** DeepSeek V4.1 Flash is selected. Both extraction arms use it; the official API currently names it `deepseek-flash`. Provider/access and per-run pinning still require verification; no global Hermes model changes.
68. **Proxy default — Initial judge model.** Reuse the selected model for the first fixed judge, in an isolated call with an identical rubric/evidence policy for both arms. This is a reversible simplicity default, not an assertion of independent ground truth.
69. **Owner-supplied — Discord destination.** Channel `1557157266824634469` is recorded as a string. This supersedes the previously unset destination only; bot access, send time and explicit enablement remain unconfirmed.
70. **Owner request — Credentials when needed.** Notify the owner before the first authorized Treg live call if its token or a selected provider's BYOK credential is missing. Public catalog inspection and architecture review do not need a Treg token. Never solicit or store secrets in the discussion documents.
71. **Documentation — Architecture visuals.** Added `architecture.md`, `architecture.html`, `diagrams/system-overview.svg` and `runtime-selections.md`. The exact original interview remains unchanged; this update is not a fabricated extra Hermes round.

See [runtime-selections.md](runtime-selections.md) for primary references and credential stages, and [architecture.md](architecture.md) for the visual overview. None of these entries authorize activation, spending, posting or implementation.