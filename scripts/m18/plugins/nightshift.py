"""Night shift key: tap when you leave the PC with agents running, tap again when you are back.

    13: {live: {plugin: nightshift.face, interval: 2},
         action: {plugin: nightshift.tap},
         hold: {plugin: nightshift.hold}}

Tap (not away) = away: writes <state>/away.json {"since": epoch, "by": "pad"} (agents read it: references/nightshift.md),
keeps the PC awake (SetThreadExecutionState from this plugin's own thread; no power-plan change) and tracks the session
keys from <state>/sessions-summary.json. The key shows a moon over one count at a time (2 s each, zeros skipped):
finished since `since` (check), waiting now (?), silent now (z).
Tap (away) = back: deletes away.json, releases the wake lock, and shows the tally under a sun for 10 minutes.
Hold = the same as tap, and on going away also turns the monitors off (SC_MONITORPOWER 2).
State survives a daemon restart: away.json and <state>/nightshift.json are read again on the next live tick.
M18_NIGHTSHIFT_DRY=1 logs the monitors-off call to <state>/nightshift-monitor.log instead of making it.
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

_VERSION = os.path.getmtime(__file__)
TALLY_KEEP = 10 * 60          # the tally shows this long after coming back
FPS = 0.5                     # away/back faces show one count per frame, 2 s each
TICK = 5.0                    # seconds between reads of sessions-summary.json while away
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
KEY = "nightshift.thread"


# ------------------------------------------------------------------ files
def state_dir(ctx=None) -> Path:
    m = os.environ.get("M18_HOME")
    if m:
        return Path(m) / "state"
    return Path(getattr(ctx, "config_dir", None) or Path.home() / "tools" / "m18") / "state"


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def away_info(sd: Path):
    """The away flag ({since, by}) or None."""
    d = _read(sd / "away.json")
    return d if isinstance(d, dict) and d.get("since") else None


def summary_rows(sd: Path) -> list:
    d = _read(sd / "sessions-summary.json") or {}
    return [r for r in d.get("sessions") or [] if isinstance(r, dict) and r.get("id")]


# ------------------------------------------------------------------ tally (pure)
def start_track(rows, since) -> dict:
    """Tracking record at the moment of going away: current states are the baseline (not counted)."""
    return {"since": since, "done": [], "last": {r["id"]: r.get("state") for r in rows}}


def step(track: dict, rows) -> bool:
    """Feed one summary read. A session counts as done when it is seen turning finished after `since`
    (it stays counted after it leaves the pad). Returns True when the record changed."""
    changed = False
    last = track.setdefault("last", {})
    done = track.setdefault("done", [])
    for r in rows:
        sid, st = r["id"], r.get("state")
        if st == "finished" and last.get(sid) != "finished" and sid not in done:
            done.append(sid)
            changed = True
        if last.get(sid) != st:
            last[sid] = st
            changed = True
    return changed


def counts(track: dict, rows) -> dict:
    return {"done": len(track.get("done") or []),
            "waiting": sum(r.get("state") == "waiting" for r in rows),
            "silent": sum(bool(r.get("silent")) for r in rows)}


# ------------------------------------------------------------------ wake lock + monitors
def _set_state(flags) -> int:
    if os.name != "nt":
        return 0
    f = ctypes.windll.kernel32.SetThreadExecutionState
    f.argtypes, f.restype = [ctypes.c_uint32], ctypes.c_uint32
    return int(f(flags))


def monitors_off(sd: Path, log=None):
    """Broadcast SC_MONITORPOWER 2 (off) from a short thread; dry mode only logs it."""
    if os.environ.get("M18_NIGHTSHIFT_DRY"):
        try:
            with open(sd / "nightshift-monitor.log", "a", encoding="utf-8") as f:
                f.write(f"{time.time():.0f} monitors off (dry)\n")
        except OSError:
            pass
        return "dry"
    if os.name != "nt":
        return "unsupported"

    def go():
        time.sleep(0.6)                                       # let the key-up event pass first
        res = ctypes.c_size_t()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x0112, 0xF170, 2, 0x0002, 2000, ctypes.byref(res))
    threading.Thread(target=go, name="nightshift-monitors", daemon=True).start()
    return "sent"


# ------------------------------------------------------------------ the away thread (holds the wake lock)
def _loop(st: dict, stop: threading.Event):
    sd = st["sd"]
    held = _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) != 0
    st["held"] = held
    try:
        track = _read(sd / "nightshift.json") or {}
        info = away_info(sd)
        if info and track.get("since") != info["since"]:      # away.json written by something else: start fresh
            track = start_track(summary_rows(sd), info["since"])
        track["wake"] = {"held": held, "at": round(time.time())}
        _write(sd / "nightshift.json", track)
        while not stop.is_set():
            if not away_info(sd):
                break
            try:
                rows = summary_rows(sd)
                if step(track, rows):
                    _write(sd / "nightshift.json", track)
                st["counts"] = counts(track, rows)
            except Exception as e:                            # noqa: BLE001 - the thread never dies on a bad read
                st["err"] = str(e)
            if not held:
                held = _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) != 0
                st["held"] = held
            stop.wait(TICK)
    finally:
        _set_state(ES_CONTINUOUS)                             # release: only this thread can clear its own request
        st["held"] = False


def _ensure_thread(ctx, sd: Path):
    st = ctx.daemon.plugin_state.setdefault(KEY, {})
    th = st.get("thread")
    if th and th.is_alive() and st.get("version") == _VERSION and st.get("sd") == sd:
        return st
    if st.get("stop"):
        st["stop"].set()                                      # an older copy of this file: it releases and exits
        if th:
            th.join(2.0)
    st.update(sd=sd, version=_VERSION, stop=threading.Event(), counts=None)
    st["thread"] = threading.Thread(target=_loop, args=(st, st["stop"]), daemon=True, name="nightshift")
    st["thread"].start()
    return st


def _stop_thread(ctx):
    st = ctx.daemon.plugin_state.get(KEY) or {}
    if st.get("stop"):
        st["stop"].set()
        if st.get("thread"):
            st["thread"].join(3.0)


# ------------------------------------------------------------------ actions
def _refresh(ctx):
    try:
        ctx.daemon.live_next.clear()                         # redraw the live keys now
    except Exception:                                        # noqa: BLE001
        pass


def go_away(ctx, monitors=False):
    sd = state_dir(ctx)
    now = round(time.time(), 1)
    _write(sd / "nightshift.json", start_track(summary_rows(sd), now))
    _write(sd / "away.json", {"since": now, "by": "pad"})
    _ensure_thread(ctx, sd)
    mon = monitors_off(sd) if monitors else None
    ctx.log(f"nightshift: away since {time.strftime('%H:%M', time.localtime(now))}"
            + (f", monitors off ({mon})" if mon else ""))
    _refresh(ctx)
    return "away"


def come_back(ctx):
    sd = state_dir(ctx)
    try:
        (sd / "away.json").unlink()
    except OSError:
        pass
    _stop_thread(ctx)                                        # joins: the thread's last write lands before ours
    track = _read(sd / "nightshift.json") or {}
    rows = summary_rows(sd)
    step(track, rows)
    track["back_at"] = round(time.time(), 1)
    track["final"] = counts(track, rows)
    _write(sd / "nightshift.json", track)
    f = track["final"]
    ctx.log(f"nightshift: back; {f['done']} done, {f['waiting']} waiting, {f['silent']} silent")
    _refresh(ctx)
    return "back"


def tap(ctx, **_):
    return come_back(ctx) if away_info(state_dir(ctx)) else go_away(ctx, monitors=False)


def hold(ctx, **_):
    return come_back(ctx) if away_info(state_dir(ctx)) else go_away(ctx, monitors=True)


# ------------------------------------------------------------------ faces
_F = {
    "0": ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.", "1": "..#..|.##..|..#..|..#..|..#..|..#..|#####",
    "2": ".###.|#...#|....#|..##.|.#...|#....|#####", "3": ".###.|#...#|....#|..##.|....#|#...#|.###.",
    "4": "...##|..#.#|.#..#|#...#|#####|....#|....#", "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.", "7": "#####|....#|...#.|..#..|..#..|..#..|..#..",
    "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.", "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    "+": ".....|..#..|..#..|#####|..#..|..#..|.....",
}
# the three tally marks, 5 x 7: finished = check, waiting = ?, silent = z
_ICON = {"done": "....#|...##|#.##.|###..|.#...|.....|.....",
         "waiting": ".###.|#...#|...#.|..#..|..#..|.....|..#..",
         "silent": "#####|...#.|..#..|.#...|#####|.....|....."}
_COL = {"done": "#55ff55", "waiting": "#ffff55", "silent": "#d8b8ff"}
_G = {c: v.split("|") for c, v in _F.items()}
_GI = {k: v.split("|") for k, v in _ICON.items()}


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _glyph(d, x, y, rows, col):
    sh = tuple(c // 4 for c in col)
    for off, c in ((1, sh), (0, col)):
        for ry, row in enumerate(rows):
            for rx, b in enumerate(row):
                if b == "#":
                    d.point((x + rx + off, y + ry + off), fill=c)


def _num(n) -> str:
    return str(n) if n < 100 else "99+"


def _big(d, x, y, rows, col, sc):
    """A 5x7 glyph at `sc` whole pixels per dot, with a one-dot drop shadow."""
    sh = tuple(c // 4 for c in col)
    for off, c in ((sc // 2 or 1, sh), (0, col)):
        for ry, row in enumerate(rows):
            for rx, b in enumerate(row):
                if b == "#":
                    d.rectangle((x + rx * sc + off, y + ry * sc + off, x + (rx + 1) * sc - 1 + off,
                                 y + (ry + 1) * sc - 1 + off), fill=c)


def tally_frames(c: dict) -> list:
    """One count per frame (a key reads as one idea): finished, then waiting and silent when not zero."""
    out = [k for k in ("done", "waiting", "silent") if c.get(k, 0)]
    return out or ["done"]


def _count(d, k, n):
    """Mark + number, centred in the lower half: the mark at 2x, the number at 3x."""
    s = _num(n)
    sc = 3 if len(s) <= 2 else 2
    col = _rgb(_COL[k]) if n else (150, 150, 168)
    w = 10 + 4 + len(s) * 6 * sc - sc
    x = (64 - w) // 2
    d.rectangle((x - 4, 37, x + w + 4, 62), fill="#100018", outline="#3a00a8")
    _big(d, x, 43 + (7 * sc - 14) // 2, _GI[k], col, 2)
    cx = x + 14
    for ch in s:
        _big(d, cx, 40 + (21 - 7 * sc) // 2, _G[ch], col, sc)
        cx += 6 * sc


def _moon(d, cx, cy, r=10):
    """A crescent: a lit disc minus an offset disc of the sky colour, on whole pixels."""
    for y in range(-r, r + 1):
        for x in range(-r, r + 1):
            if x * x + y * y <= r * r and (x - 6) ** 2 + (y + 3) ** 2 > (r - 1) ** 2:
                shade = "#fff3b0" if x + y < -4 else "#f2d35c" if x + y < 6 else "#c9a23a"
                d.point((cx + x, cy + y), fill=shade)


def _sun(d, cx, cy):
    d.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill="#ffd84a", outline="#e08a1a")
    for dx, dy in ((0, -12), (-12, 0), (12, 0), (-9, -9), (9, -9), (-9, 9), (9, 9)):
        d.rectangle((cx + dx - 1, cy + dy - 1, cx + dx + 1, cy + dy + 1), fill="#ffd84a")


_STARS = [(8, 7), (52, 6), (57, 22), (10, 28), (49, 31), (34, 3), (4, 16)]


def _sky(night: bool):
    img = Image.new("RGB", (64, 64))
    d = ImageDraw.Draw(img)
    bands = ("#0b1030", "#101845", "#16215a", "#1c2a6c") if night else ("#5b93ff", "#74a6ff", "#90b9ff", "#a9caff")
    for i in range(4):                                       # banded sky, darker at the top
        d.rectangle((0, 0 if i == 0 else 9 * i + 4, 63, 63), fill=bands[i])
    return img, d


def render_away(c: dict, themed=False) -> list:
    """Frames: moon on top, one count below per frame."""
    out = []
    for k in tally_frames(c):
        img, d = _sky(True)
        for x, y in _STARS:
            d.point((x, y), fill="#ffffff")
        _moon(d, 30, 18, 12)
        _count(d, k, c.get(k, 0))
        out.append(_out(img, themed))
    return out


def render_back(c: dict, themed=False) -> list:
    out = []
    for k in tally_frames(c):
        img, d = _sky(False)
        _sun(d, 32, 18)
        _count(d, k, c.get(k, 0))
        out.append(_out(img, themed))
    return out


def _out(img, themed):
    return img.resize((192, 192), Image.NEAREST).convert("RGBA") if themed else img


def face(ctx, **_):
    sd = state_dir(ctx)
    th = False
    try:
        th = bool(ctx.theme)
    except Exception:                                        # noqa: BLE001
        pass
    info = away_info(sd)
    if info:
        st = _ensure_thread(ctx, sd)                          # also after a daemon restart: re-arms the wake lock
        c = st.get("counts")
        if c is None:
            track = _read(sd / "nightshift.json") or {}
            c = counts(track, summary_rows(sd))
        return _cached(ctx, ("away", c["done"], c["waiting"], c["silent"], th), lambda: render_away(c, th))
    track = _read(sd / "nightshift.json") or {}
    if track.get("back_at") and time.time() - track["back_at"] < TALLY_KEEP and track.get("final"):
        c = track["final"]
        return _cached(ctx, ("back", c["done"], c["waiting"], c["silent"], th), lambda: render_back(c, th))
    return {"icon": "mdi:weather-night", "label": "Away"}


def _cached(ctx, key, make):
    """Frames are built once per tally (the daemon keys its frame cache on the list's identity)."""
    s = ctx.state
    if s.get("nk") != key:
        s["nk"], s["nimg"] = key, make()
    fr = s["nimg"]
    return {"image": fr[0]} if len(fr) == 1 else {"frames": fr, "fps": FPS}
