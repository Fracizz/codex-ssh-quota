"""Check installation/tool discovery in a disposable CODEX_HOME, never personal config."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

import server


def main():
    root = Path(__file__).resolve().parent
    originals = {root / name: (root / name).read_bytes() for name in ("mcp.json", ".mcp.json")}
    try:
        codex = server.executable()
        with tempfile.TemporaryDirectory(prefix="codex-ssh-quota-check-", dir=root.parent) as temp:
            env = dict(os.environ, CODEX_HOME=temp)
            installed = subprocess.run([sys.executable, str(root / "install.py"), "--local", "--runtime-dir", str(Path(temp) / "marketplace")], env=env,
                                       capture_output=True, timeout=60)
            if installed.returncode:
                print((installed.stdout + installed.stderr).decode("utf-8", errors="replace")[:3000])
                return 1
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            process = subprocess.Popen([codex, "app-server", "--stdio"], env=env, cwd=root,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, **kwargs)
            messages = queue.Queue()

            def read():
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        pass

            reader = threading.Thread(target=read, daemon=True)
            reader.start()

            def send(message):
                process.stdin.write(json.dumps(message).encode() + b"\n")
                process.stdin.flush()

            try:
                send({"id": 1, "method": "initialize", "params": {
                    "clientInfo": {"name": "codex_quota_install_check", "version": "0.1.0"}}})
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        response = messages.get(timeout=1)
                    except queue.Empty:
                        continue
                    if response.get("id") == 1:
                        send({"method": "initialized"})
                        send({"id": 2, "method": "mcpServerStatus/list", "params": {}})
                    elif response.get("id") == 2:
                        entries = response.get("result", {}).get("data", [])
                        for entry in entries:
                            if entry.get("pluginId") == "codex-ssh-quota@codex-ssh-quota":
                                if entry.get("toolsError"):
                                    print(str(entry["toolsError"])[:1500])
                                    return 1
                                tools = entry.get("tools", {})
                                if any(t.get("name") == "codex_quota" for t in tools.values()):
                                    print("PASS: isolated plugin install and Codex MCP tool discovery")
                                    return 0
                        print("FAIL: installed plugin is not exposed in MCP server status")
                        return 1
                print("FAIL: app-server timeout")
                return 1
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                reader.join(timeout=1)
                process.stdin.close()
                process.stdout.close()
    finally:
        for path, original in originals.items():
            path.write_bytes(original)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
