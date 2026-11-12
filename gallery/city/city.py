"""A night city across all 15 keys.

cpu:           lit windows. More windows light as CPU load rises (eased, so they fade).
ram:           above 80% it rains. Above 80% only; the drop count grows with memory use.
net:           traffic. More and faster cars on the bottom-row road as network use rises.
time_of_day:   the moon crosses the top row from left (midnight) to right (next midnight).
tap:           a building key (rows 2-3) shows the CPU % for 3 s; a road key shows net.
"""
import math
import random

from m18lib.scene import H, W, key_box, mix, text

SIGNALS = ["cpu", "ram", "net", "time_of_day"]
FPS = 8
RING = {"signal": "cpu", "color": "#4cc9f0"}

ROAD_Y = 226                       # top of the road, inside the bottom key row
SKY_TOP = "#060a1c"
SKY_BOTTOM = "#2a2566"
BUILDING = "#0b0f26"
WINDOW_LIT = "#ffd27a"
WINDOW_DARK = "#1b2250"
ROAD = "#10142e"
RAIN_ON = 0.8


def level(s, name):
    """A signal's 0..1 value; 0 when the signal is unknown."""
    try:
        v = float(getattr(s, name).value)
    except (TypeError, ValueError, AttributeError):
        return 0.0
    if v != v:                     # NaN
        return 0.0
    return min(1.0, max(0.0, v))


def ease(state, key, target, k):
    """Move a stored value toward target. The first call starts at target, so a pinned shot is not dim."""
    cur = state.get(key)
    if cur is None:
        cur = target
    cur += (target - cur) * k
    state[key] = cur
    return cur


def sky_at(y):
    return mix(SKY_TOP, SKY_BOTTOM, min(1.0, y / ROAD_Y))


def setup(state):
    rng = random.Random(7)         # fixed seed: the same skyline every run
    state["sky"] = [sky_at(y) for y in range(H)]
    state["stars"] = [(rng.randrange(W), rng.randrange(0, 90), rng.uniform(0, 6.28)) for _ in range(16)]
    buildings = []
    x, n = -6, 0
    while x < W:
        bw = rng.randrange(26, 42)
        top = rng.randrange(100, 172)
        windows = []
        wy = top + 7
        while wy + 6 <= ROAD_Y - 6:
            wx = x + 6
            while wx + 6 <= x + bw - 5:
                windows.append((wx, wy, rng.random()))   # u < cpu means lit
                wx += 9
            wy += 10
        buildings.append((x, bw, top, n % 3 == 0, windows))  # every third roof has a beacon
        x += bw + rng.randrange(2, 6)
        n += 1
    state["buildings"] = buildings
    state["drops"] = [(rng.randrange(W), rng.randrange(H), rng.uniform(0.7, 1.0)) for _ in range(70)]
    state["cars"] = [
        (rng.randrange(W + 30), i % 2, rng.uniform(0.7, 1.3),
         rng.choice(["#ff5c7a", "#e8e8f0", "#ffd27a", "#7ee081"]))
        for i in range(14)
    ]


def draw(im, d, s, t, state):
    cpu = ease(state, "cpu_e", level(s, "cpu"), 0.35)
    net = ease(state, "net_e", level(s, "net"), 0.2)
    ram = ease(state, "ram_e", level(s, "ram"), 0.2)
    tod = level(s, "time_of_day")
    sky = state["sky"]

    # sky
    for y in range(H):
        d.line((0, y, W, y), fill=sky[y])

    # stars, twinkling
    for i, (x, y, ph) in enumerate(state["stars"]):
        f = 0.5 + 0.5 * math.sin(t * 2.0 + ph)
        d.rectangle((x, y, x + 1, y + 1), fill=mix(sky[y], "#dfe8ff", 0.3 + 0.6 * f))

    # moon: left at midnight, high at dawn/dusk, right at the next midnight
    mx = -10 + tod * (W + 20)
    my = 20 + 36 * (2 * tod - 1) ** 2
    d.ellipse((mx - 10, my - 10, mx + 10, my + 10), fill="#f4ecc1")
    d.ellipse((mx - 4, my - 12, mx + 14, my + 8), fill=sky[int(min(H - 1, max(0, my)))])

    # buildings and their windows
    for bx, bw, top, beacon, windows in state["buildings"]:
        d.rectangle((bx, top, bx + bw, ROAD_Y), fill=BUILDING)
        if beacon and math.sin(t * 3 + bx) > 0:
            cx = bx + bw // 2
            d.ellipse((cx - 2, top - 5, cx + 2, top - 1), fill="#ff4d6d")
        for wx, wy, u in windows:
            d.rectangle((wx, wy, wx + 5, wy + 6), fill=WINDOW_LIT if u < cpu else WINDOW_DARK)

    # road with a centre dash line, and cars: count and speed follow net
    d.rectangle((0, ROAD_Y, W, H), fill=ROAD)
    d.line((0, ROAD_Y, W, ROAD_Y), fill="#2a3266", width=2)
    for x in range(0, W, 24):
        d.rectangle((x, 239, x + 12, 240), fill="#3a4480")
    n_cars = 2 + int(round(net * 12))
    for x0, lane, spd, col in state["cars"][:n_cars]:
        direction = 1 if lane == 0 else -1
        speed = (20 + 150 * net) * spd
        x = (x0 + direction * speed * t) % (W + 30) - 15
        y = 230 if lane == 0 else 243
        d.rectangle((x, y, x + 14, y + 6), fill=col)
        front = x + 13 if direction == 1 else x + 1
        d.rectangle((front, y + 1, front + 1, y + 2),
                    fill="#fff7c2" if direction == 1 else "#ff3b4e")

    # rain only above 80% memory
    if ram > RAIN_ON:
        k = min(1.0, 0.5 + (ram - RAIN_ON) / (1 - RAIN_ON) * 0.5)
        for x0, y0, sp in state["drops"][:int(len(state["drops"]) * k)]:
            y = (y0 + t * 190 * sp) % H
            d.line((x0, y, x0 - 3, y + 9), fill="#8fb4ea", width=1)

    # tapped key: its reading as text for a few seconds
    tapped = state.get("tapped")
    if tapped and t < tapped[1]:
        key = tapped[0]
        x0, y0, x1, y1 = key_box(key)
        d.rectangle((x0, y0, x1, y1), fill="#05081a")
        if key >= 11:
            label, value = "NET", str(getattr(s, "net").text)
        else:
            label, value = "CPU", str(s.cpu.text)
        text(d, (x0 + 32, y0 + 18), label, 11, "#9aa6d8")
        text(d, (x0 + 32, y0 + 40), value, 18, "#ffd27a")


def tap(key, x, y, s, t, state):
    if key >= 6:                   # rows 2 and 3: buildings and road
        state["tapped"] = (key, t + 3)
