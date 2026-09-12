# Linkerd GOAWAY reproduction

Reproduces gRPC request failures when Linkerd receives HTTP/2 GOAWAY while requests are in flight.

The baseline proxy uses [`e5de317d`](https://github.com/cprayer/linkerd2-proxy/commit/e5de317dfe0feb8f6ff06f07c4e8ec9f61ab7a8f). The patched proxy uses [`f0323303`](https://github.com/cprayer/linkerd2-proxy/commit/f032330383015831f8cc9f0bd1e185c8db7fd697). Both include the same [ReplayBody fix](patches/replay-unpolled-body.patch), so the comparison isolates the GOAWAY change.

## Run

Docker is the only prerequisite. Each run uses the same language for its client and server.

```sh
docker build -t linkerd-goaway-repro https://github.com/cprayer/linkerd-goaway-repro.git

docker run -d --name goaway-java --cpus 2 --memory 4g --memory-swap 4g \
  linkerd-goaway-repro --runs 5 --language java
docker run -d --name goaway-go --cpus 2 --memory 4g --memory-swap 4g \
  linkerd-goaway-repro --runs 5 --language go

docker logs -f goaway-java
docker logs -f goaway-go
```

The containers run as a non-root user, without mounts or privileged mode. The proxy, client, and server communicate only inside each container.

## Expected results

Each client first calls its server directly, then through baseline and patched Linkerd proxies.

| Client and server | Baseline | Patched |
|---|---|---|
| Java | Some RPCs fail with `INTERNAL` | All RPCs succeed through transparent retries |
| Go | Some RPCs fail with `INTERNAL` | All RPCs succeed through transparent retries |

Selected lines from actual baseline and patched Go client logs:

```text
RPC id=5 code=INTERNAL attempts=1 error=rpc error: code = Internal desc = unexpected error
SUMMARY {"attempts":20,"completed":true,"disableRetry":false,"failed":3,"requests":20,"success":17,"transparentRetries":0}

STREAM_CLOSED time=2026-09-12T07:34:41.021754961Z id=5 attempt=1 code=UNAVAILABLE description=stream terminated by RST_STREAM with error code: REFUSED_STREAM
ATTEMPT time=2026-09-12T07:34:41.021794794Z id=5 attempt=2 transparent=true
RPC id=5 code=OK attempts=2 error=<nil>
SUMMARY {"attempts":23,"completed":true,"disableRetry":false,"failed":0,"requests":20,"success":20,"transparentRetries":3}
```

RPC IDs and failure counts vary between runs. Java also checks retry-disabled and Linkerd route-retry cases; Go checks the baseline and patched cases because grpc-go cannot disable transparent retries.

Logs, metrics, and `summary.json` are saved under `/results`. Copy them from either container:

```sh
docker cp goaway-java:/results ./results-java
docker cp goaway-go:/results ./results-go
```

The original Kubernetes runner remains available with `make reproduce`.

## License

[Apache-2.0](LICENSE)
