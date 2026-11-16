"""Bounded source discovery. Reads SSH aliases only; never connects or reads keys."""
import hashlib
import os
from pathlib import Path
import re
import shlex

MAX_SOURCES = 32
HOST_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,190}"


def profile(value):
    if not isinstance(value, dict):
        return None
    mode = value.get("mode", "ssh")
    host = value.get("sshHost", "")
    if mode not in ("ssh", "local") or (mode == "ssh" and (not isinstance(host, str) or not re.fullmatch(HOST_PATTERN, host))):
        return None
    name = value.get("sourceName") or (host if mode == "ssh" else "当前主机")
    if not isinstance(name, str):
        return None
    home = os.environ.get("CODEX_HOME", str(Path.home()/".codex"))
    key = mode + ":" + (host if mode == "ssh" else str(Path(home).absolute()))
    return {"id": hashlib.sha256(key.encode()).hexdigest()[:12], "mode": mode,
            "sshHost": host if mode == "ssh" else "", "sourceName": " ".join(name.split())[:80]}


def ssh_aliases(path=None):
    path = Path(path or Path.home()/".ssh/config")
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            text = stream.read(65536)
    except OSError:
        return []
    aliases = []
    for line in text.splitlines():
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            continue
        if parts and parts[0].lower() == "host":
            aliases.extend(value for value in parts[1:] if re.fullmatch(HOST_PATTERN, value))
        if len(aliases) >= 64:
            break
    return list(dict.fromkeys(aliases))[:64]


def catalog(settings):
    primary = {key: settings[key] for key in ("mode", "sshHost", "sourceName") if key in settings}
    for key, variable in (("mode", "CODEX_QUOTA_MODE"), ("sshHost", "CODEX_QUOTA_SSH_HOST"),
                          ("sourceName", "CODEX_QUOTA_SOURCE_NAME")):
        if os.environ.get(variable):
            primary[key] = os.environ[variable]
    pinned = bool(os.environ.get("CODEX_QUOTA_MODE") or os.environ.get("CODEX_QUOTA_SSH_HOST"))
    candidates = [primary]
    if not pinned:
        extras = settings.get("sources") or []
        if isinstance(extras, list):
            candidates.extend(extras)
        if settings.get("discoverSsh", primary.get("mode", "ssh") == "ssh"):
            candidates.extend({"mode": "ssh", "sshHost": alias, "sourceName": alias} for alias in ssh_aliases())
    sources = {}
    for value in candidates:
        entry = profile(value)
        if entry and entry["id"] not in sources:
            sources[entry["id"]] = entry
    result = list(sources.values())
    return result[:MAX_SOURCES], max(0, len(result)-MAX_SOURCES)
