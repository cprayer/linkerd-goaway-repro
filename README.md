# Linkerd GOAWAY reproduction

Compare baseline and patched proxies using a grpc-java client and Go gRPC server in kind. The server sends GOAWAY after five seconds and responds after thirty seconds; each of seven cases runs twenty staggered RPCs.

## Run

Requires Docker (at least 8 GiB memory), kind, kubectl, Git, Python 3, and Make. The first run downloads dependencies and compiles two proxy builds.

```sh
make plan       # Show pinned inputs and execution scope
make reproduce  # Build, create a dedicated cluster, and verify results
make test       # Run script checks
```

The run creates privileged kind containers and installs Linkerd and CRDs in the dedicated `linkerd-goaway-repro` cluster. It uses `.build/kubeconfig` and replaces the cluster's `goaway-repro` namespace. Use a disposable environment for unfamiliar code.

## Expected results

| Configuration | Result |
|---|---|
| Baseline, one or two proxies | Some RPCs fail with `INTERNAL` |
| Patched, one or two proxies | All RPCs succeed through transparent retries |
| Patched, client retries disabled, one or two proxies | Some RPCs fail with `UNAVAILABLE` / `REFUSED_STREAM` |
| Patched, client retries disabled, Linkerd route retry enabled | All RPCs succeed through Linkerd retries |

Checks cover RPC outcomes, retry/refusal metrics, mTLS on two-proxy paths, and unexpected restarts. A baseline without failures fails verification. This tests one cluster, not multicluster routing or all GOAWAY scenarios.

Results and logs are saved under `results/`; see `results/report.md` and `results/run.log`. Failed checks exit nonzero. A prior completed run is recorded in [validation/report.md](validation/report.md).

## Cleanup and inputs

```sh
make clean
```

Deletes the owned kind cluster; retains build caches, Docker images, and results.

[versions.json](versions.json) pins proxy revisions and build inputs. The patched revision has the same source tree as the revision in the prior validation report. See [client/pom.xml](client/pom.xml) and [server/go.mod](server/go.mod) for application dependencies.

Licensed under [Apache-2.0](LICENSE).
