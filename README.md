# Linkerd GOAWAY reproduction

Compare baseline and patched Linkerd proxies using a grpc-java client and Go gRPC server. The server sends GOAWAY after five seconds and responds after thirty seconds; each of seven cases runs twenty staggered RPCs.

## Run

Docker is the only prerequisite. Build and run with one command:

```sh
docker build -t linkerd-goaway-repro https://github.com/cprayer/linkerd-goaway-repro.git && \
  docker run --name goaway-repro --network none --cpus 2 --memory 4g \
    --cap-drop ALL --security-opt no-new-privileges linkerd-goaway-repro
```

The first build compiles both proxy revisions and the client/server from source. The container runs as a non-root user, without mounts or privileged mode. All traffic stays inside the container.

The proxies use Linkerd's integration harness with local test controllers and identities. This tests the proxy behavior; it does not run Kubernetes, iptables, or service mirroring.

## Expected results

| Configuration | Result |
|---|---|
| Baseline, one or two proxies | Some RPCs fail with `INTERNAL` |
| Patched, one or two proxies | All RPCs succeed through transparent retries |
| Patched, client retries disabled, one or two proxies | Some RPCs fail with `UNAVAILABLE` / `REFUSED_STREAM` |
| Patched, client retries disabled, Linkerd route retry enabled | All RPCs succeed through Linkerd retries |

Checks cover RPC outcomes, matching retry/refusal IDs, metrics, mTLS on two-proxy paths, and unexpected process exits. A baseline without failures fails verification. Exit code `0` means all seven cases matched.

Logs and summaries are saved under `/results/<run>/`. Copy results or repeat the run:

```sh
docker cp goaway-repro:/results ./results
docker start -a goaway-repro
```

Remove the stopped container with `docker rm goaway-repro` when finished.

## Kubernetes reproduction

The original `make reproduce` runner remains available for kind; it requires kind, kubectl, Git, Python, Make, and Docker. That path uses privileged kind nodes and build mounts. `make clean` deletes its recorded cluster.

A prior kind run is recorded in [validation/report.md](validation/report.md). [versions.json](versions.json) pins proxy revisions and build inputs; see [client/pom.xml](client/pom.xml) and [server/go.mod](server/go.mod) for application dependencies.

Licensed under [Apache-2.0](LICENSE).
