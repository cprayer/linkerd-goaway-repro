# Linkerd GOAWAY reproduction

Compare an unpatched Linkerd proxy with the GOAWAY cancellation fix using a grpc-java client and a Go gRPC server in kind.

The server sends graceful GOAWAY after about five seconds and takes thirty seconds to reply. Each case makes twenty staggered RPCs. The client makes one application call per RPC and records grpc-java's transport attempts, including transparent retries.

## Run

Install Docker, kind, kubectl, Git, Python 3, and Make, then run:

```sh
make reproduce
```

This builds both proxy revisions and the client/server from source, creates a dedicated kind cluster, installs Linkerd, runs seven cases, and checks the results. Java, Go, Rust, and the Linkerd CLI do not need to be installed on the host. The first run downloads dependencies and compiles two release builds; allow several gigabytes of disk space and at least 8 GiB of memory for Docker. Subsequent runs reuse the builds.

Read [scripts/reproduce.py](scripts/reproduce.py), [versions.json](versions.json), and the Dockerfiles before execution. To print the execution scope and pinned inputs without running Docker or downloading anything:

```sh
make plan
```

## Expected results

| Case | Path | Retry configuration | Expected result |
|---|---|---|---|
| baseline-one | Client → outbound proxy → server | grpc-java default | Some RPCs fail with `INTERNAL` |
| baseline-two | Client → outbound proxy → inbound proxy → server | grpc-java default | Some RPCs fail with `INTERNAL` |
| patched-one | One proxy | grpc-java default | All RPCs succeed; transparent retries occur |
| patched-two | Two proxies with mTLS | grpc-java default | All RPCs succeed; transparent retries occur |
| patched-one-no-retry | One proxy | grpc-java `disableRetry()` | Some RPCs fail with `UNAVAILABLE` / `REFUSED_STREAM` |
| patched-two-no-retry | Two proxies with mTLS | grpc-java `disableRetry()` | Some RPCs fail with `UNAVAILABLE` / `REFUSED_STREAM` |
| patched-policy | One proxy | grpc-java `disableRetry()`; Linkerd route retry enabled | All RPCs succeed; Linkerd retries occur |

The exact number of refused requests varies with timing. A baseline run with no failed RPCs fails verification: it did not reproduce the issue. The check also requires matching refused/retried/successful RPC IDs, refusal metrics, retry metrics for the policy case, and mTLS metrics for both meshed paths. Unexpected container restarts fail verification.

Results are written to `results/report.md`, with raw client/proxy/server logs, metrics, generated manifests, image IDs, and machine-readable summaries beside it. Build output is in `results/run.log`. A successful run exits with status zero; an unmet expectation or timeout exits nonzero.

See the [observed results](validation/report.md) for a completed run with grpc-java 1.84.0.

This reproduces one- and two-proxy paths within **one cluster**. It does not exercise service mirroring, a multicluster gateway, network loss, or every GOAWAY scenario. The patch translates cancellations attributed to peer GOAWAY into `REFUSED_STREAM`; it does not add an unconditional retry loop.

## Execution scope and trust

Use a disposable VM when evaluating unfamiliar code. kind runs privileged Docker containers; a dedicated kubeconfig prevents accidental use of your normal Kubernetes context, but is not a security sandbox.

- The script creates `linkerd-goaway-repro` and uses only `.build/kubeconfig` with its explicit context. It refuses an existing cluster with that name unless this checkout has its ownership record and kubeconfig
- Each run replaces the `goaway-repro` namespace in that cluster. Linkerd and Gateway API resources are installed there, including cluster-scoped RBAC and CRDs
- Docker builds mount only this checkout's source/output/cache directories. The proxy source mount is read-only. Client/server build contexts are restricted by `.dockerignore`
- The script does not request cloud credentials or GitHub tokens. It does not mount personal credential directories or the Docker socket into build containers. Installed host tools still use their normal configuration
- Proxy sources are pinned to full Git commit IDs. The node, build, runtime, and proxy base images used directly by this repository are pinned by digest. Linkerd CLI downloads and the Gateway API manifest are checked against SHA-256 values before use
- Linkerd's generated installation still uses its release's controller/init image tags. Rust uses the upstream `Cargo.lock` with `--locked`, Go uses `go.sum`, and Maven resolves fixed dependency versions from its repository. These upstream components remain part of the trust boundary; pinning is not a vulnerability scan or an authenticity guarantee
- Downloads use GitHub, container registries, and language package repositories. Installation disables Linkerd's heartbeat, and the health check uses the pinned version. The script does not upload the results. Review logs before sharing them

`make test` checks download tampering rejection, cached-file verification, cluster ownership rejection, and the non-executing plan command. These checks cover those specific safeguards, not the security of Docker or every downloaded dependency.

## Cleanup

```sh
make clean
```

This deletes only the recorded kind cluster belonging to this checkout. Build caches, Docker images, and results are retained for inspection or reuse. To reclaim them, remove this checkout's `.build` and `results` directories and the `localhost/linkerd-goaway-*` images after inspection. The script does not run Docker prune or delete other clusters.

## Source revisions

- Baseline: [`e5de317d`](https://github.com/linkerd/linkerd2-proxy/commit/e5de317dfe0feb8f6ff06f07c4e8ec9f61ab7a8f)
- Patched: [`befe99fa`](https://github.com/cprayer/linkerd2-proxy/commit/befe99fa0c16362afe67193980f854d7b115ee24)
- [Proxy diff](https://github.com/cprayer/linkerd2-proxy/compare/e5de317dfe0feb8f6ff06f07c4e8ec9f61ab7a8f...befe99fa0c16362afe67193980f854d7b115ee24)

See [versions.json](versions.json), [client/pom.xml](client/pom.xml), and [server/go.mod](server/go.mod) for exact inputs. This repository is licensed under [Apache-2.0](LICENSE).
