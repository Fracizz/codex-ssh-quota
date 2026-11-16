"""Read-only local MCP App for Codex quota. Python 3.10+, no dependencies."""

import hashlib
import contextvars
import threading

import source_catalog
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
URI = "ui://codex-ssh-quota/panel-v3.html"
MAX_MESSAGE = 1024 * 1024
MAX_BUCKETS = 20
MAX_RESULT = 32768
CACHE_SECONDS = 600
_cache = None
_cache_lock = threading.RLock()
_source_context = contextvars.ContextVar("quota_source", default=None)


class QuotaError(Exception):
    pass


def executable():
    override = os.environ.get("CODEX_QUOTA_CODEX")
    if override:
        if not Path(override).is_file():
            raise QuotaError("CODEX_QUOTA_CODEX 不存在，请填写 codex.exe 的完整路径。")
        return override
    if os.name == "nt":
        # Use the desktop's bundled native CLI before npm's .cmd/.ps1 wrappers.
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
        candidates = list(base.glob("*/codex.exe"))
        if candidates:
            return str(max(candidates, key=lambda p: p.stat().st_mtime))
        # Microsoft Store builds ship the CLI inside their Appx package.
        try:
            package = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1).InstallLocation"],
                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            location = package.stdout.decode("utf-8", errors="replace").strip()
            bundled = Path(location) / "app/resources/codex.exe"
            if location and bundled.is_file():
                return str(bundled)
        except (OSError, subprocess.TimeoutExpired):
            pass
        found = shutil.which("codex.exe")
    else:
        found = shutil.which("codex")
    if not found:
        raise QuotaError("找不到 Codex CLI。请设置 CODEX_QUOTA_CODEX 为原生 Codex 可执行文件路径。")
    return found


def load_settings():
    path = ROOT / "settings.json"
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 16384:
            raise ValueError()
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise QuotaError("settings.json 无效，请重新运行安装器配置数据来源。")


def available_sources():
    return source_catalog.catalog(load_settings())


def selection_path():
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "CodexSSHQuota"
    return base / "selected-source.json"


def resolve_source(source=None):
    entries, _ = available_sources()
    if not entries:
        raise QuotaError("请先运行安装器配置 SSH 来源，或在 SSH 主机上使用 --local。")
    if source is None:
        try:
            saved = selection_path()
            if saved.stat().st_size <= 1024:
                source = json.loads(saved.read_text(encoding="utf-8")).get("sourceId")
        except (OSError, ValueError, AttributeError):
            pass
        return next((item for item in entries if item["id"] == source), entries[0])
    exact = next((item for item in entries if item["id"] == source), None)
    if exact:
        return exact
    matches = [item for item in entries if source in (item["sourceName"], item["sshHost"])]
    if not matches:
        raise QuotaError("来源不存在，请先调用 codex_quota_sources 查看已配置来源。")
    if len(matches) > 1:
        raise QuotaError("来源名称重复，请使用 codex_quota_sources 返回的来源 ID。")
    return matches[0]


def select_source(source):
    entry = resolve_source(source)
    path = selection_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{os.getpid()}-{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"sourceId": entry["id"]}), encoding="utf-8")
        temp.replace(path)
    except OSError as exc:
        raise QuotaError("无法保存所选来源，请检查本机缓存目录的写权限。") from exc
    return entry


def active_source():
    return _source_context.get() or resolve_source()


def source_mode():
    return active_source()["mode"]


def ssh_target():
    value = active_source().get("sshHost", "")
    if not value:
        raise QuotaError("当前来源未配置 SSH 地址。")
    return value


def source_key():
    if source_mode() == "ssh":
        return ssh_target()
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).absolute()
    return "local:" + str(home)


def source_name(fallback=None):
    return active_source().get("sourceName") or fallback or platform.node()


def read_limits(timeout=15):
    if source_mode() == "local":
        from remote_snapshot import collect
        result = collect()
        if result.get("snapshotError"):
            raise QuotaError("当前 SSH 会话主机最近未记录额度；完成一次 Codex 请求后再刷新。")
        return result
    ssh = shutil.which("ssh")
    if not ssh:
        raise QuotaError("找不到 SSH 客户端，请安装 Windows OpenSSH Client 后重试。")
    host = ssh_target()
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    command = [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
               "-o", "StrictHostKeyChecking=yes", "--", host, "python3", "-"]
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, **kwargs)
    except OSError as exc:
        raise QuotaError("无法启动 SSH，请检查 OpenSSH 客户端。") from exc
    try:
        try:
            output, _ = proc.communicate((ROOT / "remote_snapshot.py").read_bytes(), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise QuotaError("SSH 查询超时，稍后点击刷新重试。") from exc
        if proc.returncode:
            raise QuotaError("SSH 查询失败，请先在终端运行 ssh " + host + "，确认主机指纹和免密连接。")
        if len(output) > MAX_RESULT:
            raise QuotaError("SSH 额度响应超过 32KB，请检查远端 Python 环境。")
        try:
            raw = json.loads(output)
        except (ValueError, UnicodeDecodeError) as exc:
            raise QuotaError("SSH 未返回额度数据，请检查远端 python3 和 shell 启动输出。") from exc
        if not isinstance(raw, dict):
            raise QuotaError("SSH 额度数据格式不正确。")
        if raw.get("snapshotError"):
            raise QuotaError("SSH 主机最近 14 天的会话中未找到额度记录；在该环境完成一次 Codex 请求后再刷新。")
        return raw
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        for stream in (proc.stdin, proc.stdout):
            if stream and not stream.closed:
                stream.close()


def short(value, size=160):
    return value[:size] if isinstance(value, str) else None


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def normalize(raw):
    by_id = raw.get("rateLimitsByLimitId")
    if not isinstance(by_id, dict) or not by_id:
        single = raw.get("rateLimits")
        by_id = {"codex": single} if isinstance(single, dict) else {}
    buckets = []
    for key, item in list(by_id.items())[:MAX_BUCKETS]:
        if not isinstance(item, dict):
            continue
        windows = []
        for kind in ("primary", "secondary"):
            window = item.get(kind)
            if not isinstance(window, dict):
                continue
            used = number(window.get("usedPercent"))
            # Missing percentages and windows stay unknown, never imply 100% remaining.
            windows.append({"kind": kind, "usedPercent": used,
                            "remainingPercent": max(0, min(100, 100 - used)) if used is not None else None,
                            "durationMins": number(window.get("windowDurationMins")),
                            "resetsAt": number(window.get("resetsAt"))})
        credit = item.get("credits")
        buckets.append({"id": short(key), "name": short(item.get("limitName")),
                        "plan": short(item.get("planType"), 40), "windows": windows,
                        "limitReached": short(item.get("rateLimitReachedType")),
                        "credits": ({"balance": short(credit.get("balance"), 40),
                                     "unlimited": credit.get("unlimited") is True}
                                    if isinstance(credit, dict) else None)})
    earned = raw.get("rateLimitResetCredits")
    account = raw.get("accountId")
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).absolute()
    result = {"host": short(raw.get("sourceHost")) or platform.node()[:160],
              "sourceName": source_name(raw.get("sourceHost")),
              "codexHome": short(raw.get("sourceHome"), 400) or str(home)[:400],
              "snapshotRecordedAt": short(raw.get("snapshotRecordedAt"), 40),
              "accountRef": hashlib.sha256(account.encode()).hexdigest()[:8] if isinstance(account, str) else None,
              "checkedAt": int(time.time()), "buckets": buckets,
              "omittedBuckets": max(0, len(by_id) - MAX_BUCKETS),
              "availableResets": number(earned.get("availableCount")) if isinstance(earned, dict) else None,
              "ordinaryUsageAllowed": raw.get("ordinaryUsageAllowed") if type(raw.get("ordinaryUsageAllowed")) is bool else None}
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESULT:
        raise QuotaError("额度响应过大，请缩小账号配额范围后重试。")
    return result


def cache_path(host):
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "CodexSSHQuota"
    return base / (hashlib.sha256(host.encode()).hexdigest()[:16] + ".json")


def quota(force=False, source=None):
    profile = resolve_source(source)
    token = _source_context.set(profile)
    try:
        return _quota_for_source(force)
    finally:
        _source_context.reset(token)


def _quota_for_source(force):
    global _cache
    host = source_key()
    path = cache_path(host)
    with _cache_lock:
        if _cache is None:
            _cache = {}
        cached = _cache.get(host)
    if cached is None:
        try:
            if path.stat().st_size <= MAX_RESULT:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("sshHost") == host and isinstance(saved.get("buckets"), list):
                    age = max(0, time.time() - saved.get("checkedAt", 0))
                    cached = (time.monotonic()-age, saved)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    def decorate(value):
        return {**value, "sourceName": source_name(value.get("host")), "sourceId": active_source()["id"]}
    if not force and cached is not None and time.monotonic()-cached[0] < CACHE_SECONDS:
        with _cache_lock:
            _cache[host] = cached
        return decorate(cached[1])
    try:
        value = normalize(read_limits())
        value.update({"sshHost": host, "sourceMode": source_mode(), "sourceId": active_source()["id"]})
        with _cache_lock:
            _cache[host] = (time.monotonic(), value)
            if len(_cache) > source_catalog.MAX_SOURCES:
                oldest = min(_cache, key=lambda key: _cache[key][0])
                del _cache[oldest]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(f".{os.getpid()}-{threading.get_ident()}.tmp")
            temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        except OSError:
            pass
        return decorate(value)
    except QuotaError:
        if cached is not None:
            return decorate({**cached[1], "warning": "数据来源暂时不可用，显示上次缓存的额度记录。"})
        raise


TOOL = {"name": "codex_quota", "title": "Codex SSH 额度", "description": "查看 SSH 环境的 Codex 额度快照。可从桌面通过 SSH 读取，也可在 SSH 会话主机上直接读取，缓存 10 分钟。",
        "inputSchema": {"type": "object", "properties": {"force": {"type": "boolean", "description": "重新通过 SSH 读取快照"}, "source": {"type": "string", "description": "可选的来源 ID、名称或 SSH 别名"}}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "_meta": {"ui": {"resourceUri": URI, "visibility": ["app", "model"]},
                  "openai/ui": {"entrypoints": [{"type": "thread"}, {"type": "global"}]}}}


def dispatch(method, params):
    if method == "initialize":
        return {"protocolVersion": "2025-11-25", "serverInfo": {"name": "codex-ssh-quota", "version": "0.5.0"},
                "capabilities": {"tools": {}, "resources": {}}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL, {"name": "codex_quota_sources", "title": "SSH 额度来源",
                "description": "列出可查询的 SSH 来源及当前选择，每页最多16项。",
                "inputSchema": {"type": "object", "properties": {"offset": {"type": "integer", "minimum": 0}}, "additionalProperties": False},
                "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}}]}
    if method == "resources/list":
        return {"resources": [{"uri": URI, "name": "Codex SSH 额度", "mimeType": "text/html;profile=mcp-app"}]}
    if method == "resources/read" and params.get("uri") == URI:
        return {"contents": [{"uri": URI, "mimeType": "text/html;profile=mcp-app",
                              "text": (ROOT / "panel.html").read_text(encoding="utf-8"),
                              "_meta": {"ui": {"csp": {"connectDomains": [], "resourceDomains": []}},
                                        "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"], "preferredDisplayMode": "inline"}}}]}
    if method == "tools/call" and params.get("name") == "codex_quota_sources":
        arguments = params.get("arguments") or {}
        offset = arguments.get("offset", 0) if isinstance(arguments, dict) else None
        if type(offset) is not int or offset < 0 or set(arguments)-{"offset"}:
            return {"isError": True, "content": [{"type": "text", "text": "offset 必须为非负整数。"}]}
        entries, omitted = available_sources()
        data = {"sources": entries[offset:offset+16], "selectedSource": resolve_source()["id"],
                "nextOffset": offset+16 if offset+16 < len(entries) else None,
                "omittedCount": omitted}
        if omitted:
            data["omittedHint"] = "用安装器 --add-ssh NAME=HOST 添加目标来源，或用 --no-discover-ssh 仅显示明确配置。"
        return {"content": [{"type": "text", "text": "已列出 SSH 来源。"}], "structuredContent": data}
    if method == "tools/call" and params.get("name") == TOOL["name"]:
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict) or set(arguments) - {"force", "source"} or ("force" in arguments and type(arguments["force"]) is not bool) or ("source" in arguments and not isinstance(arguments["source"], str)):
            return {"isError": True, "content": [{"type": "text", "text": "参数使用 {} 或 {\"force\": true}。"}]}
        try:
            data = quota(force=arguments.get("force", False), source=arguments.get("source"))
            return {"content": [{"type": "text", "text": "已读取 Codex SSH 额度快照。"}], "structuredContent": data}
        except QuotaError as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
    raise LookupError(method)


def main():
    if "--probe" in sys.argv:
        try:
            print(json.dumps(quota(), ensure_ascii=False))
        except QuotaError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    for line in iter(lambda: sys.stdin.buffer.readline(MAX_MESSAGE + 1), b""):
        request_id = None
        try:
            if len(line) > MAX_MESSAGE:
                return 2
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError()
            if "id" not in message:
                continue
            request_id = message["id"]
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError()
            reply = {"result": dispatch(message.get("method"), params)}
        except LookupError:
            reply = {"error": {"code": -32601, "message": "Unknown method or resource"}}
        except (ValueError, TypeError):
            reply = {"error": {"code": -32600, "message": "Invalid request"}}
        except Exception:
            reply = {"error": {"code": -32603, "message": "Local quota error; run server.py --probe"}}
        reply.update({"jsonrpc": "2.0", "id": request_id})
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
