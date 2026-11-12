"""Marbles (3D): every agent session is a marble with a key of its own, on a stone floor under a moving sun.

The pad is 15 separate screens, so the scene is built on that: one marble per key, centred in it, and
a tap on a key is a tap on that session. Nothing sits half behind a bar. The bars work as a window
frame: shadows stretch behind them into the next key, and a new marble rolls in behind them.

agents:        one marble each, in its own key. Working = it rolls a small circle. Waiting for you = it
               lifts, turns gold and lights the floor and its neighbours. Finished = still, dull green.
cpu:           how fast they roll.
time_of_day:   the sun crosses: short shadows at noon, long ones at dusk; at night the marbles glow.
claude_usage:  the ring fills; on a hit limit everything stops and turns to ice.
tap:           the session's name for 3 s.
"""
import math

from m18lib import ray
from m18lib.scene import key_box, mix, text

SIGNALS = ["agents", "cpu", "time_of_day", "claude_usage"]
FPS = 6
RING = {"signal": "claude_usage", "color": "#ffc53d"}

SPECIES = {"opus": "#d9643f", "sonnet": "#e99a4b", "haiku": "#f3d58c", "luna": "#6fd3c4", "sol": "#3f9be0"}
GOLD, DONE = "#ffc53d", "#6f9c7c"
ORDER = [8, 7, 9, 3, 13, 2, 4, 12, 14, 6, 10, 1, 5, 11, 15]           # keys fill from the middle out
CAM = ray.camera((0, 15.5, -8.5), (0, 0, 0.35), fov=27)               # fixed, so a marble stays in its key
PX = 19                                                               # a marble's radius on the key, pixels


def val(sig, default=0.0):
    v = getattr(sig, "value", None)
    return default if v is None or v != v else min(1.0, max(0.0, float(v)))


def setup(state):
    cells = {}
    for k in range(1, 16):                                            # where a marble must sit to fill key k
        x0, y0, _, _ = key_box(k)
        c = ray.unproject(CAM, x0 + 32, y0 + 32, 0.8)
        edge = ray.unproject(CAM, x0 + 32 + PX, y0 + 32, 0.8)
        r = abs(edge[0] - c[0])
        cells[k] = (ray.unproject(CAM, x0 + 32, y0 + 32, r), r)
    state.update(cells=cells, slot={}, at={}, lift={}, roll=0.0, tt=0.0, last=None, tapped=None)


def draw(im, d, s, t, state):
    frozen = (s.claude_usage.data or {}).get("frozen") is not None
    dt = 0.0 if state["last"] is None else min(0.5, max(0.0, t - state["last"]))
    state["last"] = t
    if not frozen:                                                     # a hit limit stops every clock
        state["roll"] += dt * (0.6 + 2.4 * val(s.cpu))
        state["tt"] += dt
    tt = state["tt"]
    rows = {str(r["id"]): r for r in (s.agents.items or []) if isinstance(r, dict) and r.get("id")}
    slot = state["slot"]
    for sid in [i for i in slot if i not in rows]:                     # a session that ended frees its key
        del slot[sid]
        state["at"].pop(sid, None)
        state["lift"].pop(sid, None)
    free = [k for k in ORDER if k not in slot.values()]
    for sid in sorted(rows):
        if sid not in slot and free:
            slot[sid] = free.pop(0)
    tod = val(s.time_of_day, 0.5)
    sun_a = (tod - 0.25) * 2 * math.pi
    up = math.sin(sun_a)
    night = max(0.0, min(1.0, -up * 3 + 0.2))
    warm = max(0.0, 1 - abs(up) * 2.5) * (1 - night)                   # low sun: dawn and dusk
    balls = []
    for i, (sid, key) in enumerate(sorted(slot.items())):
        r, (home, size) = rows[sid], state["cells"][key]
        what = r.get("state")
        lift = state["lift"].get(sid, 0.0)
        at = state["at"].get(sid) or (home[0] - 9.0, home[2])          # new: rolls in from the left, behind the bars
        if not frozen:
            lift += ((1.0 if what == "waiting" else 0.0) - lift) * 0.25
            at = (at[0] + (home[0] - at[0]) * 0.22, at[1] + (home[2] - at[1]) * 0.22)
        state["lift"][sid], state["at"][sid] = lift, at
        wob = 0.0 if what != "working" else size * 0.22                # working: a small circle inside its key
        ph = state["roll"] + i * 1.9
        pos = (at[0] + wob * math.cos(ph), size + lift * size * (0.55 + 0.08 * math.sin(tt * 2.2 + i)),
               at[1] + wob * math.sin(ph))
        col, gloss, glow = SPECIES.get(r.get("species"), "#b48ead"), 0.55, 0.22 * night
        if what == "finished":
            col, gloss, glow = DONE, 0.1, 0.08 * night
        if lift > 0.02:
            col = mix(col, GOLD, lift)
            glow = max(glow, lift * (1.3 + 0.5 * math.sin(tt * 3)))
        if frozen:
            col, gloss, glow = mix(col, "#dff1ff", 0.7), 0.8, 0.0
        balls.append(ray.ball(pos, size * (1.0 - 0.12 * lift), col, gloss, glow))
    floor = mix(mix("#8c909e", "#b98f76", warm * 0.6), "#20222c", night * 0.85)
    sky = (mix(mix("#a9d6ff", "#f6a27a", warm), "#101a3c", night), mix("#2f6fd0", "#02030a", night))
    if frozen:
        floor, sky = "#c9d3e2", ("#d6ecff", "#7ea6d6")
    im.paste(ray.render(balls, CAM, sun=(math.cos(sun_a) * 0.9, max(up, -0.3) * 0.9 + 0.02, -0.3),
                        sun_color=mix("#fff1d6", "#ff9a5c", warm), sky=sky, floor=floor,
                        gloss=0.5 if frozen else 0.22, fog=0.0, quality=float(state["opts"].get("quality", 1.0))))
    tapped = state["tapped"]
    if tapped and t < tapped[2]:                                       # a name plate on the tapped key
        x0, y0, x1, y1 = key_box(tapped[0])
        d.rounded_rectangle((x0 + 2, y0 + 42, x1 - 2, y1 - 2), 5, fill="#0b0d14")
        text(d, (x0 + 32, y0 + 52), tapped[1], 12)


def tap(key, x, y, s, t, state):
    for sid, k in state["slot"].items():
        if k == key:
            r = next((r for r in (s.agents.items or []) if isinstance(r, dict) and str(r.get("id")) == sid), {})
            state["tapped"] = (key, str(r.get("label") or r.get("title") or sid)[:9], t + 3)
