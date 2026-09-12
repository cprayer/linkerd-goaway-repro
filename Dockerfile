FROM maven:3.9.9-eclipse-temurin-17@sha256:f58d59b6273e785ac0a4477f6e9b5ba1d7731c75b906c0f7b34076f1851318cc AS client
WORKDIR /src
COPY client/pom.xml ./
COPY client/src ./src
RUN mvn -B -ntp package

FROM golang:1.25.1@sha256:d7098379b7da665ab25b99795465ec320b1ca9d4addb9f77409c4827dc904211 AS server
WORKDIR /src
COPY server/go.mod server/go.sum ./
RUN go mod download
COPY server/ ./
RUN test -z "$(gofmt -l .)" && GOMAXPROCS=2 go test -p 2 ./... \
    && CGO_ENABLED=0 GOMAXPROCS=2 go build -p 2 -trimpath -o /server . \
    && CGO_ENABLED=0 GOMAXPROCS=2 go build -p 2 -trimpath -o /client ./cmd/client

FROM rust:1.90@sha256:e227f20ec42af3ea9a3c9c1dd1b2012aa15f12279b5e9d5fb890ca1c2bb5726c AS proxy-source
WORKDIR /src
RUN git init . \
    && git fetch --depth=1 https://github.com/cprayer/linkerd2-proxy.git e5de317dfe0feb8f6ff06f07c4e8ec9f61ab7a8f \
    && git checkout --detach FETCH_HEAD
COPY harness/goaway.rs linkerd/app/integration/examples/goaway.rs
COPY patches/replay-unpolled-body.patch /replay.patch
ENV CARGO_BUILD_JOBS=2 CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
    RUSTFLAGS="-D warnings -D deprecated --cfg tokio_unstable -C debuginfo=0" LINKERD2_PROXY_VENDOR=goaway-repro

FROM proxy-source AS baseline
ENV LINKERD2_PROXY_VERSION=0.0.0-goaway.e5de317dfe0f-replayfix
RUN git apply --check /replay.patch && git apply /replay.patch \
    && cargo build --release --locked -p linkerd-app-integration --example goaway \
    && cp target/release/examples/goaway /goaway-baseline && strip /goaway-baseline

FROM proxy-source AS patched
RUN git fetch --depth=1 https://github.com/cprayer/linkerd2-proxy.git f032330383015831f8cc9f0bd1e185c8db7fd697 \
    && git checkout --detach FETCH_HEAD
ENV LINKERD2_PROXY_VERSION=0.0.0-goaway.f03233038301-replayfix
RUN git apply --check /replay.patch && git apply /replay.patch \
    && cargo build --release --locked -p linkerd-app-integration --example goaway \
    && cp target/release/examples/goaway /goaway-patched && strip /goaway-patched

FROM eclipse-temurin:17-jre@sha256:13cc28a6cc72a38ce1f00c906be3580c1a3e604b8984d694f369a96742abc93b AS java
FROM python:3.13-slim-trixie
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=java /opt/java/openjdk /opt/java/openjdk
COPY --from=client /src/target/client.jar /app/client.jar
COPY --from=server /server /usr/local/bin/goaway-server
COPY --from=server /client /usr/local/bin/goaway-client
COPY --from=baseline /goaway-baseline /usr/local/bin/goaway-baseline
COPY --from=patched /goaway-patched /usr/local/bin/goaway-patched
COPY --from=proxy-source /src/linkerd/app/integration/src/data /src/linkerd/app/integration/src/data
COPY scripts/reproduce.py scripts/standalone.py ./scripts/
COPY versions.json ./
RUN mkdir /results && chown 65532:65532 /results
ENV PATH="/opt/java/openjdk/bin:$PATH" PYTHONUNBUFFERED=1 \
    LINKERD2_PROXY_LOG="linkerd=debug,warn" GRPC_GO_LOG_SEVERITY_LEVEL=info GRPC_GO_LOG_VERBOSITY_LEVEL=2
USER 65532:65532
ENTRYPOINT ["python3", "scripts/standalone.py"]
CMD ["--runs", "5"]
