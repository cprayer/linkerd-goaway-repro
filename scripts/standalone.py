"""Run all seven GOAWAY cases as local processes inside the Docker image."""

import argparse
import contextlib
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


def scenario(name, variant, disabled, directory):
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
            start(["goaway-server", "-port", str(port)], name + "-server.log")
            wait_for(lambda: listening(port), processes, 30)
            start(["goaway-" + variant, name, "127.0.0.1:" + str(port), str(ready)], name + "-proxy.log")
            wait_for(ready.is_file, processes, 30)
            addresses = json.loads(ready.read_text())
            client_log = directory / (name + ".log")
            start(["java", "-XX:ActiveProcessorCount=2", "-Xms32m", "-Xmx128m", "-jar", "/app/client.jar",
                   addresses["outbound"], str(disabled).lower()], client_log.name)
            match = wait_for(lambda: re.search(r"^SUMMARY (.+)$", client_log.read_text(), re.M), processes, 150)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("/results"))
    args = parser.parse_args()
    args.results.mkdir(parents=True, exist_ok=True)
    directory = args.results / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + str(time.time_ns()))
    directory.mkdir()
    reproduce.RESULTS = directory
    summaries, authorities, proxy_logs = {}, {}, {}
    signal.signal(signal.SIGTERM, interrupt)
    print("Results: " + str(directory), flush=True)
    try:
        for name, variant, _server, disabled, _policy in reproduce.CASES:
            print("Running " + name, flush=True)
            summaries[name], authorities[name] = scenario(name, variant, disabled, directory)
            proxy_logs[name] = (directory / (name + "-proxy.log")).read_text()
            print(json.dumps(summaries[name]), flush=True)
        reproduce.verify(summaries, proxy_logs, authorities)
        print("PASS: all seven conditions matched", flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        (directory / "error.txt").write_text(str(error) or "Interrupted")
        print("FAIL: " + (str(error) or "Interrupted"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
