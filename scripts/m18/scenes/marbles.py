"""Marbles (3D): every agent session is a marble circling a stone floor under a real sky.

agents:        one marble each. Working = it circles. Waiting for you = it lifts off the floor and glows gold,
               lighting the floor and its neighbours. Finished = it parks on the rim, dull green.
cpu:           how fast they circle.
time_of_day:   the sun crosses the sky; dawn, day, dusk, then night with stars, when the marbles glow.
claude_usage:  the ring fills; on a hit limit everything stops and turns to ice.
tap:           a key with a marble in it shows that session's name for 3 s.
"""
import math

from m18lib import ray
from m18lib.scene import key_box, mix, ramp, text

SIGNALS = ["agents", "cpu", "time_of_day", "claude_usage"]
FPS = 6
RING = {"signal": "claude_usage", "color": "#ffc53d"}

SPECIES = {"opus": "#d9643f", "sonnet": "#e99a4b", "haiku": "#f3d58c", "luna": "#6fd3c4", "sol": "#3f9be0"}
GOLD, DONE = "#ffc53d", "#6f9c7c"
NIGHT, DAWN, DAY, DUSK = ("#101a3c", "#02030a"), ("#f2a65a", "#27356b"), ("#a9d6ff", "#2f6fd0"), ("#f6a27a", "#3a3f8f")
SKY = [NIGHT, NIGHT, DAWN, DAY, DAY, DAY, DUSK, NIGHT, NIGHT]          # midnight .. midnight


def val(sig, default=0.0):
    v = getattr(sig, "value", None)
    return default if v is None or v != v else min(1.0, max(0.0, float(v)))


def setup(state):
    state.update(a=0.0, tt=0.0, last=None, lift={}, park={}, seen=[], cam=None, tapped=None)


def layout(s, t, state, frozen):
    """[(row, (x, y, z), radius, lift 0..1)] for every session."""
    rows = sorted((r for r in (s.agents.items or []) if isinstance(r, dict) and r.get("id")), key=lambda r: str(r["id"]))
    out = []
    n1 = min(5, len(rows))
    for i, r in enumerate(rows):
        inner = i < n1
        n, j = (n1, i) if inner else (len(rows) - n1, i - n1)
        ang = (state["a"] if inner else -state["a"] * 0.55) + j * 2 * math.pi / max(1, n)
        rad = 2.5 if inner else 4.5
        size = 1.0 if r.get("species") == "opus" else 0.8
        waiting = r.get("state") == "waiting"
        lift = state["lift"].get(r["id"], 0.0)
        if not frozen:
            lift += ((1.0 if waiting else 0.0) - lift) * 0.25         # ease up and down
        state["lift"][r["id"]] = lift
        if r.get("state") == "finished":                              # parked where it stopped, out on the rim
            ang = state["park"].setdefault(r["id"], ang)
            rad += 0.7
        else:
            state["park"].pop(r["id"], None)
        y = size + lift * (1.2 + 0.15 * math.sin(t * 2.2 + i))
        out.append((r, (rad * math.cos(ang), y, rad * math.sin(ang)), size, lift))
    return out


def draw(im, d, s, t, state):
    frozen = (s.claude_usage.data or {}).get("frozen") is not None
    dt = 0.0 if state["last"] is None else min(0.5, max(0.0, t - state["last"]))
    state["last"] = t
    if not frozen:                                                     # a hit limit stops every clock
        state["a"] += dt * (0.12 + 0.5 * val(s.cpu))
        state["tt"] += dt
    tt = state["tt"]
    tod = val(s.time_of_day, 0.5)
    sun_a = (tod - 0.25) * 2 * math.pi
    up = math.sin(sun_a)
    night = max(0.0, min(1.0, -up * 3 + 0.2))
    sky = (ramp([c[0] for c in SKY], tod), ramp([c[1] for c in SKY], tod))
    balls, seen = [], []
    for r, pos, size, lift in layout(s, tt, state, frozen):
        col = SPECIES.get(r.get("species"), "#b48ead")
        gloss, glow = 0.55, 0.18 * night
        if r.get("state") == "finished":
            col, gloss, glow = DONE, 0.1, 0.1 * night
        if lift > 0.02:
            col = mix(col, GOLD, lift)
            glow = max(glow, lift * (1.2 + 0.5 * math.sin(tt * 3)))
        if frozen:
            col, gloss, glow = mix(col, "#dff1ff", 0.7), 0.8, 0.0
        balls.append(ray.ball(pos, size, col, gloss, glow))
        seen.append((r, pos, size))
    if frozen:
        sky = ("#d6ecff", "#7ea6d6")
    cam = ray.orbit(0.6 + 0.5 * math.sin(tt * 0.13), 3.0 + 0.3 * math.sin(tt * 0.09), 9.0, look=(0, 1.3, 0), fov=46)
    state["cam"], state["seen"] = cam, seen
    im.paste(ray.render(balls, cam, sun=(math.cos(sun_a), up * 0.9, -0.35), sky=sky,
                        floor="#c9ccd6" if frozen else "#8c909e", floor2="#b4bccb" if frozen else "#747a8c",
                        tile=1.6, gloss=0.5 if frozen else 0.28, fog=0.03 if frozen else 0.008, stars=night,
                        quality=float(state["opts"].get("quality", 1.0))))
    tapped = state["tapped"]
    if tapped and t < tapped[2]:                                       # a name plate on the tapped key
        x0, y0, x1, y1 = key_box(tapped[0])
        d.rounded_rectangle((x0 + 2, y0 + 40, x1 - 2, y1 - 2), 5, fill="#0b0d14")
        text(d, (x0 + 32, y0 + 51), tapped[1], 12)


def tap(key, x, y, s, t, state):
    x0, y0, x1, y1 = key_box(key)
    cam, best = state["cam"], None
    for r, pos, size in state["seen"]:                                 # the nearest marble whose disc touches the key
        p = ray.project(cam, pos) if cam else None
        top = ray.project(cam, (pos[0], pos[1] + size, pos[2])) if p else None
        if not p or not top:
            continue
        pr = abs(p[1] - top[1])
        nx, ny = min(max(p[0], x0), x1), min(max(p[1], y0), y1)
        if (p[0] - nx) ** 2 + (p[1] - ny) ** 2 <= pr * pr:
            depth = sum((a - b) ** 2 for a, b in zip(pos, cam["eye"]))
            if best is None or depth < best[0]:
                best = (depth, str(r.get("label") or r.get("title") or r["id"]))
    if best:
        state["tapped"] = (key, best[1][:9], t + 3)
