# Linkerd GOAWAY reproduction

Compare direct calls with baseline and patched Linkerd proxies using simple Go or Java echo applications.
The server's gRPC library sends GOAWAY after five seconds and responds after thirty seconds; each case runs twenty staggered RPCs.

## Run

Docker is the only prerequisite. The first build compiles both proxy revisions and the client/server from source.
The builder and reproduction container are each limited to **2 CPUs and 4 GiB RAM**, with swap disabled.
The builder can be shared with `linkerd-replay-body-repro`.

```sh
docker buildx inspect linkerd-replay-builder >/dev/null 2>&1 || \
  docker buildx create --name linkerd-replay-builder --driver docker-container
docker buildx inspect --bootstrap linkerd-replay-builder
docker update --cpus 2 --memory 4g --memory-swap 4g buildx_buildkit_linkerd-replay-builder0
docker buildx build --builder linkerd-replay-builder --load -t linkerd-goaway-repro \
  https://github.com/cprayer/linkerd-goaway-repro.git
docker run -d --name goaway-repro --cpus 2 --memory 4g --memory-swap 4g linkerd-goaway-repro --runs 5
docker logs -f goaway-repro
```

The container runs as a non-root user, without mounts or privileged mode. All traffic stays inside the container.

The proxies use Linkerd's integration harness with local test controllers and identities. This tests the proxy behavior; it does not run Kubernetes, iptables, or service mirroring.

Choose either language with `--client java|go` and `--server go|java` (default: Java client → Go server).
For example, append `--client go --server java` to the `docker run` command for Go → Java.

## Check the result

The same client and server first run **without Linkerd**, then through the baseline and patched proxies.
Each client makes one unary echo call; the server returns the received message unchanged.

| Language | Echo client | Echo server |
|---|---|---|
| Java | [GoAwayClient.java](client/src/main/java/repro/GoAwayClient.java) | [GoAwayServer.java](client/src/main/java/repro/GoAwayServer.java) |
| Go | [client.go](server/echo/client.go) | [main.go](server/main.go) |

The [Java runner](client/src/main/java/repro/Reproduce.java) and [Go runner](server/cmd/client/main.go)
handle staggered calls and log gRPC's tracing callbacks. They do not implement retries or HTTP/2 frames.
`RPC` shows the actual call result; `STREAM_CLOSED` and `ATTEMPT` show stream status and transparent retries.
These are client-side logs. Raw Linkerd logs are saved separately in the results.

Example excerpts from a run (RPC IDs and failure counts vary):

```text
Running baseline-one
RPC id=5 code=INTERNAL attempts=1 error=io.grpc.StatusRuntimeException: INTERNAL: unexpected error
Running patched-one
RPC id=5 code=OK attempts=2
```

| Configuration | Result |
|---|---|
| Direct, without Linkerd | All 20 RPCs succeed without retries |
| Baseline, one or two proxies | Some RPCs fail with `INTERNAL` |
| Patched, one or two proxies | All RPCs succeed through transparent retries |
| Patched, client retries disabled, one or two proxies | Some RPCs fail with `UNAVAILABLE` / `REFUSED_STREAM` |
| Patched, client retries disabled, Linkerd route retry enabled | All RPCs succeed through Linkerd retries |

**PASS means direct calls succeeded, the bug was reproduced before the fix, and all selected checks passed in every run.**
Checks cover RPC outcomes, matching retry/refusal IDs, metrics, mTLS on two-proxy paths, and unexpected process exits.
A baseline without failures fails verification. A failed run stops the batch and exits nonzero; failures are not retried.
The Java client runs all seven proxy cases. The Go client runs the four baseline/patched cases:
Go's `WithDisableRetry` does not disable transparent retries, so the Java-only retry-disabled cases are excluded.
Use `--runs 1` for a single run; allow about seven minutes for Java or five minutes for Go.

The pinned proxy also has the separate [ReplayBody duplication bug](https://github.com/cprayer/linkerd-replay-body-repro).
With a Java server, the `patched-policy` case can fail with `INTERNAL: Too many requests`:
the gRPC library rejects a second message in a unary request. This is reported as a failure, not a successful GOAWAY fix.

Copy the results to inspect summaries, client/server logs, proxy logs, and metrics:

```sh
docker cp goaway-repro:/results ./results
```

Open the latest directory's **report.md**, then follow a run's **details** link to its checks and raw logs.
To repeat using the same container: `docker start -a goaway-repro`.
Remove the stopped container with `docker rm goaway-repro` when finished.

## Kubernetes reproduction

The original `make reproduce` runner remains available for kind; it requires kind, kubectl, Git, Python, Make, and Docker. That path uses privileged kind nodes and build mounts. `make clean` deletes its recorded cluster.

A prior kind run is recorded in [validation/report.md](validation/report.md). [versions.json](versions.json) pins proxy revisions and build inputs; see [client/pom.xml](client/pom.xml) and [server/go.mod](server/go.mod) for application dependencies.

Both languages use [proto/echo.proto](proto/echo.proto). Go service bindings in [server/rpc/](server/rpc/)
are generated by `protoc-gen-go-grpc v1.5.1`.

Licensed under [Apache-2.0](LICENSE).
