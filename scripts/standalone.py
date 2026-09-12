"""Compare direct, baseline, and patched gRPC calls inside the Docker image."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import reproduce


def evidence(name, result, log):
    if name.startswith("baseline-"):
        match = re.search(r"^RPC id=(\d+) code=INTERNAL attempts=(\d+) error=.*unexpected error$", log, re.M)
        if match:
            return f'GOAWAY {name} RPC {match[1]} FINAL INTERNAL attempts={match[2]} error="unexpected error"'
    elif name in ("patched-one", "patched-two"):
        match = re.search(r"^ATTEMPT .* id=(\d+) attempt=2 transparent=true$", log, re.M)
        if match and re.search(rf"^RPC id={match[1]} code=OK attempts=2(?: |$)", log, re.M):
            return f"GOAWAY {name} RPC {match[1]} REFUSED_STREAM -> transparent retry -> OK attempts=2"
    return (f"GOAWAY {name} requests={result['requests']} success={result['success']} "
            f"failed={result['failed']} transparent_retries={result['transparentRetries']}")


def wait_for(check, processes, timeout):
    deadline = time.monotonic() + timeout
    while True:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("A fixture process exited unexpectedly; inspect the case logs")
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for fixture readiness or client results")
        time.sleep(0.1)


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def scenario(name, variant, disabled, directory, language="java"):
    processes = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    ready = directory / (name + "-listeners.json")
    with contextlib.ExitStack() as stack:
        def start(command, filename):
            log = stack.enter_context((directory / filename).open("w"))
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            return process

        try:
            java = ["java", "-XX:ActiveProcessorCount=2", "-Xms32m", "-Xmx128m", "-cp", "/app/client.jar"]
            server_command = (["goaway-server", "-port", str(port)] if language == "go" else
                              java + ["repro.GoAwayServer", str(port)])
            start(server_command, name + "-server.log")
            wait_for(lambda: listening(port), processes, 30)
            addresses = {"outbound": "127.0.0.1:" + str(port), "authority": "direct"}
            if variant != "direct":
                start(["goaway-" + variant, name, addresses["outbound"], str(ready)], name + "-proxy.log")
                wait_for(ready.is_file, processes, 30)
                addresses = json.loads(ready.read_text())
            client_log = directory / (name + ".log")
            client_command = ["goaway-client"] if language == "go" else java + ["repro.Reproduce"]
            start(client_command + [addresses["outbound"], str(disabled).lower()], client_log.name)
            match = wait_for(lambda: re.search(r"^SUMMARY (.+)$", client_log.read_text(), re.M), processes, 150)
            if variant != "direct":
                with urllib.request.urlopen("http://" + addresses["admin"] + "/metrics", timeout=10) as response:
                    (directory / (name + ".prom")).write_bytes(response.read())
            return json.loads(match[1]), addresses["authority"]
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def execute(directory, language="java"):
    directory.mkdir()
    reproduce.RESULTS = directory
    summaries, authorities, proxy_logs = {}, {}, {}
    print("Results: " + str(directory), flush=True)
    cases = reproduce.CASES if language == "java" else reproduce.CASES[:4]
    try:
        print(f"Running direct ({language} client -> {language} server)", flush=True)
        direct, _ = scenario("direct", "direct", False, directory, language)
        if not (direct["completed"] and direct["requests"] == direct["success"] == direct["attempts"] == 20
                and direct["failed"] == direct["transparentRetries"] == 0):
            raise RuntimeError("Direct calls failed or retried; inspect direct.log and direct-server.log")
        print(f"DIRECT {language}->{language} requests=20 success=20 retries=0", flush=True)
        for name, variant, _server, disabled, _policy in cases:
            print("Running " + name, flush=True)
            summaries[name], authorities[name] = scenario(name, variant, disabled, directory, language)
            proxy_logs[name] = (directory / (name + "-proxy.log")).read_text()
        with contextlib.redirect_stdout(io.StringIO()):
            reproduce.verify(summaries, proxy_logs, authorities, cases)
        for name in ("baseline-one", "patched-one"):
            print(evidence(name, summaries[name], (directory / (name + ".log")).read_text()), flush=True)
        summary_path = directory / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary.update(language=language, direct=direct)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        with (directory / "report.md").open("a") as report:
            report.write(f"\nLanguage: {language}. Direct: 20/20 successful RPCs, no retries.\n")
            report.write("\n| Case | Client | Server | Proxy | Metrics |\n|---|---|---|---|---|\n")
            report.write("| direct | [log](direct.log) | [log](direct-server.log) | — | — |\n")
            for name, *_ in cases:
                report.write(f"| {name} | [log]({name}.log) | [log]({name}-server.log) | "
                             f"[log]({name}-proxy.log) | [metrics]({name}.prom) |\n")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        (directory / "error.txt").write_text(str(error) or "Interrupted")
        print("FAIL: " + (str(error) or "Interrupted"), file=sys.stderr)
        return 1


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=positive, default=5)
    parser.add_argument("--language", choices=("java", "go"), default="java")
    parser.add_argument("--results", type=Path, default=Path("/results"))
    args = parser.parse_args()
    directory = args.results / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + str(time.time_ns()))
    directory.mkdir(parents=True)
    signal.signal(signal.SIGTERM, interrupt)
    rows = ["| Run | Result | Logs and checks |", "|---|---|---|"]
    for index in range(1, args.runs + 1):
        name = f"run-{index:03d}"
        print(f"Run {index}/{args.runs}", flush=True)
        code = execute(directory / name, args.language)
        status = "FAIL" if code else "PASS"
        target = "error.txt" if code else "report.md"
        rows.append(f"| {index} | {status} | [details]({name}/{target}) |")
        (directory / "report.md").write_text("\n".join(rows) + "\n")
        summary = directory / name / "summary.json"
        if summary.exists():
            cases = json.loads(summary.read_text())["cases"]
            baseline = sum(cases[name]["failed"] for name in ("baseline-one", "baseline-two"))
            patched = sum(cases[name]["failed"] for name in ("patched-one", "patched-two"))
            comparison = f" baseline failures={baseline} -> patched failures={patched} ->"
        else:
            comparison = ""
        print(f"RUN {index}/{args.runs}{comparison} {status}", flush=True)
        if code:
            return code
    print(f"PASS: {args.runs}/{args.runs} runs; all selected checks matched in each run", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
