"""Ring plugins: agent-session status, meters, hold-to-confirm progress, Codex usage.

    7:  {live: {plugin: ring.status, interval: 5}}                 # starts the ring's background thread
    8:  {action: {plugin: ring.hold_progress, args: {seconds: 1, then: {hotkey: enter}}}, physical_only: true}
    9:  {live: {plugin: ring.codex_face, interval: 30}, action: {plugin: ring.codex_meter}}

Everything draws through ctx.leds, so the ring follows the daemon's owner rule (see api.md): the status
thread is "ambient" (priority 0, ttl 2 s, refreshed while alive) and yields to anything else; it takes
the ring back when that owner releases. Positions come from m18lib.ringmap (state/ring-map.json once
`m18ctl ringwalk` has been run, else a default map).

ring.status reads <config dir>/state/sessions-summary.json = {"working": n, "waiting": n, "finished": n}.
Absent, unreadable or all zero releases the ring to the page colour.
Arcs run clockwise from the top-left corner in this order, each sized by its share of the 22 ring LEDs
(never fewer than one LED for a nonzero count, so it reads the same at 1 session and at 40):
    working  = theme good   (green)   steady
    waiting  = theme warn   (amber)   breathes slowly (about 0.4 Hz, brightness 70-100%) only while waiting > 0
    finished = theme aqua/teal/blue   steady
Red is never used here (it means an error, and a session count is not one). The two front strips show
the single most urgent state: waiting, else working, else finished.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from m18lib import ringmap as RM

_VERSION = os.path.getmtime(__file__)
BREATH_HZ = 0.4
BREATH_MIN = 0.7
OWNER = "ring.status"
DEFAULTS = {"working": "#3ddc84", "waiting": "#ffb020", "finished": "#4da3ff"}


# ------------------------------------------------------------------------------ pure helpers
def read_summary(path: Path):
    """(working, waiting, finished) from the summary file, or None if absent or unreadable."""
    try:
        j = json.loads(Path(path).read_text(encoding="utf-8"))
        vals = tuple(max(0, int(j.get(k, 0) or 0)) for k in ("working", "waiting", "finished"))
        return vals
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def palette(theme: dict | None) -> dict:
    t = theme or {}
    return {"working": t.get("good", DEFAULTS["working"]),
            "waiting": t.get("warn", DEFAULTS["waiting"]),
            "finished": t.get("aqua") or t.get("teal") or t.get("blue") or DEFAULTS["finished"]}


def breath(t: float) -> float:
    """Brightness factor for the waiting arc: slow sine, BREATH_MIN..1.0."""
    return BREATH_MIN + (1 - BREATH_MIN) * (0.5 + 0.5 * math.sin(2 * math.pi * BREATH_HZ * t))


def status_frame(rm, working: int, waiting: int, finished: int, pal: dict, breath_f: float = 1.0) -> list:
    """24-LED frame for the three arcs; strips show the most urgent state. breath_f scales the waiting
    colour (1.0 = full)."""
    counts = [working, waiting, finished]
    names = ["working", "waiting", "finished"]
    sizes = RM.arc_sizes(counts, len(rm.clockwise))
    frame = RM.blank()
    pos = 0
    for name, n in zip(names, sizes):
        col = pal[name] if name != "waiting" else RM.scaled(pal[name], breath_f)
        for led in rm.clockwise[pos:pos + n]:
            frame[led] = col
        pos += n
    urgent = "waiting" if waiting else "working" if working else "finished"
    col = pal[urgent] if urgent != "waiting" else RM.scaled(pal[urgent], breath_f)
    for led in RM.STRIPS:
        frame[led] = col
    return frame


# ------------------------------------------------------------------------------ ring.status
def _loop(st: dict, stop: threading.Event):
    counts, last_read = None, 0.0
    while not stop.is_set():
        wait = 0.5
        try:
            ctx = st["ctx"]
            now = time.time()
            if now - last_read >= 1.0:
                counts = read_summary(ctx.config_dir / "state" / "sessions-summary.json")
                last_read = now
            if not counts or sum(counts) == 0:
                ctx.leds_release(OWNER)
            else:
                rm = RM.load(ctx.config_dir)
                b = breath(now) if counts[1] else 1.0
                ctx.leds(status_frame(rm, *counts, palette(ctx.theme), b), ttl=2.0, owner=OWNER, priority=0)
                wait = 0.05 if counts[1] else 0.25      # breathing needs frames; a static ring is only refreshed
        except Exception as e:                           # dark mode (409) lands here too
            if "dark mode" not in str(e):
                try:
                    st["ctx"].log("ring.status:", e)
                except Exception:
                    pass
            wait = 0.5
        stop.wait(wait)


def status(ctx, **_):
    """Live function: starts (or hot-reloads) the ring thread, and shows the counts on its key."""
    st = ctx.daemon.plugin_state.setdefault("ring.status.thread", {})
    st["ctx"] = ctx
    th = st.get("thread")
    if not (th and th.is_alive() and st.get("version") == _VERSION):
        if st.get("stop"):
            st["stop"].set()                             # an older copy of this file: replace it
        st["stop"] = stop = threading.Event()
        st["version"] = _VERSION
        st["thread"] = threading.Thread(target=_loop, args=(st, stop), daemon=True, name="ring.status")
        st["thread"].start()
    c = read_summary(ctx.config_dir / "state" / "sessions-summary.json")
    if not c or sum(c) == 0:
        return {"icon": "mdi:robot-outline", "label": "idle", "bg": "#2a2a33"}
    pal = palette(ctx.theme)
    top = "waiting" if c[1] else "working" if c[0] else "finished"
    return {"icon": "mdi:robot", "label": f"{c[0]}/{c[1]}/{c[2]}", "bg": pal[top]}


# ------------------------------------------------------------------------------ meter helper
def meter_frame(ctx, fraction, color, stop_at=None, track="#0c0c0c"):
    return RM.meter_frame(RM.load(ctx.config_dir), fraction, color, stop_at, track)


def meter(ctx, fraction=0.0, color=None, stop_at=None, ttl=3.0, owner=None, priority=1, **_):
    """Fill the ring clockwise from the top-left to `fraction` (0-1) in `color`, with an optional
    marker LED at `stop_at` (e.g. 0.9). Callable from other plugins' code via
    `ring.meter(ctx, ...)` if they load this file, or as an action:
    {plugin: ring.meter, args: {fraction: 0.4, color: "#3ddc84", stop_at: 0.9, ttl: 5}}."""
    color = color or palette(ctx.theme)["working"]
    return ctx.leds(meter_frame(ctx, float(fraction), color, stop_at), ttl=ttl,
                    owner=owner or f"ring.meter:{ctx.key}", priority=priority)


# ------------------------------------------------------------------------------ hold progress
def hold_progress(ctx, seconds=1.0, color=None, then=None, **_):
    """Action for a key WITHOUT `hold:` (it runs at key down). Sweeps the ring 0 -> full over
    `seconds` while the key stays down, measured from the real press time; releasing early cancels
    (ring released, nothing runs). On completion the ring flashes full for 0.3 s and `then` (an
    action or list) runs. Returns True when completed. Put `physical_only: true` on the key so
    only the real button can confirm."""
    seconds = min(max(float(seconds), 0.1), 10.0)
    rm = RM.load(ctx.config_dir)
    color = color or palette(ctx.theme)["working"]
    owner = f"ring.hold:{ctx.key}"
    t0 = ctx.pressed_at or time.time()

    def draw(fr):
        try:
            ctx.leds(RM.meter_frame(rm, fr, color, track="#0c0c0c"), ttl=1.0, owner=owner, priority=2)
        except Exception:
            pass                                          # dark mode: still time the hold
    done = False
    try:
        while True:
            fr = (time.time() - t0) / seconds
            if fr >= 1.0:
                done = True
                break
            draw(fr)
            if ctx.wait_release(0.05):
                break
        if done:
            if not ctx.wait_release(0.0):
                draw(1.0)
                time.sleep(0.3)
    finally:
        ctx.leds_release(owner)
    if done and then:
        ctx.run(then)
    return done


# ------------------------------------------------------------------------------ Codex usage
_CODEX = {"t": 0.0, "v": None}


def _tail_lines(path: Path, nbytes=1_000_000):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        n = f.tell()
        f.seek(max(0, n - nbytes))
        data = f.read()
    return data.splitlines()[::-1]


def codex_usage(root: Path | None = None, max_files: int = 6):
    """The current Codex limit window, read from the newest Codex session rollouts on disk (no model
    call, no allowance): {"used": percent, "resets_at": epoch, "window_min": n, "plan": str,
    "as_of": epoch of that reading}, or None when nothing is found. Rollouts only grow while Codex is
    used, so "as_of" can be hours old; `used` is None (stale) once resets_at has passed."""
    root = root or Path.home() / ".codex" / "sessions"
    try:
        days = sorted((d for y in root.iterdir() if y.is_dir() for m in y.iterdir() if m.is_dir()
                       for d in m.iterdir() if d.is_dir()), key=lambda p: p.parts[-3:], reverse=True)[:3]
        files = sorted((f for d in days for f in d.glob("rollout-*.jsonl")),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:max_files]
    except OSError:
        return None
    for f in files:
        try:
            for line in _tail_lines(f):
                if b'"rate_limits"' not in line or b'"limit_id":"codex"' not in line:
                    continue
                j = json.loads(line)
                rl = j["payload"]["rate_limits"]
                p = rl["primary"]
                ts = j.get("timestamp")
                as_of = (datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                         if ts else f.stat().st_mtime)
                used = float(p["used_percent"])
                resets = float(p["resets_at"])
                return {"used": used if resets > time.time() else None, "resets_at": resets,
                        "window_min": p.get("window_minutes"), "plan": rl.get("plan_type"), "as_of": as_of}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _codex_cached():
    if time.time() - _CODEX["t"] > 20:
        _CODEX["v"], _CODEX["t"] = codex_usage(), time.time()
    return _CODEX["v"]


def codex_face(ctx, **_):
    """Live face: percent of the Codex limit window used (as of Codex's last turn)."""
    u = _codex_cached()
    if not u or u["used"] is None:
        return {"icon": "mdi:chart-donut", "label": "no data", "bg": "#2a2a33"}
    pal = palette(ctx.theme)
    col = pal["working"] if u["used"] < 70 else pal["waiting"]
    return {"icon": "mdi:chart-donut", "label": f"{u['used']:.0f}%", "bg": col}


def codex_meter(ctx, seconds=6.0, stop_at=0.90, **_):
    """Action: show the Codex limit window as a ring arc (green, amber from 70%) with a marker at
    `stop_at` (0.90, the line where new Codex runs stop) for `seconds`."""
    u = _codex_cached()
    if not u or u["used"] is None:
        ctx.flash({"label": "no Codex data", "bg": "#2a2a33"}, 1.5)
        return False
    pal = palette(ctx.theme)
    fr = u["used"] / 100.0
    col = pal["working"] if fr < 0.7 else pal["waiting"]
    ctx.log(f"codex meter {u['used']:.0f}% (as of {time.strftime('%H:%M', time.localtime(u['as_of']))})")
    return ctx.leds(meter_frame(ctx, fr, col, stop_at), ttl=min(float(seconds), 60.0),
                    owner=f"ring.codex:{ctx.key}", priority=1)
