# What the city does, in one small scene

[scenes/city.py](../scripts/m18/scenes/city.py) is the scene to measure yours against, and it is
1,100 lines. Do not read it through. This page shows its eight habits in a scene of 75 lines that
you can run. When you want to see how the city itself does one of them, search that file for the
name given in brackets.

```python
"""A boiler house, as a frame picture. cpu: fire in the furnace door, smoke that stays in the sky;
ram: water in the sight glass, with the cache as a dim band above it. Tap for the numbers."""
import math
from PIL import Image, ImageDraw
from m18lib.scene import W, H, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram", "time_of_day"]
FPS = 8
LINE, PATIENCE = 0.85, 30                # the fire "roars" only after 30 s above 85 %

def setup(state):
    state.update(ease={}, held=0.0, dip=0.0, last=None, smoke=[], puff=-9.0, back={}, plate=(0, -1.0))

def eased(state, name, x, k=0.3):                                   # 1. nothing jumps
    e = state["ease"]
    e[name] = e.get(name, x) + (x - e.get(name, x)) * k
    return e[name]

def backdrop(hour):                                                 # 2. what stands still is drawn once
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    night = abs(hour - 12) / 12                                     # 0 at noon, 1 at midnight
    for y in range(H):                                              # sky, lighter toward the ground
        d.line((0, y, W, y), fill=mix(mix("#3f7fd0", "#0b1026", night), mix("#cfe3ff", "#27224a", night), y / H))
    wall = mix("#9a5a44", "#2a1a22", night * 0.8)
    d.rectangle((0, 150, W, H), fill=wall)                          # the boiler house fills the bottom row
    for x in range(0, W, 22):                                       # texture: brick courses. Small, and nothing depends on it
        d.line((x, 150, x, H), fill=mix(wall, "#000000", 0.15))
    d.rectangle((394, 20, 420, 150), fill=mix("#8a8f9c", "#20222e", night * 0.8))    # the chimney, inside column 4
    return im

def draw(im, d, s, t, state):
    cpu = eased(state, "cpu", float(s.cpu))
    ram = eased(state, "ram", float(s.ram), 0.1)
    hour = int(float(s.time_of_day) * 24)
    if hour not in state["back"]:                                   # redrawn when the hour changes, not every frame
        state["back"] = {hour: backdrop(hour)}
    im.paste(state["back"][hour])

    dt = 0.0 if state["last"] is None else min(1.0, t - state["last"])   # 3. how long, as well as how much
    state["last"] = t
    if cpu > LINE:
        state["held"], state["dip"] = state["held"] + dt, 0.0
    else:
        state["dip"] += dt
        if state["dip"] > 8:                                        # a dip of a few seconds does not end a peak
            state["held"] = 0.0
    roaring = state["held"] >= PATIENCE

    if t - state["puff"] >= 3:                                      # 4. a trace whose size is the duration
        state["puff"] = t
        state["smoke"] = [p for p in state["smoke"] if t - p[0] < 240] + [(t, cpu)]
    for born, load in state["smoke"]:                               # each puff drifts left for four minutes
        age = t - born
        x, r = 407 - age * 1.6, 5 + 16 * load
        if load > 0.15:
            d.ellipse((x - r, 30 - r * 0.5, x + r, 30 + r * 0.5), fill=mix("#54402f", "#9aa0ad", 1 - load))

    x0, y0, x1, y1 = key_box(14)                                    # 5. one big carrier per number, inside one key
    d.rectangle((x0 + 12, y0 + 14, x1 - 12, y1), fill="#1a0f0c")    # the furnace door: 40 px wide
    d.rectangle((x0 + 16, y0 + 18, x1 - 16, y1 - 4), fill=ramp(["#2a1614", "#a3300a", "#ff8a1f", "#fff2b0"], cpu))
    if roaring:                                                     # the alarm look waits for the long peak
        d.rectangle((x0 + 12, y0 + 14, x1 - 12, y1), outline="#fff2b0", width=3)

    x0, y0, x1, y1 = key_box(12)                                    # the sight glass: 24 px wide, 48 tall
    gx0, gy0, gx1, gy1 = x0 + 20, y0 + 8, x0 + 44, y0 + 56
    d.rectangle((gx0 - 2, gy0 - 2, gx1 + 2, gy1 + 2), fill="#0e1322")
    full = (gy1 - gy0) * ram
    cache = s.ram.data.get("cache")                                 # 6. what is inside the number, if the PC says
    if isinstance(cache, (int, float)):
        band = (gy1 - gy0) * min(cache, 1 - ram)
        d.rectangle((gx0, gy1 - full - band, gx1, gy1 - full), fill=mix("#58b8ff", "#0e1322", 0.6))
    d.rectangle((gx0, gy1 - full, gx1, gy1), fill="#ff6b5e" if ram > 0.9 else "#58b8ff")

    bx, by = 60 + 30 * math.sin(t * 0.2), 140 - 3 * abs(math.sin(t * 3))     # 7. small life, on top of big shapes
    d.rectangle((bx, by, bx + 3, by + 2), fill="#20222e")           # a bird hopping along the roof line

    key, until = state["plate"]                                     # 8. the exact number, when asked for
    if t < until:
        x0, y0, x1, y1 = key_box(key)
        sig = s.cpu if key % 5 >= 3 else s.ram
        d.rounded_rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), 7, fill="#0a0c18", outline="#ffc46b", width=2)
        text(d, (x0 + 32, y0 + 18), sig.label, 13, "#aab3d6")
        text(d, (x0 + 32, y0 + 42), sig.text, min(26, int(100 / max(1, len(sig.text)))), "#ffffff")

def tap(key, x, y, s, t, state):
    state["plate"] = (key, t + 4)
```

## The eight habits

1. **Ease every reading** toward its value, a third of the way each frame. Slow things (memory) ease
   slower. [`level`]
2. **Draw what stands still once.** The sky, the walls and the texture go into an image kept in
   `state`, keyed by whatever changes it (here the hour). `draw` pastes it and adds only what
   moves. [`layers`]
3. **Count how long.** Seconds over the line add up in `state`; a dip shorter than eight seconds is
   forgiven. Whatever asks for attention waits for the count, never for the reading. [`remember`]
4. **Leave a trace.** A puff every three seconds, kept for four minutes: a spike is one wisp and a
   long job is a band across the sky. [`remember`, then "smoke" in `draw`]
5. **One big carrier per number, inside one key.** The door is 40 px wide and the glass 24: both
   read from the chair. Their colours are far from the wall behind them by day and by night.
   ["# ---- ram:", "# ---- cpu:"]
6. **Split a carrier to say what is inside the number.** The cache is a dim band above the water,
   read from `s.ram.data` with `.get`, and the picture still works when it is missing. The table
   of keys for every signal is in [scene-card.md](scene-card.md). [`inside`]
7. **Small life goes on top** of shapes that read without it, and nothing depends on it. [`cat`]
8. **Exact numbers on request.** A tap gives a plate that fills the key, with the text sized to
   fit. The city puts its plates on a close-up instead. ["this side of the glass"]

What the city adds that this page leaves out: perspective and the zoom (`View`), three layers so a
train can run between them, and the cat that decides where to look (`camera`). Read those in the
file only if your scene needs them.
