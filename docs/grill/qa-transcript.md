# Need Radar：具体问题与实际回答

Hermes session：`20261006_114451_8a8410`。

本文件把具体问题、Hermes 的建议和 ChatGPT 当时提交的代理回答放在一起，便于阅读。问题与建议已核对 Hermes 原会话的公开消息；代理回答来自当时交给 Hermes 阅读的 [02-proxy-answers.md](02-proxy-answers.md)。英文内容保留原文，不以设计结论替代原始问答。

**实际过程是：Hermes 一次提出 Q1–Q30 → ChatGPT 按组回答 → Hermes 阅读后返回 `FRONTIER_EMPTY`。不是 30 轮分别往返。** 以下沿用代理回答的原分组，把相应问题放在回答前面；没有编造逐题的 Hermes 确认。某些 Hermes 初始建议后来被代理纠正，本文同时保留两者；最终设计以 [design.md](../design.md) 为准。

## 阅读索引

| 问题编号 | 主题 |
|---|---|
| Q1–Q4 | 固定输入、实验边界、公共过滤、两版预算 |
| Q5–Q9 | 候选结构、证据、sizeable、workaround、上下文范围 |
| Q10 | 去重以及避免两版互相借用证据 |
| Q11–Q14 | 固定 judge、校准、比较指标、无法得出结论的情况 |
| Q15 | 升级 serve 与回滚 |
| Q16–Q18 | 故障隔离、健康状态、Markdown 内容源 |
| Q19–Q22 | 持久化、错误 prompt 溯源、trace、数据保留 |
| Q23–Q28 | 安全、修复、Treg/其他依赖、Hermes、成本 |
| Q29–Q30 | 测试样例与验收边界 |

## 会话开始：实际提交的消息

**ChatGPT → Hermes：**

> Use grill-with-docs. Read docs/grill/00-bootstrap.md and carry out its bootstrap instruction now. Read AGENTS.md and docs/owner-brief.md. Return the whole decision frontier and session ID; do not implement.

**Hermes 回答开头：**

```text
HERMES_SESSION_ID: 20261006_114451_8a8410
```

以下是该回答中的全部 30 个问题及建议，与代理回答对应排列。

---

## Q1–Q4：固定输入、实验边界、公共过滤、两版预算

### Hermes · Q1

**Immutable comparison unit: What exactly constitutes the fixed input shared by v0/serve and v1/shadow?**

Recommended answer: Freeze one acquisition snapshot as an ordered manifest of normalized item IDs plus immutable references to retained raw payloads, retrieval provenance, timestamps, and content hashes. Both arms consume that same manifest. Late edits, deletions, enrichment, or newly arrived comments belong to a later snapshot, not one arm’s current input.

Owner intent needed: No.

### Hermes · Q2

**Source, retrieval, and extraction boundary: Where does each experimental dimension begin and end?**

Recommended answer:

- Source: enabled platforms, communities, repositories, feeds, and other searched universes.
- Retrieval: queries, ordering, time windows, pagination, item types, comment inclusion, and deterministic source-side filtering.
- Extraction: interpretation of the frozen items into candidate records.
- Common processing: lossless normalization, exact duplicate removal, safety limits, and validation that does not classify whether text represents explicit or latent friction.

Anything likely to favor complaints over workarounds—or vice versa—must be part of retrieval or extraction, not hidden in a “common” prefilter.

Owner intent needed: No.

### Hermes · Q3

**Common prefilter: What may be removed before both extraction arms run?**

Recommended answer: Remove only objectively unusable material: inaccessible/deleted content with no retained text, unsupported encodings that cannot be decoded, exact duplicate payloads, deterministic spam already identified by source metadata, and content beyond an explicit safety/resource ceiling. Do not filter by complaint keywords, sentiment, question form, engagement, first-person language, or inferred relevance before the extraction split. Record every exclusion with a reason code.

Owner intent needed: No.

### Hermes · Q4

**Comparable extraction budgets: How should output and resource limits be equalized?**

Recommended answer: Give both arms the same model, settings, ordered snapshot, context construction rules, token/request ceiling, candidate schema, maximum candidate count, and evidence allowance. Change only the extraction instruction. If batching is required, use identical deterministic batches and retry policy. Do not force both arms to emit the same number of candidates; caps must be equal, but abstention is valid.

Owner intent needed: No.

### ChatGPT · 对 Q1–Q4 的实际回答

Accept. The comparison unit is a versioned immutable snapshot of the SAME ordered normalized item bytes plus manifest, retained evidence references and provenance. A list of IDs alone is insufficient when rows can later mutate. Freeze all shared preprocessing/batching/context/truncation settings. Timestamp cutoffs and late-arriving items are explicit. A later deletion/rights request may remove retained evidence; mark affected replay unavailable rather than promise immutability overrides removal obligations.

Common prefilter: lossless normalization, exact-ID/content deduplication, objective invalid/deleted/unreadable records, allowlisted source metadata spam flags, and equal documented resource ceilings. No complaint/workaround/sentiment/engagement-based suppression. Broad AI relevance belongs to the pinned source/retrieval configuration; do not add a silent semantic filter after freeze. Record exclusion reasons and cap-induced omissions.

Only the extraction-instruction field differs initially. Pin shared model/provider, reasoning settings, tool/memory policy, schema, limits, batches, seed where supported and retry policy. Do not treat temperature zero as guaranteed determinism. Start with a shared configurable maximum K=10 submitted candidates per arm per snapshot as a reversible initial default; allow fewer/zero, never pad. Larger input volumes use the SAME deterministic batches and common bounded consolidation. An arm-specific web/tool/enrichment call is forbidden in the extraction experiment.

---

## Q5–Q9：候选结构、证据与 friction 定义

### Hermes · Q5

**Candidate identity and minimum schema: What must every extracted candidate contain?**

Recommended answer: Require:

- stable candidate ID
- experiment arm and extraction-version ID
- specific affected user or role
- user goal
- concrete obstacle or friction
- consequence or value of resolving it
- reasonably scoped intervention hypothesis
- evidence references to one or more snapshot items
- short evidence excerpts
- extraction confidence as a diagnostic, not judge input
- explicit caveats or unsupported claims
- provenance and prompt/config references

Keep candidate prose compact and avoid speculative market-size or willingness-to-pay fields.

Owner intent needed: No.

### Hermes · Q6

**Evidence sufficiency: What is the minimum grounding required for an eligible candidate?**

Recommended answer: Require at least one direct excerpt that supports the existence of the obstacle and its affected user/goal. Claims of repetition, breadth, severity, cost, or frequency require corresponding independent evidence; otherwise mark them unknown. A single strong report may establish concrete friction but not prevalence. Links alone are not evidence unless the retained snapshot contains the cited text.

Owner intent needed: No.

### Hermes · Q7

**“Meaningfully sized” interpretation: How should the judge assess size without inventing a market?**

Recommended answer: Define size as meaningful practical consequence, repeated burden, blocking impact, costly failure, or credible recurrence—not market capitalization or monetization. Permit a narrow problem affecting few users when its burden is substantial. Award evidence/size only for facts supported by the evidence package; absence of prevalence evidence should produce uncertainty rather than an automatic rejection.

Owner intent needed: No.

### Hermes · Q8

**Workaround qualification: When does a manual workflow count as latent friction?**

Recommended answer: Require evidence of avoidable burden, repeated effort, failure risk, cognitive load, coordination cost, or a constrained tradeoff. Mere use of several steps, a custom script, or an advanced workflow is insufficient. The candidate must state which burden is evidenced and distinguish observed behavior from inferred pain.

Owner intent needed: No.

### Hermes · Q9

**Evidence scope parity: May either arm use surrounding threads or external context?**

Recommended answer: No asymmetric enrichment. Both arms receive exactly the same retained post, comment, thread, and metadata scope from the frozen snapshot. If thread enrichment is tested later, classify it as a retrieval experiment and freeze the enriched dataset before comparing extraction strategies. No web search or arm-specific evidence gathering during extraction or judging.

Owner intent needed: No.

### ChatGPT · 对 Q5–Q9 的实际回答

Accept the proposed minimum semantic content. Store arm/config metadata outside the judge-facing candidate. Candidate IDs identify run-local records, not an invented permanent universal problem taxonomy. Extractor confidence is optional diagnostics and excluded from judging.

At least one preserved excerpt must support the concrete obstacle/user/goal. Quantified impact, recurrence, breadth, explicit willingness to pay, and claimed workaround burden need corresponding evidence. Distinguish direct observation, supported inference, and unknown. A severe recurring problem documented by one user may qualify; it cannot become 'many users' by inference. Like counts, copied campaigns and duplicate posts are not independent demand evidence. Do not resolve cross-platform real-world identities or build user profiles.

Manual steps/scripts alone are not pain. A latent-friction candidate must show burden or constrained tradeoff in evidence. A plausible small intervention is a hypothesis, not proof that implementation succeeds or no existing product solves it. No unsupported novelty/TAM/WTP claims. AI-learning tooling remains in scope when the learner's concrete obstacle is evidenced; generic 'how do I learn AI?' is insufficiently specific.

Both arms and judge can use only the same frozen evidence universe; judge sees each candidate's cited evidence with the same deterministic surrounding-context policy. No fresh web retrieval by an arm or judge. Extra enrichment is a later retrieval experiment, not a hidden extraction change.

---

## Q10：去重与证据泄漏

### Hermes · Q10

**Candidate duplication: How should exact and semantic duplicates be handled?**

Recommended answer: Remove exact duplicates deterministically before extraction. Preserve raw arm outputs, then run one shared, versioned consolidation procedure after extraction to identify semantic clusters without hiding arm provenance. Evaluate both raw yield and consolidated unique yield. Do not allow an LLM deduper to silently delete candidates; retain cluster membership and representative-selection evidence.

Owner intent needed: No.

### ChatGPT · 对 Q10 的实际回答

Accept with one important constraint: consolidate WITHIN each arm using the same versioned policy, retaining raw candidates and membership. Do not merge v0 and v1 evidence before scoring a candidate; otherwise one arm can borrow the other's discovery. Apply cross-arm matching only afterward to report overlap/unique contribution, with ambiguous matches marked uncertain. No permanent vector DB/ontology. Exact matching is deterministic; any semantic matcher is one small fixed evaluation helper, not a new clustering service.

---

## Q11–Q14：固定 judge、校准与比较指标

### Hermes · Q11

**Judge rubric and eligibility: How should the accepted dimensions become a fixed decision rule?**

Recommended answer: Use one versioned rubric for both arms covering:

1. friction clarity
2. evidenced pain/value
3. evidence and meaningful size
4. plausible solvability
5. reasonably scoped intervention

Require grounding as a gate. Have the judge return per-dimension scores, cited evidence, unsupported-claim flags, uncertainty, and an overall eligible/ineligible result. Fix model, prompt, settings, schema, and evidence package for each experiment. Avoid arm labels and extraction-language clues in the judge input where practical.

Owner intent needed: No.

### Hermes · Q12

**Judge calibration: What prevents one fixed judge from becoming unquestioned ground truth?**

Recommended answer: Before relying on comparative scores, create a small arm-blind fixture set containing clear positives, clear negatives, explicit complaints, benign manual workflows, narrow high-impact problems, duplicated evidence, and unsupported market claims. Have the owner eventually review only genuinely ambiguous product examples; routine calibration can be handled by the proxy. Preserve judge disagreement and uncertainty rather than forcing certainty.

Owner intent needed: Owner review is useful before production promotion, but is not required to complete this design.

### Hermes · Q13

**Primary comparison metric: What denominator defines “useful yield”?**

Recommended answer: Use frozen eligible source items as the primary denominator:

unique judge-eligible candidate clusters / eligible input items

Also report:

- eligible clusters per 100 successfully processed items
- candidate precision: eligible clusters / emitted clusters
- evidence-grounding failure rate
- abstention rate
- cost and latency per eligible cluster
- overlap and unique contribution by arm

Never use only “accepted candidates per emitted candidate,” because an arm can improve that number by emitting almost nothing.

Owner intent needed: No.

### Hermes · Q14

**Small or empty samples: When is comparison inconclusive?**

Recommended answer: Mark a run inconclusive when the common snapshot is empty, too few items were successfully processed, either arm breaches its budget, evidence retention fails, or judge coverage is incomplete. Report descriptive results without claiming superiority. Set any numerical minimum only after fixture and pilot data; do not invent statistical significance from one daily run.

Owner intent needed: No.

### ChatGPT · 对 Q11–Q14 的实际回答

Use the same fixed five dimensions and grounding gate. Operational verdicts: ELIGIBLE, NEEDS_EVIDENCE, INELIGIBLE. Each dimension records supported / weak-or-unsupported / unknown plus a short evidence-based reason; no weighted pseudo-precise overall opportunity score. ELIGIBLE requires a concrete grounded friction, meaningful evidenced burden, plausible software/AI/automation improvement and a bounded intervention. Material unknowns become NEEDS_EVIDENCE. Unknown population prevalence alone is not a material failure when substantial practical burden is evidenced. Clear non-friction/contradiction/out-of-scope is INELIGIBLE. This is a versioned rule, not a per-arm subjective preference.

Hide arm labels, extractor prompt/confidence, cost and version name from the judge. Fix judge model/rubric/settings/evidence policy within an experiment epoch. Bias checks can swap anonymous order and rerun a small fixed subset; no full second evaluation platform. Fixture expectations are semantic constraints, not hidden claims of human-labelled ground truth. Automatic disagreement/uncertainty stays visible. Do NOT add a personal-feedback module or require the owner to continually label outputs. Ambiguous facts get abstention; only genuinely changed product intent would require asking the owner.

Primary descriptive measure: unique judge-eligible frictions per common frozen input count, within equal K/call/token budgets. Also preserve eligible absolute count, precision among emitted distinct candidates, grounding/unknown rates, cost, latency, overlap/unique contribution, and coverage. Call these 'judge-qualified', not proven market truth. Never call yield global recall: the unobserved internet is unknown. Cost per eligible result is undefined when none qualify; use n/a plus actual spend, not divide-by-zero or zero-cost claims.

Empty input is a valid operational status but not evidence of equal strategy quality. Unequal processing, missing evidence, changed model, incomplete judging or budget failures produce an inconclusive comparison. Prefer inconclusive over post-hoc intersection selection. Preserve partial outputs for diagnosis. Repeat comparable snapshots before drawing a conclusion; no automatic significance claim from one day.

---

## Q15：升级 serve 与回滚

### Hermes · Q15

**Promotion and rollback: What would justify replacing v0 with v1?**

Recommended answer: Do not auto-promote. Require repeated comparable runs showing better unique eligible yield or materially better evidence-backed coverage, with no unacceptable regression in grounding, cost, latency, or failure rate. Promotion requires owner review because it changes the user-facing extraction strategy. Keep the prior extraction version selectable for rollback. Roll back when grounding or reliability breaches an agreed safety threshold, not merely because one day’s yield is lower.

Owner intent needed: Yes—final production promotion and acceptable tradeoff thresholds belong to the owner.

### ChatGPT · 对 Q15 的实际回答

No automatic promotion or rollback policy with invented thresholds. Produce a comparison recommendation after repeat runs; the serving version changes only on an explicit later instruction. Retain the prior pinned version for deliberate rollback. This is not a present decision blocker and does not introduce preference feedback. Threshold tuning and any tradeoff approval belong to that future change request.

---

## Q16–Q18：故障隔离、健康状态、报告内容源

### Hermes · Q16

**Partial-arm failure: What happens when serve or shadow fails?**

Recommended answer:

- Serve succeeds: produce the canonical serve report even if shadow extraction, judging, or comparison fails.
- Shadow succeeds but serve fails: retain shadow artifacts for diagnosis, but do not publish them as the daily digest.
- One arm partially processes the snapshot: do not compare unmatched subsets as if complete; either compare the deterministic intersection with an explicit degraded label or declare the run inconclusive.
- Judge failure: preserve extraction artifacts; do not fabricate evaluation.
- Optional renderer failure: retain Markdown and continue permitted delivery from the canonical report when safe.
- Delivery failure: preserve the report and record delivery failure separately; do not rerun extraction merely to resend.

Owner intent needed: No.

### Hermes · Q17

**“No findings” versus degraded coverage: What status model should reports use?**

Recommended answer: Distinguish at least:

- healthy run with findings
- healthy run with no eligible findings
- degraded source coverage
- degraded extraction
- evaluation unavailable
- report-generation failure
- delivery failure
- scheduled run missing

A successful process exit or HTTP response must not imply healthy data. The user-facing digest must not say “no problems found” when acquisition or analysis was incomplete.

Owner intent needed: No.

### Hermes · Q18

**Canonical output and rendering: What artifact is authoritative?**

Recommended answer: Make one structured canonical result plus a deterministic Markdown rendering authoritative. Generate HTML/PDF and Discord excerpts deterministically from that same result. Do not perform another LLM rewrite during presentation. Serve rendering/delivery must not depend on shadow completion. Treat Quarto and Chromium as unverified options; approve them only after a narrow fixture preflight demonstrates acceptable HTML/PDF output and operational simplicity.

Owner intent needed: No.

### ChatGPT · 对 Q16–Q18 的实际回答

Accept failure isolation. Shadow and comparison/judge failure must not stop a valid serve digest. Do not substitute shadow automatically when serve fails. Comparison is offline/asynchronous relative to report publication. Serve findings are presented as evidence-linked candidate signals, not as judge-certified market facts; when assessment is absent, display evaluation unavailable/unevaluated instead of fabricating a score. Structural/evidence-reference validation still applies before publishing. If serve itself fails, a status-only failure notice can replace findings; never re-send yesterday's report as today's results.

CLARIFICATION to Q18: the FINAL canonical human-facing report is report.md, as the owner selected. Structured candidate/evaluation artifacts are intermediate analysis data, not a second independently edited report. A deterministic common template may render those intermediates into Markdown. HTML, PDF and Discord projections come from the frozen Markdown and the same manifest. Do not have two independently authored truths or an extra LLM beautification/summarization call. Top summary sections must already be in the canonical report.

A report revision receives its own ID/hash; an evaluator finishing later must not silently alter a previously sent report. HTML is the initial visual projection; PDF is supported but optional per run and cannot block Markdown delivery. Use Quarto/Chromium only after a small renderer check. Shadow initially archives raw candidates plus Markdown/evaluation artifacts, with no automatic HTML/PDF rendering or delivery. A bounded delivery retry reuses the same report and payload; uncertain send outcomes need reconciliation, not claims of exactly-once delivery.

---

## Q19–Q22：持久化、prompt 溯源、trace、数据保留

### Hermes · Q19

**Minimum durable state: What must be retained to replay and diagnose a run?**

Recommended answer: Retain only:

- run and experiment IDs
- source/retrieval/extraction/judge/render configuration versions
- snapshot manifest and hashes
- normalized item records and raw-artifact references
- deterministic exclusion/deduplication decisions
- batch/context manifests
- prompt template and resolved prompt references
- model/settings and resource usage
- raw structured arm outputs
- validation and judge outputs
- semantic-cluster membership
- report artifact references
- delivery attempt/outcome
- timestamps, failure states, and lineage links

SQLite plus filesystem artifacts is sufficient initially. Do not introduce a workflow engine, event bus, vector database, or separate analytics store without demonstrated need.

Owner intent needed: No.

### Hermes · Q20

**Prompt-construction diagnosis: How should resolved LLM inputs be observable?**

Recommended answer: Save the versioned template, config IDs, ordered input IDs, context assembly manifest, truncation decisions, and a redacted resolved-prompt artifact or reproducible prompt hash/reference. Record deterministic transformations before the call. Do not rely solely on Langfuse or application logs, and do not claim automatic visibility into opaque third-party internals.

Owner intent needed: No.

### Hermes · Q21

**Trace topology: Should acquisition, analysis, evaluation, rendering, and delivery share one trace?**

Recommended answer: No. Give each execution its own run/trace, connected by stable run, snapshot, candidate, evaluation, and report IDs. Use OpenTelemetry-compatible context only where it adds diagnostic value. A single trace spanning daily asynchronous jobs is unnecessary and brittle. Validate whether Langfuse plus structured logs provides enough LLM visibility before operating additional telemetry infrastructure.

Owner intent needed: No.

### Hermes · Q22

**Retention and redaction: What is the least necessary data policy?**

Recommended answer: Keep retained source text only when necessary for evidence, replay, and permitted use; otherwise retain minimal normalized excerpts and content hashes. Never put credentials, cookies, authorization headers, personal access tokens, or full third-party response bodies into traces. Apply deterministic secret redaction before telemetry export. Make trace/raw-artifact retention configurable and fail closed if provider terms or authorization are unclear.

Owner intent needed: Yes for any production retention duration or external telemetry destination; no duration should be invented during this interview.

### ChatGPT · 对 Q19–Q22 的实际回答

Accept SQLite plus files and the proposed information categories; do not implement a table per noun or build an event-sourcing framework. Records reference immutable content versions, not only mutable item IDs. Persist discovery origin/query/provider/run separately from unique item identity. Retain processing decisions and actual resolved prompts under the approved data policy; a hash without content or reconstructible inputs does not enable diagnosis.

Each scheduled execution has a run/trace; link acquisition -> snapshot -> both arms -> evaluation -> report -> delivery with stable IDs and span links where supported. Spans include deterministic config resolution, selection, normalization, context assembly, truncation, prompt rendering, output validation and presentation. No claim that wrapper instrumentation reveals opaque third-party internals. The current Langfuse docs explicitly warn its default SDK filter focuses on LLM spans; verify our normal spans/root/context survive export. Prefer minimal backend plus structured logs/counts and separate missed-run detection, not a mandatory new monitoring stack.

Redact secrets before persistence/export. Minimize public source personal data; never include secrets in trace baggage. Retention duration, external telemetry destination/region, provider terms and authorized model data path remain UNSET fail-closed live activation inputs. No new external export or source-data retention obligation is approved by this interview. Within future approved retention, snapshot contents permit replay; after removal, mark the limitation honestly.

---

## Q23–Q28：安全、修复、依赖与成本

### Hermes · Q23

**Untrusted-content handling: How should source text be isolated from instructions?**

Recommended answer: Treat every retrieved post, comment, link, and attachment as quoted evidence, never instructions. Delimit source content structurally, exclude it from system/developer instruction positions, require schema-constrained output, block source-requested tool calls or secret access, and validate citations against the frozen snapshot. Do not let extracted URLs trigger automatic browsing or execution.

Owner intent needed: No.

### Hermes · Q24

**Repair proposals: What may the system do after diagnosing failures?**

Recommended answer: It may generate a read-only repair proposal containing evidence, suspected root cause, affected stage, confidence, and a narrowly scoped suggested change. It must not mutate production configuration, install packages, rotate credentials, deploy, alter schedules, or retry paid requests without separate authorization. Deterministic bounded retries may be designed only for clearly transient failures and must preserve attempt lineage.

Owner intent needed: Owner authorization is required for any future automatic mutation; default is proposal-only.

### Hermes · Q25

**Acquisition-provider validation: What must be proven before selecting Treg or another provider?**

Recommended answer: Use a narrow, non-paid preflight or documented fixture to verify identity and interface, authorization and retention terms, Reddit/X coverage, posts versus comments, pagination, time filtering, stable IDs, edits/deletions, ordering, quotas, error semantics, response consistency, and VPS compatibility. Until verified, describe Treg only as a candidate. Do not make it architecturally mandatory or run paid/live tests during this interview.

Owner intent needed: Owner approval is required before meaningful spend, credentials, or acceptance of provider terms.

### Hermes · Q26

**Optional dependencies: What role should OpenMagpie, Agent-Reach, and direct APIs have?**

Recommended answer: Keep all outside the initial mandatory path. Consider direct GitHub/HN APIs only when those sources are activated. Consider Agent-Reach only as a separately measured enrichment or fallback after the base path works. Consider OpenMagpie only if it can replace—not duplicate—a substantial acquisition/monitoring responsibility. Do not operate overlapping ingestion systems speculatively.

Owner intent needed: No.

### Hermes · Q27

**Scheduler and delivery validation: What must be proven about Hermes before relying on it?**

Recommended answer: Conduct later narrow preflights for unattended deterministic command scheduling, environment/config handling, timeout and overlap behavior, missed-run detection, artifact persistence, exit-state reporting, Discord delivery semantics, and correlation IDs. Do not reproduce Hermes scheduling or gateway capabilities in this project unless a verified gap blocks the minimum system.

Owner intent needed: Credentials, destination channel, send time, and permissions remain fail-closed owner-supplied activation inputs.

### Hermes · Q28

**Cost controls: What defaults apply before budget values exist?**

Recommended answer: Design explicit per-run limits for retrieved items, LLM requests/tokens, retries, and optional rendering/enrichment. With no owner-approved dollar cap or provider budget, paid acquisition and production LLM schedules remain disabled. Fixture and mock validation may proceed later without live spending. Budget exhaustion should degrade visibly, never silently change models, evidence scope, or experimental parity.

Owner intent needed: Yes—the owner must approve ongoing spend and production caps.

### ChatGPT · 对 Q23–Q28 的实际回答

Accept quoted untrusted content, no source-triggered tools/URL execution and proposal-only repair. A future deterministic retry policy can be bounded for transient errors; auth/permissions failures require intervention, not evasion. No rotating-proxy/CAPTCHA/stealth project is authorized.

Fixtures prove parser, contracts and error handling; they cannot prove live VPS access, anti-bot resistance, provider permission, completeness or actual billing. Those require a separately authorized narrow live preflight. Treg is preferred to investigate, NOT a validated/mandatory vendor; OpenMagpie/Agent-Reach/direct APIs remain alternatives, not parallel mandatory layers. Select one minimal acquisition path before building more integrations.

Installed Hermes CLI help already confirms --no-agent/--script/--workdir/--deliver and model pinning; runtime scheduling, script path restrictions (~/.hermes/scripts), isolation, tracing and Discord behavior still need preflight. No jobs were created.

No approved live dollar cap/key/destination means no live activation. Later gates: acquisition/provider permission and capped spend; extractor+judge model costs; data/telemetry retention/destination; Discord target/time and explicit enablement. Do not ask the owner these merely to finish design documents. Record them in an activation checklist and proceed with independent design. This review itself only invokes the requested Hermes interview using its existing configuration, not production data APIs.

---

## Q29–Q30：测试样例与验收

### Hermes · Q29

**Smallest fixture set: What examples are required before implementation authorization?**

Recommended answer: Define a compact fixture corpus covering:

- explicit, well-evidenced pain
- explicit complaint with no solvable obstacle
- genuine workaround with evidenced burden
- benign manual workflow with no evidenced friction
- repeated decision burden
- narrow but high-impact problem
- vague broad complaint
- unsupported market-size claim
- duplicated/cross-posted evidence
- prompt-injection text
- deleted or malformed item
- empty snapshot
- partial acquisition
- one-arm failure
- judge failure
- renderer and delivery failure

Expected properties should focus on grounding, parity, abstention, lineage, and failure isolation—not brittle exact prose.

Owner intent needed: No.

### Hermes · Q30

**Acceptance seams: What must be demonstrated before any live pilot?**

Recommended answer: Require fixture-based proof that:

1. both arms consume byte-identical snapshot manifests;
2. only extraction instructions differ;
3. common filters do not encode arm-specific relevance;
4. candidate citations resolve to retained evidence;
5. unsupported size/value claims are flagged;
6. exact and semantic duplicate handling preserves provenance;
7. judge inputs are arm-blind and otherwise identical;
8. serve survives shadow/judge/PDF failures;
9. degradation is distinguishable from zero findings;
10. a final report can be traced backward through prompts, transformations, normalized items, and retrieval origin;
11. untrusted source instructions cannot invoke actions;
12. no secret-bearing fields enter artifacts or telemetry.

Owner intent needed: No.

### ChatGPT · 对 Q29–Q30 的实际回答

Accept compact fixture coverage and property-based acceptance seams. Add: arm metadata leakage; shared filter discarding benign/real workarounds unfairly; budget exhaustion/model fallback; duplicate evidence counted as independent; input edits after snapshot; ambiguous send outcome; and deterministic prompt bug with otherwise successful LLM call. These are test-case descriptions now, not implemented tests or proof of a working system.

No current owner-required design choice remains in the frontier as answered. Future activation and promotion approvals are explicitly reserved, not fabricated. Continue with any genuinely new material contradiction or missing contract. If none, print FRONTIER_EMPTY and finish. Remain read-only; documentation finalization will follow separately.

---

## 批量回答如何提交，以及 Hermes 的实际确认

以上 ChatGPT 回答当时保存在 `docs/grill/02-proxy-answers.md`，不是逐题发送。该文件开头还包含以下指令：

> Read `docs/evidence-notes.md` for the limited checks completed while you interviewed. Do not research externally or rewrite documents in this frontier turn. Accept settled answers, identify only NEW material gaps, and finish with the next short frontier or FRONTIER_EMPTY.

恢复同一个 session 时，实际发送给 Hermes 的消息为：

> Continue this same grill-with-docs session as the interviewer. Read docs/grill/02-proxy-answers.md and docs/evidence-notes.md. Accept the proxy answers to Q1-Q30. Stay read-only, do not implement, do not browse externally, and do not invoke interactive clarification tools. Recompute only the NEW material frontier; do not repeat settled questions or treat deferred activation inputs as current blockers. Print at most eight new questions with recommendations, or FRONTIER_EMPTY if nothing material remains. End the turn immediately after the frontier.

**Hermes 的完整公开回复：**

```text
FRONTIER_EMPTY
```

这之后没有第二批追问。随后另开一个同 session 的文档定稿回合，不是新一轮设计问答。

## 文档定稿回合：实际消息

**ChatGPT → Hermes：**

> The grill frontier is closed (FRONTIER_EMPTY). Perform DOCUMENTATION-ONLY finalization in this same session. Read docs/grill/02-proxy-answers.md and preserve all reconciliations. Use file tools to create only docs/design.md and docs/decisions.md in this turn; do not alter owner-brief.md or existing interview records. Keep design around 150-220 lines and decisions around 70-120 lines, linking authoritative inputs instead of copying them. Design must cover the minimal system/dependency boundary (Treg tentative, optional alternatives), extraction-only parity, fixed judge/size interpretation, metrics and inconclusive cases, no personal-feedback feature, shared neutral prefilter, within-arm dedupe before cross-arm comparison, Markdown as FINAL canonical report, independent serving, deterministic prompt provenance, trace topology, security, activation gates, and a compact acceptance matrix. Decisions should mark owner-approved versus reversible proxy default versus deferred live activation; zero current owner design blockers; K=10 is configurable proxy default, not owner mandate; no auto-promotion. Do not turn this into a milestone. No implementation, installations, live calls, credentials, schedules, deployments, git operations, or external research. If your normal skill expects context/ADR artifacts, use these two documents as the current design/context and compact decision record; avoid duplicate boilerplate. Finish with DOCS_FINALIZED and exact filenames, then end the turn.

**Hermes 的完整公开回复：**

```text
DOCS_FINALIZED

docs/design.md
docs/decisions.md
```

## 原始记录与核对方式

原有文件未覆盖：

- [01-hermes-frontier.md](01-hermes-frontier.md)：问题与建议的压缩整理版。
- [02-proxy-answers.md](02-proxy-answers.md)：当时提交的代理回答文件。
- [03-hermes-closure.md](03-hermes-closure.md)：收敛确认。
- [session.md](session.md)：会话 ID、运行方式与文档定稿记录。
- [discussion.md](discussion.md)：中文结论与导航，不是问答全文。

本次使用 Hermes 的本地 session export 只读核对公开的 user/assistant 消息，没有重新运行模型或重新开展访谈。核对命令：

```sh
hermes sessions export --format jsonl --session-id 20261006_114451_8a8410 --redact - \
  | jq -r '.messages[] | select(.role == "user" or (.role == "assistant" and (.content // "") != "")) | "ROLE: \(.role)\n\(.content)\n--- END MESSAGE ---"'
```

本文仅含公开提问、建议、已提交的代理回答和结束消息，不含模型内部推理、凭证或无关工具输出。
