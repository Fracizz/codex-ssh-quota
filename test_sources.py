import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import hud
import server
import source_catalog


class SourceTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"CODEX_QUOTA_MODE": "", "CODEX_QUOTA_SSH_HOST": "", "CODEX_QUOTA_SOURCE_NAME": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.settings = {"mode": "ssh", "sshHost": "alpha", "sourceName": "Alpha", "discoverSsh": False,
                         "sources": [{"sshHost": "beta", "sourceName": "Beta"}]}

    def test_discovers_literal_aliases_without_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"config"
            path.write_text('Host *\n  IdentityFile PRIVATE_KEY_PATH\nHost dev staging\n  HostName private.example\nHost !excluded wild*\nHost "quoted-name"\n')
            self.assertEqual(source_catalog.ssh_aliases(path), ["dev", "staging", "quoted-name"])
        with patch.object(source_catalog, "ssh_aliases", return_value=["alpha", "other"]):
            result, omitted = source_catalog.catalog({**self.settings, "discoverSsh": True})
            self.assertEqual([row["sourceName"] for row in result], ["Alpha", "Beta", "other"])
            self.assertEqual(omitted, 0)

    def test_catalog_bound_and_pinned_environment(self):
        with patch.object(source_catalog, "ssh_aliases", return_value=[f"host-{i}" for i in range(64)]):
            result, omitted = source_catalog.catalog({**self.settings, "discoverSsh": True})
            self.assertEqual(len(result), 32)
            self.assertEqual(omitted, 34)
            with patch.dict(os.environ, {"CODEX_QUOTA_SSH_HOST": "pinned"}):
                result, _ = source_catalog.catalog(self.settings)
                self.assertEqual([row["sshHost"] for row in result], ["pinned"])

    def test_selection_survives_reload_and_rejects_unknown_sources(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, "load_settings", return_value=self.settings), patch.object(server, "selection_path", return_value=Path(temp)/"selection.json"):
            selected = server.select_source("Beta")
            self.assertEqual(server.resolve_source()["id"], selected["id"])
            with self.assertRaises(server.QuotaError):
                server.resolve_source("unknown")
            Path(temp, "selection.json").write_text('{"sourceId":"removed"}')
            self.assertEqual(server.resolve_source()["sourceName"], "Alpha")

    def test_installer_can_extend_existing_runtime_sources(self):
        import install
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)/"runtime"
            package = runtime/"plugin"
            package.mkdir(parents=True)
            (package/"settings.json").write_text(json.dumps(self.settings))
            with patch.object(server, "load_settings", return_value={}), patch.object(server, "executable", return_value="codex"), patch.object(install.subprocess, "run") as run, patch("builtins.print"):
                run.return_value.returncode = 0
                self.assertEqual(install.main(["--runtime-dir", str(runtime), "--add-ssh", "Gamma=gamma"]), 0)
            result = json.loads((package/"settings.json").read_text())
            self.assertEqual(result["sshHost"], "alpha")
            self.assertEqual([entry["sshHost"] for entry in result["sources"]], ["beta", "gamma"])

    def test_concurrent_queries_keep_source_identity_and_cache(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, "load_settings", return_value=self.settings), patch.object(server, "selection_path", return_value=Path(temp)/"selection.json"), patch.object(server, "cache_path", side_effect=lambda host: Path(temp)/(host+".json")), patch.object(server, "_cache", None):
            profiles, _ = server.available_sources()
            barrier = threading.Barrier(2)
            release_alpha = threading.Event()
            def read():
                host = server.ssh_target()
                barrier.wait(timeout=3)
                if host == "alpha":
                    release_alpha.wait(timeout=3)
                self.assertEqual(server.ssh_target(), host)
                return {"sourceHost": host+"-machine", "rateLimits": {"primary": {
                    "usedPercent": 10 if host == "alpha" else 50, "windowDurationMins": 10080}}}
            with patch.object(server, "read_limits", side_effect=read), concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(server.quota, True, profiles[0]["id"])
                b = pool.submit(server.quota, True, profiles[1]["id"])
                beta = b.result(timeout=4)
                server.select_source(profiles[1]["id"])
                release_alpha.set()
                alpha = a.result(timeout=4)
            self.assertEqual((alpha["sourceName"], alpha["host"]), ("Alpha", "alpha-machine"))
            self.assertEqual((beta["sourceName"], beta["host"]), ("Beta", "beta-machine"))
            self.assertEqual(alpha["buckets"][0]["windows"][0]["remainingPercent"], 90)
            self.assertEqual(beta["buckets"][0]["windows"][0]["remainingPercent"], 50)
            self.assertEqual(json.loads(Path(temp,"alpha.json").read_text())["sshHost"], "alpha")
            self.assertEqual(json.loads(Path(temp,"beta.json").read_text())["sshHost"], "beta")

    def test_late_source_result_cannot_replace_selected_display(self):
        beta = {"sourceId": "b", "sourceName": "Beta"}
        state = {"source": {"id": "b"}, "data": beta, "error": None, "pending": {"a"},
                 "results": {}, "nextReads": {}}
        applied = hud.accept_source_result(state, {"sourceId": "a", "data": {"sourceId": "a", "sourceName": "Alpha"}})
        self.assertFalse(applied)
        self.assertEqual(state["data"], beta)
        self.assertIn("a", state["results"])
        hud.accept_source_result(state, {"sourceId": "b", "data": {"sourceId": "a"}})
        self.assertIsNone(state["data"])
        self.assertIn("来源校验", state["error"])


if __name__ == "__main__":
    unittest.main()
