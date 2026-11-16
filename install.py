"""Configure a private runtime copy, then install with Codex's plugin CLI."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import server
import source_catalog

PLUGIN_ID = "codex-ssh-quota@codex-ssh-quota"
FILES = ("server.py", "source_catalog.py", "remote_snapshot.py", "panel.html", "hud.py", "LICENSE", "plugin.json", ".codex-plugin/plugin.json")


def prepare(runtime, settings):
    root = Path(__file__).resolve().parent
    package = runtime / "plugin"
    for relative in FILES:
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    launch = {"command": Path(sys.executable).name,
              "args": ["${PLUGIN_ROOT}/server.py"],
              "env": {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}}
    portable = {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": {"codex-ssh-quota": {"type": "stdio", **launch}}}
    legacy = {"mcpServers": {"codex-ssh-quota": launch}}
    for name, data in (("mcp.json", portable), (".mcp.json", legacy), ("settings.json", settings)):
        (package / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    catalog["plugins"][0]["source"]["path"] = "./plugin"
    destination = runtime / ".agents/plugins/marketplace.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Codex SSH Quota on this Codex execution host.")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--ssh-host", help="Existing SSH alias to query from the desktop host")
    sources.add_argument("--local", action="store_true", help="Read snapshots on the current SSH execution host")
    parser.add_argument("--runtime-dir", type=Path, default=Path(__file__).resolve().parent / ".runtime")
    parser.add_argument("--hud", action="store_true", help="Start the Windows main-window quota companion")
    parser.add_argument("--name", help="SSH display name shown in the main-window quota bar")
    parser.add_argument("--add-ssh", action="append", default=[], metavar="NAME=HOST", help="Add or rename a selectable SSH source")
    parser.add_argument("--discover-ssh", action=argparse.BooleanOptionalAction, default=None, help="Include literal Host aliases from the SSH config")
    args = parser.parse_args(argv)
    if args.hud and os.name != "nt":
        parser.error("--hud requires the Windows desktop host")
    try:
        configured = server.load_settings()
        saved = args.runtime_dir.resolve() / "plugin/settings.json"
        if not configured and saved.exists():
            if saved.stat().st_size > 16384:
                parser.error("runtime settings exceed 16 KiB")
            configured = json.loads(saved.read_text(encoding="utf-8"))
            if not isinstance(configured, dict):
                parser.error("runtime settings must be an object")
        if args.local:
            settings = {"mode": "local"}
        elif args.ssh_host:
            settings = {"mode": "ssh", "sshHost": args.ssh_host}
        else:
            settings = configured
        mode = settings.get("mode", os.environ.get("CODEX_QUOTA_MODE", "ssh"))
        if mode == "ssh":
            host = settings.get("sshHost") or os.environ.get("CODEX_QUOTA_SSH_HOST", "")
            import re
            if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,190}", host):
                parser.error("use --ssh-host <SSH alias>, or --local on the SSH execution host")
            settings = {"mode": "ssh", "sshHost": host}
        elif mode != "local":
            parser.error("mode must be ssh or local")
        else:
            settings = {"mode": "local"}
        for key in ("sources", "discoverSsh"):
            if key in configured:
                settings[key] = configured[key]
        label = args.name or configured.get("sourceName") or os.environ.get("CODEX_QUOTA_SOURCE_NAME")
        if label:
            if not isinstance(label, str):
                parser.error("display name must be a string")
            label = " ".join(label.split())
            if len(label) > 80:
                parser.error("display name must be at most 80 characters")
            settings["sourceName"] = label
        if args.discover_ssh is not None:
            settings["discoverSsh"] = args.discover_ssh
        extras = list(settings.get("sources") or [])
        for value in args.add_ssh:
            name, separator, host = value.partition("=")
            entry = source_catalog.profile({"sshHost": host, "sourceName": name})
            if not separator or not name.strip() or len(name.strip()) > 80 or entry is None:
                parser.error("--add-ssh expects NAME=HOST with a valid SSH alias")
            if settings["mode"] == "ssh" and settings["sshHost"] == host:
                settings["sourceName"] = args.name or entry["sourceName"]
            else:
                extras = [item for item in extras if item.get("sshHost") != host]
                extras.append({"mode": "ssh", "sshHost": host, "sourceName": entry["sourceName"]})
        if len(extras) >= source_catalog.MAX_SOURCES:
            parser.error("at most 32 explicit sources are supported")
        if extras:
            settings["sources"] = extras
        codex = server.executable()
        runtime = args.runtime_dir.resolve()
        if runtime == Path(__file__).resolve().parent:
            parser.error("runtime directory must differ from the source directory")
        prepare(runtime, settings)
        commands = ([codex, "plugin", "marketplace", "add", str(runtime)],
                    [codex, "plugin", "add", PLUGIN_ID])
        for command in commands:
            result = subprocess.run(command, capture_output=True, timeout=60)
            if result.returncode:
                output = (result.stderr or result.stdout).decode("utf-8", errors="replace")
                print(output[:4000], file=sys.stderr)
                print("安装未完成，请检查 Codex CLI 后重新运行安装命令。", file=sys.stderr)
                return result.returncode
        if args.hud:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            launcher = str(pythonw) if pythonw.exists() else sys.executable
            hud = str(runtime / "plugin/hud.py")
            subprocess.run([launcher, hud, "--stop"], timeout=10, check=True)
            subprocess.Popen([launcher, hud], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    except server.QuotaError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, subprocess.TimeoutExpired):
        print("安装命令无法运行或超时，请检查 Codex CLI 后重试。", file=sys.stderr)
        return 1
    print("已安装 Codex SSH 额度。请在此执行主机上打开新会话；客户端保留旧入口时重启后再试。")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
