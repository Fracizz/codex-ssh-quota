"""Runs on the SSH host via stdin. Emits quota fields only, never chat text."""
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import platform

MAX_FILES = 64
TAIL_BYTES = 512 * 1024


def collect(home=None):
    home = Path(home or os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    files = []
    today = datetime.now(timezone.utc).date()
    for days in range(14):
        date = today - timedelta(days=days)
        folder = home / "sessions" / date.strftime("%Y/%m/%d")
        if not folder.is_dir():
            continue
        try:
            candidates = []
            with os.scandir(folder) as entries:
                for index, entry in enumerate(entries):
                    if index >= 256:
                        break
                    if entry.name.endswith(".jsonl") and entry.is_file():
                        candidates.append((entry.stat().st_mtime, Path(entry.path)))
            files.extend(sorted(candidates, reverse=True)[:MAX_FILES - len(files)])
        except OSError:
            continue
        if len(files) >= MAX_FILES:
            break
    best = None
    for _, path in sorted(files, reverse=True):
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - TAIL_BYTES))
                lines = stream.read(TAIL_BYTES).splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if b'"rate_limits"' not in line:
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            limits = payload.get("rate_limits")
            if not isinstance(limits, dict) or not limits:
                continue
            stamp = event.get("timestamp", "")
            if not isinstance(stamp, str):
                continue
            if best is None or stamp > best[0]:
                best = (stamp, limits)
            break
    if best is None:
        return {"snapshotError": "no_recent_quota_snapshot"}
    stamp, limits = best
    def number(value):
        try:
            return value if type(value) in (int, float) and math.isfinite(value) else None
        except OverflowError:
            return None
    def window(value):
        if not isinstance(value, dict):
            return None
        return {"usedPercent": number(value.get("used_percent")),
                "windowDurationMins": number(value.get("window_minutes")),
                "resetsAt": number(value.get("resets_at"))}
    credits = limits.get("credits")
    return {"sourceHost": platform.node()[:160], "sourceHome": str(home)[:400],
            "snapshotRecordedAt": stamp[:40],
            "rateLimits": {"limitId": str(limits.get("limit_id") or "codex")[:160],
                           "limitName": None, "planType": str(limits.get("plan_type") or "")[:40] or None,
                           "primary": window(limits.get("primary")),
                           "secondary": window(limits.get("secondary")),
                           "credits": {"balance": str(credits.get("balance", ""))[:40],
                                       "unlimited": credits.get("unlimited") is True} if isinstance(credits, dict) else None}}


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
