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


if __name__ == "__main__":
    unittest.main()
