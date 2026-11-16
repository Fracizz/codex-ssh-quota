import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import server
import remote_snapshot


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(server.os.environ, {"CODEX_QUOTA_MODE": "ssh", "CODEX_QUOTA_SSH_HOST": "example-host"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_local_source_does_not_start_ssh(self):
        with patch.dict(server.os.environ, {"CODEX_QUOTA_MODE": "local"}), patch.object(remote_snapshot, "collect", return_value={"rateLimits": {}}), patch.object(server.subprocess, "Popen") as launch:
            self.assertEqual(server.read_limits(), {"rateLimits": {}})
            launch.assert_not_called()
            self.assertTrue(server.source_key().startswith("local:"))

    def test_installer_stages_private_settings(self):
        import install
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "runtime"
            before = (Path(server.ROOT) / "mcp.json").read_bytes()
            install.prepare(runtime, {"mode": "ssh", "sshHost": "private-example"})
            settings = json.loads((runtime / "plugin/settings.json").read_text())
            self.assertEqual(settings["sshHost"], "private-example")
            mcp = json.loads((runtime / "plugin/mcp.json").read_text())
            self.assertFalse(Path(mcp["mcpServers"]["codex-ssh-quota"]["command"]).is_absolute())
            self.assertEqual((Path(server.ROOT) / "mcp.json").read_bytes(), before)

    def test_unknown_window_does_not_mean_full_quota(self):
        data = server.normalize({"rateLimits": {"primary": {"usedPercent": None}}})
        self.assertIsNone(data["buckets"][0]["windows"][0]["remainingPercent"])
        self.assertEqual(server.normalize({})["buckets"], [])

    def test_multiple_buckets_and_window_durations(self):
        raw = {"rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 4, "windowDurationMins": 10080}, "secondary": None},
            "other": {"primary": {"usedPercent": 60, "windowDurationMins": 300}}}}
        data = server.normalize(raw)
        self.assertEqual([b["windows"][0]["remainingPercent"] for b in data["buckets"]], [96, 40])
        self.assertEqual(data["buckets"][0]["windows"][0]["durationMins"], 10080)

    def test_secret_fields_and_output_limits(self):
        raw = {"access_token": "SECRET", "accountId": "PRIVATE", "rateLimitResetCredits": {
            "availableCount": 3, "credits": [{"id": "PRIVATE_RESET_ID"}]},
            "rateLimitsByLimitId": {str(i): {"limitName": "X" * 10000} for i in range(50)}}
        data = server.normalize(raw)
        output = json.dumps(data)
        self.assertNotIn("SECRET", output)
        self.assertNotIn("PRIVATE", output)
        self.assertEqual(data["omittedBuckets"], 30)
        self.assertLess(len(output.encode()), server.MAX_RESULT)

    def test_cache_does_not_repeat_queries_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(server, "_cache", None), patch.object(server, "cache_path", return_value=Path(temp)/"cache.json"), patch.object(server, "read_limits", return_value={}) as read:
                first = server.quota()
                server.quota()
                self.assertEqual(read.call_count, 1)
                server._cache = None
                self.assertEqual(server.quota(), first)
                self.assertEqual(read.call_count, 1)
                read.side_effect = server.QuotaError("offline")
                fallback = server.quota(force=True)
                self.assertIn("缓存", fallback["warning"])
                self.assertEqual(fallback["checkedAt"], first["checkedAt"])

    def test_mcp_handshake_resource_and_safe_error(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": server.URI}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "codex_quota", "arguments": {"bad": 1}}}]
        result = subprocess.run([sys.executable, str(Path(server.__file__))],
                                input="".join(json.dumps(m) + "\n" for m in messages),
                                text=True, capture_output=True, timeout=10, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([m["id"] for m in replies], [1, 2, 3, 4])
        tool = replies[1]["result"]["tools"][0]
        self.assertIn({"type": "thread"}, tool["_meta"]["openai/ui"]["entrypoints"])
        self.assertIn("ui/initialize", replies[2]["result"]["contents"][0]["text"])
        self.assertTrue(replies[3]["result"]["isError"])

    def test_process_timeout_and_ssh_error(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "fake.py"
            fake.write_text('import sys,time\nsys.stdin.read()\ntime.sleep(10)\n', encoding="utf-8")
            real_popen = subprocess.Popen
            def launch(command, **kwargs):
                self.assertIn("BatchMode=yes", command)
                self.assertIn("StrictHostKeyChecking=yes", command)
                self.assertIn(server.ssh_target(), command)
                return real_popen([sys.executable, str(fake)], **kwargs)
            with patch.object(server.shutil, "which", return_value="ssh"), patch.object(server.subprocess, "Popen", side_effect=launch):
                with self.assertRaisesRegex(server.QuotaError, "超时"):
                    server.read_limits(timeout=0.1)
            fake.write_text('import sys\nsys.stdin.read()\nprint("SECRET")\nsys.exit(255)\n', encoding="utf-8")
            with patch.object(server.shutil, "which", return_value="ssh"), patch.object(server.subprocess, "Popen", side_effect=launch):
                with self.assertRaisesRegex(server.QuotaError, "SSH 查询失败") as error:
                    server.read_limits(timeout=2)
                self.assertNotIn("SECRET", str(error.exception))

    def test_remote_snapshot_emits_quota_only(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            date = datetime.now(timezone.utc).strftime("%Y/%m/%d")
            folder = home / "sessions" / date
            folder.mkdir(parents=True)
            events = [{"type":"response_item", "payload":{"type":"message", "content":"PRIVATE_CHAT"}},
                      {"timestamp":"2026-11-16T07:41:00Z", "type":"event_msg", "payload":{"type":"token_count", "rate_limits":{"primary":{"used_percent":4,"window_minutes":10080,"resets_at":1795259054},"plan_type":"pro","secret":"PRIVATE_TOKEN"}}}]
            (folder/"rollout.jsonl").write_text("".join(json.dumps(e)+"\n" for e in events))
            result = remote_snapshot.collect(home)
            self.assertEqual(result["rateLimits"]["primary"]["usedPercent"], 4)
            self.assertEqual(result["snapshotRecordedAt"], events[1]["timestamp"])
            self.assertNotIn("PRIVATE", json.dumps(result))
            self.assertEqual(remote_snapshot.collect(home / "missing")["snapshotError"], "no_recent_quota_snapshot")

    def test_invalid_ssh_target_is_rejected(self):
        with patch.dict(server.os.environ, {"CODEX_QUOTA_SSH_HOST":"-oProxyCommand=anything"}):
            with self.assertRaises(server.QuotaError):
                server.ssh_target()


if __name__ == "__main__":
    unittest.main()
