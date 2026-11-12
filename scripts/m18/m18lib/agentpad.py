"""The agents store: what each AI agent session has put on the pad, and the fair-use rules.

Shared by scripts/m18/pad.py (the agents' CLI, the only writer of an agent's own file) and
plugins/agents.py (the daemon side, the only reader that draws anything). No PIL here.

    <config dir>/state/agents/<sid>.json         one agent's things (written by pad.py, atomic)
    <config dir>/state/agents/<sid>.inbox/*.json  presses of that agent's buttons (written by the plugin)
    <config dir>/state/agents/_pad.json           what the plugin did: flashes, ring use, refusals, purges

The rules live in QUOTA and are applied twice: pad.py refuses with a clear message, and the plugin
applies them again when it reads the files, so a hand-written file cannot get around them.
Nothing in a doc is ever run: buttons only produce events, faces are pixel grids, text is limited to
the pixel font's letters.
"""
from __future__ import annotations

import json
import os
import re
import time
import zlib
from contextlib import contextmanager
from pathlib import Path

QUOTA = {
    "buttons": 3,              # parked buttons per agent
    "cards": 2,                # open cards per agent (feed agent:<sid>)
    "status_chars": 8,
    "label_chars": 6,
    "button_chars": 8,
    "ttl_default": 2 * 3600,   # status, progress and buttons last this long unless given --ttl
    "ttl_max": 8 * 3600,
    "doc_idle": 8 * 3600,      # a whole doc with no write for this long is dropped
    "ring_s_per_hour": 600,    # seconds of ring time per agent in any rolling hour
    "flash_gap_s": 120,        # one attention flash per agent every 2 min ...
    "flash_per_hour": 6,       # ... and at most 6 an hour
    "pad_flash_gap_s": 15,     # pad-wide: no two flashes (from any agents) closer than this
    "attention_s": 600,        # a flash keeps the key's border pulsing this long, or until the user taps it
    "frames": 4,               # face animation frames
    "fps": 4,
    "doc_bytes": 64 * 1024,
    "dead_grace_s": 15 * 60,   # an agent that is not a live session, has no live waiter and wrote nothing
}                              # for this long is purged with everything it posted

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
BTN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TEXT_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-./% ")
# the session keys' 16x16 palette (plugins/sessions.py PALETTE), plus up to 6 agent colours as digits 1-6
BASE_PALETTE = {"K": "#161616", "W": "#f4f4f4", "G": "#b4b4b4", "S": "#7c7c7c", "D": "#454545", "R": "#cc3a30",
                "O": "#ee8c2c", "Y": "#f2d33a", "L": "#72cc40", "E": "#2f7d2c", "C": "#3fcbdc", "B": "#3a5bd2",
                "P": "#8e3fca", "N": "#7d4c2a", "T": "#dbb47e"}
EXTRA = "123456"
TONES = {"info": "#ffffff", "ok": "#55ff55", "warn": "#ffcc33", "err": "#ff5555"}
AGENT_COLORS = ["#3fcbdc", "#72cc40", "#ee8c2c", "#8e3fca", "#3a5bd2", "#f2d33a", "#dbb47e", "#e0569b"]


class PadError(Exception):
    """A refused request. code: 2 bad input, 4 quota or rate limit, 5 not found."""

    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


# ------------------------------------------------------------------ where
def home() -> Path:
    return Path(os.environ.get("M18_HOME") or Path.home() / "tools" / "m18")


def store_dir(base: Path) -> Path:
    return Path(base) / "state" / "agents"


def check_id(sid: str) -> str:
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise PadError(f"agent id {sid!r}: 1-80 letters, digits, '-' or '_'")
    return sid


def doc_path(base, sid) -> Path:
    return store_dir(base) / f"{check_id(sid)}.json"


def inbox_dir(base, sid) -> Path:
    return store_dir(base) / f"{check_id(sid)}.inbox"


def ledger_path(base) -> Path:
    return store_dir(base) / "_pad.json"


def read_json(path: Path, limit: int | None = None):
    try:
        p = Path(path)
        if limit and p.stat().st_size > limit:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    for i in range(20):                      # Windows: a reader holding the file makes replace fail briefly
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02 * (i + 1))
    tmp.unlink(missing_ok=True)
    raise OSError(f"could not write {path}")


@contextmanager
def locked(base, sid, timeout=5.0):
    """One writer per agent file at a time (a background `pad wait` and a foreground `pad status`)."""
    lk = store_dir(base) / f".{check_id(sid)}.lock"
    lk.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + timeout
    while True:
        try:
            fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lk.stat().st_mtime > 10:
                    lk.unlink(missing_ok=True)       # a crashed writer's lock
                    continue
            except OSError:
                pass
            if time.time() > end:
                raise PadError("another pad command for this agent is still writing; try again", 4)
            time.sleep(0.03)
    try:
        yield
    finally:
        lk.unlink(missing_ok=True)


# ------------------------------------------------------------------ validation
def clean_text(s, n, what) -> str:
    if not isinstance(s, str):
        raise PadError(f"{what}: text")
    t = " ".join(s.upper().split())
    bad = sorted(set(t) - TEXT_OK)
    if bad:
        raise PadError(f"{what}: only A-Z 0-9 + - . / % and spaces show on the key (not {''.join(bad)!r})")
    if not t:
        raise PadError(f"{what}: empty")
    if len(t) > n:
        raise PadError(f"{what}: at most {n} characters on a 64 px key (got {len(t)}: {t!r})")
    return t


def clean_ttl(v) -> float:
    if v is None:
        return float(QUOTA["ttl_default"])
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise PadError("ttl: seconds")
    if not (f > 0) or f != f:
        raise PadError("ttl: positive seconds")
    return min(f, float(QUOTA["ttl_max"]))


def clean_color(c, what="color"):
    if c is None:
        return None
    if not isinstance(c, str) or not HEX_RE.match(c):
        raise PadError(f"{what}: #rrggbb")
    return c.lower()


def clean_grid(rows, palette_keys, what="face") -> list[str]:
    legal = set(".") | set(BASE_PALETTE) | set(palette_keys)
    if not isinstance(rows, list) or len(rows) != 16:
        raise PadError(f"{what}: exactly 16 rows of 16 characters")
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, str) or len(r) != 16:
            raise PadError(f"{what}: row {i + 1} must be 16 characters (got {len(r) if isinstance(r, str) else r!r})")
        bad = sorted(set(r) - legal)
        if bad:
            raise PadError(f"{what}: row {i + 1} uses {bad}; legal: '.' {''.join(BASE_PALETTE)} and your palette digits")
        out.append(r)
    if sum(c != "." for r in out for c in r) < 30:
        raise PadError(f"{what}: nearly empty; draw at least 30 pixels")
    return out


def clean_face(face: dict) -> dict:
    if not isinstance(face, dict):
        raise PadError("face: object")
    pal = face.get("palette") or {}
    if not isinstance(pal, dict) or any(k not in EXTRA for k in pal):
        raise PadError(f"face.palette: up to 6 colours keyed {','.join(EXTRA)}")
    pal = {k: clean_color(v, f"face.palette.{k}") for k, v in pal.items()}
    frames = face.get("frames") or ([face["rows"]] if face.get("rows") else None)
    if not frames:
        raise PadError("face: rows (16 x 16 characters) or frames (a list of those)")
    if len(frames) > QUOTA["frames"]:
        raise PadError(f"face: at most {QUOTA['frames']} frames")
    frames = [clean_grid(f, pal, f"face frame {i + 1}") for i, f in enumerate(frames)]
    fps = face.get("fps", 2)
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not (0 < fps <= QUOTA["fps"]):
        raise PadError(f"face.fps: 0-{QUOTA['fps']}")
    out = {"frames": frames, "fps": float(fps), "palette": pal}
    if face.get("label"):
        out["label"] = clean_text(face["label"], QUOTA["label_chars"], "face.label")
    if face.get("color"):
        out["color"] = clean_color(face["color"])
    return out


def clean_button(b: dict, now: float) -> dict:
    if not isinstance(b, dict):
        raise PadError("button: object")
    bid = b.get("id")
    if not isinstance(bid, str) or not BTN_RE.match(bid):
        raise PadError("button id: 1-24 of a-z 0-9 - _")
    out = {"id": bid, "label": clean_text(b.get("label") or bid, QUOTA["button_chars"], "button label"),
           "sticky": bool(b.get("sticky")), "created": float(b.get("created") or now)}
    if b.get("color"):
        out["color"] = clean_color(b["color"])
    out["until"] = float(b["until"]) if b.get("until") else now + clean_ttl(b.get("ttl"))
    return out


def feed_of(sid: str) -> str:
    """Card feed for an agent's cards (the card store caps feeds at 40 chars)."""
    return "ag:" + sid if len(sid) <= 37 else "ag:" + sid[:28] + "%08x" % zlib.crc32(sid.encode())


def sid_of_feed(feed: str, known=()) -> str | None:
    for s in known:
        if feed_of(s) == feed:
            return s
    s = feed[3:] if feed.startswith("ag:") else ""
    return s if ID_RE.match(s) and feed_of(s) == feed else None


def color_for(sid: str, doc: dict | None = None) -> str:
    f = (doc or {}).get("face") or {}
    return f.get("color") or AGENT_COLORS[zlib.crc32(sid.encode()) % len(AGENT_COLORS)]


# ------------------------------------------------------------------ reading a doc as the plugin sees it
def effective(doc: dict, now: float) -> dict | None:
    """The doc with expired, malformed or over-quota parts dropped. None = nothing left to show.
    Only known fields survive: anything else in the file (a 'key', an 'action', 'run') is ignored."""
    if not isinstance(doc, dict):
        return None
    try:
        sid = check_id(doc.get("id"))
    except PadError:
        return None
    upd = float(doc.get("updated") or 0)
    if now - upd > QUOTA["doc_idle"]:
        return None
    out = {"id": sid, "agent": str(doc.get("agent") or "agent")[:12], "updated": upd,
           "created": float(doc.get("created") or upd), "title": ""}
    if isinstance(doc.get("title"), str):
        out["title"] = doc["title"][:80]
    st = doc.get("status")
    if isinstance(st, dict) and float(st.get("until") or 0) > now:
        try:
            out["status"] = {"text": clean_text(st.get("text"), QUOTA["status_chars"], "status"),
                             "tone": st.get("tone") if st.get("tone") in TONES else "info",
                             "until": float(st["until"])}
        except PadError:
            pass
    pg = doc.get("progress")
    if isinstance(pg, dict) and float(pg.get("until") or 0) > now:
        try:
            v = min(max(float(pg.get("value")), 0.0), 1.0)
            out["progress"] = {"value": v, "ring": bool(pg.get("ring")), "until": float(pg["until"]),
                               "set": float(pg.get("set") or upd)}
        except (TypeError, ValueError):
            pass
    if isinstance(doc.get("face"), dict):
        try:
            out["face"] = clean_face(doc["face"])
        except PadError:
            pass
    btns = []
    for b in doc.get("buttons") or []:
        try:
            cb = clean_button(b, now)
        except PadError:
            continue
        if cb["until"] > now and cb["id"] not in {x["id"] for x in btns}:
            btns.append(cb)
    btns.sort(key=lambda b: b["created"])
    out["buttons"] = btns[:QUOTA["buttons"]]          # oldest win: a flood can't push the user's parked one off
    fl = doc.get("flash")
    if isinstance(fl, dict) and isinstance(fl.get("n"), int):
        out["flash"] = {"n": fl["n"], "at": float(fl.get("at") or 0)}
    w = doc.get("waiters")
    out["waiters"] = {str(k): float(v) for k, v in w.items() if str(k).isdigit()} if isinstance(w, dict) else {}
    out["asks"] = [{"card": a["card"][:120], "until": float(a["until"])} for a in (doc.get("asks") or [])
                   if isinstance(a, dict) and isinstance(a.get("card"), str)
                   and isinstance(a.get("until"), (int, float)) and a["until"] > now][:QUOTA["cards"]]
    keep = out.get("status") or out.get("progress") or out.get("face") or out["buttons"] or out["asks"] or \
        (out.get("flash") and now - out["flash"]["at"] < QUOTA["attention_s"])
    return out if keep else None


# ------------------------------------------------------------------ fair-use ledger (written by the plugin)
def ring_used(ledger: dict, sid: str, now: float) -> float:
    """Seconds of ring time `sid` had in the last hour."""
    spans = (ledger.get("ring") or {}).get(sid) or []
    t0 = now - 3600
    return sum(max(0.0, min(b, now) - max(a, t0)) for a, b in spans if b > t0)


def flash_refusal(ledger: dict, sid: str, now: float) -> str | None:
    """Why a flash by `sid` now would be refused, or None."""
    mine = [t for t in (ledger.get("flashes") or {}).get(sid, []) if now - t < 3600]
    if mine and now - max(mine) < QUOTA["flash_gap_s"]:
        return f"rate: one flash per {QUOTA['flash_gap_s']} s per agent (next in {int(QUOTA['flash_gap_s'] - (now - max(mine))) + 1} s)"
    if len(mine) >= QUOTA["flash_per_hour"]:
        return f"rate: at most {QUOTA['flash_per_hour']} flashes an hour per agent"
    last_any = max([t for ts in (ledger.get("flashes") or {}).values() for t in ts] or [0])
    if now - last_any < QUOTA["pad_flash_gap_s"]:
        return f"rate: another agent flashed {int(now - last_any)} s ago (pad-wide gap {QUOTA['pad_flash_gap_s']} s)"
    return None


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ inbox
def inbox_put(base, sid, ev: dict) -> str:
    d = inbox_dir(base, sid)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}.json"
    write_json(d / name, ev)
    return name


def inbox_list(base, sid) -> list[tuple[Path, dict]]:
    d = inbox_dir(base, sid)
    out = []
    try:
        names = sorted(p for p in d.iterdir() if p.suffix == ".json" and not p.name.startswith("."))
    except OSError:
        return out
    for p in names:
        ev = read_json(p)
        if isinstance(ev, dict):
            out.append((p, ev))
    return out
