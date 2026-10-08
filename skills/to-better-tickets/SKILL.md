---
name: to-better-tickets
description: Break a plan, spec, grill/design decisions, or conversation into small tracer-bullet tickets with genuine blocking edges, explicit supported behavior and acceptable failure modes. Use before publishing or revising GitHub, Linear, or local Markdown tickets for agent execution.
---

# To Better Tickets

Adapted from Matt Pocock's [to-tickets](https://github.com/mattpocock/skills/blob/main/skills/engineering/to-tickets/SKILL.md) (MIT; original copyright preserved in [LICENSE](LICENSE)). Keep the original tracer-bullet, blocking-edge and user-review workflow; this variant preserves design decisions and separates offline implementation from live activation.

## Process

### 1. Gather context

Start from the current conversation. Read a referenced spec, issue body/comments, architecture decision, or grill record before drafting. Use the configured issue tracker and its label vocabulary; when unknown, ask one direct question. Don't reopen settled product decisions.

### 2. Explore the codebase (optional)

Inspect existing behavior, tests, glossary and ADRs if needed. Prefactor only where it makes the requested change easier: "Make the change easy, then make the easy change."

### 3. Draft tracer-bullet vertical slices

- Each ticket cuts a narrow but **complete** user-visible path across the relevant layers, rather than dividing work by layer or platform arbitrarily.
- Each slice must be independently demonstrable or verifiable and fit within one fresh agent context window.
- Give each ticket only its genuine blocking edges: the work that must be finished **before it can be implemented and tested**.
- For wide mechanical refactors that cannot land green slice by slice, use **expand → migrate in independently green batches → contract** instead.

### 4. Preserve the delivery boundary

For every ticket, carry forward the relevant design decisions:

- **Must Work:** supported inputs, observable outcome, essential guarantees.
- **Acceptable Failure:** permitted rejection, withholding, partial/unknown status, unsupported cases, and deferred work.

Turn these into short, verifiable acceptance criteria. Never silently expand the supported input space, replace an approved safe failure with a requirement to parse every edge case, or relax an explicit safety guarantee.

Treat implementation dependencies and live activation gates separately. If fixtures, recordings or fake services prove the new behavior offline, credentials, live source approval, billing, scheduled execution and real-provider checks do **not** block that implementation ticket. Put those checks on separate activation/live acceptance tickets; the actual live pilot still requires them.

### 5. Quiz the user

Show an ordered draft with each ticket's **title**, **delivered end-to-end behavior**, and **blocked-by edges**. Ask whether the granularity, dependencies and any merges/splits are correct. Iterate until approved, then do **one** consistency check: compare Must Work / Acceptable Failure against source decisions and flag only missing or conflicting boundaries.

### 6. Publish to the configured tracker

- **Local files:** one Markdown ticket per file under `.scratch/<feature>/issues/<NN>-<slug>.md` in dependency order; declare blockers in each file.
- **GitHub/Linear:** one issue per ticket, in dependency order; use native dependencies when supported and mirror them in text only when needed. Keep both representations synchronized when editing existing issues.
- Add `ready-for-agent` only for issues whose **implementation** is runnable; do not label a live-only issue ready while external activation gates remain unsatisfied.
- When a source issue is a parent, follow the tracker convention for sub-issues; never close or modify a parent as a side effect of decomposition.

## Ticket template

```markdown
## What to build
One narrow, verifiable end-to-end behavior.

## Delivery boundary
**Must Work:** ...
**Acceptable Failure:** ...

## Acceptance criteria
- [ ] Supported path produces a reproducible result.
- [ ] Explicit safe fallback is verified and not reported as complete.

## Blocked by
None (or only true implementation blockers).

## External activation gates
Only if needed; not implementation blockers.
```

Avoid stale implementation-file paths and speculative requirements. Do not replicate generic repo policy in every ticket when a shared contract already exists.

## Example

An LLM source-result adapter can accept fixture Reddit/X responses, preserve deterministic IDs/provenance and validate normalized evidence offline. It may unblock downstream processing without a live API key. Reddit live acquisition and X live acquisition are separate tickets and do not block offline development.

## Attribution

Derivative of [Matt Pocock / to-tickets](https://github.com/mattpocock/skills/blob/main/skills/engineering/to-tickets/SKILL.md), MIT License, © 2026 Matt Pocock. Original behavior preserved: gather context, optional code exploration, vertical tracer bullets, dependency edges, wide-refactor expand/contract, owner granularity review, publish in dependency order. Additions: bounded delivery contracts, one-time consistency check, and offline-vs-live dependency separation.
