# Need Radar — ticket review

Status: **review draft, not published**.

Architecture review is ready; no owner architecture decisions remain. This document records the final same-session proposal, not implementation or live-operation authorization. Ticket numbers are planning identifiers, not GitHub issue numbers.

## Principle and component boundaries

Prove one thin product path through Reddit, then extend the shared path with source-specific adapters. A vertical ticket includes its own end-to-end acceptance; do not create a gate after every increment. The final scheduled live pilot is the separate integration acceptance boundary.

Target: AI application-layer builder friction. Core V0 is **Reddit + X**. GitHub Issues/Discussions is optional and nonblocking; HN is backlog and YouTube is excluded. Scope follows [target-scope.md](target-scope.md); experiment and reporting contracts follow [design.md](design.md).

| Boundary | Responsibility |
|---|---|
| Source-specific | Acquisition, provider-response normalization, source-qualified identity mapping, thread relationships and coverage facts |
| Shared | Persistence/versioning, exact dedupe/idempotency, frozen snapshots, context/prompt construction, extraction execution, serve/shadow runner, fixed judge, canonical Markdown and lineage |
| Multiple sources | Mixed-source coverage/degradation and cross-source semantic overlap within each arm |
| Presentation / delivery | Local deterministic projections and separately stateful Discord delivery/reconciliation |

Only extraction instructions differ in the first v0/v1 experiment. Both arms share frozen evidence, settings, budgets, schema and one fixed judge. Serve must remain independent of shadow/evaluation failures. Every implementation ticket includes its own end-to-end acceptance and extends shared observability. Live authorization gates are additional to the technical dependencies below.

## Final ticket outline

### 01. Establish the minimal offline serve tracer

**Blocked by:** none.

**Responsibility:** Run a small normalized fixture through persisted frozen input, v0 prompt construction, a fake model boundary, evidence validation and canonical Markdown, establishing only the contracts needed for that path.

### 02. Establish shared deterministic and LLM observability

**Blocked by:** 01.

**Responsibility:** Instrument the tracer with structured logs, local trace/span context, reconstructible prompt/artifact lineage and a tested Langfuse exporter boundary, keeping authorized remote verification explicit and nonblocking for local analysis.

### 03. Prove bounded Reddit acquisition and retain reusable recordings

**Blocked by:** none.

**Responsibility:** Verify the approved Reddit route using a reusable thin collector with request limits, bounded retries, fail-closed spending controls and sanitized response/provenance recordings.

### 04. Connect recorded Reddit to the shared serve path

**Blocked by:** 02, 03.

**Responsibility:** Add Reddit-specific normalization, thread relationships and coverage translation while reusing the existing persistence, snapshot, extraction, validation, report and tracing path.

### 05. Establish stable evidence identity and replay semantics

**Blocked by:** 04.

**Responsibility:** Prove source-qualified identity, exact deduplication, idempotent re-import, content versioning, multiple discovery origins and frozen snapshot stability through repeated and edited Reddit inputs.

### 06. Add the shared extraction-only shadow runner

**Blocked by:** 05.

**Responsibility:** Execute v0 and v1 against identical frozen input and shared settings with enforced equal resource ceilings, independent outcomes and no shadow publication or serve substitution.

### 07. Assess candidates under one fixed, arm-blind rubric

**Blocked by:** 05.

**Responsibility:** Apply bounded within-arm consolidation and the fixed judge to produce evidence-grounded assessments while preserving uncertainty and leaving valid serve output independent of assessment failures.

### 08. Compare assessed extraction arms under verified parity

**Blocked by:** 06, 07.

**Responsibility:** Perform post-score cross-arm matching and descriptive comparison with correct denominators, resource accounting, uncertainty and explicit inconclusive states.

### 09. Prove bounded X acquisition and retain reusable recordings

**Blocked by:** none.

**Responsibility:** Verify the approved X route using bounded reusable collection, retry and spending controls, preserving response recordings and time-window, pagination and conversation-coverage limitations.

### 10. Connect recorded X to the existing shared path

**Blocked by:** 05, 09.

**Responsibility:** Add X-specific identity, reply/quote relationships and coverage translation without recreating downstream persistence, snapshots, extraction, judging or reporting.

### 11. Produce one honest Reddit+X experiment

**Blocked by:** 08, 10.

**Responsibility:** Assemble one common snapshot with per-source coverage/degradation and extend established within-arm consolidation for demonstrated cross-source overlap without inflating independent evidence.

### 12. Verify the approved DeepSeek live-model route

**Blocked by:** 02.

**Responsibility:** Exercise the shared invocation boundary with bounded authorized requests, verifying model identity, isolation, usage/cost reporting, retry and budget enforcement, no fallback and replayable request/response lineage.

### 13. Render deterministic report presentations

**Blocked by:** 04.

**Responsibility:** Project immutable Markdown into readable standalone HTML and, where straightforward with a verified renderer, optional PDF while preserving content identity and keeping rendering failures nonblocking.

### 14. Deliver and reconcile a frozen report through Discord

**Blocked by:** 04.

**Responsibility:** Verify Hermes/channel behavior at the delivery boundary and implement deterministic Markdown-derived payloads, bounded retries and ambiguous-outcome reconciliation, with actual send/read-back gated by authorization.

### 15. Prepare and test bounded scheduled operation

**Blocked by:** 11, 12, 14.

**Responsibility:** Connect the existing collectors, model execution and delivery through Hermes, implementing and testing overlap protection, timeouts, shared run-budget accounting, independent missed-run detection and a disable procedure before a real scheduled pilot.

### 16. Verify one authorized scheduled Reddit+X pilot

**Blocked by:** 15.

**Responsibility:** Run the already implemented core path with approved live inputs and limits, verifying actual trigger, acquisition, experiment, report, delivery, telemetry and health outcomes without introducing new component behavior.

### 17. Optional: prove GitHub acquisition

**Blocked by:** no technical ticket; optional-extension activation gate.

**Responsibility:** Verify official application-layer repository identities and Issues/Discussions access through bounded collection that produces reusable recordings and capability evidence.

### 18. Optional: connect GitHub evidence to the shared path

**Blocked by:** 05, 17; optional-extension activation gate.

**Responsibility:** Normalize GitHub issues/discussions and comments/replies into the existing evidence contract and verify downstream reuse without introducing a GitHub-specific analytical pipeline.

## Dependency DAG

```text
Shared tracer and experiment:

01 --> 02 --> 04 <-- 03
        |      |
        |      v
        |     05
        |    / | \
        |   v  v  v
        |  06 07 10 <-- 09
        |   \ /   |
        |    v    |
        |    08   |
        |     \  /
        |      v
        |      11
        v
        12

Presentation, delivery and operation:

04 --> 13   [HTML / optional PDF; not a delivery blocker]
04 --> 14   [Discord from canonical Markdown]
11 + 12 + 14 --> 15 --> 16

Optional GitHub extension:

05 + 17 --> 18
```

Prioritize 06–08 as the next shared product increment after Reddit works. Assessment 07 and shadow execution 06 can develop independently; comparison 08 joins them. X preflight and adapter work can proceed independently where dependencies allow. GitHub starts only after the Reddit+X core works end to end and its optional extension is activated: this is product sequencing, not a technical dependency on cross-source semantics. Neither 17 nor 18 blocks core work.

## Observability ownership

**02 owns the shared foundation:** structured deterministic events; local trace/span context; stable run/snapshot/artifact/report/model-call IDs; actual redacted or reconstructible prompts and model outputs; configuration, input order, context/truncation decisions, validation and usage evidence; redaction before persistence/export; and the Langfuse exporter boundary. A seeded deterministic prompt-construction bug must be traceable upstream, not merely visible in the LLM span.

- 03/09/17 preserve acquisition requests, response recordings, coverage and usage evidence; adapters 04/10/18 connect them to shared normalization traces.
- 05 records import, dedupe, version and discovery-origin decisions.
- 06–08/11 add arm, consolidation, judge and comparison lineage without separate per-source tracing systems.
- 12 records real model request/response identity and enforcement outcomes.
- 13/14 add renderer, report-revision, payload and delivery-attempt links.
- 15/16 add scheduled execution and independent health evidence, using separate linked executions rather than one cross-day trace.

Local logs/spans/artifacts must work without remote Langfuse. Remote export is explicitly **unverified, verified or failed** and stays disabled until authorized and verified, including non-LLM span/context preservation. A local test sink does not establish remote success. Remote telemetry failure must not block valid serving. No new telemetry platform is implied.

## Why these boundaries

- **07 versus 08:** assessment checks each candidate's grounding/value/solvability under one rubric; comparison checks parity, denominators and post-score overlap across already assessed outputs. A descriptive difference is not a general strategy recommendation.
- **Former bundled runtime/provider/trace-delivery preflight:** removed because model access, Discord, scheduling and telemetry have different approvals and failure modes. Model verification belongs in 12; Hermes delivery capability in 14; scheduler behavior in 15; optional authorized Langfuse verification in 02.
- **13 versus 14:** rendering is local deterministic transformation; Discord is externally stateful and needs authorization and reconciliation. Markdown delivery does not depend on rendering.
- **05:** establishes stable evidence semantics, not a data-erasure subsystem. Retention/removal requirements are resolved before affected real-data use through narrowly scoped prerequisite work when necessary.
- **15 versus 16:** operational behavior is implemented and tested before the live pilot. The pilot verifies composition rather than discovering missing implementation.

The pilot's prerequisite ownership is explicit: acquisition bounds/retries/spending controls in 03/09; evidence stability in 05; arm ceilings/isolation in 06; assessment/comparison in 07/08; mixed-source behavior in 11; live model controls in 12; delivery reconciliation in 14; scheduler and aggregate controls in 15; tracing in 02 and every extension. Missing behavior returns to its owning ticket, not into 16.

## Core completion and execution gates

Core completion is the authorized, verified **Reddit+X** path through acquisition, normalization, shared frozen evidence, serve/shadow, one fixed judge/comparison, canonical report, delivery and bounded scheduled operation. GitHub is optional/nonblocking. HTML is an independent presentation deliverable; optional PDF never gates Markdown delivery or the core pilot.

Unresolved execution gates do not reopen the architecture:

- Implementation authorization, approved exact communities/query seeds/provider routes, and source access/retention rights.
- Hard acquisition/model/run limits, billing evidence and permitted retries; no invented budget or automatic top-up/fallback.
- Approved real-data retention/removal policy, including derived artifacts; required removal work must precede the affected data use. Snapshot immutability does not override removal obligations.
- Approved DeepSeek-serving provider/account, credentials and model data path. The selected model remains **DeepSeek V4.1 Flash**; no global Hermes model change.
- Authorized Langfuse destination/region and payload policy before remote export; local tracing remains sufficient for diagnosis.
- Renderer availability and any separately authorized installation; no automatic package setup.
- Hermes Discord access and explicit send permission for channel **1557157266824634469**, followed by actual read-back verification.
- Schedule/time, unattended-operation limits, pilot authorization and disable procedure before activation.
- Explicit activation of optional GitHub, with official repository slugs/activity verified rather than guessed.

Preserve permitted sanitized source/model recordings privately outside the public repository. Offline tests replay them or clearly labeled synthetic fixtures; missing recordings never trigger silent live calls. No new provider, seed, budget, retention period or implementation language is approved here.

## Conditional split notes

- **Within-arm consolidation (07):** keep it as a small bounded shared helper only; a separate model policy, semantic uncertainty contract or substantial implementation requires a prerequisite split. Do not claim semantic deduplication from exact matching alone.
- **GitHub Issues/Discussions (18):** keep together unless actual API/thread/pagination uncertainty warrants a split; REST versus GraphQL alone is not a reason.
- **Renderer/PDF (13):** HTML can complete independently; difficult renderer setup or PDF support is separately scoped/deferred rather than making core delivery wait.
- **Mixed-source overlap (11):** extend existing consolidation only; substantial new semantic work requires a narrow split, not a clustering platform hidden inside integration.

No owner architecture decisions remain. This is the single current ticket review draft; publication and execution are separate actions.
