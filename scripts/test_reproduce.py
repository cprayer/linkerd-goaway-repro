import hashlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import reproduce


class SafetyTests(unittest.TestCase):
    def test_download_rejects_unexpected_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cli"
            with patch("reproduce.urllib.request.urlopen", return_value=io.BytesIO(b"modified")):
                with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                    reproduce.download("https://example.test/cli", target, hashlib.sha256(b"expected").hexdigest())
            self.assertFalse(target.exists())

    def test_download_checks_cached_bytes_before_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "cli"
            target.write_bytes(b"modified")
            with patch("reproduce.urllib.request.urlopen", return_value=io.BytesIO(b"expected")) as fetch:
                reproduce.download("https://example.test/cli", target, hashlib.sha256(b"expected").hexdigest())
            fetch.assert_called_once()
            self.assertEqual(target.read_bytes(), b"expected")

    def test_rejects_cluster_without_this_checkouts_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            owner, config = root / "owner.json", root / "kubeconfig"
            with patch.multiple(reproduce, ROOT=root, OWNER=owner, KUBECONFIG=config):
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    reproduce.assert_owned()
                config.touch()
                owner.write_text(json.dumps({"cluster": reproduce.CLUSTER, "repository": str(root / "other")}))
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    reproduce.assert_owned()
                owner.write_text(json.dumps({"cluster": reproduce.CLUSTER, "repository": str(root)}))
                reproduce.assert_owned()

    def test_plan_does_not_execute_commands_or_create_directories(self):
        with patch("sys.argv", ["reproduce.py", "--plan"]), \
                patch("reproduce.run") as run, \
                patch("pathlib.Path.mkdir") as mkdir, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            reproduce.main()
        run.assert_not_called()
        mkdir.assert_not_called()
        self.assertIn(reproduce.CLUSTER, output.getvalue())


class RouteReadinessTests(unittest.TestCase):
    def test_fresh_accepted_route_without_observed_generation(self):
        conditions = [{"type": kind, "status": "True"} for kind in ("Accepted", "ResolvedRefs")]
        route = {"metadata": {"generation": 1}, "status": {"parents": [{
            "controllerName": "linkerd.io/policy-controller", "conditions": conditions,
        }]}}
        self.assertTrue(reproduce.route_ready(route))
        route["metadata"]["generation"] = 2
        self.assertFalse(reproduce.route_ready(route))
        for condition in conditions:
            condition["observedGeneration"] = 2
        self.assertTrue(reproduce.route_ready(route))
        conditions[0]["status"] = "False"
        self.assertFalse(reproduce.route_ready(route))


if __name__ == "__main__":
    unittest.main()
