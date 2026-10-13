# DeepSeek route verification

On October 13, 2026, the owner-approved direct DeepSeek route completed one
synthetic-only request through the shared invocation boundary. The request and
returned model were `deepseek-flash`; the response reported 47 tokens and
validated successfully. Retry and failure behavior, request/token/spend bounds,
model mismatch, no fallback, replay lineage, and failed-span reporting are
covered by offline tests.

The usage-derived conservative estimate was 15 microUSD under the approved
50,000 microUSD cumulative cap. Account billing was not queried and remains
unknown; no billing amount is asserted. No requests are unknown or in flight.
The sanitized replay, local trace, structured log, and OTLP span remain in
private state. External telemetry export was disabled. This verifies the
bounded synthetic route only; it does not verify source-data use, billing
reconciliation, or activation for scheduled/product runs.

Run the network-denied verifier tests with temporary state under Hermes TMPDIR:

```sh
TMPDIR="$HOME/.hermes/cache/scratch" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_deepseek_verify.py' -v
```

The test suite uses synthetic responses and does not make provider requests.
