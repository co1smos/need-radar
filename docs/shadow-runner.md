# Offline extraction-only shadow runner

`need_radar.shadow` consumes a completed serve run and one synthetic v1 outcome. It reuses the served v0 snapshot, selection, context, truncation, prompt, model-call, and validation artifacts; it does not repeat v0 or modify the serve report. The v1 arm uses the same `execute_extraction` seam, shared resolved settings, fixed schema, one-attempt retry policy, exact-frozen-context evidence policy, and configured request/token/candidate ceilings. The candidate cap defaults to 10 and may be lowered or raised in the explicit shared settings.

Run the end-to-end offline check with:

```sh
TMPDIR=/home/ubuntu/.hermes/cache/scratch PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. python3 scripts/check_shadow_runner.py
```

The check runs ordinary serve first, invokes v1 separately, compares snapshot/context hashes and prompt data, verifies shared configuration and resource use, checks the serve report hash, and confirms the shadow output contains no report. It also activates network, credential-path, and canonical live-state denial. Fixtures and ceilings are synthetic test inputs, not approved live settings.

Observed October 9, 2026: `status=passed`; v0 serve `success` with 1 candidate; v1 shadow `complete` with 0 candidates; extraction instructions differ while execution seam, ordered context, and shared settings match; serve report hash is unchanged; shadow publication is disabled. Snapshot SHA-256: `116a5523103f33dec5ff4650081e016ed4f42e5fcd87d4a190cf9ed6fceaf615`. Context SHA-256: `57f978ed4fd2412bd9e2e0f03957aea17c8e3d7477158b8240c8c874a2729710`. The result is synthetic/offline only and does not verify live sources, providers, permissions, or billing.

The runner accepts only `provider: synthetic_fixture`. Arm or resource failures persist their outputs and failure evidence and mark the comparison `incomplete`; they do not fall back to v0/v1 substitution or publish a report. A comparison also remains incomplete when v0 configuration or lineage is absent or differs.
