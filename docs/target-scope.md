# Need Radar target and source scope

Updated: 2026-10-06. Status: owner-approved scope for source selection, retrieval design, extraction prompts, judging, and ticket generation. This document supersedes earlier optional-source examples where they conflict.

## 1. What Need Radar is trying to find

Need Radar should discover **application-layer friction in building with AI**: concrete problems encountered by people using existing models to build, operate, extend, or learn to build AI applications.

The target is not "anything difficult in AI." The system should bias toward problems that can plausibly be improved by a reasonably scoped tool, repository, workflow, harness feature, plugin, integration, automation, or deterministic/LLM-assisted application component.

Examples that are in scope:

- agent harness and runtime behavior;
- context construction, context handoff, compaction recovery, and application memory;
- model routing as an application policy/tool, including cost/quality/task routing;
- plugins, tools, MCP integrations, connectors, and permission/credential UX;
- agent reliability, retries, recovery, validation, guardrails, and human-in-the-loop workflows;
- evaluation and observability for AI applications, including tracing deterministic prompt construction as well as LLM calls;
- coding-agent and other AI-assisted developer workflows;
- orchestration between models, tools, agents, and deterministic code;
- developer UX around configuring, debugging, testing, and operating AI applications;
- practical learning friction for people trying to build AI applications, when the obstacle is specific enough to support a scoped intervention.

The following are **not the primary target**:

- model architecture research;
- pretraining/training algorithm optimization;
- GPU kernels, CUDA optimization, distributed training, compiler work;
- low-level inference-engine performance, serving internals, quantization research, or model-weight optimization;
- benchmark/model-quality research that does not expose an application-layer workflow problem.

A topic that touches infrastructure can still qualify **only when the observed friction is at the application boundary**. Example: "users manually choose between expensive and cheap models for each coding task" is in scope as an application-level routing problem; "optimize transformer kernels for lower latency" is not.

## 2. What counts as a useful finding

The fixed judge remains unchanged. A useful finding is a **concrete, valuable, meaningfully sized, evidenced, solvable friction with a reasonably scoped intervention**.

"Sizeable" means meaningful burden, recurrence, blocking impact, costly failure, or a constrained tradeoff. It does not require a large market, monetization, or invented willingness to pay.

The system should surface the friction and evidence. It does not decide whether the owner personally wants to build it.

## 3. Initial source scope

The **core first-round source set** is:

1. **Reddit**
2. **X**

These two sources must reach the end-to-end product path without depending on GitHub. **GitHub Issues/Discussions is an optional source extension after the Reddit+X path is working end to end.** It may reuse the same shared downstream components, but it must not block the core Reddit+X report, serve/shadow experiment, delivery, scheduling, or bounded live pilot.

### Reddit

Use selected communities with a high density of people building or using AI applications. Exact communities and retrieval rules should be proposed and evaluated, not silently treated as owner-approved merely because they appeared in an earlier draft.

### X

Use public search/query strategies aimed at AI-application workflows and builder friction. Exact query families are an experimental/retrieval choice, not an owner-approved fixed list unless separately recorded.

### GitHub Issues/Discussions — optional extension

After Reddit+X work end to end, optionally track **popular, actively used open-source AI application-layer projects**, especially projects whose users file concrete workflow, integration, agent, tool, context, reliability, or developer-experience issues.

Owner-provided examples include:

- Hermes Agent;
- pi agent;
- OpenClaw.

Verify exact repository slugs and activity before implementation; the examples establish the **kind of repository**, not permission to invent a specific GitHub URL.

Prefer repositories that expose application-layer friction. Avoid choosing repositories primarily because they are large if their issues are dominated by model internals, training, inference-engine, GPU, kernel, quantization, or other low-level infrastructure work.

GitHub Issues/Discussions are valuable because a feature request, recurring bug, duplicate issue, workaround, or high-engagement discussion can already be close to a concrete problem statement.

## 4. Source backlog / out of initial scope

- **GitHub Issues/Discussions:** optional extension after the Reddit+X core path works end to end; not a blocker for the core pilot.
- **Hacker News:** backlog source. Do not create first-round acquisition, implementation, or verification tickets for HN unless the owner explicitly promotes it.
- **YouTube:** not part of the current source plan.
- Other sources remain future candidates only.

Optional, tentative, backlog, future, and alternative sources must **not** be promoted into implementation tickets, blockers, or acquisition tests without an explicit source-scope decision.

## 5. Source and target are separate from the shadow experiment

The first serve/shadow experiment still changes **extraction only**:

- v0 / serve: explicit pain, complaint, request, missing capability;
- v1 / shadow: workaround, repeated manual workflow/decision, latent friction.

Both arms consume the same frozen source/retrieval snapshot. The application-layer target is shared by both arms; it must not become a v0-specific complaint filter or a v1-specific workaround filter before the extraction split.

Later source or retrieval experiments may deliberately change source/retrieval strategy one dimension at a time while holding downstream extraction and judging fixed.

## 6. Retrieval implications

Retrieval should maximize evidence about application-layer builder friction without requiring complaint keywords at the common-filter stage.

Good source/retrieval signals include:

- feature requests and recurring issues;
- users describing repeated manual steps or glue code;
- users asking whether a tool/capability exists;
- failure recovery, context rebuilding, model/tool switching, repeated verification;
- self-built wrappers/scripts that compensate for a product gap;
- permission, configuration, integration, observability, evaluation, reliability, or developer-workflow friction.

Do not treat popularity, engagement, "AI" keywords, or infrastructure vocabulary as sufficient evidence of relevance.

## 7. Ticket-generation guardrails

Ticket generation must treat this file plus `docs/design.md` and `docs/decisions.md` as authoritative.

- Do not promote backlog sources into active scope.
- Do not silently choose new product targets.
- Proposed communities, queries, repositories, providers, budgets, or retention periods must remain proposals unless explicitly approved.
- Use **vertical tracer bullets**: each implementation ticket should produce a narrow, independently verifiable end-to-end capability rather than a horizontal layer such as "build storage," then "build prompt builder," then "add observability."
- Observability is a cross-cutting acceptance requirement of every real execution slice, not a late standalone feature. A slice that performs acquisition, prompt construction, model invocation, rendering, or delivery must preserve enough lineage to diagnose its own failures.
- A small preflight/spike may exist when it verifies an external dependency or provider boundary, but it must have a bounded question and artifact and must not become speculative multi-provider engineering.

## 8. Relationship to other documents

- `docs/design.md`: system architecture, experiment, judge, reporting, observability.
- `docs/runtime-selections.md`: model, Discord destination, credentials.
- `docs/activation-checklist.md`: live gates and approvals.
- `docs/grill/qa-transcript.md`: historical interview; it is not updated retroactively to pretend these later decisions were part of that interview.
