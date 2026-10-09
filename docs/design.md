# Need Radar design
Status: finalized design; offline serve and deterministic HTML projection implemented; no live activation authorization.
Interview session: `20261006_114451_8a8410`.
Visual overview: [architecture.md](architecture.md) and [browser diagram](architecture.html).
Owner update on 2026-10-06: selected DeepSeek V4.1 Flash and Discord channel `1557157266824634469`. See [runtime-selections.md](runtime-selections.md) for the project-only mapping and credential handoff. No runtime configuration was changed.
## Authority and scope
It reconciles the authoritative owner intent in [owner-brief.md](owner-brief.md).
It incorporates the settled proxy answers in [grill/02-proxy-answers.md](grill/02-proxy-answers.md).
Dependency observations and remaining factual preflights are in [evidence-notes.md](evidence-notes.md).
Decision authority and provenance are summarized in [decisions.md](decisions.md).
The product discovers concrete, valuable, evidenced **AI application-layer** friction in building with or learning to build AI applications. See [target-scope.md](target-scope.md) for the active source boundary and the distinction from model-internal / low-level AI infrastructure work.
A finding identifies an affected user, goal, obstacle, evidence, and a plausibly bounded intervention.
Small but consequential problems qualify; venture scale, monetization, novelty, and willingness to pay are not assumed.
There is no personal-interest recommender, owner-preference score, or continuous personal-feedback feature.
This design covers the smallest useful system boundary and the first extraction-only experiment.
It is not a milestone, implementation plan, deployment approval, or permission to incur cost.
## Minimal system boundary
The shared path acquires items, normalizes and preserves provenance, and freezes one ordered snapshot.
It then branches into independent serve and shadow extraction, each with the same validation and within-arm consolidation.
The serving branch renders valid serve candidates into canonical `report.md`, then deterministic HTML/PDF/Discord projections.
A separate evaluation branch judges both arms and compares results without sharing discoveries between them.
Evaluation completion is NOT a prerequisite for serving. Retain enough state and lineage to diagnose every branch.
Use SQLite plus filesystem artifacts initially.
Do not add a workflow engine, event bus, vector database, ontology, custom frontend, or separate analytics store without measured need.
Hermes is the preferred future scheduler and Discord entry point, subject to narrow runtime preflight.
The project does not reproduce Hermes scheduling or gateway functions unless a verified gap blocks the minimal system.
Treg is a tentative preferred acquisition candidate, not a validated or mandatory dependency.
Its endpoint behavior, terms, authorization, retention rights, coverage, pagination, IDs, freshness, quotas, billing units, and VPS reliability require a separately authorized live preflight.
OpenMagpie is an alternative ingestion subsystem, not a parallel mandatory layer.
Agent-Reach is optional later enrichment or fallback, not part of the first data path.
The core initial source set is Reddit + X. GitHub Issues/Discussions is an optional extension after the Reddit+X end-to-end path is working and must not block the core pilot; when added, prefer official GitHub APIs/CLI. Hacker News remains backlog and is considered only if explicitly activated later.
Select one minimal acquisition path before adding integrations.
## Experimental boundary
Source, retrieval, and extraction are separate experiment dimensions.
The first experiment changes extraction only.
- v0 / serve extracts explicit pain, complaints, feature requests, and missing capabilities.
- v1 / shadow extracts workarounds, repeated manual workflows or decisions, and latent friction.
Everything else is pinned within an experiment epoch:
- source universe;
- retrieval queries, order, pagination, item types, and time window;
- ordered normalized item bytes and evidence universe;
- preprocessing, batching, context, and truncation rules;
- model, provider, reasoning settings, tools, memory policy, seed where supported, and retries;
- candidate schema and resource limits;
- judge model, rubric, settings, and evidence policy;
- report constraints.
Only the extraction-instruction field differs among behavioral settings; run/arm IDs and output locations may differ as bookkeeping.
Neither arm may browse, invoke tools, or enrich its evidence independently.
For later source or retrieval experiments, the selected dimension may intentionally produce different raw inputs. Freeze and retain each arm's snapshot, keep the comparison window/resource policy and downstream extraction/judge fixed, and predeclare appropriate denominators. The identical-input requirement applies to the initial extraction experiment, not to a test whose explicit purpose is to retrieve different evidence.
## Frozen comparison input
A comparison consumes a versioned immutable snapshot of the same ordered normalized item bytes.
The snapshot includes a manifest, evidence references, provenance, timestamp cutoff, and content hashes.
Late arrivals and edits belong to a later snapshot.
If a deletion or rights request removes retained evidence, replay is marked unavailable; immutability never overrides removal obligations.
The shared configurable maximum is initially `K=10` submitted candidates per arm per snapshot.
This is a reversible proxy default, not an owner mandate.
Arms may emit fewer or zero candidates and never pad output.
Both arms receive equal call, token, batching, and retry ceilings.
Larger snapshots use the same deterministic batches and common bounded consolidation.
## Neutral shared processing
Shared preprocessing may perform only:
- lossless normalization;
- exact-ID and exact-content deduplication;
- rejection of objectively invalid, deleted, or unreadable records;
- allowlisted source-metadata spam exclusions;
- equal documented safety and resource ceilings.
It must not filter by complaint language, workaround language, sentiment, engagement, first-person form, or inferred friction relevance.
Broad AI relevance belongs to the pinned source and retrieval configuration.
No silent semantic relevance filter is inserted after snapshot freeze.
Every exclusion receives a reason code.
The fixture set must include benign and genuine workarounds to detect prefilter bias.
## Candidate and evidence contract
Each candidate states a specific affected user or role, goal, concrete obstacle, consequence, bounded intervention hypothesis, evidence references, excerpts, and caveats.
Run, arm, and configuration metadata remain outside the judge-facing candidate.
Extractor confidence is optional diagnostic metadata and is hidden from the judge.
At least one retained excerpt must support the obstacle, user, and goal.
Recurrence, breadth, quantified impact, willingness to pay, and workaround burden require corresponding evidence.
Direct observation, supported inference, and unknown are distinguished.
One severe report can establish concrete friction but cannot establish many affected users.
Likes, copied campaigns, and duplicates do not become independent demand evidence.
No cross-platform identity resolution or user profiling is performed.
Manual steps or a custom script alone do not establish pain.
Latent friction requires evidenced burden, failure risk, cognitive load, coordination cost, repeated effort, or constrained tradeoff.
An intervention is a plausible hypothesis, not proof of novelty, feasibility, or product absence.
Generic interest in learning AI is insufficient without a concrete learning obstacle.
## Deduplication and comparison
Exact item deduplication occurs before extraction.
Candidate consolidation occurs within each arm using the same versioned policy.
Raw candidates and cluster membership are retained.
No v0 and v1 evidence is merged before candidates are judged.
Cross-arm matching happens only after within-arm scoring to report overlap and unique contribution.
Ambiguous cross-arm matches remain uncertain.
Any semantic matcher is one fixed evaluation helper, not a permanent clustering service.
## Fixed judge
Both arms use one arm-blind, versioned judge.
The judge does not receive arm labels, extractor prompt or confidence, cost, or strategy name.
The five dimensions are:
1. friction clarity;
2. evidenced pain or value;
3. evidence and meaningful size;
4. plausible solvability;
5. bounded scope fit.
Grounding is a gate.
Each dimension records `supported`, `weak-or-unsupported`, or `unknown` with a short evidence-based reason.
Verdicts are `ELIGIBLE`, `NEEDS_EVIDENCE`, or `INELIGIBLE`.
There is no weighted pseudo-precise opportunity score.
`ELIGIBLE` requires grounded concrete friction, meaningful evidenced burden, plausible software/AI/automation improvement, and a bounded intervention.
Material unknowns produce `NEEDS_EVIDENCE`.
Clear non-friction, contradiction, or out-of-scope content is `INELIGIBLE`.
Unknown population prevalence alone does not reject a substantially evidenced practical burden.
Meaningful size means consequential burden, recurrence, blocking impact, costly failure, or constrained tradeoff—not invented market size.
Judge uncertainty and disagreement remain visible.
A small fixed arm-blind subset may be rerun in swapped anonymous order as a bias check.
The judge is not ground truth and does not create an owner-labeling loop.
## Metrics and inconclusive comparisons
The primary descriptive metric is unique judge-eligible frictions per common frozen input count under equal budgets.
Also report:
- absolute eligible count;
- precision among emitted distinct candidates;
- grounding and unknown rates;
- abstention rate;
- cost and latency;
- coverage and processing counts;
- overlap and unique contribution by arm;
- cost per eligible result when defined.
Results are called judge-qualified, not proven market truth.
Yield is never called global recall because the unobserved internet is unknown.
When no result is eligible, cost per eligible result is `n/a` and actual spend remains visible.
An empty input is a valid operational state but not evidence of strategy equality.
Comparison is inconclusive for unequal processing, missing evidence, changed shared configuration, incomplete judging, budget exhaustion, prohibited fallback, or arm failure.
Preserve partial artifacts for diagnosis.
Repeat comparable snapshots before recommending a strategy; do not claim significance from one day.
There is no automatic promotion or rollback threshold.
A serving change requires explicit later instruction, and the prior pinned version remains available for deliberate rollback.
## Serving, reporting, and failure isolation
Serve publication is independent of shadow extraction, judging, and comparison.
A valid serve report proceeds when shadow or evaluator work fails.
Shadow never substitutes automatically for failed serve.
If serve fails, a status-only failure notice may replace findings; yesterday's report is never resent as today's result.
Serve findings are evidence-linked signals, not certified market facts.
Missing assessment is displayed as unavailable or unevaluated.
Structural and evidence-reference validation remains mandatory before publication.
The FINAL canonical human-facing artifact is `report.md`.
Structured candidates and evaluations are intermediate analysis artifacts, not a second report.
A deterministic common template creates Markdown containing its own top summary sections.
HTML, optional PDF, and Discord content are deterministic projections from the frozen Markdown and manifest.
No extra LLM beautification, summary, or independent authoring pass is permitted.
A report revision receives a distinct ID and hash.
Late evaluation never silently changes an already sent report.
HTML is the initial visual projection; PDF is optional and cannot block Markdown.
The offline implementation uses a deterministic canonical-Markdown subset, embedded CSS, escaped untrusted text, and report-hash lineage; its synthetic fixture does not verify target-VPS browser fonts or visual pagination.
PDF remains deferred until a renderer already available in the target environment verifies fonts, links, and page breaks; no renderer installation is authorized by this implementation.
Shadow initially archives candidates, Markdown, and evaluation artifacts without visual rendering or delivery.
Delivery retry reuses the same report and payload.
Ambiguous send outcomes require reconciliation rather than exactly-once claims.
## State, provenance, and trace topology
Persist stable IDs linking acquisition, snapshot, arm extraction, evaluation, report revision, and delivery attempt.
Persist discovery origin, query, provider, and run separately from unique item identity.
Records reference immutable content versions, not only mutable IDs.
Retain normalization, selection, exclusion, dedupe, batching, context assembly, and truncation decisions.
For every LLM call, retain the versioned template, resolved configuration, ordered input IDs, context manifest, truncation decisions, actual redacted resolved prompt or reconstructible content, model settings, output, validation result, and resource usage.
A hash without retained or reconstructible inputs is insufficient for diagnosis.
Each scheduled execution has its own run and trace.
Acquisition, analysis, evaluation, rendering, and delivery may be separate executions linked by stable IDs and span links.
Do not invent one long-lived trace across asynchronous jobs.
Instrument deterministic config resolution, selection, normalization, prompt construction, validation, and presentation as well as LLM calls.
Use structured logs, counts, and independent missed-run detection.
Verify that any Langfuse/OpenTelemetry setup retains normal spans and context; do not assume default LLM-focused filtering does so.
Instrumentation cannot reveal opaque uninstrumented third-party internals.
## Security and data boundaries
Treat retrieved posts, comments, links, and attachments as quoted untrusted data, never instructions.
Place source text only in delimited data fields, never instruction roles.
Source content cannot trigger tools, browsing, URL execution, secret access, or configuration changes.
Schema validation and citation resolution check outputs against the frozen snapshot.
Redact secrets before persistence or export.
Never place credentials, cookies, authorization headers, tokens, or secrets in artifacts, logs, traces, reports, or trace baggage.
Minimize retained public-source personal data and do not build user profiles.
Retention, provider permission, model data path, and external telemetry destination remain fail-closed until approved.
Repairs are proposal-only, with evidence, affected stage, suspected cause, confidence, and a narrow suggested change.
No automatic deployment, package installation, credential rotation, schedule change, paid retry, stealth, CAPTCHA bypass, or proxy cycling is authorized.
Future deterministic retries may cover bounded transient failures; authentication and permission failures require intervention.
## Activation gates
Before activation, separately establish and approve:
- one acquisition provider/path, its terms, permission, credentials, endpoint behavior, and retention rights;
- hard acquisition and model spend caps;
- access and data paths for the selected DeepSeek V4.1 Flash model (both extractors; also the initial judge as a reversible default);
- retention duration and telemetry destination/region;
- Hermes scheduling isolation, script-path behavior, tracing, overlap, timeout, and missed-run handling;
- access to the supplied Discord channel `1557157266824634469`, send time, permissions, file limits, reconciliation, and explicit enablement;
- renderer availability, offline assets, fonts, evidence links, and pagination on the target VPS.
Unset gates remain fail-closed and do not block this completed design.
There are zero current owner design blockers.
Promotion and live activation remain explicit future owner actions.
## Compact acceptance matrix
| Area | Fixture or check | Required property |
|---|---|---|
| Parity | Same frozen manifest in both arms | Byte-identical ordered input and shared settings |
| Experiment | Compare resolved configurations | Only extraction instruction differs |
| Prefilter | Complaint, benign workflow, real workaround | No strategy-specific suppression |
| Snapshot | Edit source after freeze | Current run remains on frozen bytes |
| Grounding | Supported and unsupported claims | Citations resolve; unsupported claims flagged |
| Size | Narrow severe case and vague broad claim | Consequence can qualify; invented prevalence cannot |
| Dedupe | Exact copies and semantic overlap | No duplicate demand; provenance retained |
| Leakage | Arm metadata and cross-arm-only evidence | Judge is arm-blind; no evidence borrowing |
| Budget | Exhaustion and fallback attempt | Comparison inconclusive; no silent model change |
| Empty/partial | Empty snapshot and one-arm failure | Honest status; no false equality |
| Prompt lineage | Deterministic prompt bug, successful call | Fault traceable before, through, and after LLM span |
| Injection | Source text requests actions/secrets | Treated only as data; no action occurs |
| Reporting | Shadow, judge, HTML, or PDF failure | Valid serve Markdown remains independent |
| Revision | Evaluation completes after delivery | Sent report is unchanged; new revision gets new ID |
| Delivery | Timeout or ambiguous send result | Same payload retained; outcome reconciled |
| Secrets | Secret-shaped fixture fields | Redacted before artifacts or telemetry |
| Acquisition | Recorded provider fixture | Parser/contracts pass without claiming live coverage |
| Live preflight | Separately authorized narrow checks | Facts verified before provider or scheduler selection |
Passing fixtures proves contracts and failure handling, not live access, completeness, permission, reliability, or billing behavior.
Implementation and activation require separate authorization.
