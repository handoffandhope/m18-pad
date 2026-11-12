# Scene card: design a live picture for the pad

This page is all you need to design a scene. Do not read the daemon, the other references or the
bundled plugins: the runtime already does the plumbing (frame loop, cutting into keys, previews,
reload on save, the LED ring).

A scene is one Python file, `$M18/scenes/<name>.py`, that draws one 440 x 252 picture. The pad
shows it through 15 square windows (5 columns x 3 rows of 64 px keys). Live numbers from the PC
arrive as **signals**, each already scaled to 0..1. `s` holds only the signals named in `SIGNALS`.

## The file

```python
"""One line: what it shows and which signal drives what."""
import math, random
from m18lib.scene import W, H, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram"]                       # the ones you use
FPS = 8                                        # 1-20
RING = {"signal": "cpu", "color": "#4cc9f0"}   # optional: LED ring as a meter of one signal

def setup(state):                              # optional, runs once: random layout, slow work
    state["dots"] = [(random.randrange(W), random.randrange(H)) for _ in range(40)]

def draw(im, d, s, t, state):                  # every frame
    # d: PIL ImageDraw on the 440 x 252 RGB image `im`; t: seconds since start
    # s.cpu.value 0..1 (None if unknown: use float(s.cpu)), s.cpu.text "46%", s.cpu.label "CPU"
    d.rectangle((0, 0, W, H), fill=mix("#0b1026", "#3a0d2e", s.ram.value))
    for i, (x, y) in enumerate(state["dots"]):
        r = 2 + 6 * s.cpu.value * (0.5 + 0.5 * math.sin(t * 2 + i))
        d.ellipse((x - r, y - r, x + r, y + r), fill="#4cc9f0")

def tap(key, x, y, s, t, state):               # optional: key 1-15 pressed, (x, y) its centre
    state["tapped"] = (key, t + 3)             # e.g. show a reading on that key until t + 3
```

Helpers: `mix(c1, c2, f)` blends two colours; `ramp([c1, c2, c3], f)` walks a list of colours;
`text(d, (x, y), "46%", size=14, fill="#ffffff")` draws centred text (sizes 10-22 read on a key);
`key_box(key)` gives a key's `(x0, y0, x1, y1)`. Colours are `"#rrggbb"` or `(r, g, b)`; there is
no alpha, so fade by mixing toward the background colour. Keep state in `state`, never in globals.

## Signals

| Name | 0 → 1 means | Good for |
|---|---|---|
| `cpu` | idle → flat out, changes every second | activity, heat, crowds, speed |
| `ram` | memory empty → full, slow | weather, water level, pressure |
| `gpu` | graphics card idle → busy | a second kind of effort |
| `net` | 10 KB/s → 10 MB/s, jumpy | traffic, flow, chatter |
| `disk` | system drive empty → full, nearly fixed | terrain, sediment, clutter |
| `battery` | empty → full (`None` on a desktop) | energy, daylight left |
| `time_of_day` | midnight → next midnight, 0.5 = noon | sun, moon, sky colour |
| `weekday` | Monday → Sunday | weekly rhythm |

`m18ctl signals` lists these plus any the user's setup agent added in `$M18/signals/`. If the
picture needs a number that is not listed (unread mail, a build status), say so in your report;
adding a signal is a separate, small job. Some signals carry a list too (`s.agents.items`, one dict
per running agent session); `m18ctl signals` marks those.

## Rules of the glass

- Key column c (0-4) shows x = c·94 … c·94+63; row r (0-2) shows y = r·94 … r·94+63. The 30 px
  strips between are **hidden behind bars**. Shapes may cross a bar (they continue correctly on
  the other side), but anything that must be read (text, a face, an icon) sits inside one key.
- **Design for the grid, since the pad is 15 separate screens.** Pick one of these and say which in the
  docstring:
  - *One thing per key.* Each subject (a session, a building, a gauge) gets a key of its own and is
    centred in it (`x0 + 32, y0 + 32` from `key_box`). A tap on the key is then a tap on that thing.
  - *The bars are a frame.* Draw one continuous picture and treat the bars as window bars, shelf
    edges or comic gutters: big soft things (sky, water, shadows, light) run behind them, and a
    thing that moves passes behind a bar and comes out the other side.
  Never leave a subject parked half behind a bar, and never let it straddle two keys while it rests.
- Each key is small: bold shapes, strong contrast, at most two short words per key.
- Map each signal to something a person can see change: count, size, speed, colour, position.
  Say which is which in the docstring.
- Move things with `t` (`x = (t * 40) % W`), and ease state toward a signal a little each frame
  so nothing jumps.
- `draw` runs up to 20 times a second: build lists and backgrounds in `setup`, loop over a few
  hundred shapes at most.

## 3D

`m18lib.ray` is a small ray tracer: balls on a floor under a sky, with a sun, soft shadows,
reflections and lamps. y is up and the floor is y = 0, so a ball of radius r rests at y = r.

```python
from m18lib import ray

def draw(im, d, s, t, state):
    balls = [ray.ball((0, 1, 0), 1, "#e63946", gloss=0.6),            # (x, y, z), radius, colour
             ray.ball((2, 0.5 + 2 * s.cpu.value, 1), 0.5, "#ffd166", glow=2.0)]   # glow = a lamp
    cam = ray.orbit(t * 0.2, height=4, dist=11)                       # or ray.camera(eye, look)
    im.paste(ray.render(balls, cam, sun=(0.5, 0.7, -0.4), sky=("#f2a65a", "#1b2a5c"),
                        floor="#8c909e", floor2="#747a8c"))
    # then draw 2D on top with d as usual
```

`render` also takes `gloss` (the floor's), `fog`, `stars` (0..1, for night; put the sun below the
horizon with a negative y) and `quality`. A frame costs about 0.15 s with ten balls, so set `FPS = 6`.
`ray.project(cam, (x, y, z))` gives the canvas pixel of a 3D point. `ray.unproject(cam, x, y, height)`
goes the other way, so with a fixed camera you can sit a ball exactly in the middle of a key.
[scenes/marbles.py](../scripts/m18/scenes/marbles.py) is a full example.

## Make it, look at it

```
m18ctl scene new NAME                                # writes scenes/NAME.py from a template
m18ctl scene shot NAME --t 2,10 --set cpu=0.1 ram=0.2    # PNG, bars blacked out, no daemon needed
m18ctl scene shot NAME --t 2,10 --set cpu=0.9 ram=0.9    # the other extreme
```

`shot` prints the PNG path and any error with its line number; open the PNG. It holds one frame per
`--t` time, stacked top to bottom, and the scene runs at about 4 frames a second up to each time, so
eased values have settled. The file name includes the pinned values, so two shots do not overwrite
each other. Check both extremes of every signal you use: the picture must read differently. Signals
not pinned with `--set` are simulated. Add `--tap 8` to press key 8 at the first time and see what
`tap` draws. Then add the page, one line under `pages:` in `$M18/macros.yaml`:

```yaml
  NAME: {scene: NAME}
```

and run `m18ctl validate`. The running daemon picks the file up on save. Report the file, the
signal-to-picture mapping, and the two PNG paths.
