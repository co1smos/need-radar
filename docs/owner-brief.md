# Need Radar - owner brief and proxy interview mandate

Date: 2026-10-06. Status: authoritative recap of this conversation; not an implementation authorization.

## 1. Product intent (owner approved)

An independent project that continuously discovers concrete **AI application-layer** friction in building with or learning to build with AI. The output should identify valuable, meaningfully sized, evidence-backed problems that can plausibly be improved through a reasonably scoped tool, repository, software feature, harness capability, plugin/integration, automation, or LLM workflow. Deterministic solutions are equally acceptable. Model-internal research and low-level training/inference/GPU optimization are not the primary target. This is not restricted to venture-scale startup ideas or monetizable SaaS.

Examples of broad starting points: automatic model routing; practical AI-learning repositories. The system must narrow these into a specific affected user, goal, obstacle, evidence, and possible small intervention, rather than merely reporting 'AI costs too much' or 'people want to learn AI'. A small issue is eligible; sizeable refers to meaningful friction/value, not invented market size.

No personal-interest recommender or in-product owner-feedback system. The owner explicitly removed 'would Youyou want to build this?' from the evaluator; later personal decisions happen outside this project.

## 2. Operating preferences (owner approved)

- Run eventually on an Ubuntu VPS under /home/ubuntu/projects/need-radar.
- AI should implement after authorization; the owner reviews direction, architecture, and genuine tradeoffs, not hand-codes the system.
- Simplicity is first priority; reuse existing solutions and avoid speculative infrastructure. Runtime cost is also important.
- Collect daily at minimum; collection can be more frequent than a daily report. The exact polling cadence is a reversible configuration choice, not a promised requirement.
- Core active sources are Reddit and X. GitHub Issues/Discussions is an optional extension after the Reddit+X path works end to end, and must not block the core product/pilot. When added, GitHub should emphasize popular, actively used open-source AI application-layer projects whose users expose concrete workflow/integration/agent/tool friction; owner examples include Hermes Agent, pi agent, and OpenClaw (verify exact repository slugs before implementation). Hacker News is backlog, and YouTube is not in the current source plan. Tags/communities/repos are acceptable seeds.
- No token-expensive LLM-driven browser scraping loop. Deterministic collectors and existing data-access services are preferred. Do not assume Playwright itself consumes LLM tokens; deterministic HTML-to-PDF use is different.
- Hermes is the preferred scheduler/agent/Discord entry point for now, subject to actual installed capabilities. No need to reproduce its gateway or scheduling framework.
- Failures must be diagnosed, including silent failures and incorrect deterministic inputs. AI may later propose repairs; production mutation/autodeploy is not authorized.

## 3. Fixed evaluation goal (owner approved)

Both serve and shadow must ALWAYS use the same evaluation standard within a comparison:

'Is this a concrete, valuable, meaningfully sized, evidenced, solvable friction in AI development, with a reasonably scoped intervention?'

Candidate dimensions proposed and accepted in principle: friction clarity; actual pain/value; evidence/size; solvability; scope fit. Do not infer a large market or willingness to pay without evidence. The evaluator is not allowed to reward explicit complaints in one arm and speculative novelty in another. Strategy-specific diagnostics may exist but must not redefine success.

The judge's rubric/model/settings/evidence package must be versioned and held fixed for a given experiment. LLM-as-a-judge is acceptable; deterministic checks come first. Judge output is not unquestionable ground truth. No personal taste scoring or personalized feedback loop.

## 4. Serve/shadow experiment (owner approved)

Three separable experimental dimensions:

1. Source: where to look (communities/platforms/repos).
2. Retrieval: how to retrieve within that universe (queries, order, pagination, posts/comments).
3. Extraction: how to interpret a fixed retrieved dataset.

Change EXACTLY ONE dimension in v0 versus v1. Never simultaneously change source, retrieval, and extraction and then attribute results to one layer.

The first experiment changes EXTRACTION ONLY:

- v0 / serve: extract explicit pain, complaints, feature requests, missing capabilities.
- v1 / shadow: mine workarounds, repeated manual workflow/decisions, and latent friction.
- Same sources, retrieval settings, time window, immutable raw input snapshot, model/settings except extraction instructions, output schema, resource budget, evaluator, and report constraints.
- Only serve may deliver a user-facing daily Discord digest. Shadow stores artifacts/traces and is evaluated; no duplicate notifications or side effects.
- Both arms output the same candidate schema and receive the same evidence-grounded rubric. A workflow is not automatically a pain just because it contains manual steps.
- Source/retrieval alternatives remain documented examples in an experiment backlog, not active concurrent work.

Backlog examples: curated versus broader communities; community feeds versus problem-pattern search; posts versus posts+comments; keyword versus semantic retrieval; workaround mining; repeated decisions; failure/recovery behavior; self-built scripts/wrappers; explicit missing capabilities; cost/latency/reliability friction.

## 5. Output contract (owner approved intent; renderer is tentative)

Keep a raw human/agent-readable Markdown daily report. Also produce a good-looking HTML and/or PDF view from the SAME content through deterministic rendering, not another independent LLM rewrite. Serve-only Discord content should be extracted from that report, not re-reasoned into inconsistent facts. Report source coverage/degradation separately from 'no useful findings'. Preserve links/evidence.

Proposed, not yet verified stack: Markdown -> Quarto + small CSS/theme -> standalone HTML -> Chromium print-to-PDF. Existing alternatives can replace this if simpler. A custom frontend application is out of scope. Shadow need not pay for expensive visual rendering when canonical output suffices.

Reference from the conversation (not yet audited in this workspace): https://github.com/shubhaviatiningsih-byte/fortune-liuyao-skill/blob/main/references/frontend-contract.md . The intended borrowed principle is one content result, multiple deterministic presentations, not its domain or exact layout.

## 6. Observability (owner emphasized)

End-to-end observability, NOT just LLM trace/eval. Given an incorrect prompt or final finding, trace backwards through deterministic selection, normalization, config resolution, context assembly, template rendering, raw items, and retrieval origin. Capture versioned inputs/outputs/artifact references where needed; logs alone do not explain how a prompt was built.

Also need health counts, freshness, failures, deduplication rates, latency/cost, scheduled-run detection, and delivery outcomes. A successful exit or HTTP 200 is not sufficient. Heavy raw payloads/secrets must not go into telemetry. Prefer versioned artifacts + IDs/hashes and structured metadata. Do not promise a wrapper can automatically instrument unmodified third-party internals.

Tentative tooling: OpenTelemetry for application spans/context; Langfuse for spans/LLM observation/evaluation; structured logs and an independent heartbeat for run health. Verify actual support and choose the minimum viable set. Ingestion, daily analysis, evaluation, and publication may be different runs; preserve lineage correctly instead of inventing a single long-lived trace for everything.

## 7. Tentative dependency boundary - NOT established facts

Earlier assistant replies made detailed claims without a completed live audit. Do not promote them to validated requirements.

Current preference to investigate:
- Reddit/X: Treg as a third-party data-access layer plus a thin deterministic collector.
- SQLite for items, discovery provenance, run/experiment state, exact-ID dedupe.
- Hermes for scheduling, analysis, and eventual Discord delivery.
- Direct official APIs/feed for GitHub/HN only when needed.
- OpenMagpie remains an alternative monitoring/ingestion subsystem, not simultaneously mandatory.
- Agent-Reach remains optional selective thread enrichment/fallback, not mandatory on the first data path.

Unknown until verified: Treg identity/interface/pricing, provider authorization and data terms, pagination/time filtering/comment coverage, VPS reliability, exact credentials, quota behavior, backend consistency; OpenMagpie and Agent-Reach actual current capabilities; Hermes no-agent cron and trace integration details. Third-party access does not guarantee completeness, access rights, retention rights, or anti-bot immunity. Do not buy credits, use personal social cookies, install packages, or test paid services in this documentation interview.

## 7a. Subsequent owner update (2026-10-06)

The owner selected **DeepSeek V4.1 Flash** and provided Discord channel **`1557157266824634469`**. Do not ask the owner to choose the model or repeat the channel again. The owner wants to see the visual architecture before proceeding and asks to be told when Treg API keys or other credentials actually become necessary.

These inputs are documented in [runtime-selections.md](runtime-selections.md). Model access/provider, budgets, retention/export permissions and send time remain activation gates. No implementation, global Hermes model change, schedule or message is authorized by supplying these design inputs. [architecture.md](architecture.md) now contains an actual SVG diagram and editable Mermaid source.

The original interview below remains historical context; references to unknown model/channel choices are superseded by this update only where applicable.

A later owner update also fixed the product/source target in [target-scope.md](target-scope.md): the core first-round sources are Reddit and X; GitHub Issues/Discussions is an optional extension after the core end-to-end path works and is not a blocker; HN is backlog; YouTube is excluded; findings should focus on AI application-layer builder friction rather than model/infra internals. This later update supersedes earlier optional-source wording where it conflicts.

## 8. Questions for Hermes to stress-test

Find missing material decisions, contradictions, failure cases, and overengineering. Especially:
- Boundary between source/retrieval/extraction, and a common prefilter that does not systematically suppress v1's evidence.
- Comparable immutable inputs, candidate counts/limits, shared evidence scope, deterministic validation, semantic duplicates, judge bias/calibration, useful-yield denominator, empty inputs and failed arms, promotion/rollback rules.
- The minimum state/lineage needed for source-strategy replay and prompt construction diagnosis, without inventing a framework.
- Report/evaluation coupling: serve should not fail just because a shadow judge or optional PDF failed; no unfair shadow access to extra evidence; conditional formatting versus content generation.
- Security: untrusted source text/prompt injection, secret redaction, trace retention, dependency/API approvals, and safe repair proposals.
- Acquisition proof requirements before making vendor claims, budget cap and source/Discord activation gates.
- Smallest acceptance tests and fixture-based examples needed BEFORE implementation, not a full implementation plan or new milestone.

## 9. Scope and authority of this run

Create documentation and conduct one real Hermes grill-with-docs session. ChatGPT proxies routine reversible decisions using this brief and evidence. The owner alone can approve genuine product-intent changes, irreversible/security changes, meaningful ongoing spend, or reserved choices.

Do not ask redundant questions. Unknown credentials, dollar caps, destination channel, and send time can be explicitly recorded as fail-closed activation gates; they need not block completing the design interview if nothing is deployed or spent. Do not invent these owner choices. Distinguish an unresolved design decision from a factual preflight check or a deferred activation input.

Keep interview rounds read-only. After the frontier is empty (or only genuinely owner-blocked questions remain), finalize docs separately. No application code, dependencies, live schedules, account changes, deployment, messages to Discord, Git initialization, or production changes.
