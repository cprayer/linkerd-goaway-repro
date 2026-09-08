# Observed results

Executed on 2026-09-08 (Asia/Seoul) using macOS arm64, Docker, kind v0.32.0, Kubernetes v1.36.1, Linkerd edge-26.8.2, grpc-java 1.84.0 (Java 17), and grpc-go 1.75.1.

```sh
make test
make reproduce
```

Five script checks passed. All seven expectations passed across 140 RPCs using the proxy revisions recorded in [summary.json](summary.json).

| Case | Successful RPCs | Failed RPCs | Transparent retries | Expected behavior |
|---|---:|---:|---:|---|
| baseline-one | 17/20 | 3 | 0 | PASS |
| baseline-two | 16/20 | 4 | 0 | PASS |
| patched-one | 20/20 | 0 | 3 | PASS |
| patched-two | 20/20 | 0 | 3 | PASS |
| patched-one-no-retry | 17/20 | 3 | 0 | PASS |
| patched-two-no-retry | 17/20 | 3 | 0 | PASS |
| patched-policy | 20/20 | 0 | 0 | PASS |

The default grpc-java cases recovered through three transparent retries each. The Linkerd policy case succeeded with one Java attempt per RPC and a positive Linkerd retry-success counter. Verification also checked refusal metrics, mTLS counters for the meshed paths, the running proxy revision, and application/sidecar restart counts.

See [summary.json](summary.json) for pinned inputs and RPC counts, and [images.json](images.json) for the local image IDs. Exact refusal counts can vary between runs. These are observations from this environment, not a security certification or a multicluster/service-mirror test.
