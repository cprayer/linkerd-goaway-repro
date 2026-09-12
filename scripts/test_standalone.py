import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import reproduce
import standalone


class StandaloneTests(unittest.TestCase):
    def test_evidence_shows_the_observed_failure_and_retry(self):
        result = dict(requests=20, success=20, failed=0, transparentRetries=3)
        baseline = "RPC id=6 code=INTERNAL attempts=1 error=rpc error: code = Internal desc = unexpected error\n"
        patched = ("ATTEMPT time=t id=5 attempt=2 transparent=true\n"
                   "RPC id=5 code=OK attempts=2\n")
        self.assertEqual(standalone.evidence("baseline-one", result, baseline),
                         'GOAWAY baseline-one RPC 6 FINAL INTERNAL attempts=1 error="unexpected error"')
        self.assertEqual(standalone.evidence("patched-one", result, patched),
                         "GOAWAY patched-one RPC 5 REFUSED_STREAM -> transparent retry -> OK attempts=2")

    def test_shared_verifier_requires_retry_ids_and_mtls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summaries, proxies, authorities = {}, {}, {}
            for name, variant, server, disabled, policy in reproduce.CASES:
                fails = int(variant == "baseline" or disabled and not policy)
                retries = int(variant == "patched" and not disabled)
                summaries[name] = dict(completed=True, requests=20, success=20-fails, failed=fails,
                                       attempts=20+retries, transparentRetries=retries)
                proxies[name] = "release 0.0.0-goaway." + reproduce.VERSIONS[variant][:12]
                authorities[name] = name + ".test:50052"
                log = ""
                if variant == "baseline":
                    log = "RPC id=0 code=INTERNAL attempts=1\n"
                elif disabled and not policy:
                    log = "RPC id=0 code=UNAVAILABLE attempts=1 error=REFUSED_STREAM\n"
                elif retries:
                    log = ("STREAM_CLOSED time=t id=0 code=UNAVAILABLE description=REFUSED_STREAM\n"
                           "ATTEMPT time=t id=0 attempt=2 transparent=true\n"
                           "RPC id=0 code=OK attempts=2\n")
                (root / (name + ".log")).write_text(log)
                metrics = 'request_total{error="REFUSED"} 1\n' if variant == "patched" else ""
                if policy:
                    metrics += 'outbound_grpc_route_retry_successes_total{route="test"} 1\n'
                if server != "plain":
                    metrics += 'tcp_open_total{peer="dst",tls="true",authority="' + authorities[name] + '"} 1\n'
                (root / (name + ".prom")).write_text(metrics)
            with patch.object(reproduce, "RESULTS", root), contextlib.redirect_stdout(io.StringIO()):
                reproduce.verify(summaries, proxies, authorities)
                path = root / "patched-one.log"
                original = path.read_text()
                path.write_text(original.replace("transparent=true", "transparent=false"))
                with self.assertRaisesRegex(RuntimeError, "patched-one"):
                    reproduce.verify(summaries, proxies, authorities)
                path.write_text(original)
                path = root / "patched-two.prom"
                path.write_text(path.read_text().replace('tls="true"', 'tls="false"'))
                with self.assertRaisesRegex(RuntimeError, "patched-two"):
                    reproduce.verify(summaries, proxies, authorities)

    def test_wait_rejects_exited_process_even_with_ready_file(self):
        with self.assertRaisesRegex(RuntimeError, "exited unexpectedly"):
            standalone.wait_for(lambda: True, [SimpleNamespace(poll=lambda: 1)], 1)

    def test_batch_repeats_and_stops_on_failure(self):
        for codes in ([0, 0], [1]):
            with self.subTest(codes=codes), tempfile.TemporaryDirectory() as directory, \
                    patch("sys.argv", ["standalone.py", "--runs", "2", "--results", directory]), \
                    patch.object(standalone, "execute", side_effect=codes) as execute, \
                    patch.object(standalone.signal, "signal"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(standalone.main(), codes[-1])
                self.assertEqual(execute.call_count, len(codes))
                paths = [call.args[0] for call in execute.call_args_list]
                self.assertEqual(len(set(paths)), len(codes))
                report = next(Path(directory).glob("*/report.md")).read_text()
                self.assertIn("FAIL" if codes[-1] else "PASS", report)
                self.assertIn("run-001/error.txt" if codes[-1] else "run-002/report.md", report)

    def test_runs_must_be_positive(self):
        for value in ("0", "-1", "invalid"):
            with self.subTest(value=value), patch("sys.argv", ["standalone.py", "--runs", value]), \
                    patch.object(standalone, "execute") as execute, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    standalone.main()
                self.assertEqual(error.exception.code, 2)
                execute.assert_not_called()

    def test_failed_direct_control_stops_before_proxy_cases(self):
        direct = dict(completed=True, requests=20, success=19, failed=1, attempts=20, transparentRetries=0)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(standalone, "scenario", return_value=(direct, "direct")) as scenario, \
                patch.object(reproduce, "verify") as verify, \
                patch.object(reproduce, "RESULTS", reproduce.RESULTS), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root = Path(directory) / "run"
            self.assertEqual(standalone.execute(root, "go"), 1)
            scenario.assert_called_once_with("direct", "direct", False, root, "go")
            verify.assert_not_called()
            self.assertIn("Direct calls failed", (root / "error.txt").read_text())


if __name__ == "__main__":
    unittest.main()
