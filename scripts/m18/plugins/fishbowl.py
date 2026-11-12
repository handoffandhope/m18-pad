"""Fishbowl: the whole pad becomes one aquarium, and every agent session on the pad lives in it as a creature.

    1: {live: {plugin: fishbowl.face, interval: 5, args: {idle_min: 0}}, action: {plugin: fishbowl.open}}

`fishbowl.open` (action) takes the pad over; physical b1 leaves. `fishbowl.face` (live, optional) draws a small
bowl on its key, keeps the reef up to date, and with `idle_min: N` (default 0 = off) opens the bowl by itself
after N minutes without a press. The bowl re-arms its modal every frame (the daemon's 120 s cap is per arm), so
it stays until b1, dark mode, or a crash (then the daemon ends it RENEW_S later).

PAGE MODE (scripts/m18/sets-tank.yaml): the tank is a normal page. Every key is `live: fishbowl.tile`; keys with
`args: {fixture: lamp|buoy|gauge|chest}` show that fixture on the water and keep their own action. Water keys use
`fishbowl.tap` / `fishbowl.hold`. One engine thread renders at PAGE_FPS and pushes only changed tiles into
live_faces; it sleeps off-page, in dark mode and under any modal. Creature and fixture art: m18lib/fishart.py.

ONE TANK SEEN THROUGH 15 WINDOWS. The world is tiles.block_size(5, 3) px with the bars included (gap read from
m18lib.tiles.GAP at open; 30 px measured), so ~45% of the tank is behind bars. Screens are habitats, bars are
transit and hiding places:
    top row      the glass: a waiting creature comes here, faces you, bigger, gold; a crashed one floats belly-up
    middle row   open water lanes (swimmers cross bars at a steady speed) and kelp (blocked creatures tangled)
    bottom row   sand: finished creatures rest with a flag; readers nibble along it; the reef grows here
    bars         a silent creature hides in one, only its pale eye peeking into a screen, a "z" above it
Anything the user must read (resting, waiting, plates) sits fully inside one screen.

EVERY VISUAL IS A FACT (sources in parentheses; nothing is invented when a source is missing):
    state            swim / glass / stuck / rest / hide          (sessions.scan, same as the session keys)
    species          opus+fable large slow fish, sonnet mid, haiku tiny darting; codex luna = moon jelly,
                     sol = sunfish, other codex = mid fish       (last assistant `model` / codex turn_context)
    fry              one per subagent written to in the last 60 s, schooling behind the parent   (Claude only)
    what it does     read = nibbles along the sand, edit = carries a block, run = bubble bursts,
                     web = leaves through the surface and comes back   (last tool call, within 90 s)
    belly-up         last record is an API error other than a usage limit; floats until tapped (acknowledged)
    water            level falls with the 5-hour Claude usage (claude-limit.json fh); a limit hit freezes the
                     tank, the ice thinning toward the reset      (plugins/claude_limit.py's state file)
    reef             one block per `git commit` a session runs (seen in its transcript), coloured like it; an
                     ended session leaves its 16x16 sprite; blocks fade and erode after 7 days (cap 120)
    night            away mode on (state/away.json) or 21:00-06:00 local: lights out, creatures glow
Taps on keys 1-15 act on release: a creature on (or half behind the bar next to) the key -> its name plate for
3 s, a second tap while it shows opens the session (sessions.open_session); a tap on a belly-up creature
acknowledges the crash. Hold (>= settings.hold_ms) on a creature = nudge: sessions.put_clipboard(NUDGE_TEXT) +
sessions.open_session, the same calls sessions.nudge makes. Empty water: ripple and food; swimmers chase it.

Ring = the waterline: slow blue shimmer (icy when frozen, dim at night); gold breathing on the LEDs nearest
each creature that needs the user, front strips gold while any does. Priority 1 with an explicit yield: the bowl
draws only while the ring is free, its own, or held by the ambient ring.status, so any other owner wins.

Details, costs and checks: references/fishbowl.md, fishbowl_check.py.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import math
import os
import random
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from m18lib import fishart as A

FPS = 5.0                    # creature motion; 5 fps keeps the bowl under 10 % of a core (references/fishbowl.md)
BUBBLE_HZ = 15.0             # bubbles move on their own clock (fish move every frame)
AMBIENT_FPS = 2.0            # kelp sway, surface wave, sea-pickle glow
RENEW_S = 20.0               # each frame re-arms the modal for this long
PLATE_S = 3.0
SCAN_S = 2.0                 # roster refresh (sessions.scan has its own 2 s cache)
FACT_S = 5.0                 # transcript facts refresh per session (cached on mtime+size anyway)
ENV_S = 10.0                 # limit / night refresh
RING_OWNER = "fishbowl"
HATCH_S = 3.0
PULSE_HZ = 0.5
TOOL_FRESH = 90.0            # a tool call older than this says nothing about what the session is doing now
SUB_FRESH = 60.0             # a subagent file written within this is a live subagent
REEF_DAYS = 7.0
REEF_CAP = 120
KEEP_CAP, KEEP_DAYS = 5, 3.0
NIGHT_HOURS = (21, 6)
LEVEL_DROP = 22              # px the surface falls at 100 % of the 5-hour window (stays inside the top row)
TAIL = 256 * 1024             # codex lines are long; 64 KB held only a few records
NUDGE_TEXT = "still going?"
_VERSION = os.path.getmtime(__file__)

# ------------------------------------------------------------------------------ palette (hillside ocean)
WATER = ("#2c63c4", "#285bb8", "#2453aa", "#204a9b", "#1c428c", "#183a7d", "#15336f", "#122c61")
MURK = "#3d5a3a"
AIR = "#0b0f18"
RAY = "#3a74d2"
SURFACE = ("#9cc8ff", "#5a98ec")
SAND = ("#dbd3a0", "#d1c793", "#c9be89", "#e3dcae", "#bfb27c")
KELP = ("#3d8b2a", "#2f7321", "#57a83a")
SEAGRASS = ("#4f9a31", "#3f8226")
CORAL = {"tube": ("#3157d6", "#1f3a9e"), "brain": ("#d9529f", "#a8326f"), "fire": ("#c8302f", "#8e1d1c"),
         "horn": ("#d8c43f", "#a8932a"), "bubble": ("#a52fb3", "#741f80")}
PICKLE = ("#5f7f22", "#e8ff9a")
ICE = ("#dff4ff", "#b8e2f8", "#9fd0ee")
GOLD, GOLD_DIM = "#ffff55", "#c08a00"
FLAG, WOOD = "#55ff55", "#8b5a2b"
LAVENDER = "#d8b8ff"
BUBBLE = ("#d8f0ff", "#8fc3ff")
FOOD = ("#b07a3b", "#7a4f22")
BLOCK = ("#8a8a8a", "#6e6e6e")
STATE_COL = {"swim": "#55ffff", "glass": "#ffff55", "stuck": "#ffff55", "rest": "#55ff55", "hide": LAVENDER,
             "hatch": "#ffffff", "leave": "#aaaaaa", "dead": "#ff5555"}
STATE_WORD = {"swim": "WORKING", "glass": "WAITING", "stuck": "BLOCKED", "rest": "DONE", "hide": "QUIET",
              "hatch": "NEW", "leave": "GONE", "dead": "CRASHED"}
TOOL_WORD = {"read": "READING", "edit": "EDITING", "run": "RUNNING", "web": "ON WEB"}
SPRITE_PAL = {"K": "#161616", "W": "#f4f4f4", "G": "#b4b4b4", "S": "#7c7c7c", "D": "#454545", "R": "#cc3a30",
              "O": "#ee8c2c", "Y": "#f2d33a", "L": "#72cc40", "E": "#2f7d2c", "C": "#3fcbdc", "B": "#3a5bd2",
              "P": "#8e3fca", "N": "#7d4c2a", "T": "#dbb47e"}
_KIND = {"question": [".###.", "#...#", "....#", "..##.", "..#..", ".....", "..#.."],
         "approval": ["......#", ".....##", "#...##.", "##.##..", ".###...", "..#...."],
         "blocked": ["##", "##", "##", "##", "..", "##"]}
_EGG = ["..###..", ".#WWW#.", "#WWGWW#", "#WGWWW#", "#WWWGW#", "#WGWWW#", ".#WWW#."]
_Z = ["###", "..#", ".#.", "###"]
_RGB: dict = {}


def _rgb(h):
    if isinstance(h, tuple):
        return h[:3]
    c = _RGB.get(h)
    if c is None:
        s = str(h).lstrip("#")
        c = _RGB[h] = tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    return c


_MIX: dict = {}


def _mix(a, b, t):
    k = (a, b, t)
    c = _MIX.get(k)
    if c is None:
        if len(_MIX) > 4000:
            _MIX.clear()
        x, y = _rgb(a), _rgb(b)
        c = _MIX[k] = tuple(int(round(x[i] + (y[i] - x[i]) * t)) for i in range(3))
    return c


def _hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c[:3])


def _seed(s) -> int:
    return int(hashlib.sha1(str(s).encode()).hexdigest()[:8], 16)


def pulse(t: float) -> float:
    return 0.5 - 0.5 * math.cos(2 * math.pi * PULSE_HZ * t)


# ------------------------------------------------------------------------------ geometry
class Geo:
    """The tank in key pixels, bars included. Key k (1-15, row-major) shows world rect key_rect(k)."""

    def __init__(self, gap=None):
        if gap is None:
            from m18lib import tiles as T
            gap = T.GAP
        self.gap = int(gap)
        self.pitch = 64 + self.gap
        self.W, self.H = 5 * 64 + 4 * self.gap, 3 * 64 + 2 * self.gap
        self.sand = self.H - 12

    def key_rect(self, k):
        r, c = divmod(k - 1, 5)
        x0, y0 = c * self.pitch, r * self.pitch
        return x0, y0, x0 + 64, y0 + 64

    def centre(self, k):
        x0, y0, _, _ = self.key_rect(k)
        return x0 + 32, y0 + 32

    def key_at(self, x, y):
        c, cx = divmod(int(math.floor(x)), self.pitch)
        r, cy = divmod(int(math.floor(y)), self.pitch)
        if 0 <= c < 5 and 0 <= r < 3 and cx < 64 and cy < 64:
            return 1 + r * 5 + c
        return None

    def tiles(self, img):
        return {k: img.crop(self.key_rect(k)) for k in range(1, 16)}

    def to_case(self, x, y):
        from m18lib import ringmap as RM
        kx, ky = RM.KEY_X, RM.KEY_Y
        return (kx[0] + (x - 32) / (4 * self.pitch) * (kx[4] - kx[0]),
                ky[0] + (y - 32) / (2 * self.pitch) * (ky[2] - ky[0]))


# ------------------------------------------------------------------------------ facts from transcripts
_FACTS: dict = {}
_TOOLS = {"Read": "read", "Grep": "read", "Glob": "read", "LS": "read", "NotebookRead": "read",
          "Edit": "edit", "Write": "edit", "MultiEdit": "edit", "NotebookEdit": "edit", "apply_patch": "edit",
          "Bash": "run", "PowerShell": "run", "exec": "run", "shell": "run", "local_shell": "run",
          "WebFetch": "web", "WebSearch": "web", "web_search": "web", "web_search_call": "web"}


def tool_class(name: str) -> str | None:
    if not name:
        return None
    if name in _TOOLS:
        return _TOOLS[name]
    n = name.lower()
    if "browser" in n or "chrome" in n or "web" in n or "fetch" in n:
        return "web"
    return None


def _iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _tail_objs(path, n=TAIL):
    with builtins.open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - n))
        data = f.read()
    lines = data.splitlines()
    if size > n:
        lines = lines[1:]
    out = []
    for ln in lines:
        if not (b'"assistant"' in ln or b'tool_' in ln or b'turn_context' in ln or b'apiError' in ln
                or b'custom_tool_call' in ln or b'function_call' in ln or b'"error"' in ln or b'web_search' in ln):
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def parse_claude(objs) -> dict:
    """model, last tool (class + time), commit ids (successful `git commit` Bash calls), crash (last record is an
    API error other than a usage limit) from a Claude Code transcript tail."""
    model, tool, tool_t, err = None, None, None, None
    calls, ok = {}, set()
    for o in objs:
        if o.get("type") == "assistant":
            m = o.get("message") or {}
            if m.get("model") and m.get("model") != "<synthetic>":
                model = m["model"]
            ae = o.get("apiError") or (o.get("isApiErrorMessage") and (o.get("error") or "api_error"))
            err = (str(ae), o.get("uuid") or o.get("timestamp")) if ae and ae != "usage_limit_reached" else None
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool, tool_t = tool_class(b.get("name")), _iso(o.get("timestamp"))
                    cmd = json.dumps(b.get("input") or {})
                    if b.get("name") in ("Bash", "PowerShell") and "git commit" in cmd and "--dry-run" not in cmd:
                        calls[b.get("id")] = 1
                elif isinstance(b, dict) and b.get("type") == "text":
                    err = None if not ae else err
        elif o.get("type") == "user":
            for b in (o.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in calls \
                        and not b.get("is_error"):
                    ok.add(b["tool_use_id"])
            err = None
    return {"model": model, "tool": tool, "tool_t": tool_t, "commits": sorted(ok), "error": err}


def parse_codex(objs) -> dict:
    model, tool, tool_t, err = None, None, None, None
    commits = []
    for o in objs:
        p = o.get("payload") or {}
        if o.get("type") == "turn_context" and p.get("model"):
            model = p["model"]
        typ = p.get("type")
        if typ in ("custom_tool_call", "function_call", "local_shell_call", "web_search_call"):
            name = p.get("name") or typ
            tool, tool_t = tool_class(name), _iso(o.get("timestamp"))
            arg = json.dumps(p.get("input") or p.get("arguments") or p.get("action") or "")
            if tool == "run" and "git commit" in arg:
                commits.append(p.get("call_id") or p.get("id") or o.get("timestamp"))
            err = None
        elif typ in ("error", "stream_error"):
            err = (str(p.get("message") or typ)[:60], o.get("timestamp"))
        elif typ in ("task_started", "agent_message", "task_complete"):
            err = None
    return {"model": model, "tool": tool, "tool_t": tool_t, "commits": commits, "error": err}


def _head_model(path):
    """Codex writes turn_context once per turn; a long turn can push it out of the tail, so look at the head too."""
    import re
    try:
        with builtins.open(path, "rb") as f:
            for _ in range(60):                    # session_meta lines can be 100 KB+; read whole lines
                ln = f.readline(8 * 1024 * 1024)
                if not ln:
                    break
                if b'"turn_context"' in ln:
                    mm = re.search(rb'"model"\s*:\s*"([^"]+)"', ln)
                    if mm:
                        return mm.group(1).decode("utf-8", "replace")
    except OSError:
        pass
    return None


def _subagents(path, now) -> int:
    d = Path(str(path)[:-6]) / "subagents" if str(path).endswith(".jsonl") else None
    if not d:
        return 0
    try:
        return sum(1 for e in os.scandir(d) if e.name.endswith(".jsonl") and now - e.stat().st_mtime < SUB_FRESH)
    except OSError:
        return 0


def session_facts(path, kind, now=None) -> dict:
    """Facts for one session, cached on (mtime, size); the subagent count is re-read every call (cheap scandir)."""
    now = now or time.time()
    out = {"model": None, "tool": None, "tool_t": None, "commits": [], "error": None, "subagents": 0}
    if not path:
        return out
    try:
        st = os.stat(path)
    except OSError:
        return out
    key = (st.st_mtime, st.st_size)
    c = _FACTS.get(str(path))
    if not c or c[0] != key:
        try:
            objs = _tail_objs(path)
            f = parse_codex(objs) if kind == "codex" else parse_claude(objs)
            if kind == "codex" and not f["model"]:
                f["model"] = _head_model(path)
        except OSError:
            f = dict(out)
        if len(_FACTS) > 200:
            _FACTS.clear()
        c = _FACTS[str(path)] = (key, f)
    f = dict(out, **c[1])
    if kind != "codex":
        f["subagents"] = _subagents(path, now)
    if f.get("tool_t") is None or now - f["tool_t"] > TOOL_FRESH:
        f["tool"] = None
    return f


def species_for(kind, model) -> str:
    m = str(model or "").lower()
    if kind == "codex":
        return "luna" if "luna" in m else "sol" if "sol" in m else "fish"
    if "opus" in m or "fable" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    return "fish"


# ------------------------------------------------------------------------------ environment
def read_env(state_dir: Path, now=None, night="auto") -> dict:
    """{fh: 5-hour % or None, frozen: fraction of the hit window left or None, resets_at, night: bool}."""
    now = now or time.time()
    env = {"fh": None, "frozen": None, "resets_at": None, "night": False, "limit_src": None}
    try:
        j = json.loads((Path(state_dir) / "claude-limit.json").read_text(encoding="utf-8"))
        last = j.get("last") or {}
        if last.get("state") == "hit" and last.get("resets_at") and last["resets_at"] > now:
            win = 7 * 86400 if last.get("type") == "seven_day" else 5 * 3600
            env["frozen"] = max(0.02, min(1.0, (last["resets_at"] - now) / win))
            env["resets_at"] = last["resets_at"]
        elif last.get("fh") is not None and now - float(j.get("updated") or 0) < 6 * 3600:
            env["fh"] = max(0.0, min(100.0, float(last["fh"])))
        env["limit_src"] = "stale" if last.get("stale") else "ok"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if night == "on":
        env["night"] = True
    elif night == "auto":
        h = time.localtime(now).tm_hour
        a, b = NIGHT_HOURS
        env["night"] = (Path(state_dir) / "away.json").exists() or h >= a or h < b
    return env


# ------------------------------------------------------------------------------ roster
def mode_for(row: dict) -> str | None:
    """A session row -> creature mode; None = not in the bowl."""
    st = row.get("state")
    if row.get("crashed") and not row.get("acked") and st in ("finished", "waiting", "working"):
        return "dead"
    if row.get("attn") and st in ("finished", "waiting", "working"):
        return "glass"                          # an agent flashed: it comes to the glass until the user taps it
    if st == "waiting":
        return "stuck" if row.get("kind") == "blocked" else "glass"
    if st == "working":
        return "hide" if row.get("silent") else "swim"
    if st == "finished":
        return "rest"
    return None


def fallback_grid(sid) -> list:
    h = hashlib.sha256(str(sid).encode()).digest()
    cols = "ROYLCBPTE"
    body, acc = cols[h[0] % 9], cols[(h[0] // 9 + 3) % 9]
    g = [["."] * 16 for _ in range(16)]
    for y in range(3, 13):
        for x in range(4, 8):
            on = (h[(y * 4 + x) % 32] >> (x % 8)) & 1 or x == 7 or y in (6, 7, 8, 9)
            if on:
                g[y][x] = g[y][15 - x] = acc if h[(y + x) % 32] % 5 == 0 else body
    out = [r[:] for r in g]
    for y in range(16):
        for x in range(16):
            if g[y][x] == "." and any(0 <= y + dy < 16 and 0 <= x + dx < 16 and g[y + dy][x + dx] != "."
                                      for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                out[y][x] = "K"
    return ["".join(r) for r in out]


def _valid_grid(g):
    return isinstance(g, list) and len(g) == 16 and all(isinstance(x, str) and len(x) == 16 for x in g)


def _summary_rows(state_dir: Path) -> list:
    try:
        j = json.loads((state_dir / "sessions-summary.json").read_text(encoding="utf-8"))
        rows = [dict(r) for r in (j.get("sessions") or []) if isinstance(r, dict) and r.get("id")]
    except (OSError, ValueError, TypeError):
        return []
    for r in rows:
        f = None
        try:
            f = json.loads((state_dir / "session-faces" / f"{r['id']}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        icon = f.get("icon") if isinstance(f, dict) else None
        r["grid"] = icon if _valid_grid(icon) else fallback_grid(r["id"])
        r["label"] = (f or {}).get("label") or (str(r.get("title") or "session").split() or ["session"])[0][:10]
        r.setdefault("kind_agent", r.get("agent"))
    return rows


def load_rows(ctx, source="auto", now=None) -> list:
    """Pad sessions as rows {id, title, state, kind, silent, urgency, label, grid, agent, path}."""
    d = ctx.daemon
    if source != "summary":
        try:
            scan = d.plugin_fn("sessions.scan")
            face_for = d.plugin_fn("sessions.face_for")
            ss = scan()
            out = []
            for s in ss:
                if not s.get("on_pad"):
                    continue
                label, grid = face_for(s, ss)
                out.append(dict(id=s["id"], title=s.get("title"), state=s.get("state"), kind=s.get("wkind"),
                                silent=bool(s.get("silent")), silent_s=s.get("silent_s"), urgency=s.get("urgency"),
                                label=label, grid=grid, agent=s.get("kind"), path=s.get("path")))
            return out
        except Exception as e:                                  # noqa: BLE001
            if source == "sessions":
                raise
            _note(ctx, f"fishbowl: sessions plugin unavailable ({e}); reading sessions-summary.json")
    return _summary_rows(Path(ctx.config_dir) / "state")


def enrich(rows, acked: dict, now=None) -> list:
    """Add transcript facts: species, tool, subagents, commits, crashed/acked."""
    now = now or time.time()
    for r in rows:
        f = session_facts(r.get("path"), r.get("agent"), now) if r.get("path") else {}
        r.setdefault("species", species_for(r.get("agent"), f.get("model")))
        r.setdefault("model", f.get("model"))
        r.setdefault("tool", f.get("tool") if r.get("state") == "working" else None)
        r.setdefault("subagents", f.get("subagents", 0) if r.get("state") == "working" else 0)
        r.setdefault("commits", f.get("commits") or [])
        err = f.get("error")
        if err and "crashed" not in r:
            r["crashed"] = True
            r["err_key"] = hashlib.sha1(json.dumps(err).encode()).hexdigest()[:12]
        r["acked"] = bool(r.get("crashed")) and acked.get(r["id"]) == r.get("err_key")
    return rows


def _note(ctx, msg):
    st = ctx.daemon.plugin_state.setdefault("fishbowl", {})
    if st.get("last_note") != msg:
        st["last_note"] = msg
        ctx.log(msg)


# ------------------------------------------------------------------------------ reef (persistent)
def reef_load(path: Path) -> dict:
    try:
        r = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(r, dict):
            r.setdefault("blocks", [])
            r.setdefault("keeps", [])
            r.setdefault("acked", {})
            return r
    except (OSError, ValueError):
        pass
    return {"blocks": [], "keeps": [], "acked": {}}


def reef_save(path: Path, reef: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(reef), encoding="utf-8")
    os.replace(tmp, path)


def reef_update(reef: dict, rows, now, ended=()) -> bool:
    """Add a block per new commit id, a keepsake per ended session, erode old ones. True if changed."""
    changed = False
    have = {b["id"] for b in reef["blocks"]}
    for r in rows:
        col = A.hexc(ident_for(r["id"], r.get("grid") if _valid_grid(r.get("grid")) else None)["body"])
        for cid in r.get("commits") or []:
            bid = f"{r['id'][:12]}:{cid}"
            if bid not in have:
                reef["blocks"].append({"id": bid, "sid": r["id"], "c": col, "t": now})
                have.add(bid)
                changed = True
    for r in ended:
        if not any(k["sid"] == r["id"] for k in reef["keeps"]) and _valid_grid(r.get("grid")):
            reef["keeps"].append({"sid": r["id"], "grid": r["grid"], "t": now})
            changed = True
    nb = [b for b in reef["blocks"] if now - b["t"] < REEF_DAYS * 86400][-REEF_CAP:]
    nk = [k for k in reef["keeps"] if now - k["t"] < KEEP_DAYS * 86400][-KEEP_CAP:]
    if len(nb) != len(reef["blocks"]) or len(nk) != len(reef["keeps"]):
        changed = True
    reef["blocks"], reef["keeps"] = nb, nk
    return changed


# ------------------------------------------------------------------------------ creatures (art: m18lib/fishart.py)
_IDENT: dict = {}


def ident_for(sid, grid) -> dict:
    """Identity colours and pattern for a session: from its 16x16 sprite, else a hash of its id. Stable per id."""
    k = (sid, tuple(grid) if grid else None)
    v = _IDENT.get(k)
    if v is None:
        if len(_IDENT) > 400:
            _IDENT.clear()
        v = _IDENT[k] = dict(A.identity(sid, grid), sid=str(sid))
    return v


POSTURE = {      # state -> (view, look, eyes): what a stranger reads without a legend
    "swim": ("side", "side", "open"),       # purposeful swim, tail beating
    "glass": ("front", "side", "open"),     # at the glass, facing you, gold glint
    "stuck": ("side", "side", "open"),      # tangled in kelp, struggling (fast jitter)
    "rest": ("side", "side", "closed"),     # on the sand, eyes shut, content, a little flag
    "hide": ("side", "pale", "open"),       # behind a bar, pale, one eye showing
    "dead": ("side", "belly", "x"),         # belly-up at the surface, x eyes
    "hatch": ("egg", "side", "open"),
    "leave": ("side", "side", "open"),
}
TURN_S = 0.2


class Fish:
    def __init__(self, row, x, y, mode, t, rng):
        self.id = row["id"]
        self.update(row)
        self.x, self.y = float(x), float(y)
        self.mode, self.mode_t, self.born = mode, t, t
        self.face = 1 if rng.random() < 0.5 else -1
        self.turn_t = -9.0
        self.target = self.spot = None
        self.lane = None
        self.next_bubble = t + rng.uniform(0.3, 1.0)
        self.next_z = t + rng.uniform(1.0, 3.0)
        self.next_lane = t + rng.uniform(6, 14)
        self.leaving = False
        self.away_until = 0.0
        self.fry = []                          # [(phase, born)]

    def update(self, row):
        self.row = dict(row)
        self.grid = row.get("grid") if _valid_grid(row.get("grid")) else fallback_grid(row["id"])
        self.label = str(row.get("label") or row.get("title") or "session")
        self.kind = row.get("kind")
        self.species = row.get("species") if row.get("species") in A.SPEED else "fish"
        self.ident = ident_for(self.id, self.grid)
        self.speed = A.SPEED[self.species] * (0.9 + (_seed(self.id) % 5) / 20)
        self.tool = row.get("tool")
        self.want_fry = min(int(row.get("subagents") or 0), 6)
        self.attn = bool(row.get("attn"))

    def set_face(self, d, t):
        if d != self.face:
            self.face = d
            self.turn_t = t

    def pose(self, t, frozen=False):
        """(view, frame, look, closed, xeye) for time t."""
        view, look, eyes = POSTURE.get(self.mode, POSTURE["swim"])
        if frozen:
            return (view if view != "egg" else "side", 0, "frost", eyes == "closed", eyes == "x")
        if self.mode == "glass":
            blink = (t + _seed(self.id) % 7) % 4.3 < 0.18
            return ("front", int(t * 4) % 4, look, blink, False)
        if self.mode in ("rest", "hide", "dead"):
            fr = int(t * 1.5) % 4 if self.mode == "rest" else 0          # a slow, sleepy fin wave
            return ("side", fr if self.species != "luna" else int(t) % 2, look, eyes == "closed", eyes == "x")
        if self.mode == "stuck":
            return ("side", int(t * 10) % 4, look, False, False)
        hz = {"haiku": 10, "opus": 6, "luna": 2.5, "sol": 3.5}.get(self.species, 7) * (1.6 if self.leaving else 1)
        n = 6 if self.species == "opus" else 4
        return ("side", int(t * hz) % n, look, False, False)

    def sprite(self, pose):
        """-> (RGBA facing right (or facing you), anchors {eye: [(x, y)], mouth: (x, y), head: (x, y)})."""
        view, fr, look, closed, xeye = pose
        if view == "front":
            img = A.front(self.species, self.ident, fr, look, closed)
            return img, {"eye": [], "mouth": (img.width // 2, int(img.height * 0.7)), "head": (img.width // 2, 0)}
        return A.side(self.species, self.ident, fr, look, closed, xeye)

    def size(self, mode=None):
        if mode is None:
            mode = self.mode
        m0, self.mode = self.mode, mode
        try:
            img, _ = self.sprite(self.pose(0.0))
        finally:
            self.mode = m0
        return img.size

    def bbox(self):
        w, h = self.size()
        return self.x - w / 2, self.y - h / 2, self.x + w / 2, self.y + h / 2


_MEMO: dict = {}


def _memo(key, src, make):
    """make() once per (key, this very src object); the entry keeps src alive so its id stays its own."""
    k = (key, id(src))
    hit = _MEMO.get(k)
    if hit is not None and hit[0] is src:
        return hit[1]
    if len(_MEMO) > 1500:
        _MEMO.clear()
    out = make()
    _MEMO[k] = (src, out)
    return out


def _flip(img):
    return _memo("flip", img, lambda: img.transpose(Image.FLIP_LEFT_RIGHT))


def _outline(img, color, width=1, alpha=255):
    color = tuple(_rgb(color))
    return _memo(("outline", color, width, alpha), img, lambda: _outline_make(img, color, width, alpha))


def _outline_make(img, color, width=1, alpha=255):
    a = img.split()[3]
    big = Image.new("L", (img.width + 2 * width, img.height + 2 * width), 0)
    big.paste(a, (width, width))
    ring = big.filter(ImageFilter.MaxFilter(2 * width + 1))
    if alpha < 255:
        ring = ring.point(lambda v: alpha if v else 0)
    out = Image.new("RGBA", big.size, tuple(_rgb(color)) + (255,))
    out.putalpha(ring)
    out.alpha_composite(img, (width, width))
    return out


class _Rec:
    """Stands in for the world image and its ImageDraw while Tank._paint runs: records each call as
    (bbox, kind, args...) instead of drawing. Pasted images are named by content, so the same sprite made twice
    reads as the same call."""
    _KEYS: dict = {}                       # id(image) -> (image, content key); the entry keeps the id its own

    def __init__(self):
        self.ops = []
        self.images = {}

    def key(self, im):
        hit = _Rec._KEYS.get(id(im))
        if hit is not None and hit[0] is im:
            k = hit[1]
        else:
            if len(_Rec._KEYS) > 3000:
                _Rec._KEYS.clear()
            k = hash((im.size, im.mode, im.tobytes()))
            _Rec._KEYS[id(im)] = (im, k)
        self.images[k] = im
        return k

    def paste(self, im, xy, mask=None):
        x, y = xy
        self.ops.append(((x, y, x + im.width - 1, y + im.height - 1), "p", self.key(im), x, y))

    def rectangle(self, xy, fill=None, outline=None):
        x0, y0, x1, y1 = xy
        self.ops.append(((int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)), int(math.ceil(y1))),
                         "r", x0, y0, x1, y1, fill, outline))

    def point(self, xy, fill=None):
        x, y = xy
        self.ops.append(((x, y, x, y), "pt", x, y, fill))

    def line(self, xy, fill=None, width=1):
        xs, ys = xy[0::2], xy[1::2]
        self.ops.append(((int(math.floor(min(xs))) - width, int(math.floor(min(ys))) - width,
                          int(math.ceil(max(xs))) + width, int(math.ceil(max(ys))) + width),
                         "l", tuple(xy), fill, width))

    def ellipse(self, xy, outline=None):
        x0, y0, x1, y1 = xy
        self.ops.append(((int(math.floor(x0)) - 1, int(math.floor(y0)) - 1, int(math.ceil(x1)) + 1,
                          int(math.ceil(y1)) + 1), "e", x0, y0, x1, y1, outline))


# ------------------------------------------------------------------------------ the tank
class Tank:
    """Simulation + drawing, no daemon: Tank(Geo()).sync(rows, t); .set_env(env); .step(dt, t); .render(t)."""

    def __init__(self, geo: Geo, seed=1, themed=True):
        self.geo = geo
        self.rng = random.Random(seed)
        self.fish: dict[str, Fish] = {}
        self.gone: list[Fish] = []
        self.ended: list[dict] = []            # rows of sessions that left while the bowl watched (for keepsakes)
        self.bubbles, self.food, self.ripples, self.zs, self.shells, self.puffs = [], [], [], [], [], []
        self.bub_acc, self.bub_t = 0.0, 0.0
        self._bub_ops = (None, [])
        self._badge_ops: dict = {}
        self._amb_q = (None, None, None)
        self._dl = {"sig": {}, "amb": None, "amb_keys": {}, "amb_tiles": {}, "frozen_t": -1.0}
        self.plate = None
        self.t = 0.0
        self.themed = themed
        self.lock = threading.RLock()
        self.env = {"fh": None, "frozen": None, "night": False}
        self.reef = {"blocks": [], "keeps": [], "acked": {}}
        self._reef_img = (None, None)
        self.docs, self.attn, self.fx = {}, set(), {"sys": (None, None, None), "cards": (0, 1)}
        self.next_col_bubble = self.next_pump = 0.0
        self.set_layout(None, {})

    # ---------------------------------------------------------------- layout (page mode: fixtures)
    def set_layout(self, tank_keys=None, fixtures=None):
        """tank_keys: the keys that show the tank (None = all 15); fixtures {key: lamp|buoy|gauge|chest}: keys
        whose readable art sits on top of the water. Nothing rests, waits or speaks on a fixture or a foreign key."""
        with self.lock:
            fixtures = {int(k): v for k, v in (fixtures or {}).items() if v in A.READABLE}
            keys = set(range(1, 16)) if tank_keys is None else {int(k) for k in tank_keys}
            self.fixtures = fixtures
            self.blocked = set(fixtures) | (set(range(1, 16)) - keys)
            free = lambda ks: [k for k in ks if k not in self.blocked]          # noqa: E731
            self.glass_keys = free([3, 2, 4, 1, 5]) + free([8, 7, 9, 6, 10]) + free([13, 12, 14, 11, 15]) or [3]
            self.top_keys = set(free([1, 2, 3, 4, 5]))
            self.rest_keys = free([13, 12, 14, 11, 15]) or [13]
            self.bottom_free = free([11, 12, 13, 14, 15]) or [13]
            self.kelp = self._kelp_plants()
            self._bgs, self._amb = {}, (None, None)
            self._assign_spots()

    def readable_boxes(self):
        """World rects a creature, bubble or sign must never cover: the fixtures' readable art."""
        out = []
        for k, name in self.fixtures.items():
            x0, y0, _, _ = self.geo.key_rect(k)
            a, b, c, d = A.READABLE[name]
            out.append((k, name, (x0 + a, y0 + b, x0 + c, y0 + d)))
        return out

    def set_docs(self, docs, attn=()):
        """Agents' posts ({sid: effective doc} from plugins/agents.py) shown on the sessions' own creatures."""
        with self.lock:
            self.docs = dict(docs or {})
            self.attn = set(attn)

    def set_fixture_data(self, sys_=None, cards=None):
        with self.lock:
            if sys_ is not None:
                self.fx["sys"] = tuple(sys_)
            if cards is not None:
                self.fx["cards"] = tuple(cards)

    # ---------------------------------------------------------------- environment
    def set_env(self, env):
        with self.lock:
            self.env = dict(self.env, **env)

    def set_reef(self, reef):
        with self.lock:
            self.reef = reef
            self._reef_img = (None, None)

    def surface_y(self):
        fh = self.env.get("fh")
        return 2 + (int(round(fh / 100 * LEVEL_DROP / 2)) * 2 if fh else 0)

    # ---------------------------------------------------------------- roster
    def sync(self, rows, t, first=False):
        with self.lock:
            seen = set()
            for r in rows:
                m = mode_for(r)
                if m is None:
                    continue
                seen.add(r["id"])
                f = self.fish.get(r["id"])
                if f is None:
                    if first:
                        f = Fish(r, *self._random_water(), m, t, self.rng)
                    else:
                        f = Fish(r, self._free_sand_x(), self.geo.sand - 5, "hatch", t, self.rng)
                        f.after = m
                    self.fish[r["id"]] = f
                else:
                    f.update(r)
                    if f.mode == "hatch":
                        f.after = m
                    elif f.mode != m:
                        f.mode, f.mode_t, f.target = m, t, None
            for fid in [k for k in self.fish if k not in seen]:
                f = self.fish.pop(fid)
                f.mode, f.mode_t, f.leaving = "leave", t, True
                f.target = (-60.0, f.y) if f.x < self.geo.W / 2 else (self.geo.W + 60.0, f.y)
                self.gone.append(f)
                if not first:
                    self.ended.append(f.row)
            self._assign_spots()
            if first:
                for f in self.fish.values():
                    if f.spot:
                        f.x, f.y = f.spot

    def _by(self, mode):
        return sorted((f for f in self.fish.values() if (getattr(f, "after", None) if f.mode == "hatch" else f.mode) == mode),
                      key=lambda f: (-(f.row.get("urgency") or 0), f.born, f.id))

    def _assign_spots(self):
        if not hasattr(self, "glass_keys"):
            return
        g = self.geo
        used = []
        surf = self.surface_y()
        for f in self._by("glass"):
            k = self.glass_keys[len(used) % len(self.glass_keys)]
            used.append(k)
            cx, cy = g.centre(k)
            h = f.size("glass")[1]
            y0 = g.key_rect(k)[1]
            sy = max(cy + 6, y0 + surf + h / 2 + 3) if k in self.top_keys else cy + 6
            f.spot = (cx, min(sy, y0 + 62 - h / 2))
        for f in self._by("dead"):
            free = [k for k in self.glass_keys if k not in used]
            k = free[0] if free else self.glass_keys[0]
            used.append(k)
            cx, _ = g.centre(k)
            h = f.size("dead")[1]
            y0 = g.key_rect(k)[1]
            f.spot = (cx, y0 + (surf if k in self.top_keys else 2) + h / 2 + 1)
        rs = self._by("rest")
        n = len(self.rest_keys)
        for i, f in enumerate(rs):
            k = self.rest_keys[i % n]
            cx, _ = g.centre(k)
            dx = 0 if len(rs) <= n else (-12 if (i // n) % 2 == 0 else 12) + (i // (2 * n)) * 4
            h = f.size("rest")[1]
            f.spot = (cx + dx - 3, g.sand + 4 - h / 2)
        plants = [p for p in self.kelp if "x" in p]
        for i, f in enumerate(self._by("stuck")):
            p = plants[[1, 3, 0, 4, 2][i % 5] % len(plants)]
            f.spot = (p["x"] + 2, p["stuck_y"])
        # silent: hidden in a vertical bar, only its eye peeking into the (tank) screen on its right
        bars = [(c, r) for r in (1, 2, 0) for c in (1, 3, 0, 2) if (r * 5 + c + 2) not in self.blocked] or [(1, 1)]
        for i, f in enumerate(self._by("hide")):
            c, r = bars[i % len(bars)]
            m0, f.mode = f.mode, "hide"
            img, anc = f.sprite(f.pose(0.0))
            f.mode = m0
            w = img.width
            ex = max(x for x, _ in anc["eye"]) if anc["eye"] else w - 3
            xb1 = (c + 1) * g.pitch                                  # left edge of the screen the eye peeks into
            f.spot = (xb1 + 3 - ex + w / 2, r * g.pitch + 30 + (i // len(bars)) * 10)
            f.face = 1
        for f in self.fish.values():
            if f.mode in ("swim", "leave"):
                f.spot = None

    def _random_water(self):
        g = self.geo
        return self.rng.uniform(24, g.W - 24), self.rng.uniform(g.pitch + 10, g.pitch + 54)

    def _lane_y(self, f):
        """Swim lanes sit inside screens (rows' interiors), never on a horizontal bar."""
        g = self.geo
        if f.tool == "read":
            h = f.size()[1]
            return g.sand - h / 2 - 1
        lanes = [g.pitch + 18, 2 * g.pitch + 20, 42, g.pitch + 46]
        if f.lane is None:
            f.lane = _seed(f.id) % 4
        return lanes[f.lane % len(lanes)]

    def _free_sand_x(self):
        g = self.geo
        taken = [f.x for f in self.fish.values() if f.mode in ("rest", "hatch")]
        cands = [g.centre(k)[0] + dx for k in self.bottom_free for dx in (-6, 6, 0)]
        self.rng.shuffle(cands)
        cands.sort(key=lambda x: -min((abs(x - t) for t in taken), default=999))
        return cands[0]

    # ---------------------------------------------------------------- input
    def hit(self, key):
        g = self.geo
        x0, y0, x1, y1 = g.key_rect(key)
        hb = g.gap / 2
        x0, y0, x1, y1 = x0 - hb, y0 - hb, x1 + hb, y1 + hb
        cx, cy = g.centre(key)
        best = None
        with self.lock:
            for f in self.fish.values():
                if f.mode == "hatch" and self.t - f.mode_t < HATCH_S * 0.9:
                    continue
                if f.away_until > self.t:
                    continue
                a, b, c, d = f.bbox()
                if c < x0 or a > x1 or d < y0 or b > y1:
                    continue
                dist = (f.x - cx) ** 2 + (f.y - cy) ** 2
                if best is None or dist < best[0]:
                    best = (dist, f.id)
        return best[1] if best else None

    def tap(self, key, t, held=False):
        """-> ("plate"|"open"|"ack"|"nudge", id) or ("food", None)."""
        with self.lock:
            fid = self.hit(key)
            if fid is None:
                self.feed(key, t)
                return ("food", None)
            f = self.fish[fid]
            if held:
                self.plate = {"id": fid, "key": key, "until": t + PLATE_S, "word": "NUDGED"}
                return ("nudge", fid)
            if f.mode == "dead":
                self.plate = {"id": fid, "key": key, "until": t + PLATE_S, "word": "CRASHED"}
                f.row["acked"] = True
                f.mode, f.mode_t = mode_for(f.row) or "rest", t
                self._assign_spots()
                return ("ack", fid)
            p = self.plate
            if p and p["id"] == fid and t < p["until"]:
                self.plate = None
                return ("open", fid)
            self.plate = {"id": fid, "key": key, "until": t + PLATE_S}
            return ("plate", fid)

    def feed(self, key, t):
        cx, cy = self.geo.centre(key)
        self.ripples.append((cx, cy, t))
        for _ in range(4):
            self.food.append({"x": cx + self.rng.uniform(-12, 12), "y": cy + self.rng.uniform(-10, 4),
                              "t": t, "w": self.rng.uniform(0, 6.28)})

    # ---------------------------------------------------------------- simulation
    def step(self, dt, t=None):
        with self.lock:
            self.t = t if t is not None else self.t + dt
            t = self.t
            g = self.geo
            frozen = self.env.get("frozen") is not None
            if not frozen:
                for f in list(self.fish.values()) + self.gone:
                    self._move(f, dt, t)
                self.gone = [f for f in self.gone if -50 < f.x < g.W + 50]
                if t >= self.next_col_bubble:
                    self.bubbles.append([self.kelp[-1]["col_x"] + self.rng.choice((-1, 0, 1)), g.sand - 2, 30.0,
                                         self.rng.choice((1, 1, 2)), t])
                    self.next_col_bubble = t + 0.45
                gk = next((k for k, v in self.fixtures.items() if v == "gauge"), None)
                load = max([v for v in self.fx["sys"] if v is not None] or [0])
                if gk and load >= 75 and t >= self.next_pump:          # a busy PC: the filter pumps bubbles
                    x0, y0, _, _ = g.key_rect(gk)
                    self.bubbles.append([x0 + 14 + self.rng.choice((-1, 0, 1)), y0 + 2, 34.0,
                                         self.rng.choice((1, 2, 2)), t])
                    self.next_pump = t + (0.18 if load >= 90 else 0.35)
                surf = self.surface_y()
                self.bub_acc += dt                                    # bubbles tick at BUBBLE_HZ: a key is resent
                if self.bub_acc >= 1.0 / BUBBLE_HZ:                    # whole for every bubble pixel that moves
                    for b in self.bubbles:
                        b[1] -= b[2] * self.bub_acc
                    self.bub_acc = 0.0
                    self.bub_t = t
                self.bubbles = [b for b in self.bubbles if b[1] > surf + 2]
                for fd in self.food:
                    if fd["y"] < g.sand - 2:
                        fd["y"] += 9 * dt
            self.food = [fd for fd in self.food if t - fd["t"] < 14]
            self.ripples = [r for r in self.ripples if t - r[2] < 0.75]
            for z in self.zs:
                z[1] -= 5 * dt
            self.zs = [z for z in self.zs if t - z[2] < 3.0]
            self.shells = [s for s in self.shells if t - s[2] < 2.5]
            self.puffs = [p for p in self.puffs if t - p[2] < 0.6]
            if self.plate and t >= self.plate["until"]:
                self.plate = None

    def _seek(self, f, tx, ty, speed, dt):
        dx, dy = tx - f.x, ty - f.y
        dist = math.hypot(dx, dy)
        if dist < 0.5:
            f.x, f.y = tx, ty
            return True
        s = min(dist, speed * dt)
        f.x += dx / dist * s
        f.y += dy / dist * s
        if abs(dx) > 1.5:
            f.set_face(1 if dx > 0 else -1, self.t)
        return dist <= speed * dt

    def _move(self, f, dt, t):
        g = self.geo
        m = f.mode
        # fry: grow toward the live subagent count, leave when it drops
        while len(f.fry) < f.want_fry:
            f.fry.append((self.rng.uniform(0, 6.28), t))
        if len(f.fry) > f.want_fry:
            f.fry = f.fry[:f.want_fry]
        if f.leaving:
            self._seek(f, f.target[0], f.target[1], 60, dt)
            return
        if m == "hatch":
            if t - f.mode_t >= HATCH_S:
                self.shells.append((f.x, g.sand - 4, t))
                for _ in range(3):
                    self.bubbles.append([f.x + self.rng.uniform(-4, 4), g.sand - 8, 22.0, 1, t])
                f.mode, f.mode_t = getattr(f, "after", "swim"), t
                f.y = g.sand - 14
                self._assign_spots()
            return
        if m == "swim":
            self._swim(f, dt, t)
            return
        if m == "hide" and t >= f.next_z and f.spot:
            w, h = f.size()
            self.zs.append([f.spot[0] + w / 2 - 2, f.spot[1] - h / 2 - 2, t])
            f.next_z = t + self.rng.uniform(2.5, 4.0)
        if f.spot is None:
            self._assign_spots()
        if f.spot is None:
            return
        speed = {"glass": 45, "dead": 40, "hide": 25}.get(m, 30)
        self._seek(f, f.spot[0], f.spot[1], speed, dt)
        if m == "hide":
            f.face = 1
        elif m == "glass" and math.hypot(f.spot[0] - f.x, f.spot[1] - f.y) < 2:
            f.face = 1

    def _swim(self, f, dt, t):
        g = self.geo
        if f.away_until > t:                                  # out through the surface (web)
            return
        if getattr(f, "out", False):                          # back from the web: drop in from the surface
            f.out = False
            f.y = -20
            self.ripples.append((f.x, self.surface_y() + 2, t))
        food = [fd for fd in self.food if math.hypot(fd["x"] - f.x, fd["y"] - f.y) < 140]
        if food:
            fd = min(food, key=lambda q: math.hypot(q["x"] - f.x, q["y"] - f.y))
            if self._seek(f, fd["x"], fd["y"], f.speed * 2.2, dt) or math.hypot(fd["x"] - f.x, fd["y"] - f.y) < 5:
                self.food.remove(fd)
            return
        if f.tool == "web":
            if f.y > -30:
                if f.y < self.surface_y() + 6 and not getattr(f, "splashed", False):
                    self.ripples.append((f.x, self.surface_y() + 2, t))
                    f.splashed = True
                self._seek(f, f.x + f.face * 6, -40, f.speed * 1.6, dt)
                return
            f.away_until, f.out, f.splashed = t + self.rng.uniform(4, 7), True, False
            return
        # steady horizontal transit along a lane; turn at the walls; switch lanes now and then
        ly = self._lane_y(f)
        if t >= f.next_lane and f.tool != "read":
            f.lane = (f.lane or 0) + 1 + self.rng.randrange(2)
            f.next_lane = t + self.rng.uniform(8, 16)
        if f.x < 20:
            f.set_face(1, t)
        elif f.x > g.W - 20:
            f.set_face(-1, t)
        sp = f.speed
        if f.species == "haiku":                              # darting: bursts and pauses
            sp *= 2.4 if (t * 1.3 + _seed(f.id) % 7 / 7) % 1 < 0.35 else 0.25
        if f.tool == "read":
            sp *= 0.5
        f.x += f.face * sp * dt
        f.y += max(-14 * dt, min(14 * dt, ly - f.y))
        if f.tool == "read" and int(t * 2) != int((t - dt) * 2):    # nibble: a sand puff under its mouth
            img, _ = f.sprite(f.pose(t))
            self.puffs.append((f.x + f.face * img.width / 2, g.sand, t))
        if t >= f.next_bubble:
            img, anc = f.sprite(f.pose(t))
            mx, my = anc["mouth"]
            bx = f.x + f.face * (mx - img.width / 2) + f.face * 2
            by = f.y - img.height / 2 + my - 2
            n = 5 if f.tool == "run" else 1
            for i in range(n):
                self.bubbles.append([bx + self.rng.uniform(-3, 3), by - i * 3, 16.0 + i * 2, 1 + (i % 3 == 2), t])
            f.next_bubble = t + (self.rng.uniform(1.0, 1.6) if f.tool == "run" else self.rng.uniform(0.7, 1.4))

    # ---------------------------------------------------------------- drawing: static layers
    def _kelp_plants(self):
        g = self.geo
        plants = []
        blocked = getattr(self, "blocked", set())
        for k, dx in ((6, -12), (7, 10), (8, -14), (9, 12), (10, -10)):
            if k in blocked and len(blocked) < 15:
                continue
            cx, cy = g.centre(k)
            plants.append({"x": cx + dx, "top": cy - 24, "stuck_y": cy + 2, "phase": _seed(k) % 7, "k": k})
        col = max(getattr(self, "bottom_free", None) or [15])
        plants.append({"col_x": g.centre(col)[0] + 22})
        return plants

    def _bg(self):
        """Water to the surface (level = 5-hour usage), murk with usage, night darkening; cached per state."""
        g = self.geo
        surf = self.surface_y()
        fh = self.env.get("fh") or 0.0
        night = bool(self.env.get("night"))
        frozen = self.env.get("frozen") is not None
        key = (surf, int(fh // 10), night, frozen)
        if key in self._bgs:
            return self._bgs[key]
        rng = random.Random(7)
        img = Image.new("RGB", (g.W, g.H), _rgb(WATER[0]))
        px = img.load()
        n = len(WATER)
        murk = min(0.35, fh / 100 * 0.35)
        for ty in range(0, g.H, 4):
            for tx in range(0, g.W, 4):
                f = max(0.0, (ty - surf)) / max(1, g.sand - surf) * (n - 1)
                i = int(f)
                if ((tx // 4) + (ty // 4)) % 2 == 0 and f - i > 0.5:
                    i += 1
                c = _rgb(WATER[min(i, n - 1)])
                if (tx + ty * 0.55) % 150 < 22 and ty < g.pitch * 2:
                    c = _mix(c, RAY, 0.55 * (1 - ty / (g.pitch * 2)))
                c = _mix(c, MURK, murk)
                if frozen:
                    c = _mix(c, ICE[2], 0.25)
                for yy in range(ty, min(ty + 4, g.H)):
                    for xx in range(tx, min(tx + 4, g.W)):
                        px[xx, yy] = c
        d = ImageDraw.Draw(img)
        if surf > 2:
            d.rectangle((0, 0, g.W, surf - 1), fill=_rgb(AIR))
            for x in range(0, g.W, 8):                                  # the glass rim's reflection
                d.point((x, 1), fill=_rgb("#26324a"))
        for tx in range(0, g.W, 4):
            h = 2 + int(2 + 2 * math.sin(tx * 0.031) + 1.5 * math.sin(tx * 0.077 + 1))
            for ty in range(g.sand - h // 2 * 2, g.H, 4):
                d.rectangle((tx, ty, tx + 3, ty + 3), fill=_rgb(rng.choice(SAND)))
        for k, kind, dx in ((11, "tube", 20), (12, "fire", -22), (13, "horn", 22), (14, "brain", -20),
                            (15, "bubble", -24)):
            x = g.centre(k)[0] + dx
            a, b = CORAL[kind]
            for ddx, hh in ((0, 10), (-4, 6), (4, 7)):
                d.rectangle((x + ddx - 1, g.sand - 1 - hh, x + ddx, g.sand - 1), fill=_rgb(a))
                d.rectangle((x + ddx - 1, g.sand - 1 - hh, x + ddx, g.sand - hh), fill=_rgb(b))
        mx = self.kelp[-1]["col_x"]
        d.rectangle((mx - 5, g.sand - 3, mx + 4, g.sand + 5), fill=_rgb("#7a2a0c"))
        for (ax, ay) in ((-3, -1), (1, 1), (-1, 3), (2, -2)):
            d.rectangle((mx + ax, g.sand + ay, mx + ax + 1, g.sand + ay + 1), fill=_rgb("#ff8a1e"))
        self._draw_reef(img)
        if night:
            img = img.point(lambda v: int(v * 0.28))
        img = self._lamp_light(img, night)
        if len(self._bgs) > 8:
            self._bgs.clear()
        self._bgs[key] = img
        return img

    def _lamp_light(self, img, night):
        """The lamp fixture's cone of warm light down through the water to the sand (it continues behind the bar
        into the screens below). At night it is the one lit pool in the tank."""
        lk = next((k for k, v in self.fixtures.items() if v == "lamp"), None)
        if lk is None:
            return img
        g = self.geo
        x0, y0, _, _ = g.key_rect(lk)
        bx, by = x0 + A.LAMP_HOT[0], y0 + A.LAMP_HOT[1]
        mask = Image.new("L", img.size, 0)
        md = ImageDraw.Draw(mask)
        for spread, a in ((1.0, 50), (0.7, 85), (0.42, 120)):
            w0, w1 = 13 * spread, 46 * spread
            md.polygon([(bx - w0, by), (bx + w0, by), (bx + w1, g.H), (bx - w1, g.H)], fill=a)
        col = Image.new("L", (1, g.H), 0)
        for y in range(g.H):                                         # fades with depth
            col.putpixel((0, y), int(255 * max(0.0, 1 - max(0, y - by) / (g.H - by) * 0.65)) if y >= by else 0)
        from PIL import ImageChops
        mask = ImageChops.multiply(mask, col.resize(img.size))
        k = 0.75 if night else 0.42
        mask = mask.point(lambda v: int(v * k))
        warm = Image.new("RGB", img.size, (255, 226, 150))
        return Image.composite(warm, img, mask)

    def _reef_slots(self):
        """Columns for reef blocks: 4 px wide, at the left and right thirds of each bottom screen (the centre is
        where a finished creature rests)."""
        g = self.geo
        xs = []
        for k in self.bottom_free:
            x0 = g.key_rect(k)[0]
            xs += [x0 + 2 + 4 * i for i in range(3)] + [x0 + 50 + 4 * i for i in range(3)]
        return xs

    def _draw_reef(self, img):
        g = self.geo
        d = ImageDraw.Draw(img)
        slots = self._reef_slots()
        heights = [0] * len(slots)
        now = time.time()
        for b in self.reef.get("blocks", []):
            i = _seed(b["id"]) % len(slots)
            for _ in range(len(slots)):                              # find a column under 6 blocks
                if heights[i] < 6:
                    break
                i = (i + 7) % len(slots)
            x, y = slots[i], g.sand - 1 - (heights[i] + 1) * 4
            heights[i] += 1
            age = max(0.0, min(1.0, (now - b["t"]) / (REEF_DAYS * 86400)))
            c = _mix(b.get("c", BLOCK[0]), "#5a7a4a", 0.6 * age)     # older blocks moss over
            d.rectangle((x, y, x + 3, y + 3), fill=c)
            d.point((x, y), fill=_mix(c, "#ffffff", 0.35))
            d.point((x + 3, y + 3), fill=_mix(c, "#000000", 0.4))
        for j, kp in enumerate(self.reef.get("keeps", [])[:KEEP_CAP]):   # 16x16 keepsakes, 1 px per cell
            ks = [k for k in (12, 14, 11, 15, 13) if k in self.bottom_free] or [13]
            k = ks[j % len(ks)]
            x0 = g.key_rect(k)[0] + (34 if j < len(ks) else 14)
            y0 = g.sand - 17
            for yy, row in enumerate(kp["grid"]):
                for xx, ch in enumerate(row):
                    if ch != ".":
                        d.point((x0 + xx, y0 + yy), fill=_mix(SPRITE_PAL.get(ch, "#7c7c7c"), "#c9be89", 0.25))

    def _ambient(self, t):
        q = int(t * AMBIENT_FPS)
        aq = self._amb_q
        if aq[0] == q and aq[1] is self.env and aq[2] is self.kelp:  # same tick, same environment object
            return self._amb[1]
        self._amb_q = (q, self.env, self.kelp)
        bg = self._bg()
        if self._amb[0] == (q, id(bg)):
            return self._amb[1]
        g = self.geo
        ph = q / AMBIENT_FPS
        night = bool(self.env.get("night"))
        dim = (lambda c: tuple(int(v * 0.3) for v in c)) if night else (lambda c: c)   # noqa: E731
        img = bg.copy()
        d = ImageDraw.Draw(img)
        surf = self.surface_y()
        if self.env.get("frozen") is None:
            s1, s0, rect, sin = dim(_rgb(SURFACE[1])), dim(_rgb(SURFACE[0])), d.rectangle, math.sin
            for x in range(0, g.W, 2):
                y = surf + int(round(1.2 * sin(x * 0.11 + ph * 2.2)))
                rect((x, max(0, y - 1), x + 1, y), fill=s1)
                rect((x, y, x + 1, y + 1), fill=s0)
        kc = [dim(_rgb(c)) for c in KELP[:3]]
        for p in self.kelp[:-1]:
            n = (g.sand - p["top"]) // 4
            for s in range(n):
                y = g.sand - (s + 1) * 4
                sway = int(round(3 * math.sin(ph * 1.3 + p["phase"] + s * 0.35) * (s / max(1, n))))
                x = p["x"] + sway
                d.rectangle((x - 1, y, x + 1, y + 3), fill=kc[s % 2])
                if s % 3 == 1:
                    lx = x + 2 if (s // 3) % 2 else x - 3
                    d.rectangle((lx, y + 1, lx + 1, y + 2), fill=kc[2])
        for k, dx in ((11, -10), (12, 6), (13, -4), (14, 10), (15, 2)):
            cx = g.centre(k)[0] + dx
            for j, hh in enumerate((7, 10, 6)):
                bend = int(round(math.sin(ph * 1.7 + cx + j)))
                d.line((cx + j * 2, g.sand, cx + j * 2 + bend, g.sand - hh), fill=dim(_rgb(SEAGRASS[j % 2])))
        glow = 0.5 + 0.5 * math.sin(ph * 1.1)
        for k, dx in ((12, 20), (14, 24)):                       # sea pickles: the tank's lamps, brighter at night
            cx = g.centre(k)[0] + dx
            for j in (0, 4):
                d.rectangle((cx + j, g.sand - 4, cx + j + 2, g.sand), fill=dim(_rgb(PICKLE[0])))
                d.rectangle((cx + j, g.sand - 6, cx + j + 2, g.sand - 5),
                            fill=_mix(PICKLE[0], PICKLE[1], 0.35 + 0.65 * glow))
        self._amb = ((q, id(bg)), img)
        return img

    # ---------------------------------------------------------------- drawing: per frame
    def render(self, t=None):
        with self.lock:
            t = self.t if t is None else t
            g = self.geo
            night = bool(self.env.get("night"))
            frozen = self.env.get("frozen")
            img = self._ambient(t).copy()
            d = ImageDraw.Draw(img)
            self._paint(img, d, t, night, frozen)
            if frozen is not None:
                lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                self._draw_ice(ImageDraw.Draw(lay), frozen)
                a = lay.split()[3].point(lambda v: 150 if v else 0)
                lay.putalpha(a)
                img.paste(lay, (0, 0), lay)
            self._draw_signs(img, t, frozen is not None)
            return img

    def _paint(self, img, d, t, night, frozen):
        """Everything that moves, drawn through `img.paste` and `d.rectangle / line / point / ellipse` only, so a
        _Rec can stand in for both and record the calls (dirty_faces)."""
        if True:
            for s in self.shells:
                x, y = int(s[0]), int(s[1])
                d.rectangle((x - 6, y, x - 3, y + 3), fill=_rgb("#e8e4cf"))
                d.rectangle((x + 3, y, x + 6, y + 3), fill=_rgb("#e8e4cf"))
            for p in self.puffs:
                a = (t - p[2]) / 0.6
                for dx in (-3, 0, 3):
                    px_, py_ = int(p[0] + dx * (1 + a)), int(p[1] - 5 - 7 * a)
                    d.rectangle((px_, py_, px_ + 1, py_ + 1), fill=_rgb(SAND[3]))
            for fd in self.food:
                x, y = int(fd["x"] + 1.5 * math.sin(t * 3 + fd["w"])), int(fd["y"])
                d.rectangle((x, y, x + 1, y + 1), fill=_rgb(FOOD[0]))
            order = sorted(list(self.fish.values()) + self.gone,
                           key=lambda f: ({"rest": 0, "hatch": 0, "stuck": 1, "hide": 1, "swim": 3, "leave": 3,
                                           "dead": 4, "glass": 5}.get(f.mode, 3), f.id))
            for f in order:
                self._draw_fish(img, d, f, t, night, frozen is not None)
            bcol = _rgb(BUBBLE[0]) if not night else _rgb("#6a8ab0")
            rec = d if isinstance(d, _Rec) else None
            bkey = (self.bub_t, len(self.bubbles), self.bubbles[-1][4] if self.bubbles else 0, night)
            if rec is not None and self._bub_ops[0] == bkey:      # bubbles stand still between their ticks
                rec.ops.extend(self._bub_ops[1])
            else:
                n0 = len(rec.ops) if rec is not None else 0
                for b in self.bubbles:
                    x, y = int(b[0] + math.sin(self.bub_t * 4 + b[4] * 7) * 1.2), int(b[1])
                    if b[3] == 1:
                        d.rectangle((x - 1, y - 1, x + 1, y + 1), outline=bcol)
                    else:
                        d.rectangle((x - 2, y - 2, x + 1, y + 1), outline=bcol)
                if rec is not None:
                    self._bub_ops = (bkey, rec.ops[n0:])
            for z in self.zs:
                col = _mix(WATER[2] if not night else "#0a1020", LAVENDER, round((1 - (t - z[2]) / 3.0) * 8) / 8)
                for ry, row in enumerate(_Z):
                    for rx, ch in enumerate(row):
                        if ch == "#":
                            d.point((int(z[0]) + rx, int(z[1]) + ry), fill=col)
            for (x, y, t0) in self.ripples:
                r = 3 + (t - t0) / 0.75 * 16
                d.ellipse((x - r, y - r * 0.6, x + r, y + r * 0.6), outline=_mix("#cfe9ff", WATER[2], (t - t0) / 0.75))

    def _draw_ice(self, d, frac):
        """Ice from the surface down (translucent: frozen creatures show through), thickness = share of the limit
        window still to wait; cracks multiply as it thaws."""
        g = self.geo
        th = 6 + int(round(frac * 50))
        for tx in range(0, g.W, 4):
            edge = th + int(3 * math.sin(tx * 0.13)) + (_seed(tx) % 3)
            for ty in range(0, edge, 4):
                c = ICE[(_seed(tx * 31 + ty) % 3)] if ty < edge - 4 else ICE[2]
                d.rectangle((tx, ty, tx + 3, ty + 3), fill=_rgb(c))
        for i in range(int((1 - frac) * 14)):                   # cracks multiply as the reset nears
            x = (_seed(i) % (g.W - 20)) + 10
            d.line((x, 2, x + 6, th // 2, x + 2, th - 2), fill=_rgb("#7fb0d0"))

    def _draw_fish(self, img, d, f, t, night=False, frozen=False):
        g = self.geo
        if f.mode == "hatch":
            age = t - f.mode_t
            x, y = int(f.x), int(g.sand - 5)
            wob = (-1, 1)[int(age * 8) % 2] if age > HATCH_S * 0.4 else 0
            for ry, row in enumerate(_EGG):
                for rx, ch in enumerate(row):
                    if ch != ".":
                        col = {"#": "#5f6b3a", "W": "#efe9cf", "G": "#7fa34a"}[ch]
                        px, py = x - 7 + rx * 2 + wob, y - 10 + ry * 2
                        d.rectangle((px, py, px + 1, py + 1), fill=_rgb(col))
            if age > HATCH_S * 0.7:
                for (cx, cy) in ((-3, -3), (-1, -1), (1, -3), (3, -1)):
                    d.rectangle((x + cx + wob, y - 6 + cy, x + cx + wob + 1, y - 6 + cy), fill=_rgb("#3a3a2a"))
            return
        if f.away_until > t:
            return
        pose = f.pose(t, frozen)
        spr, anc = f.sprite(pose)
        side = pose[0] == "side"
        if side and f.face < 0:                                      # mirrored, never moonwalking
            spr = _flip(spr)
            w = spr.width
            anc = {"eye": [(w - 1 - x, y) for x, y in anc["eye"]], "mouth": (w - 1 - anc["mouth"][0], anc["mouth"][1]),
                   "head": (w - 1 - anc["head"][0], anc["head"][1])}
        if side and not frozen and t - f.turn_t < TURN_S and f.mode in ("swim", "leave", "glass", "dead"):
            w0 = spr.width
            spr = _memo("squash", spr, lambda: A.squash(spr))        # a turn: seen end-on for an instant
            sx = spr.width / w0
            anc = {"eye": [], "mouth": (int(anc["mouth"][0] * sx), anc["mouth"][1]),
                   "head": (int(anc["head"][0] * sx), anc["head"][1])}
        bob = 0
        if not frozen:
            if f.mode == "glass":
                bob = int(round(1.5 * math.sin(t * 2.1 + _seed(f.id) % 5)))
            elif f.mode == "dead":
                bob = int(round(1.0 * math.sin(t * 1.2)))
            elif f.species == "luna" and f.mode == "swim":
                bob = (0, -2)[pose[1] % 2]
        jit = (-1, 1)[int(t * 10) % 2] if (f.mode == "stuck" and not frozen) else 0
        o = 0
        if f.mode in ("glass", "stuck") or (f.attn and f.mode not in ("dead", "hide")):
            ph = 0.6 if frozen else round(pulse(t * (1.6 if f.mode == "stuck" else 1.0)) * 6) / 6   # 7 steps: fewer redraws
            spr = _outline(spr, _mix(GOLD_DIM, GOLD, ph))
            o = 1
        elif night and f.mode != "hide":                           # night: a soft glow in its own colour
            spr = _outline(spr, _mix(f.ident["body"], "#ffffff", 0.3), width=2, alpha=110)
            o = 2
        x0 = int(round(f.x - spr.width / 2)) + jit
        y0 = int(round(f.y - spr.height / 2)) + bob
        for i, (ph, born) in enumerate(f.fry):                      # subagents: fry schooling behind the parent
            fr = A.fry(f.ident, int(t * 6 + i) % 2)
            if f.face < 0:
                fr = _flip(fr)
            fx = f.x - f.face * (spr.width / 2 + 7 + (i // 2) * 9) + 2 * math.sin(t * 3 + ph)
            fy = f.y + (-6 if i % 2 == 0 else 6) + 2 * math.cos(t * 2.5 + ph)
            img.paste(fr, (int(fx) - 4, int(fy) - 2), fr)
        img.paste(spr, (x0, y0), spr)
        mx, my = anc["mouth"][0] + o + x0, anc["mouth"][1] + o + y0
        if f.mode == "swim" and f.tool == "edit" and not frozen:      # carrying a block in its mouth
            bx = mx + (1 if f.face > 0 else -6)
            by = my - 3
            d.rectangle((bx, by, bx + 5, by + 5), fill=_rgb(BLOCK[0]))
            d.rectangle((bx, by, bx + 5, by + 1), fill=_rgb("#a8a8a8"))
            d.rectangle((bx + 2, by + 3, bx + 3, by + 4), fill=_rgb(BLOCK[1]))
        if f.mode == "stuck":                                         # kelp wrapped round it
            w, h = spr.size
            sway = 0 if frozen else int(t * 10) % 2
            d.line((x0 - 2, y0 + h * 0.25 + sway, x0 + w + 2, y0 + h * 0.7), fill=_rgb(KELP[0]), width=2)
            d.line((x0 + w * 0.25, y0 - 3, x0 + w * 0.6, y0 + h + 2), fill=_rgb(KELP[1]), width=2)
            d.line((x0 + w * 0.7, y0 - 2, x0 + w * 0.45, y0 + h + 3), fill=_rgb(KELP[2]), width=1)
        doc = self.docs.get(f.id) or {}
        if f.mode in ("glass", "stuck") and not (doc.get("buttons") or doc.get("status")):
            kb = _KIND.get("blocked" if f.mode == "stuck" else (f.kind if f.kind in _KIND else "question"))
            bw = len(kb[0]) * 2
            bx, by = int(f.x) - bw // 2 + (0 if f.mode == "glass" else f.face * 6), y0 - len(kb) * 2 - 4
            k = g.key_at(f.x, f.y)
            if k:                                                      # keep the badge on the creature's screen
                kx0, ky0, kx1, _ = g.key_rect(k)
                by = max(by, ky0 + 2)
                bx = max(kx0 + 2, min(bx, kx1 - bw - 3))
            ph = round(pulse(t * (1.6 if f.mode == "stuck" else 1.0)) * 6) / 6 if not frozen else 0.3
            col = _mix(GOLD_DIM, GOLD, 0.4 + 0.6 * ph)
            rec = d if isinstance(d, _Rec) else None
            bk = (id(kb), bx, by, col)
            hit = self._badge_ops.get(f.id) if rec is not None else None
            if hit is not None and hit[0] == bk:                    # same badge, same place, same glow: same calls
                rec.ops.extend(hit[1])
            else:
                n0 = len(rec.ops) if rec is not None else 0
                d.rectangle((bx - 2, by - 2, bx + bw + 1, by + len(kb) * 2 + 1), fill=_rgb("#161616"))
                for ry, row in enumerate(kb):
                    for rx, ch in enumerate(row):
                        if ch == "#":
                            d.rectangle((bx + rx * 2, by + ry * 2, bx + rx * 2 + 1, by + ry * 2 + 1), fill=col)
                if rec is not None:
                    if len(self._badge_ops) > 64:
                        self._badge_ops.clear()
                    self._badge_ops[f.id] = (bk, rec.ops[n0:])
        if f.mode == "rest":                                          # a little flag in the sand by its tail
            fx = x0 - 3 if f.face > 0 else x0 + spr.width + 1
            mk = self._marking(doc)
            if mk is not None:
                img.paste(mk, (fx, g.sand - mk.height + 2), mk)
            else:
                top = g.sand - 15
                d.rectangle((fx, top, fx, g.sand + 1), fill=_rgb(WOOD))
                wave = int(t * 1.5) % 2 if not frozen else 0
                d.rectangle((fx + 1, top, fx + 6, top + 3 + wave), fill=_rgb(FLAG))
                d.point((fx + 6, top + 4 + wave), fill=_rgb(FLAG)) if wave else None

    @staticmethod
    def _marking(doc):
        fc = doc.get("face") if doc else None
        if not fc or not fc.get("frames"):
            return None
        from m18lib import agentpad as AP
        return A.marking(tuple(fc["frames"][0]), fc.get("palette") or {}, AP.BASE_PALETTE)

    def _draw_signs(self, img, t, frozen):
        """Agents' posts on their own creature, kept inside the creature's (tank) screen: a speech bubble with the
        parked button word (gold) or the status word (tone colour), a progress bar under it, its marking flag."""
        if not self.docs:
            return
        from m18lib import agentpad as AP
        g = self.geo
        for f in self.fish.values():
            doc = self.docs.get(f.id)
            if not doc or f.away_until > t or f.mode in ("hatch", "leave"):
                continue
            k = g.key_at(f.x, f.y)
            if k is None or k in self.blocked:
                continue
            kx0, ky0, kx1, ky1 = g.key_rect(k)
            w, h = f.size()
            top, bot = f.y - h / 2, f.y + h / 2
            btns, st, pg = doc.get("buttons") or [], doc.get("status"), doc.get("progress")
            sign = None
            if btns:
                sign = A.bubble(str(btns[0].get("label") or "?")[:8], (30, 20, 6), A.GOLD,
                                "left" if f.face > 0 else "right")
            elif st and st.get("text"):
                tone = st.get("tone") or "info"
                fg = (20, 16, 30) if tone == "info" else _rgb(AP.TONES[tone])
                fill = (250, 250, 245) if tone == "info" else (24, 20, 34)
                sign = A.bubble(st["text"][:8], fg, fill, "left" if f.face > 0 else "right")
            if sign is not None:
                bx = int(f.x + (2 if f.face > 0 else -sign.width - 2))
                by = int(top - sign.height)
                bx = max(kx0 + 1, min(bx, kx1 - sign.width - 1))
                by = max(ky0 + 1, min(by, ky1 - sign.height - 1))
                img.paste(sign, (bx, by), sign)
            if pg:
                bw = max(14, min(26, w))
                bar = A.progress_bar(pg.get("value", 0.0), A.readable_on_water(f.ident["body"]), bw)
                px = int(max(kx0 + 1, min(f.x - bw / 2, kx1 - bw - 1)))
                py = int(bot + 3)
                if py + 4 > ky1 - 1:
                    py = int(top - 6) if sign is None else int(ky1 - 5)
                img.paste(bar, (px, max(ky0 + 1, py)), bar)
            if sign is None and f.mode != "rest":
                mk = self._marking(doc)
                if mk is not None:
                    mx = int(max(kx0 + 1, min(f.x - mk.width / 2, kx1 - mk.width - 1)))
                    my = int(max(ky0 + 1, top - mk.height - 1))
                    img.paste(mk, (mx, my), mk)

    # ---------------------------------------------------------------- faces
    def faces(self, t=None, pen=None):
        img = self.render(t)
        tl = self.geo.tiles(img)
        for k, name in self.fixtures.items():
            tl[k] = self.fixture_tile(tl[k], k, name, self.t if t is None else t)
        out = {k: {"image": im} for k, im in tl.items()}
        p = self.plate
        if p and pen is not None and p["key"] not in self.blocked:
            f = self.fish.get(p["id"])
            if f:
                out[p["key"]] = {"image": plate_tile(tl[p["key"]], f, pen, p.get("word"))}
        return out

    def dirty_faces(self, t=None, pen=None):
        """{key: 64x64 image} for the keys whose picture changed since the last call, without drawing the others.
        The paint code runs against a recorder; each key's list of draw calls (plus its ambient window, fixture
        art and plate) is compared with the last one, and only a key whose list differs is composed. Pixel-equal
        to faces() (fishbowl_check: "display list")."""
        with self.lock:
            t = self.t if t is None else t
            g, dl = self.geo, self._dl
            night = bool(self.env.get("night"))
            frozen = self.env.get("frozen")
            if frozen is not None:                                  # ice is one translucent sheet: full path,
                if abs(t - dl["frozen_t"]) < 0.25:                  # and nothing moves, so 4 looks a second
                    return {}
                dl["frozen_t"] = t
                dl["sig"] = {}
                return {k: fc["image"] for k, fc in self.faces(t, pen).items()}
            amb = self._ambient(t)
            if amb is not dl["amb"]:                                # new ambient frame: which windows really changed
                dl["amb"] = amb
                tiles, keys = {}, {}
                for k in range(1, 16):
                    tl = amb.crop(g.key_rect(k))
                    tiles[k], keys[k] = tl, hash(tl.tobytes())
                dl["amb_tiles"], dl["amb_keys"] = tiles, keys
            rec = _Rec()
            self._paint(rec, rec, t, night, frozen)
            self._draw_signs(rec, t, False)
            per = {}
            pitch = g.pitch
            cover = dl.setdefault("cover", {})                      # bbox -> the keys it touches
            if len(cover) > 6000:
                cover.clear()
            for op in rec.ops:
                ks = cover.get(op[0])
                if ks is None:
                    bx0, by0, bx1, by1 = op[0]
                    ks = []
                    for r in range(max(0, by0 // pitch), min(2, by1 // pitch) + 1):
                        if by0 >= r * pitch + 64:
                            continue
                        for c in range(max(0, bx0 // pitch), min(4, bx1 // pitch) + 1):
                            if bx0 < c * pitch + 64:
                                ks.append(1 + r * 5 + c)
                    ks = cover[op[0]] = tuple(ks)
                for k in ks:
                    lst = per.get(k)
                    if lst is None:
                        lst = per[k] = []
                    lst.append(op[1:])
            p = self.plate
            pk = p["key"] if (p and pen is not None and p["key"] not in self.blocked and self.fish.get(p["id"])) else None
            out = {}
            for k in range(1, 16):
                name = self.fixtures.get(k)
                ov = self.fixture_overlay(k, name, t) if name else None
                sig = (dl["amb_keys"][k], tuple(per.get(k, ())), rec.key(ov) if ov is not None else None)
                if k == pk:
                    sig = None                                      # a name plate: composed every frame it shows
                elif dl["sig"].get(k) == sig:
                    continue
                dl["sig"][k] = sig
                x0, y0, _, _ = g.key_rect(k)
                tile = dl["amb_tiles"][k].copy()
                d = ImageDraw.Draw(tile)
                for op in per.get(k, ()):
                    kind = op[0]
                    if kind == "p":
                        im = rec.images[op[1]]
                        tile.paste(im, (op[2] - x0, op[3] - y0), im)
                    elif kind == "r":
                        d.rectangle((op[1] - x0, op[2] - y0, op[3] - x0, op[4] - y0), fill=op[5], outline=op[6])
                    elif kind == "pt":
                        d.point((op[1] - x0, op[2] - y0), fill=op[3])
                    elif kind == "l":
                        d.line(tuple(v - (x0 if i % 2 == 0 else y0) for i, v in enumerate(op[1])), fill=op[2],
                               width=op[3])
                    else:
                        d.ellipse((op[1] - x0, op[2] - y0, op[3] - x0, op[4] - y0), outline=op[5])
                if ov is not None:
                    base = tile.convert("RGBA")
                    base.alpha_composite(ov)
                    tile = base.convert("RGB")
                if k == pk:
                    tile = plate_tile(tile, self.fish[p["id"]], pen, p.get("word"))
                out[k] = tile
            return out

    def fixture_overlay(self, k, name, t):
        """The fixture's 64x64 RGBA art for this moment (the buoy floats on the current waterline)."""
        frozen = self.env.get("frozen") is not None
        if name == "lamp":
            return A.lamp(0 if frozen else int(t * 1.5) % 3)
        if name == "buoy":
            surf = self.surface_y() if k <= 5 else 20                  # only the top row has the surface
            return A.buoy(surf, 0 if frozen else int(round(1.2 * math.sin(t * 2.2))), frozen)
        if name == "gauge":
            return A.gauge(*(None if v is None else int(round(v)) for v in self.fx["sys"]))
        n, prio = self.fx["cards"]
        return A.chest(n, prio, int(t * 2) % 4 if n and not frozen else 0)

    def fixture_tile(self, tile, k, name, t):
        """A fixture's art over its window of the tank: the water, sand and any creature behind show only where
        the art is transparent, so its readable parts are never covered."""
        base = tile.convert("RGBA")
        base.alpha_composite(self.fixture_overlay(k, name, t))
        return base.convert("RGB")

    def pad_view(self, t=None, rm=None, ring=None, scale=2, pen=None):
        """The tank as the pad shows it: screens with black bars between them, the case, the ring (preview)."""
        from m18lib import ringmap as RM
        g = self.geo
        tl = self.faces(t, pen)
        kx, ky = RM.KEY_X, RM.KEY_Y
        cw = (4 * g.pitch) / (kx[4] - kx[0])
        ch = (2 * g.pitch) / (ky[2] - ky[0])
        m = 14
        W, H = int(cw) + 2 * m, int(ch) + 2 * m
        can = Image.new("RGB", (W, H), _rgb("#101014"))
        cd = ImageDraw.Draw(can)
        cd.rounded_rectangle((m - 6, m - 6, W - m + 5, H - m + 5), radius=18, fill=_rgb("#060608"))
        ox, oy = kx[0] * cw - 32 + m, ky[0] * ch - 32 + m
        for k, fc in tl.items():
            x0, y0, _, _ = g.key_rect(k)
            im = fc["image"]
            can.paste(im.resize((64, 64), Image.LANCZOS) if im.size != (64, 64) else im, (int(ox + x0), int(oy + y0)))
        for k, (bx, by) in RM.BTN_XY.items():
            x, y = bx * cw + m, by * ch + m
            cd.rounded_rectangle((x - 26, y - 10, x + 26, y + 10), radius=6, fill=_rgb("#1c1c22"),
                                 outline=_rgb("#2c2c34"))
        if rm is not None and ring is not None:
            for i, c in enumerate(ring):
                x, y = rm.xy[i]
                col = _rgb(c) if c else (20, 20, 20)
                px, py = x * cw + m, y * ch + m
                if i in RM.STRIPS:
                    cd.rectangle((px - 16, py - 3, px + 16, py + 3), fill=col)
                else:
                    cd.ellipse((px - 5, py - 5, px + 5, py + 5), fill=col)
        return can.resize((W * scale, H * scale), Image.NEAREST)

    # ---------------------------------------------------------------- ring
    def ring_frame(self, rm, t):
        from m18lib import ringmap as RM
        needs = [f for f in self.fish.values() if f.mode in ("glass", "stuck", "dead")]
        pts = [self.geo.to_case(*(f.spot or (f.x, f.y))) for f in needs]
        br = 0.35 + 0.65 * pulse(t)
        frozen = self.env.get("frozen") is not None
        night = bool(self.env.get("night"))
        out = []
        for i in range(RM.N_LEDS):
            x, y = rm.xy[i]
            s = rm.s[i] if rm.s[i] is not None else 1.0 + x
            depth = min(max(y, 0.0), 1.0)
            if frozen:
                c = _mix(ICE[0], ICE[2], depth)
                c = tuple(v * (0.75 + 0.1 * math.sin(s * 3)) for v in c)
            else:
                base = _mix("#3f8fe8", "#0f2f78", depth)
                sh = 0.62 + 0.38 * (0.5 + 0.5 * math.sin(2 * math.pi * (s * 0.75 - 0.07 * t))) \
                    * (0.6 + 0.4 * math.sin(2 * math.pi * (s * 0.25 + 0.031 * t)))
                c = tuple(v * sh * (0.35 if night else 1.0) for v in base)
            if i in RM.STRIPS:
                c = _mix("#0f2f78", "#2a5fb0", 0.5 + 0.5 * math.sin(2 * math.pi * 0.05 * t + i)) if not frozen else c
                if night:
                    c = tuple(v * 0.35 for v in c)
                if needs:
                    c = _mix(c, GOLD, 0.85 * br)
            elif pts:
                w = max(math.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * 0.16 ** 2)) for px, py in pts)
                c = _mix(c, GOLD, min(1.0, w * 1.15) * br)
            out.append(_hex(c))
        return out


# ------------------------------------------------------------------------------ name plate
class _PenHost:
    def __init__(self, cfg, base):
        self.cfg, self.base = cfg, base


def make_pen(daemon_like):
    from m18lib import tiles as T
    return T.Pen(daemon_like)


def plate_tile(tile, f, pen, word=None):
    """Label and what it is doing, on a tooltip plate across the lower part of its key."""
    if word is None:
        word = TOOL_WORD.get(f.tool) if f.mode == "swim" and f.tool else STATE_WORD.get(f.mode, "")
    col = STATE_COL.get(f.mode, "#ffffff") if word != "NUDGED" else GOLD
    lab = f.label.upper() if pen.pixel else f.label
    if pen.pixel:
        im = tile.convert("RGBA")
    else:
        im = tile.resize((192, 192), Image.NEAREST).convert("RGBA")
    pen.plate(im, (1, 30, 62, 62))
    pen.word(im, 32, 41, lab, "#ffffff", maxw=56, top=2, lines=1, maxh=14)
    pen.text(im, 32, 55, word, col, scale=1)
    return im.convert("RGB")


# ------------------------------------------------------------------------------ daemon glue
def _themed(ctx) -> bool:
    try:
        return bool(ctx.theme)
    except Exception:                                              # noqa: BLE001
        return False


def _ring_free(d) -> bool:
    cur = d.leds_owner
    if not cur:
        return True
    if cur.get("until") and time.time() >= cur["until"]:
        return True
    return cur.get("owner") in (RING_OWNER, "ring.status")


def _session(ctx, sid):
    scan = ctx.daemon.plugin_fn("sessions.scan")
    return next((x for x in scan(force=True) if x["id"] == sid), None)


def _open_session(ctx, sid) -> str:
    try:
        s = _session(ctx, sid)
        if not s:
            ctx.log(f"fishbowl: session {sid[:8]} not found to open")
            return "missing"
        r = ctx.daemon.plugin_fn("sessions.open_session")(ctx, s)
        ctx.log(f"fishbowl: open {sid[:8]} -> {r}")
        return r
    except Exception as e:                                         # noqa: BLE001
        ctx.log(f"fishbowl: open {sid[:8]} failed: {e}")
        return "error"


def _nudge(ctx, sid) -> str:
    """The calls sessions.nudge makes (clipboard + open), by name, for a session picked by id rather than slot."""
    try:
        s = _session(ctx, sid)
        if not s:
            return "missing"
        put = ctx.daemon.plugin_fn("sessions.put_clipboard")
        text = getattr(ctx.daemon.plugin_fn("sessions.nudge"), "__globals__", {}).get("NUDGE_TEXT", NUDGE_TEXT)
        ok = put(str(text))
        r = ctx.daemon.plugin_fn("sessions.open_session")(ctx, s)
        ctx.log(f"fishbowl: nudge {sid[:8]} clipboard={'ok' if ok else 'failed'} {r}")
        return r
    except Exception as e:                                         # noqa: BLE001
        ctx.log(f"fishbowl: nudge {sid[:8]} failed: {e}")
        return "error"


def _reef_path(ctx) -> Path:
    return Path(ctx.config_dir) / "state" / "fishbowl-reef.json"


def refresh(ctx, tank, st, source, now, t, night="auto", first=False, attn=()):
    """Roster + facts + environment + reef, one pass. attn: session ids an agent flashed for."""
    rp = _reef_path(ctx)
    reef = st.get("reef") or reef_load(rp)
    rows = enrich(load_rows(ctx, source, now), reef.get("acked", {}), now)
    for r in rows:
        r["attn"] = r["id"] in attn
    tank.set_env(read_env(Path(ctx.config_dir) / "state", now, night))
    tank.sync(rows, t, first=first)
    ended, tank.ended = tank.ended, []
    if reef_update(reef, rows, now, ended):
        try:
            reef_save(rp, reef)
        except OSError as e:
            _note(ctx, f"fishbowl: reef save: {e}")
        tank.set_reef(reef)
    elif st.get("reef") is None:
        tank.set_reef(reef)
    st["reef"] = reef


def _loop(ctx, st, stop, fps, source, night):
    d = ctx.daemon
    tank, pen = st["tank"], st["pen"]
    rm = None
    t0 = time.time()
    last_scan = t0
    frame_dt = 1.0 / fps
    nxt = t0
    stats = st["stats"]
    try:
        from m18lib import ringmap as RM
        while not stop.is_set():
            now = time.time()
            c0 = time.thread_time()
            if now - last_scan >= SCAN_S:
                try:
                    refresh(ctx, tank, st, source, now, now - t0, night)
                except Exception as e:                             # noqa: BLE001
                    _note(ctx, f"fishbowl: roster: {e}")
                last_scan = now
            tank.step(frame_dt, now - t0)
            faces = tank.faces(now - t0, pen)
            c1 = time.thread_time()
            if not ctx.modal_update(st["tok"], faces, RENEW_S):
                break
            c2 = time.thread_time()
            stats["frames"] += 1
            stats["cpu_draw"] = stats.get("cpu_draw", 0.0) + (c1 - c0)      # roster + simulation + drawing
            stats["cpu_push"] = stats.get("cpu_push", 0.0) + (c2 - c1)      # daemon redraw + device writes
            try:
                if _ring_free(d):
                    if rm is None or stats["frames"] % 40 == 0:
                        rm = RM.load(ctx.config_dir)
                    ctx.leds(tank.ring_frame(rm, now - t0), ttl=2.0, owner=RING_OWNER, priority=1)
            except Exception as e:                                 # noqa: BLE001
                if "dark" not in str(e):
                    _note(ctx, f"fishbowl: ring: {e}")
            stats["cpu_ring"] = stats.get("cpu_ring", 0.0) + (time.thread_time() - c2)
            nxt += frame_dt
            wait = nxt - time.time()
            if wait < 0:
                nxt, wait = time.time(), 0
            stop.wait(wait)
    finally:
        try:
            ctx.leds_release(RING_OWNER)
        except Exception:                                          # noqa: BLE001
            pass
        st["running"] = False
        st["ended_at"] = time.time()


def open(ctx, fps=FPS, source="auto", gap=None, night="auto", **_):     # noqa: A001 - the action's name
    """Action: take the pad over with the bowl. b1 leaves."""
    st = ctx.daemon.plugin_state.setdefault("fishbowl", {})
    if st.get("running") and st.get("thread") and st["thread"].is_alive():
        return "already open"
    geo = Geo(gap)
    tank = Tank(geo, seed=int(time.time()), themed=_themed(ctx))
    st.pop("reef", None)
    try:
        refresh(ctx, tank, st, source, time.time(), 0.0, night, first=True)
    except Exception as e:                                         # noqa: BLE001
        ctx.log(f"fishbowl: roster at open: {e}")
    pen = make_pen(ctx.daemon)
    stop = threading.Event()
    st.update(tank=tank, pen=pen, stop=stop, running=True, stats={"frames": 0, "opened": time.time()}, down={})
    fps = max(1.0, min(float(fps), 20.0))
    try:
        hold_ms = float(ctx.daemon.cfg["settings"].get("hold_ms", 450))
    except Exception:                                              # noqa: BLE001
        hold_ms = 450.0

    def on_key(c, key, down, t):
        if not (1 <= key <= 15):
            return
        if down:
            st["down"][key] = t
            return
        t_down = st["down"].pop(key, None)
        held = t_down is not None and (t - t_down) * 1000 >= hold_ms
        what, fid = tank.tap(key, tank.t, held=held)
        if what == "open":
            _open_session(c, fid)
        elif what == "nudge":
            _nudge(c, fid)
        elif what == "ack":
            _ack(c, st, tank, fid)
        c.log(f"fishbowl: tap {key}{' held' if held else ''} -> {what} {fid[:8] if fid else ''}")

    st["tok"] = ctx.modal(tank.faces(0.0, pen), on_key=on_key, timeout=RENEW_S, yields=True)
    th = threading.Thread(target=_loop, args=(ctx, st, stop, fps, source, night), daemon=True, name="fishbowl")
    st["thread"] = th
    th.start()
    ctx.log(f"fishbowl: open, {len(tank.fish)} creature(s), gap {geo.gap}px, {fps:g} fps")
    return "open"


def _ack(ctx, st, tank, fid):
    reef = st.get("reef") or reef_load(_reef_path(ctx))
    f = tank.fish.get(fid)
    reef.setdefault("acked", {})[fid] = (f.row.get("err_key") if f else None)
    try:
        reef_save(_reef_path(ctx), reef)
    except OSError:
        pass


def close(ctx, **_):
    st = ctx.daemon.plugin_state.get("fishbowl") or {}
    if st.get("stop"):
        st["stop"].set()
    if st.get("tok") is not None:
        ctx.daemon.modal_end("fishbowl", tok=st["tok"])


# ------------------------------------------------------------------------------ live key + idle entry
def _last_press(d) -> float:
    with d.ev_cv:
        evs = [e["t"] for e in d.events if e.get("type") == "press"]
    return max(evs) if evs else 0.0


def _idle_loop(box, st, stop):
    while not stop.wait(5.0):
        try:
            ctx = box["ctx"]
            d = ctx.daemon
            mins = float(box.get("idle_min") or 0)
            if mins <= 0:
                continue
            fb = d.plugin_state.get("fishbowl") or {}
            if fb.get("running") or d.modal is not None or d.dark_key is not None:
                continue
            ref = max(_last_press(d), st.get("started", 0.0), fb.get("ended_at", 0.0))
            if time.time() - ref >= mins * 60:
                ctx.log(f"fishbowl: idle {mins:g} min -> open")
                open(ctx, **(box.get("args") or {}))
        except Exception as e:                                     # noqa: BLE001
            try:
                box["ctx"].log(f"fishbowl idle: {e}")
            except Exception:                                      # noqa: BLE001
                pass
            time.sleep(25)


def face(ctx, idle_min=0, reef=True, **args):
    """Live face for the bowl's key: a small bowl, one fish per session (gold = needs you). Also keeps the reef
    growing while the bowl is closed (every 60 s) and, with idle_min > 0, runs the idle watcher."""
    st = ctx.daemon.plugin_state.setdefault("fishbowl.idle", {})
    st.setdefault("started", time.time())
    box = st.setdefault("box", {})
    box.update(ctx=ctx, idle_min=idle_min,
               args={k: v for k, v in args.items() if k in ("fps", "source", "gap", "night")})
    th = st.get("thread")
    live = not getattr(ctx, "preview", False)     # a preview draws the face only: no watcher, no reef write
    if live and float(idle_min or 0) > 0 and not (th and th.is_alive() and st.get("version") == _VERSION):
        if st.get("stop"):
            st["stop"].set()
        st["stop"] = stop = threading.Event()
        st["version"] = _VERSION
        st["thread"] = threading.Thread(target=_idle_loop, args=(box, st, stop), daemon=True, name="fishbowl.idle")
        st["thread"].start()
    fb = ctx.daemon.plugin_state.get("fishbowl") or {}
    now = time.time()
    if live and reef and not fb.get("running") and now - st.get("reef_t", 0) > 60:
        st["reef_t"] = now
        try:
            rows = enrich(load_rows(ctx, args.get("source", "auto"), now), {}, now)
            rp = _reef_path(ctx)
            r = reef_load(rp)
            if reef_update(r, rows, now):
                reef_save(rp, r)
        except Exception as e:                                     # noqa: BLE001
            _note(ctx, f"fishbowl: reef: {e}")
    rows = _summary_rows(Path(ctx.config_dir) / "state")
    modes = [mode_for(r) for r in rows]
    key = tuple(modes)
    cache = ctx.state.setdefault("fishbowl.face", {})
    if cache.get("k") != key:
        cache["k"], cache["img"] = key, bowl_icon(modes)
    return {"image": cache["img"]}


def bowl_icon(modes) -> Image.Image:
    im = Image.new("RGB", (64, 64), _rgb("#16161e"))
    d = ImageDraw.Draw(im)
    d.ellipse((6, 8, 57, 59), fill=_rgb("#8fb8e8"))
    d.ellipse((8, 10, 55, 57), fill=_rgb(WATER[1]))
    d.rectangle((6, 8, 57, 20), fill=_rgb("#16161e"))
    d.rectangle((14, 18, 49, 21), fill=_rgb("#cfe9ff"))
    d.rectangle((14, 50, 49, 56), fill=_rgb(SAND[0]))
    d.rectangle((20, 34, 21, 50), fill=_rgb(KELP[0]))
    d.rectangle((43, 30, 44, 50), fill=_rgb(KELP[1]))
    spots = [(26, 28), (36, 36), (30, 42), (40, 26), (24, 38), (34, 30), (42, 42), (28, 33)]
    for i, m in enumerate([m for m in modes if m][:8]):
        x, y = spots[i]
        y = 47 if m == "rest" else 24 if m == "glass" else y
        c = _rgb(STATE_COL.get(m, "#55ffff"))
        d.rectangle((x, y, x + 4, y + 2), fill=c)
        d.rectangle((x - 2, y - 1, x - 1, y + 3), fill=c)
        d.point((x + 3, y), fill=(16, 16, 16))
    return im.resize((192, 192), Image.NEAREST)


# ------------------------------------------------------------------------------ page mode: the tank as a normal page
# Each key of the page that shows the tank is `live: {plugin: fishbowl.tile}`; a fixture adds `args: {fixture: lamp|
# buoy|gauge|chest}` and keeps its own action. One engine thread steps one shared world at PAGE_FPS, renders it once,
# and for each tank key whose pixels changed puts the new face straight into the daemon's live faces and redraws only
# those keys. `tile` (run by the daemon every `interval`) returns that same cached dict, so the daemon sees no change
# and does nothing; it only keeps the engine alive. The engine sleeps while the page is not shown, in dark mode and
# under any takeover (cards, agents, mixer, the modal bowl).
PAGE_FPS = 30.0
RING_S = 0.125               # ring frames while the tank shows (the ttl on each is 2 s)
SYS_S = 1.5
TILE_PLUGIN = "fishbowl.tile"
_ENGINE_LOCK = threading.Lock()
_LAYOUTS: dict = {}


def page_layout(cfg, page):
    """(tank keys, {key: fixture}, opts) for a page: the keys whose live plugin is fishbowl.tile."""
    ck = (id(cfg), page)
    v = _LAYOUTS.get(ck)
    if v is not None:
        return v
    from m18lib import config as C
    keys, fx, opts = set(), {}, {}
    try:
        kk = C.resolved_keys(cfg, page)
    except Exception:                                              # noqa: BLE001
        kk = {}
    for k, kd in kk.items():
        lv = kd.get("live") if isinstance(kd, dict) else None
        if not (isinstance(k, int) and 1 <= k <= 15 and isinstance(lv, dict) and lv.get("plugin") == TILE_PLUGIN):
            continue
        keys.add(k)
        a = lv.get("args") or {}
        if a.get("fixture") in A.READABLE:
            fx[k] = a["fixture"]
        for o in ("fps", "source", "night", "gap"):
            if o in a:
                opts.setdefault(o, a[o])
    if len(_LAYOUTS) > 16:
        _LAYOUTS.clear()
    v = _LAYOUTS[ck] = (frozenset(keys), fx, opts)
    return v


def _page_state(d):
    return d.plugin_state.setdefault("fishbowl.page", {})


_CODE = object()             # one per load of this file: a hot reload hands the page to an engine running the new code


def _ensure_engine(ctx):
    d = ctx.daemon
    st = _page_state(d)
    st["ctx"] = ctx
    th = st.get("thread")
    if th and th.is_alive() and st.get("version") == _VERSION and st.get("code") is _CODE:
        return st
    with _ENGINE_LOCK:
        th = st.get("thread")
        if th and th.is_alive() and st.get("version") == _VERSION and st.get("code") is _CODE:
            return st
        if st.get("stop"):
            st["stop"].set()
        stop = threading.Event()
        st.update(stop=stop, version=_VERSION, code=_CODE, faces={}, dig={}, sys=(None, None, None), active=False,
                  stats={"frames": 0, "pushed": 0, "cpu_draw": 0.0, "cpu_push": 0.0, "cpu_ring": 0.0,
                         "active_s": 0.0, "started": time.time()})
        st.pop("tank", None)
        st.pop("reef", None)
        st["thread"] = threading.Thread(target=_page_loop, args=(d, st, stop), daemon=True, name="fishbowl.page")
        st["thread"].start()
        threading.Thread(target=_sys_loop, args=(d, st, stop), daemon=True, name="fishbowl.sys").start()
    return st


def _sys_loop(d, st, stop):
    """CPU, RAM and GPU for the filter fixture (the same sources as plugins/sysstats.py), only while the tank shows."""
    try:
        import psutil
    except ImportError:
        psutil = None
    gpu = None
    while not stop.wait(SYS_S):
        if not st.get("active") or not st.get("want_sys"):
            continue
        try:
            cpu = psutil.cpu_percent(None) if psutil else None
            ram = psutil.virtual_memory().percent if psutil else None
            g = None
            try:
                gpu = gpu or d.plugin_fn("sysstats._gpu")
                g = gpu()[0]
            except Exception:                                      # noqa: BLE001
                gpu = None
            st["sys"] = (cpu, ram, g)
        except Exception:                                          # noqa: BLE001
            pass


def _agents_view(ctx, now):
    """(docs, attention ids) from plugins/agents.py's own state; keeps its rules and ring running."""
    d = ctx.daemon
    try:
        d.plugin_fn("agents.tick")(ctx)
    except Exception:                                              # noqa: BLE001
        return {}, set()
    P = d.plugin_state.get("agents") or {}
    return dict(P.get("docs") or {}), {sid for sid, until in (P.get("attn") or {}).items() if until > now}


def _cards_now(d):
    try:
        cs = d.cards.ordered()
    except Exception:                                              # noqa: BLE001
        return (0, 1)
    return (len(cs), max([int(c.get("priority", 1)) for c in cs] or [1]))


def _page_loop(d, st, stop):
    stats = st["stats"]
    tank = pen = rm = None
    lay_seen = None
    owned = False
    last_scan = last_ring = rm_at = 0.0
    t0 = time.time()
    nxt = t0
    try:
        from m18lib import ringmap as RM
        while not stop.is_set():
            ds = getattr(d, "stop", None)
            if ds is not None and ds.is_set():                     # the daemon is shutting down
                break
            frame_dt = 1.0 / PAGE_FPS
            try:
                page, cfg = d.page, d.cfg
                keys, fx, opts = page_layout(cfg, page) if cfg else (frozenset(), {}, {})
                active = bool(keys) and d.dark_key is None and d.modal is None
                st["active"] = active
                if not active:
                    if owned:
                        d.leds_release(RING_OWNER, "fishbowl: page hidden")
                        owned = False
                    stop.wait(0.25)
                    nxt = time.time()
                    continue
                ctx = st["ctx"]
                fps = max(1.0, min(float(opts.get("fps", PAGE_FPS)), 60.0))
                frame_dt = 1.0 / fps
                now = time.time()
                c0 = time.thread_time()
                gap = opts.get("gap")
                if tank is None or (gap is not None and int(gap) != tank.geo.gap):
                    tank = Tank(Geo(gap), seed=int(now), themed=True)
                    tank.set_layout(keys, fx)
                    lay_seen = (page, keys, tuple(sorted(fx.items())))
                    docs, attn = _agents_view(ctx, now)
                    tank.set_docs(docs, attn)
                    st.pop("reef", None)
                    try:
                        refresh(ctx, tank, st, opts.get("source", "auto"), now, now - t0, opts.get("night", "auto"),
                                first=True, attn=attn)
                    except Exception as e:                         # noqa: BLE001
                        _note(ctx, f"fishbowl page: roster: {e}")
                    pen = make_pen(d)
                    st["tank"] = tank
                    last_scan = now
                lk = (page, keys, tuple(sorted(fx.items())))
                if lk != lay_seen:
                    tank.set_layout(keys, fx)
                    lay_seen = lk
                st["want_sys"] = "gauge" in fx.values()
                if now - last_scan >= SCAN_S:
                    last_scan = now
                    docs, attn = _agents_view(ctx, now)
                    tank.set_docs(docs, attn)
                    tank.set_fixture_data(cards=_cards_now(d))
                    try:
                        refresh(ctx, tank, st, opts.get("source", "auto"), now, now - t0, opts.get("night", "auto"),
                                attn=attn)
                    except Exception as e:                         # noqa: BLE001
                        _note(ctx, f"fishbowl page: roster: {e}")
                tank.set_fixture_data(sys_=st.get("sys"))
                tank.step(frame_dt, now - t0)
                faces = tank.dirty_faces(now - t0, pen)           # only the keys whose picture changed
                c1 = time.thread_time()
                changed = {}
                for k in sorted(faces):
                    if k not in keys:
                        continue
                    im = faces[k]
                    dg = hash(im.tobytes())
                    key = (page, k)
                    if st["dig"].get(key) != dg:
                        st["dig"][key] = dg
                        fc = {"image": im, "_fid": ("fishbowl", page, k, dg)}
                        st["faces"][key] = fc
                        d.live_faces[key] = fc
                        changed[k] = (im, dg)
                if changed:
                    blit = getattr(d, "blit", None)
                    if blit is not None:                           # straight to the pad: no face pipeline per frame
                        blit(page, changed, "fishbowl")
                    elif d.page == page and d.modal is None and d.dark_key is None:
                        d.redraw(keys=sorted(changed))
                c2 = time.thread_time()
                stats["frames"] += 1
                stats["pushed"] += len(changed)
                stats["cpu_draw"] += c1 - c0
                stats["cpu_push"] += c2 - c1
                stats["active_s"] += frame_dt
                # ring = the waterline; any agent ring (progress --ring, a flash) and any other owner wins
                P = d.plugin_state.get("agents") or {}
                agent_ring = bool(P.get("ring_flash")) or any((x.get("progress") or {}).get("ring")
                                                              for x in tank.docs.values())
                if agent_ring or not _ring_free(d):
                    if owned and (d.leds_owner or {}).get("owner") == RING_OWNER:
                        d.leds_release(RING_OWNER, "fishbowl: yields to agents")
                    owned = False
                elif owned and now - last_ring < RING_S:
                    pass                                           # the waterline shimmers slowly: 8 frames/s is plenty
                else:
                    last_ring = now
                    if rm is None or now - rm_at > 10:
                        rm, rm_at = RM.load(ctx.config_dir), now
                    try:
                        owned = bool(d.leds_set(tank.ring_frame(rm, now - t0), 2.0, None, RING_OWNER, 1))
                    except Exception:                              # noqa: BLE001 - dark mode refuses frames
                        owned = False
                stats["cpu_ring"] += time.thread_time() - c2
            except Exception as e:                                 # noqa: BLE001
                try:
                    d.log(f"fishbowl page: {e}")
                except Exception:                                  # noqa: BLE001
                    pass
                stop.wait(1.0)
            nxt += frame_dt
            wait = nxt - time.time()
            if wait < 0:
                nxt, wait = time.time(), 0
            elif wait > 0:
                time.sleep(wait)                                   # high-resolution on Windows; Event.wait is ~15 ms
    finally:
        if owned:
            try:
                d.leds_release(RING_OWNER, "fishbowl: engine stopped")
            except Exception:                                      # noqa: BLE001
                pass
        st["active"] = False
        stop.set()                                                 # takes the stats thread down with the engine


_WATER_TILE = None


def _water_tile():
    global _WATER_TILE
    if _WATER_TILE is None:
        _WATER_TILE = Image.new("RGB", (64, 64), _rgb(WATER[3]))
    return _WATER_TILE


def tile(ctx, fixture=None, **_):
    """Live function for every key of the tank page (fixtures pass `fixture`). Starts the engine and returns the
    face it last pushed for this key (the same dict object, so the daemon sees no change)."""
    if getattr(ctx, "preview", False):
        return _preview_tile(ctx)
    st = _ensure_engine(ctx)
    f = st["faces"].get((ctx.page, ctx.key))
    return f if f is not None else {"image": _water_tile()}


def _preview_tile(ctx):
    """A still of the tank for a preview render: no engine, no ring, no reef write."""
    d = ctx.daemon
    st = d.plugin_state.setdefault("fishbowl.preview", {})
    now = time.time()
    if now - st.get("t", 0) > 5 or st.get("page") != ctx.page:
        keys, fx, opts = page_layout(d.cfg, ctx.page)
        tank = Tank(Geo(opts.get("gap")), seed=7)
        tank.set_layout(keys or None, fx)
        try:
            P = d.plugin_state.get("agents") or {}
            rows = enrich(load_rows(ctx, opts.get("source", "auto"), now), {}, now)
            tank.set_env(read_env(Path(ctx.config_dir) / "state", now, opts.get("night", "auto")))
            tank.set_docs(P.get("docs") or {})
            tank.sync(rows, 0.0, first=True)
            tank.set_reef(reef_load(_reef_path(ctx)))
        except Exception as e:                                     # noqa: BLE001
            ctx.log(f"fishbowl preview: {e}")
        tank.set_fixture_data(cards=_cards_now(d))
        for i in range(10):
            tank.step(0.2, i * 0.2)
        st.update(t=now, page=ctx.page, faces=tank.faces(2.0, make_pen(d)))
    return {"image": st["faces"][ctx.key]["image"]}


def tap(ctx, **_):
    """Action for a water key: a creature on (or half behind the bar beside) it -> its name plate, a second tap
    opens the session; a belly-up one is acknowledged; one that flashed counts as seen; one with buttons parked
    opens the agents takeover. Empty water: food."""
    d = ctx.daemon
    st = _page_state(d)
    tank = st.get("tank")
    if tank is None or not ctx.key:
        return None
    fid = tank.hit(ctx.key)
    if fid:
        P = d.plugin_state.get("agents") or {}
        doc = (P.get("docs") or {}).get(fid)
        if (P.get("attn") or {}).get(fid, 0) > time.time():
            try:
                d.plugin_fn("agents._seen")(d, ctx, fid)
            except Exception as e:                                 # noqa: BLE001
                ctx.log(f"fishbowl: seen {fid[:8]}: {e}")
            with tank.lock:
                tank.attn.discard(fid)
                f = tank.fish.get(fid)
                if f is not None:
                    f.row["attn"] = f.attn = False
                    f.mode, f.mode_t, f.target = mode_for(f.row) or f.mode, tank.t, None
                    tank._assign_spots()
        if doc and doc.get("buttons"):
            ctx.log(f"fishbowl: tap {ctx.key} -> agent buttons {fid[:8]}")
            return d.plugin_fn("agents._takeover")(ctx, ("agent", fid, None))
    what, fid = tank.tap(ctx.key, tank.t)
    if what == "open":
        _open_session(ctx, fid)
    elif what == "ack":
        _ack(ctx, st, tank, fid)
    ctx.log(f"fishbowl: tap {ctx.key} -> {what} {fid[:8] if fid else ''}")
    return None


def hold(ctx, **_):
    """Hold action for a water key: on a creature = nudge it (clipboard + open, like sessions.nudge); on empty water
    = every agent that has posted (agents.deck), or food when none has."""
    d = ctx.daemon
    st = _page_state(d)
    tank = st.get("tank")
    if tank is None or not ctx.key:
        return None
    fid = tank.hit(ctx.key)
    if fid:
        tank.tap(ctx.key, tank.t, held=True)
        ctx.log(f"fishbowl: hold {ctx.key} -> nudge {fid[:8]}")
        return _nudge(ctx, fid)
    if (d.plugin_state.get("agents") or {}).get("docs"):
        return d.plugin_fn("agents.deck")(ctx)
    tank.feed(ctx.key, tank.t)
    return None
