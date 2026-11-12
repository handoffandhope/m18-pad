"""How much of the Claude 5-hour usage window is spent (0..1), from plugins/claude_limit.py's state file."""
import json
import math
import time
from pathlib import Path

ABOUT = "share of the Claude 5-hour usage window spent; data.frozen = share of a hit limit's wait still left"
LABEL = "USAGE"
PERIOD = 10


def read(hub):
    now, out = time.time(), {"value": None, "text": "--", "data": {"frozen": None, "resets_at": None}}
    try:
        j = json.loads((Path(hub.base or ".") / "state" / "claude-limit.json").read_text(encoding="utf-8"))
        last = j.get("last") or {}
    except (OSError, ValueError, AttributeError):        # no claude_limit plugin running here: unknown, not invented
        return out
    if last.get("state") == "hit" and last.get("resets_at") and last["resets_at"] > now:
        win = 7 * 86400 if last.get("type") == "seven_day" else 5 * 3600
        out["data"] = {"frozen": max(0.02, min(1.0, (last["resets_at"] - now) / win)), "resets_at": last["resets_at"]}
        out.update(value=1.0, text="limit")
    elif last.get("fh") is not None and now - float(j.get("updated") or 0) < 6 * 3600:
        v = max(0.0, min(100.0, float(last["fh"])))
        out.update(value=v / 100, text=f"{v:.0f}%")
    return out


def sim(t):
    v = 0.5 - 0.5 * math.cos(t / 40)                     # fills and drains every four minutes
    return {"value": v, "text": f"{v * 100:.0f}%", "data": {"frozen": None, "resets_at": None}}
