import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build"
RESULTS = ROOT / "results"
VERSIONS = json.loads((ROOT / "versions.json").read_text())
CLUSTER = VERSIONS["cluster"]
KUBECONFIG = BUILD / "kubeconfig"
OWNER = BUILD / "cluster-owner.json"
NAMESPACE = "goaway-repro"
K = ["kubectl", "--kubeconfig", str(KUBECONFIG), "--context", "kind-" + CLUSTER]
LOG = None
CASES = [
    ("baseline-one", "baseline", "plain", False, False),
    ("baseline-two", "baseline", "baseline", False, False),
    ("patched-one", "patched", "plain", False, False),
    ("patched-two", "patched", "patched", False, False),
    ("patched-one-no-retry", "patched", "plain", True, False),
    ("patched-two-no-retry", "patched", "patched", True, False),
    ("patched-policy", "patched", "plain", True, True),
]


def run(command, *, capture=False, stdin=None, timeout=600, quiet=False):
    display = "+ " + shlex.join(map(str, command))
    if not quiet:
        print(display, flush=True)
    if LOG is not None:
        LOG.write(display + "\n")
    result = subprocess.run(
        list(map(str, command)), input=stdin, text=True, timeout=timeout,
        stdout=subprocess.PIPE if capture else LOG,
        stderr=LOG,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        raise RuntimeError(f"command exited {result.returncode}; see results/run.log")
    return result.stdout if capture else None


def download(url, path, sha256):
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        print("Downloading", url, flush=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise RuntimeError(f"SHA-256 mismatch: {url}")
        path.write_bytes(data)


def assert_owned():
    identity = {"cluster": CLUSTER, "repository": str(ROOT)}
    if not OWNER.exists() or json.loads(OWNER.read_text()) != identity or not KUBECONFIG.exists():
        raise RuntimeError("An existing cluster has this name but is not owned by this checkout")


def prerequisites():
    for command in ("docker", "kind", "kubectl", "git"):
        if shutil.which(command) is None:
            raise RuntimeError(f"Install {command} first; see README.md")
    run(["docker", "info"], capture=True)
    if not re.fullmatch(r"linkerd-goaway-repro(?:-[a-z0-9-]+)?", CLUSTER):
        raise RuntimeError("cluster name must start with linkerd-goaway-repro")


def linkerd_cli():
    system = platform.system().lower()
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    suffix = "darwin" if system == "darwin" and arch == "amd64" else f"{system}-{arch}"
    digest = VERSIONS["linkerd_cli_sha256"].get(suffix)
    if digest is None:
        raise RuntimeError("Supported hosts: macOS/Linux on arm64 or amd64")
    path = BUILD / "linkerd"
    version = VERSIONS["linkerd"]
    download(f"https://github.com/linkerd/linkerd2/releases/download/{version}/linkerd2-cli-{version}-{suffix}", path, digest)
    path.chmod(0o755)
    return [str(path), "--kubeconfig", str(KUBECONFIG), "--context", "kind-" + CLUSTER]


def setup():
    clusters = run(["kind", "get", "clusters"], capture=True).splitlines()
    identity = {"cluster": CLUSTER, "repository": str(ROOT)}
    if CLUSTER in clusters:
        assert_owned()
    else:
        OWNER.write_text(json.dumps(identity))
        run(["kind", "create", "cluster", "--name", CLUSTER, "--image", VERSIONS["node_image"],
             "--kubeconfig", KUBECONFIG, "--wait", "180s"], timeout=300)
    cli = linkerd_cli()
    crds = run(K + ["get", "crd", "grpcroutes.gateway.networking.k8s.io", "--ignore-not-found", "-o", "name"], capture=True)
    if not crds.strip():
        run(K + ["apply", "--server-side", "-f", BUILD / "gateway-api.yaml"])
    run(K + ["wait", "--for=condition=Established", "crd/grpcroutes.gateway.networking.k8s.io", "--timeout=120s"])
    installed = run(K + ["-n", "linkerd", "get", "deployment", "linkerd-destination", "--ignore-not-found", "-o", "name"], capture=True)
    if not installed.strip():
        run(K + ["apply", "-f", "-"], stdin=run(cli + ["install", "--crds"], capture=True))
        run(K + ["apply", "-f", "-"], stdin=run(cli + ["install", "--disable-heartbeat"], capture=True))
    run(cli + ["check", "--expected-version", VERSIONS["linkerd"], "--wait", "180s"], timeout=240)


def proxy_image(variant):
    return "localhost/linkerd-goaway-proxy:" + VERSIONS[variant][:12]


def build_proxy(variant):
    revision = VERSIONS[variant]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Proxy revisions must be full commit hashes")
    source = BUILD / (variant + "-source")
    output = BUILD / (variant + "-image")
    output.mkdir(exist_ok=True)
    git = ["git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper="]
    if not source.exists():
        run(git + ["init", str(source)])
    run(git + ["-C", source, "fetch", "--depth=1", VERSIONS["proxy_repository"], revision], timeout=300)
    run(git + ["-C", source, "checkout", "--detach", revision])
    actual = run(git + ["-C", source, "rev-parse", "HEAD"], capture=True).strip()
    if actual != revision:
        raise RuntimeError("Proxy checkout does not match the pinned revision")
    if run(git + ["-C", source, "status", "--porcelain"], capture=True).strip():
        raise RuntimeError("Proxy source contains changes; inspect the checkout before rebuilding")
    stamp = output / "build.json"
    binary = output / "linkerd2-proxy"
    settings = {"revision": revision, "rust": VERSIONS["rust_image"], "base": VERSIONS["proxy_image"]}
    previous = json.loads(stamp.read_text()) if stamp.exists() else {}
    digest = hashlib.sha256(binary.read_bytes()).hexdigest() if binary.exists() else None
    if previous.get("settings") != settings or previous.get("sha256") != digest or digest is None:
        mounts = [(source, "/src", "ro"), (output, "/out", "rw")]
        for folder, target in [("cargo-registry", "/usr/local/cargo/registry"),
                               ("cargo-git", "/usr/local/cargo/git"),
                               ("target-" + revision, "/target")]:
            path = BUILD / folder
            path.mkdir(exist_ok=True)
            mounts.append((path, target, "rw"))
        command = ["docker", "run", "--rm"]
        for path, target, access in mounts:
            command += ["-v", f"{path}:{target}:{access}"]
        command += ["-e", "CARGO_BUILD_JOBS=2", "-e", "CARGO_TARGET_DIR=/target",
                    "-e", "RUSTFLAGS=-D warnings -D deprecated --cfg tokio_unstable -C debuginfo=0",
                    "-e", "LINKERD2_PROXY_VERSION=0.0.0-goaway." + revision[:12],
                    "-e", "LINKERD2_PROXY_VENDOR=goaway-repro", "-w", "/src", VERSIONS["rust_image"],
                    "sh", "-ec", "cargo build --release --locked -p linkerd2-proxy\ncp /target/release/linkerd2-proxy /out/linkerd2-proxy\nstrip /out/linkerd2-proxy"]
        run(command, timeout=3600)
        stamp.write_text(json.dumps({"settings": settings, "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}))
    run(["docker", "build", "--build-arg", "BASE_IMAGE=" + VERSIONS["proxy_image"],
         "-f", ROOT / "proxy/Dockerfile", "-t", proxy_image(variant), output], timeout=600)


def build_images():
    for variant in ("baseline", "patched"):
        build_proxy(variant)
    for app in ("server", "client"):
        run(["docker", "build", "-t", f"localhost/linkerd-goaway-{app}:repro", ROOT / app], timeout=1200)
    images = [proxy_image(v) for v in ("baseline", "patched")]
    images += [f"localhost/linkerd-goaway-{app}:repro" for app in ("server", "client")]
    data = run(["docker", "image", "inspect"] + images, capture=True)
    (RESULTS / "images.json").write_text(json.dumps([
        {"id": item["Id"], "tags": item["RepoTags"]} for item in json.loads(data)
    ], indent=2) + "\n")
    return images


def annotations(variant):
    return {"linkerd.io/inject": "enabled", "config.linkerd.io/proxy-image": "localhost/linkerd-goaway-proxy",
            "config.linkerd.io/proxy-version": VERSIONS[variant][:12],
            "config.linkerd.io/image-pull-policy": "Never", "config.linkerd.io/proxy-log-level": "linkerd=debug,warn"}


def deployment(name, pod_annotations, container):
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}}, "template": {
                "metadata": {"labels": {"app": name}, "annotations": pod_annotations},
                "spec": {"containers": [container]}}}}


def apply(items, name):
    manifest = json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=2)
    path = RESULTS / (name + ".json")
    path.write_text(manifest + "\n")
    run(K + ["apply", "-f", path])


def route_ready(route):
    return any(
        parent.get("controllerName") == "linkerd.io/policy-controller"
        and all(any(
            condition["type"] == kind and condition["status"] == "True"
            and condition.get("observedGeneration", 1) == route["metadata"]["generation"]
            for condition in parent.get("conditions", [])
        ) for kind in ("Accepted", "ResolvedRefs"))
        for parent in route.get("status", {}).get("parents", [])
    )


def deploy():
    run(K + ["delete", "namespace", NAMESPACE, "--ignore-not-found", "--wait=true", "--timeout=120s"], timeout=150)
    servers = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}}]
    for variant in ("plain", "baseline", "patched"):
        pod_annotations = {"linkerd.io/inject": "disabled"} if variant == "plain" else annotations(variant)
        servers.append(deployment("server-" + variant, pod_annotations, {
            "name": "server", "image": "localhost/linkerd-goaway-server:repro", "imagePullPolicy": "Never",
            "ports": [{"containerPort": 50052}], "readinessProbe": {"tcpSocket": {"port": 50052}},
            "env": [{"name": "GRPC_GO_LOG_SEVERITY_LEVEL", "value": "info"},
                    {"name": "GRPC_GO_LOG_VERBOSITY_LEVEL", "value": "2"}],
        }))
    apply(servers, "servers")
    for variant in ("plain", "baseline", "patched"):
        run(K + ["-n", NAMESPACE, "rollout", "status", "deploy/server-" + variant, "--timeout=180s"], timeout=200)
    routes, clients = [], []
    for name, variant, server, disabled, policy in CASES:
        service = "grpc-" + name
        routes += [
            {"apiVersion": "v1", "kind": "Service", "metadata": {"name": service, "namespace": NAMESPACE},
             "spec": {"selector": {"app": "server-" + server}, "ports": [{"name": "grpc", "port": 50052,
                       "targetPort": 50052, "appProtocol": "kubernetes.io/h2c"}]}},
            {"apiVersion": "gateway.networking.k8s.io/v1", "kind": "GRPCRoute",
             "metadata": {"name": service, "namespace": NAMESPACE, "annotations": {
                 "retry.linkerd.io/grpc": "unavailable", "retry.linkerd.io/limit": "1",
                 "retry.linkerd.io/timeout": "60s"} if policy else {}},
             "spec": {"parentRefs": [{"group": "core", "kind": "Service", "name": service, "port": 50052}],
                      "rules": [{"matches": [{"method": {"service": "repro.Echo", "method": "UnaryEcho", "type": "Exact"}}]}]}}
        ]
        clients.append(deployment(name, annotations(variant), {
            "name": "client", "image": "localhost/linkerd-goaway-client:repro", "imagePullPolicy": "Never",
            "args": [service + ":50052", str(disabled).lower()],
            "resources": {"requests": {"memory": "64Mi"}, "limits": {"memory": "256Mi"}},
        }))
    apply(routes, "routes")
    deadline = time.monotonic() + 60
    while True:
        data = json.loads(run(K + ["-n", NAMESPACE, "get", "grpcroutes", "-o", "json"], capture=True, quiet=True))
        if len(data["items"]) == len(CASES) and all(route_ready(route) for route in data["items"]):
            break
        if time.monotonic() > deadline:
            raise RuntimeError("GRPCRoutes were not accepted before starting the clients")
        time.sleep(2)
    apply(clients, "clients")


def collect():
    summaries = {}
    for name, *_ in CASES:
        run(K + ["-n", NAMESPACE, "rollout", "status", "deploy/" + name, "--timeout=180s"], timeout=200)
    deadline = time.monotonic() + 240
    while len(summaries) != len(CASES):
        for name, *_ in CASES:
            if name in summaries:
                continue
            log = run(K + ["-n", NAMESPACE, "logs", "deploy/" + name, "-c", "client"], capture=True, quiet=True)
            (RESULTS / (name + ".log")).write_text(log)
            match = re.search(r"^SUMMARY (.+)$", log, re.M)
            if match:
                summaries[name] = json.loads(match[1])
                print(f"{name}: {summaries[name]['success']}/20 RPCs succeeded", flush=True)
        if time.monotonic() > deadline:
            raise RuntimeError("Clients did not complete before the collection deadline")
        if len(summaries) != len(CASES):
            time.sleep(3)
    pods = json.loads(run(K + ["-n", NAMESPACE, "get", "pods", "-o", "json"], capture=True))
    (RESULTS / "pods.json").write_text(json.dumps(pods, indent=2) + "\n")
    for pod in pods["items"]:
        name = pod["metadata"]["name"]
        for container in pod["spec"].get("initContainers", []) + pod["spec"]["containers"]:
            if container["name"] != "client":
                log = run(K + ["-n", NAMESPACE, "logs", name, "-c", container["name"]], capture=True)
                (RESULTS / (name + "-" + container["name"] + ".log")).write_text(log)
        statuses = pod["status"].get("containerStatuses", []) + pod["status"].get("initContainerStatuses", [])
        if any(c["restartCount"] for c in statuses):
            raise RuntimeError(f"Unexpected container restart: {name}")
    for name, *_ in CASES:
        pod = next(p for p in pods["items"] if p["metadata"]["labels"].get("app") == name)
        path = f"/api/v1/namespaces/{NAMESPACE}/pods/{pod['metadata']['name']}:4191/proxy/metrics"
        (RESULTS / (name + ".prom")).write_text(run(K + ["get", "--raw", path], capture=True))
    return summaries


def verify(summaries, proxy_logs=None, authorities=None):
    rows, problems = [], []
    if proxy_logs is None:
        pods = json.loads((RESULTS / "pods.json").read_text())["items"]
        proxy_logs = {
            pod["metadata"]["labels"]["app"]: (RESULTS / (pod["metadata"]["name"] + "-linkerd-proxy.log")).read_text()
            for pod in pods if pod["metadata"]["labels"].get("app") in summaries
        }
    if authorities is None:
        authorities = {name: f"grpc-{name}.{NAMESPACE}.svc.cluster.local:50052" for name, *_ in CASES}
    for name, variant, server, disabled, policy in CASES:
        result = summaries[name]
        log = (RESULTS / (name + ".log")).read_text()
        metrics = (RESULTS / (name + ".prom")).read_text()
        valid = result["completed"] and result["requests"] == 20 and result["success"] + result["failed"] == 20
        proxy_log = proxy_logs[name]
        valid &= "release 0.0.0-goaway." + VERSIONS[variant][:12] in proxy_log
        if variant == "baseline":
            valid &= result["failed"] > 0 and result["transparentRetries"] == 0
            valid &= len(re.findall(r"^RPC .*code=INTERNAL", log, re.M)) == result["failed"]
        elif policy:
            valid &= result["success"] == 20 and result["attempts"] == 20 and result["transparentRetries"] == 0
            valid &= any(line.startswith("outbound_grpc_route_retry_successes_total") and float(line.rsplit(" ", 1)[1]) > 0 for line in metrics.splitlines())
        elif disabled:
            valid &= result["failed"] > 0 and result["transparentRetries"] == 0
            valid &= len(re.findall(r"^RPC .*code=UNAVAILABLE.*REFUSED_STREAM", log, re.M)) == result["failed"]
        else:
            refused = set(re.findall(r"^STREAM_CLOSED .*? id=(\d+).*REFUSED_STREAM", log, re.M))
            retried = set(re.findall(r"^ATTEMPT .*? id=(\d+).*transparent=true", log, re.M))
            recovered = set(re.findall(r"^RPC id=(\d+) code=OK attempts=2", log, re.M))
            valid &= result["success"] == 20 and result["transparentRetries"] > 0
            valid &= result["attempts"] == 20 + result["transparentRetries"]
            valid &= refused == retried == recovered and len(retried) == result["transparentRetries"]
        if variant == "patched":
            valid &= any('error="REFUSED"' in line and float(line.rsplit(" ", 1)[1]) > 0 for line in metrics.splitlines())
        if server != "plain":
            valid &= any(line.startswith("tcp_open_total") and 'peer="dst"' in line and 'tls="true"' in line
                         and f'authority="{authorities[name]}"' in line
                         and float(line.rsplit(" ", 1)[1]) > 0 for line in metrics.splitlines())
        rows.append(f"| {name} | {result['success']}/20 | {result['failed']} | {result['transparentRetries']} | {'PASS' if valid else 'FAIL'} |")
        if not valid:
            problems.append(name)
    report = "| Case | Successful RPCs | Failed RPCs | Transparent retries | Expected behavior |\n|---|---:|---:|---:|---|\n" + "\n".join(rows) + "\n"
    (RESULTS / "report.md").write_text(report)
    (RESULTS / "summary.json").write_text(json.dumps({"versions": VERSIONS, "cases": summaries, "failed_checks": problems}, indent=2) + "\n")
    print(report, flush=True)
    if problems:
        raise RuntimeError("Unexpected results or GOAWAY not reproduced: " + ", ".join(problems))


def main():
    global LOG
    parser = argparse.ArgumentParser(description="Reproduce GOAWAY cancellations and verify the patched proxy")
    parser.add_argument("--clean", action="store_true", help="delete only this checkout's kind cluster")
    parser.add_argument("--plan", action="store_true", help="show scope and pinned inputs without running commands")
    args = parser.parse_args()
    if args.plan:
        print(f"Create/reuse kind cluster: {CLUSTER}\nKubeconfig: {KUBECONFIG}\n"
              f"Replace namespace on each run: {NAMESPACE}\nBuild/cache directory: {BUILD}\n"
              f"Logs, manifests, metrics: {RESULTS}\n"
              "Build both proxy commits, Go server, and Java client using Docker.\n"
              "Install Linkerd and Gateway API CRDs in this cluster.\n"
              "Run seven cases (140 RPCs); keep the cluster until make clean.\n"
              "make clean deletes only the cluster owned by this checkout.\n"
              "Docker runs privileged kind nodes; use a disposable VM for isolation.\n")
        print(json.dumps(VERSIONS, indent=2))
        return
    BUILD.mkdir(exist_ok=True, mode=0o700)
    lock = (BUILD / "run.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Another reproduction command is running in this checkout") from None
    RESULTS.mkdir(exist_ok=True)
    LOG = (RESULTS / "run.log").open("a", buffering=1)
    prerequisites()
    if args.clean:
        assert_owned()
        run(["kind", "delete", "cluster", "--name", CLUSTER, "--kubeconfig", KUBECONFIG])
        OWNER.unlink()
        return
    gateway = VERSIONS["gateway_api"]
    download(f"https://github.com/kubernetes-sigs/gateway-api/releases/download/{gateway}/standard-install.yaml",
             BUILD / "gateway-api.yaml", VERSIONS["gateway_api_sha256"])
    images = build_images()
    setup()
    run(["kind", "load", "docker-image", "--name", CLUSTER] + images, timeout=600)
    deploy()
    verify(collect())
    print("PASS: all seven conditions matched. Results: results/report.md", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
