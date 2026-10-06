# Round 2 input - proxy answers to Q1-Q30

Session: `20261006_114451_8a8410`. These answers apply owner-approved requirements and reversible conservative defaults. They do not authorize implementation or live activation.

Read `docs/evidence-notes.md` for the limited checks completed while you interviewed. Do not research externally or rewrite documents in this frontier turn. Accept settled answers, identify only NEW material gaps, and finish with the next short frontier or FRONTIER_EMPTY.

## Q1-Q4: frozen inputs and neutral shared processing

Accept. The comparison unit is a versioned immutable snapshot of the SAME ordered normalized item bytes plus manifest, retained evidence references and provenance. A list of IDs alone is insufficient when rows can later mutate. Freeze all shared preprocessing/batching/context/truncation settings. Timestamp cutoffs and late-arriving items are explicit. A later deletion/rights request may remove retained evidence; mark affected replay unavailable rather than promise immutability overrides removal obligations.

Common prefilter: lossless normalization, exact-ID/content deduplication, objective invalid/deleted/unreadable records, allowlisted source metadata spam flags, and equal documented resource ceilings. No complaint/workaround/sentiment/engagement-based suppression. Broad AI relevance belongs to the pinned source/retrieval configuration; do not add a silent semantic filter after freeze. Record exclusion reasons and cap-induced omissions.

Only the extraction-instruction field differs initially. Pin shared model/provider, reasoning settings, tool/memory policy, schema, limits, batches, seed where supported and retry policy. Do not treat temperature zero as guaranteed determinism. Start with a shared configurable maximum K=10 submitted candidates per arm per snapshot as a reversible initial default; allow fewer/zero, never pad. Larger input volumes use the SAME deterministic batches and common bounded consolidation. An arm-specific web/tool/enrichment call is forbidden in the extraction experiment.

## Q5-Q9: candidates and evidence

Accept the proposed minimum semantic content. Store arm/config metadata outside the judge-facing candidate. Candidate IDs identify run-local records, not an invented permanent universal problem taxonomy. Extractor confidence is optional diagnostics and excluded from judging.

At least one preserved excerpt must support the concrete obstacle/user/goal. Quantified impact, recurrence, breadth, explicit willingness to pay, and claimed workaround burden need corresponding evidence. Distinguish direct observation, supported inference, and unknown. A severe recurring problem documented by one user may qualify; it cannot become 'many users' by inference. Like counts, copied campaigns and duplicate posts are not independent demand evidence. Do not resolve cross-platform real-world identities or build user profiles.

Manual steps/scripts alone are not pain. A latent-friction candidate must show burden or constrained tradeoff in evidence. A plausible small intervention is a hypothesis, not proof that implementation succeeds or no existing product solves it. No unsupported novelty/TAM/WTP claims. AI-learning tooling remains in scope when the learner's concrete obstacle is evidenced; generic 'how do I learn AI?' is insufficiently specific.

Both arms and judge can use only the same frozen evidence universe; judge sees each candidate's cited evidence with the same deterministic surrounding-context policy. No fresh web retrieval by an arm or judge. Extra enrichment is a later retrieval experiment, not a hidden extraction change.

## Q10: duplicate treatment without cross-arm leakage

Accept with one important constraint: consolidate WITHIN each arm using the same versioned policy, retaining raw candidates and membership. Do not merge v0 and v1 evidence before scoring a candidate; otherwise one arm can borrow the other's discovery. Apply cross-arm matching only afterward to report overlap/unique contribution, with ambiguous matches marked uncertain. No permanent vector DB/ontology. Exact matching is deterministic; any semantic matcher is one small fixed evaluation helper, not a new clustering service.

## Q11-Q14: one evaluator and honest metrics

Use the same fixed five dimensions and grounding gate. Operational verdicts: ELIGIBLE, NEEDS_EVIDENCE, INELIGIBLE. Each dimension records supported / weak-or-unsupported / unknown plus a short evidence-based reason; no weighted pseudo-precise overall opportunity score. ELIGIBLE requires a concrete grounded friction, meaningful evidenced burden, plausible software/AI/automation improvement and a bounded intervention. Material unknowns become NEEDS_EVIDENCE. Unknown population prevalence alone is not a material failure when substantial practical burden is evidenced. Clear non-friction/contradiction/out-of-scope is INELIGIBLE. This is a versioned rule, not a per-arm subjective preference.

Hide arm labels, extractor prompt/confidence, cost and version name from the judge. Fix judge model/rubric/settings/evidence policy within an experiment epoch. Bias checks can swap anonymous order and rerun a small fixed subset; no full second evaluation platform. Fixture expectations are semantic constraints, not hidden claims of human-labelled ground truth. Automatic disagreement/uncertainty stays visible. Do NOT add a personal-feedback module or require the owner to continually label outputs. Ambiguous facts get abstention; only genuinely changed product intent would require asking the owner.

Primary descriptive measure: unique judge-eligible frictions per common frozen input count, within equal K/call/token budgets. Also preserve eligible absolute count, precision among emitted distinct candidates, grounding/unknown rates, cost, latency, overlap/unique contribution, and coverage. Call these 'judge-qualified', not proven market truth. Never call yield global recall: the unobserved internet is unknown. Cost per eligible result is undefined when none qualify; use n/a plus actual spend, not divide-by-zero or zero-cost claims.

Empty input is a valid operational status but not evidence of equal strategy quality. Unequal processing, missing evidence, changed model, incomplete judging or budget failures produce an inconclusive comparison. Prefer inconclusive over post-hoc intersection selection. Preserve partial outputs for diagnosis. Repeat comparable snapshots before drawing a conclusion; no automatic significance claim from one day.

## Q15: promotion

No automatic promotion or rollback policy with invented thresholds. Produce a comparison recommendation after repeat runs; the serving version changes only on an explicit later instruction. Retain the prior pinned version for deliberate rollback. This is not a present decision blocker and does not introduce preference feedback. Threshold tuning and any tradeoff approval belong to that future change request.

## Q16-Q18: independent serving and canonical Markdown

Accept failure isolation. Shadow and comparison/judge failure must not stop a valid serve digest. Do not substitute shadow automatically when serve fails. Comparison is offline/asynchronous relative to report publication. Serve findings are presented as evidence-linked candidate signals, not as judge-certified market facts; when assessment is absent, display evaluation unavailable/unevaluated instead of fabricating a score. Structural/evidence-reference validation still applies before publishing. If serve itself fails, a status-only failure notice can replace findings; never re-send yesterday's report as today's results.

CLARIFICATION to Q18: the FINAL canonical human-facing report is report.md, as the owner selected. Structured candidate/evaluation artifacts are intermediate analysis data, not a second independently edited report. A deterministic common template may render those intermediates into Markdown. HTML, PDF and Discord projections come from the frozen Markdown and the same manifest. Do not have two independently authored truths or an extra LLM beautification/summarization call. Top summary sections must already be in the canonical report.

A report revision receives its own ID/hash; an evaluator finishing later must not silently alter a previously sent report. HTML is the initial visual projection; PDF is supported but optional per run and cannot block Markdown delivery. Use Quarto/Chromium only after a small renderer check. Shadow initially archives raw candidates plus Markdown/evaluation artifacts, with no automatic HTML/PDF rendering or delivery. A bounded delivery retry reuses the same report and payload; uncertain send outcomes need reconciliation, not claims of exactly-once delivery.

## Q19-Q22: minimal state and complete deterministic lineage

Accept SQLite plus files and the proposed information categories; do not implement a table per noun or build an event-sourcing framework. Records reference immutable content versions, not only mutable item IDs. Persist discovery origin/query/provider/run separately from unique item identity. Retain processing decisions and actual resolved prompts under the approved data policy; a hash without content or reconstructible inputs does not enable diagnosis.

Each scheduled execution has a run/trace; link acquisition -> snapshot -> both arms -> evaluation -> report -> delivery with stable IDs and span links where supported. Spans include deterministic config resolution, selection, normalization, context assembly, truncation, prompt rendering, output validation and presentation. No claim that wrapper instrumentation reveals opaque third-party internals. The current Langfuse docs explicitly warn its default SDK filter focuses on LLM spans; verify our normal spans/root/context survive export. Prefer minimal backend plus structured logs/counts and separate missed-run detection, not a mandatory new monitoring stack.

Redact secrets before persistence/export. Minimize public source personal data; never include secrets in trace baggage. Retention duration, external telemetry destination/region, provider terms and authorized model data path remain UNSET fail-closed live activation inputs. No new external export or source-data retention obligation is approved by this interview. Within future approved retention, snapshot contents permit replay; after removal, mark the limitation honestly.

## Q23-Q28: security, existing tools and activation gates

Accept quoted untrusted content, no source-triggered tools/URL execution and proposal-only repair. A future deterministic retry policy can be bounded for transient errors; auth/permissions failures require intervention, not evasion. No rotating-proxy/CAPTCHA/stealth project is authorized.

Fixtures prove parser, contracts and error handling; they cannot prove live VPS access, anti-bot resistance, provider permission, completeness or actual billing. Those require a separately authorized narrow live preflight. Treg is preferred to investigate, NOT a validated/mandatory vendor; OpenMagpie/Agent-Reach/direct APIs remain alternatives, not parallel mandatory layers. Select one minimal acquisition path before building more integrations.

Installed Hermes CLI help already confirms --no-agent/--script/--workdir/--deliver and model pinning; runtime scheduling, script path restrictions (~/.hermes/scripts), isolation, tracing and Discord behavior still need preflight. No jobs were created.

No approved live dollar cap/key/destination means no live activation. Later gates: acquisition/provider permission and capped spend; extractor+judge model costs; data/telemetry retention/destination; Discord target/time and explicit enablement. Do not ask the owner these merely to finish design documents. Record them in an activation checklist and proceed with independent design. This review itself only invokes the requested Hermes interview using its existing configuration, not production data APIs.

## Q29-Q30: acceptance

Accept compact fixture coverage and property-based acceptance seams. Add: arm metadata leakage; shared filter discarding benign/real workarounds unfairly; budget exhaustion/model fallback; duplicate evidence counted as independent; input edits after snapshot; ambiguous send outcome; and deterministic prompt bug with otherwise successful LLM call. These are test-case descriptions now, not implemented tests or proof of a working system.

No current owner-required design choice remains in the frontier as answered. Future activation and promotion approvals are explicitly reserved, not fabricated. Continue with any genuinely new material contradiction or missing contract. If none, print FRONTIER_EMPTY and finish. Remain read-only; documentation finalization will follow separately.
