"""One line saying what this scene shows and which signal drives what."""
import math

from m18lib.scene import H, W, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram"]
FPS = 8
RING = {"signal": "cpu", "color": "#4cc9f0"}


def setup(state):                       # once: build anything random or slow
    state["dots"] = [(i * 37 % W, i * 53 % H) for i in range(40)]


def draw(im, d, s, t, state):           # every frame; 440 x 252 canvas
    d.rectangle((0, 0, W, H), fill=mix("#0b1026", "#3a0d2e", s.ram.value))
    for i, (x, y) in enumerate(state["dots"]):
        r = 2 + 6 * s.cpu.value * (0.5 + 0.5 * math.sin(t * 2 + i))
        d.ellipse((x - r, y - r, x + r, y + r), fill=ramp(["#4cc9f0", "#f72585"], s.cpu.value))
    tapped = state.get("tapped")
    if tapped and t < tapped[1]:
        x0, y0, x1, y1 = key_box(tapped[0])
        d.rectangle((x0, y0, x1, y1), fill="#000000")
        text(d, (x0 + 32, y0 + 32), s.cpu.text, 18)


def tap(key, x, y, s, t, state):        # optional
    state["tapped"] = (key, t + 3)
