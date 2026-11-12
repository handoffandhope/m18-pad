"""Night city, built on the pad's grid: five towers, one per column of keys, and the bars between the
screens are the alleys between them. A tower's lit floors are its signal, filling from the street up:
CPU, memory, network, disk, and a clock tower whose face shows the time of day. Behind the towers one
sky runs across the whole pad: the moon (the sun by day) crosses it behind the bars, traffic follows
the network, rain falls when memory is tight. A roof beacon blinks above 90 %. Tap a key to read its
tower's signal for 3 s."""
import math
import random

from m18lib.scene import H, W, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram", "net", "disk", "time_of_day"]
FPS = 10
RING = {"signal": "cpu", "color": "#ffc46b"}

ROAD = H - 16
COLUMN = ["cpu", "ram", "net", "disk", "time_of_day"]           # the tower in each column of keys
ROOF = [26, 106, 40, 118, 34]                                   # each roof's y: always inside a key, never behind a bar
BODY = ["#14182c", "#101324", "#181b32", "#0d101e", "#1a1830"]
WARM = ["#ffd678", "#ffbe5f", "#c4e8ff"]
CARS = ["#e8e8f0", "#ff6b6b", "#ffd166", "#5ec8ff"]


def setup(state):
    rnd = random.Random(7)
    wins = []                                                   # (column, x, y, order 0..1 from the street up, colour)
    for c in range(4):                                          # the clock tower has a face, not windows
        cells = [(c * 94 + 6 + i * 11, r * 94 + 5 + j * 15)
                 for r in range(3) for j in range(4) for i in range(5)
                 if r * 94 + 5 + j * 15 > ROOF[c] + 6 and r * 94 + 5 + j * 15 + 9 < ROAD - 4]
        ys = sorted({y for _, y in cells}, reverse=True)
        for x, y in cells:
            wins.append((c, x, y, (ys.index(y) + rnd.random() * 0.8) / len(ys), rnd.choice(WARM)))
    state["wins"] = wins
    state["far"] = [(x, rnd.randint(40, 120)) for x in range(-6, W, 17)]   # a far skyline, behind everything
    state["stars"] = [(rnd.randrange(W), rnd.randrange(150), rnd.random() * 6.28) for _ in range(60)]
    state["drops"] = [(rnd.randrange(W), rnd.random(), rnd.randint(6, 11)) for _ in range(90)]
    state["cars"] = [(rnd.random(), rnd.choice(CARS), rnd.random() < 0.5) for _ in range(14)]
    state["lit"] = {}                                           # window index -> brightness 0..1


def level(s, name):
    v = getattr(s, name).value
    return 0.0 if v is None or v != v else min(1.0, max(0.0, float(v)))


def draw(im, d, s, t, state):
    day = level(s, "time_of_day")
    night = 0.5 + 0.5 * math.cos(day * 2 * math.pi)             # 1 at midnight, 0 at noon
    top = ramp(["#3a6ea8", "#0b1030", "#060816"], night)
    low = ramp(["#b8d4ee", "#4a3566", "#221c40"], night)
    for y in range(0, H, 2):
        d.rectangle((0, y, W, y + 1), fill=mix(top, low, y / H))
    for x, y, ph in state["stars"]:
        a = night * (0.55 + 0.45 * math.sin(t * 1.3 + ph))
        if a > 0.15:
            d.point((x, y), fill=mix(top, "#ffffff", a))

    mx = ((day + 0.25) % 1.0) * (W + 40) - 20                   # one crossing per day, behind the bars and the towers
    my = 40 - 24 * math.sin(((day + 0.25) % 1.0) * math.pi)
    d.ellipse((mx - 11, my - 11, mx + 11, my + 11), fill=mix("#fff3c4", "#f4f1e6", night))
    if night > 0.5:
        d.ellipse((mx - 5, my - 13, mx + 13, my + 7), fill=mix(top, low, my / H))

    for x, h in state["far"]:
        d.rectangle((x, ROAD - h, x + 15, ROAD), fill=mix(low, "#0a0c18", 0.55))

    for c in range(5):                                          # the towers: exactly one key wide
        x0, body = c * 94, mix(BODY[c], "#39405c", (1 - night) * 0.6)
        d.rectangle((x0, ROOF[c], x0 + 63, ROAD), fill=body)
        d.rectangle((x0, ROOF[c], x0 + 63, ROOF[c] + 2), fill=mix(body, "#8a93c0", 0.35))
        d.rectangle((x0 + 26, ROAD - 12, x0 + 37, ROAD), fill="#0a0c18")            # the door
        v = level(s, COLUMN[c])
        d.line((x0 + 32, ROOF[c] - 9, x0 + 32, ROOF[c]), fill="#5a6288")            # mast and beacon
        hot = c < 4 and v > 0.9 and int(t * 2) % 2 == 0
        d.ellipse((x0 + 30, ROOF[c] - 12, x0 + 34, ROOF[c] - 8), fill="#ff4d4d" if hot else "#3a2030")

    lit = state["lit"]
    for i, (c, x, y, order, col) in enumerate(state["wins"]):   # floors light from the street up
        b = lit.get(i, 0.0)
        b += 0.25 if order < level(s, COLUMN[c]) * 1.08 else -0.12
        b = lit[i] = max(0.0, min(1.0, b))
        d.rectangle((x, y, x + 7, y + 9), fill=mix("#1e2442", col, b))

    cx, cy = 4 * 94 + 32, 94 + 32                               # the clock: its face fills key 10
    d.ellipse((cx - 25, cy - 25, cx + 25, cy + 25), fill=mix("#f6ecc8", "#ffe9a8", night), outline="#0a0c18", width=2)
    for k in range(12):
        a = k * math.pi / 6
        d.line((cx + 20 * math.sin(a), cy - 20 * math.cos(a), cx + 23 * math.sin(a), cy - 23 * math.cos(a)), fill="#3a3144")
    for turn, ln, wd in ((day * 2, 12, 4), (day * 24, 19, 2)):  # hour hand, minute hand
        a = turn * 2 * math.pi
        d.line((cx, cy, cx + ln * math.sin(a), cy - ln * math.cos(a)), fill="#1a1626", width=wd)

    d.rectangle((0, ROAD, W, H), fill="#0b0c14")
    for x in range(int(-t * 30) % 24 - 24, W, 24):
        d.rectangle((x, ROAD + 8, x + 10, ROAD + 8), fill="#3a3c52")
    n = 2 + int(level(s, "net") * (len(state["cars"]) - 2))     # more traffic, more cars
    for ph, col, left in state["cars"][:n]:
        p = (ph + t / 5.0) % 1.0
        x, y = ((1 - p) if left else p) * (W + 30) - 15, ROAD + (10 if left else 2)
        d.rectangle((x, y, x + 11, y + 4), fill=col)
        d.rectangle((x - 2 if left else x + 11, y + 1, x if left else x + 13, y + 3), fill="#fff7c2" if not left else "#ff3b3b")

    rain = max(0.0, (level(s, "ram") - 0.6) / 0.4)              # starts at 60 % memory, full at 100 %
    for x, ph, ln in state["drops"][:int(rain * len(state["drops"]))]:
        y = ((ph + t / 1.4) % 1.0) * (H + 20) - 10
        d.line((x, y, x - 2, y + ln), fill="#8fa6d8")

    show = state.get("show")                                    # tap: the tower's reading over the tapped key
    if show and t < show[1]:
        x0, y0, x1, y1 = key_box(show[0])
        sig = getattr(s, COLUMN[(show[0] - 1) % 5])
        d.rectangle((x0, y0, x1, y1), fill="#0a0c18", outline="#ffc46b")
        text(d, (x0 + 32, y0 + 20), sig.label, 12, "#9aa3c7")
        text(d, (x0 + 32, y0 + 40), sig.text, 18)


def tap(key, x, y, s, t, state):
    state["show"] = (key, t + 3)
