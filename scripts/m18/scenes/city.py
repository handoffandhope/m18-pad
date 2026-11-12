"""Night city: windows light up with CPU load, traffic follows the network, rain falls when memory
is tight, the moon crosses the sky once a day. Tap a key to read its column's signal for 3 s."""
import math
import random

from m18lib.scene import H, W, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram", "net", "disk", "time_of_day"]
FPS = 10
RING = {"signal": "cpu", "color": "#ffc46b"}

ROAD = H - 26
COLUMN = ["cpu", "ram", "net", "disk", "time_of_day"]           # what a tap on each column reads
WARM = ["#ffd678", "#ffbe5f", "#c4e8ff"]
CARS = ["#e8e8f0", "#ff6b6b", "#ffd166", "#5ec8ff"]


def setup(state):
    rnd = random.Random(7)
    blds, x = [], -4
    while x < W + 4:
        bw, bh = rnd.randint(22, 44), rnd.randint(74, 190)
        blds.append((x, bw, bh, rnd.choice(["#101324", "#14182c", "#0d101e", "#181b32"])))
        x += bw + rnd.randint(0, 3)
    state["blds"] = blds
    state["wins"] = [(wx, wy, rnd.random(), rnd.choice(WARM))
                     for bx, bw, bh, _ in blds
                     for wy in range(H - bh + 10, ROAD - 12, 10)
                     for wx in range(bx + 4, bx + bw - 6, 8)]
    state["stars"] = [(rnd.randrange(W), rnd.randrange(70), rnd.random() * 6.28) for _ in range(44)]
    state["drops"] = [(rnd.randrange(W), rnd.random(), rnd.randint(6, 11)) for _ in range(90)]
    state["cars"] = [(rnd.random(), rnd.choice(CARS), rnd.random() < 0.5) for _ in range(14)]
    state["lit"] = {}                                           # window index -> brightness 0..1


def draw(im, d, s, t, state):
    day = s.time_of_day.value
    night = 0.5 + 0.5 * math.cos(day * 2 * math.pi)             # 1 at midnight, 0 at noon
    top = ramp(["#3a6ea8", "#0b1030", "#060816"], night)
    low = ramp(["#b8d4ee", "#4a3566", "#221c40"], night)
    for y in range(0, H, 2):
        d.rectangle((0, y, W, y + 1), fill=mix(top, low, y / H))

    for x, y, ph in state["stars"]:
        a = night * (0.55 + 0.45 * math.sin(t * 1.3 + ph))
        if a > 0.15:
            d.point((x, y), fill=mix(top, "#ffffff", a))

    mx = ((day + 0.25) % 1.0) * (W + 40) - 20                   # one crossing per day: the moon by night, the sun by day
    my = 46 - 30 * math.sin(((day + 0.25) % 1.0) * math.pi)
    d.ellipse((mx - 11, my - 11, mx + 11, my + 11), fill=mix("#fff3c4", "#f4f1e6", night))
    if night > 0.5:
        d.ellipse((mx - 5, my - 13, mx + 13, my + 7), fill=mix(top, low, my / H))

    for x, bw, bh, col in state["blds"]:
        d.rectangle((x, H - bh, x + bw, ROAD), fill=mix(col, "#39405c", (1 - night) * 0.6))

    want, lit = 0.08 + 0.9 * s.cpu.value, state["lit"]          # share of windows lit; each one fades
    for i, (x, y, thr, col) in enumerate(state["wins"]):
        b = lit.get(i, 0.0)
        b += (0.25 if thr < want else -0.12)
        b = lit[i] = max(0.0, min(1.0, b))
        d.rectangle((x, y, x + 3, y + 5), fill=mix("#1e2442", col, b))

    d.rectangle((0, ROAD, W, H), fill="#0b0c14")
    for x in range(int(-t * 30) % 24 - 24, W, 24):
        d.rectangle((x, ROAD + 12, x + 10, ROAD + 12), fill="#3a3c52")
    n = 2 + int(s.net.value * (len(state["cars"]) - 2))         # more traffic, more cars
    for ph, col, left in state["cars"][:n]:
        p = (ph + t / 5.0) % 1.0
        x, y = ((1 - p) if left else p) * (W + 30) - 15, ROAD + (16 if left else 5)
        d.rectangle((x, y, x + 11, y + 4), fill=col)
        d.rectangle((x - 2 if left else x + 11, y + 1, x if left else x + 13, y + 3), fill="#fff7c2" if not left else "#ff3b3b")

    rain = max(0.0, (s.ram.value - 0.6) / 0.4)                  # starts at 60 % memory, full at 100 %
    for x, ph, ln in state["drops"][:int(rain * len(state["drops"]))]:
        y = ((ph + t / 1.4) % 1.0) * (H + 20) - 10
        d.line((x, y, x - 2, y + ln), fill="#8fa6d8")

    show = state.get("show")                                    # tap: a reading over the tapped key
    if show and t < show[1]:
        x0, y0, x1, y1 = key_box(show[0])
        sig = getattr(s, COLUMN[(show[0] - 1) % 5])
        d.rectangle((x0, y0, x1, y1), fill="#0a0c18", outline="#ffc46b")
        text(d, (x0 + 32, y0 + 20), sig.label, 12, "#9aa3c7")
        text(d, (x0 + 32, y0 + 40), sig.text, 18)


def tap(key, x, y, s, t, state):
    state["show"] = (key, t + 3)
