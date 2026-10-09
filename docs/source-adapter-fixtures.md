# Source-result adapter fixture contract

`fixtures/source_results/synthetic_reddit_x.json` is an offline-only contract example, not a provider recording. The assumed envelope is intentionally small and must not be treated as the actual Treg Reddit/X schema.

- Reddit mock envelope: `partial`, a shared `origin`, and `posts[]`. Each post has `id`, `title`, `selftext`, `created_at`, `permalink`, `edited`, `deleted`, `partial`, optional `thread_id`/`parent_id`, and optional `comments[]` with corresponding comment fields (`body` instead of title/selftext).
- X mock envelope: `partial`, a shared `origin`, and `posts[]`. Each entry has `id`, `text`, `created_at`, `url`, `edited`, `deleted`, `partial`, `conversation_id`, and `in_reply_to_id`.
- The fake semantic normalizer returns only `{results:[{record_id, normalized_text}]}`. Deterministic code binds each result to its input record and copies IDs, timestamps, references, origin, relationships, and flags; generated text cannot set those fields.
- Canonical evidence preserves source text separately from `normalized_text`, uses source-qualified IDs, and records unknown metadata as unknown/incomplete rather than guessing. Deleted/empty records are withheld from the normalizer prompt.
- The normalized items are frozen in ordered `snapshot.json`; `need_radar.snapshot.read_frozen_snapshot` exposes that persisted item list for serve and future shadow consumers. The existing synthetic v0 fixture response then runs through the same validation/report path.

Tests deny network, credential-file, and canonical live-state access in the test process and in offline subprocesses. They use predetermined mock envelopes and model outputs only. Passing fixtures establishes neither actual-provider compatibility, response coverage, nor source rights; issue #20 must check private sanitized recordings separately.
