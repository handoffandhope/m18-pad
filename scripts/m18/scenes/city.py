"""A city block on the pad's grid: five buildings, one per column of keys, the bars between the screens
are the alleys. Each building is built to show its own number:

cpu          a power plant: the furnace arches glow hotter, the chimneys smoke harder, the dial climbs.
ram          apartments under a water tank: the tank's sight glass fills; above 90 % it drips. People keep
             their own hours in the windows. It rains over the whole block when memory is tight.
net          a telecom tower: the mast sends out more rings, the light strip up the facade runs faster.
disk         a container yard: the stack under the crane grows toward the beam.
time_of_day  a clock tower: the hands, the sky behind everything, the lamps and lit windows at night.

One sky runs behind it all: sun or moon, stars, clouds and traffic pass behind the bars.
Tap a key to read its building's number for 3 s."""
import math
import random

from m18lib.scene import H, W, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram", "net", "disk", "time_of_day"]
FPS = 10
RING = {"signal": "cpu", "color": "#ffc46b"}

ROAD = H - 16
COLUMN = ["cpu", "ram", "net", "disk", "time_of_day"]           # the building in each column of keys
CARS = ["#e8e8f0", "#ff6b6b", "#ffd166", "#5ec8ff"]
BOXES = ["#c8553d", "#2f6f9f", "#e0a526", "#3f8f6b", "#8a5fbf", "#d9d2c0", "#b5462f", "#2b8c8c"]
DARK = "#0a0c1a"


def setup(state):
    rnd = random.Random(7)
    state["far"] = [(x, rnd.randint(40, 120)) for x in range(-6, W, 17)]
    state["stars"] = [(rnd.randrange(W), rnd.randrange(150), rnd.random() * 6.28) for _ in range(60)]
    state["drops"] = [(rnd.randrange(W), rnd.random(), rnd.randint(6, 11)) for _ in range(90)]
    state["cars"] = [(rnd.random(), rnd.choice(CARS), rnd.random() < 0.5) for _ in range(14)]
    state["homes"] = [(94 + 5 + 15 * i, y, rnd.random() * 6.28, rnd.random(), rnd.random())     # apartment windows
                      for y in (118, 138, 192, 210) for i in range(4)]
    state["boxes"] = [rnd.choice(BOXES) for _ in range(45)]
    state["clouds"] = [(rnd.random(), 8 + rnd.randrange(40), 14 + rnd.randrange(12)) for _ in range(4)]
    state["ease"] = {}


def level(s, name, state=None, k=0.3):
    v = getattr(s, name).value
    v = 0.0 if v is None or v != v else min(1.0, max(0.0, float(v)))
    if state is not None:                                       # eased, so nothing jumps
        e = state["ease"]
        v = e[name] = e.get(name, v) + (v - e.get(name, v)) * k
    return v


def draw(im, d, s, t, state):
    day = level(s, "time_of_day")
    cpu, ram, net, disk = (level(s, n, state) for n in ("cpu", "ram", "net", "disk"))
    night = 0.5 + 0.5 * math.cos(day * 2 * math.pi)             # 1 at midnight, 0 at noon
    dusk = min(1.0, max(0.0, (night - 0.35) * 3))               # lamps and windows come on

    def sh(col, k=0.62):                                        # a daytime colour as it looks now
        return mix(col, DARK, night * k)

    # ---- the sky, behind everything and behind the bars
    top = ramp(["#3a6ea8", "#0b1030", "#060816"], night)
    low = ramp(["#b8d4ee", "#4a3566", "#221c40"], night)
    for y in range(0, H, 2):
        d.rectangle((0, y, W, y + 1), fill=mix(top, low, y / H))
    for x, y, ph in state["stars"]:
        a = night * (0.55 + 0.45 * math.sin(t * 1.3 + ph))
        if a > 0.15:
            d.point((x, y), fill=mix(top, "#ffffff", a))
    if night > 0.6 and t % 23 < 0.7:                            # now and then, a shooting star
        k = (t % 23) / 0.7
        x, y = 60 + (int(t / 23) * 97) % 300 + 40 * k, 6 + 18 * k
        d.line((x, y, x - 12, y - 5), fill=mix(top, "#ffffff", 1 - k))
    mx = ((day + 0.25) % 1.0) * (W + 40) - 20                   # one crossing a day: the moon by night, the sun by day
    my = 40 - 24 * math.sin(((day + 0.25) % 1.0) * math.pi)
    d.ellipse((mx - 11, my - 11, mx + 11, my + 11), fill=mix("#fff3c4", "#f4f1e6", night))
    if night > 0.5:
        d.ellipse((mx - 5, my - 13, mx + 13, my + 7), fill=mix(top, low, my / H))
    for ph, y, r in state["clouds"]:
        x = ((ph + t / 160) % 1.0) * (W + 80) - 40
        col = mix(mix(top, "#ffffff", 0.75), top, night * 0.85)
        d.ellipse((x - r, y - r * 0.45, x + r, y + r * 0.45), fill=col)
        d.ellipse((x - r * 0.3, y - r * 0.75, x + r * 0.6, y + r * 0.3), fill=col)
    for x, h in state["far"]:
        d.rectangle((x, ROAD - h, x + 15, ROAD), fill=mix(low, DARK, 0.5))

    # ---- 1. cpu: the power plant
    X = 0
    for cx, cy in ((8, 24), (40, 40)):                          # two chimneys, with smoke
        d.rectangle((X + cx, cy, X + cx + 12, 114), fill=sh("#a3a3ae"))
        d.rectangle((X + cx + 9, cy, X + cx + 12, 114), fill=sh("#7d7d8a"))
        for band in (0, 10):
            d.rectangle((X + cx, cy + band, X + cx + 12, cy + band + 4), fill=sh("#d6453d", 0.45))
        for i in range(1 + int(cpu * 5)):
            p = (t * (0.07 + 0.45 * cpu) + i / (1 + int(cpu * 5)) + cx) % 1.0
            px, py, r = X + cx + 6 + 16 * p + 2 * math.sin(t + i), cy - 3 - 24 * p, 3 + 5 * p
            d.ellipse((px - r, py - r, px + r, py + r), fill=mix(mix("#d8d8e0", "#55586a", cpu), mix(top, low, 0.2), p * 0.8))
    brick = sh("#8a4b3a")
    d.rectangle((X, 112, X + 63, ROAD), fill=brick)
    for y in range(116, ROAD, 6):
        d.line((X, y, X + 63, y), fill=mix(brick, DARK, 0.25))
    d.rectangle((X, 110, X + 63, 113), fill=sh("#c9b8a8"))
    fire = ramp(["#2a1614", "#a3300a", "#ff8a1f", "#fff2b0"], min(1.0, cpu * 1.05))
    for i in range(3):                                          # furnace arches: hotter with load
        ax = X + 5 + i * 19
        glow = mix(fire, "#fff7d0", 0.25 * cpu * (0.5 + 0.5 * math.sin(t * 9 + i * 2)))
        d.rectangle((ax - 1, 127, ax + 15, 153), fill=mix(brick, DARK, 0.45))
        d.pieslice((ax - 1, 119, ax + 15, 137), 180, 360, fill=mix(brick, DARK, 0.45))
        d.rectangle((ax + 1, 129, ax + 13, 153), fill=glow)
        d.pieslice((ax + 1, 121, ax + 13, 137), 180, 360, fill=glow)
        d.line((ax + 7, 122, ax + 7, 153), fill=mix(glow, DARK, 0.5))
        d.line((ax + 1, 140, ax + 13, 140), fill=mix(glow, DARK, 0.5))
    d.rectangle((X, 190, X + 63, 193), fill=sh("#6f7480"))      # a pipe, a row of small panes, the dial, the door
    for i in range(3):
        d.rectangle((X + 5 + i * 11, 204, X + 12 + i * 11, 212), fill=mix("#1c2233", fire, 0.25 + 0.75 * (cpu > 0.25 + i * 0.2)))
    gx, gy = X + 46, 204
    d.ellipse((gx - 9, gy - 9, gx + 9, gy + 9), fill="#e9e4d4", outline=DARK)
    d.arc((gx - 7, gy - 7, gx + 7, gy + 7), 300, 30, fill="#d6453d", width=2)
    a = math.radians(-120 + 240 * cpu)
    d.line((gx, gy, gx + 7 * math.sin(a), gy - 7 * math.cos(a)), fill="#1a1626", width=2)
    d.rectangle((X + 10, ROAD - 14, X + 24, ROAD), fill=mix(brick, DARK, 0.6))

    # ---- 2. ram: apartments under a water tank
    X = 94
    wood, steel = sh("#7a5a3c"), sh("#5b6170")
    for a, b in (((16, 52), (11, 108)), ((48, 52), (53, 108)), ((16, 58), (48, 52)), ((48, 58), (16, 52))):
        d.line((X + a[0], a[1], X + b[0], b[1]), fill=steel, width=2)
    d.rectangle((X + 11, 18, X + 53, 52), fill=wood)
    for x in range(X + 16, X + 53, 6):
        d.line((x, 19, x, 51), fill=mix(wood, DARK, 0.3))
    for y in (24, 44):
        d.line((X + 11, y, X + 53, y), fill=steel)
    d.polygon(((X + 8, 18), (X + 32, 5), (X + 56, 18)), fill=sh("#4d5364"))
    d.rectangle((X + 28, 21, X + 36, 49), fill="#0e1322")       # the sight glass: memory in use
    fill = 26 * ram
    water = "#ff6b5e" if ram > 0.9 else "#58b8ff"
    d.rectangle((X + 29, 48 - fill, X + 35, 48), fill=water)
    d.line((X + 29, 48 - fill, X + 35, 48 - fill), fill=mix(water, "#ffffff", 0.6))
    if ram > 0.9:
        for i in range(3):
            p = (t * 1.2 + i / 3) % 1.0
            d.rectangle((X + 20 + i * 12, 53 + 9 * p, X + 21 + i * 12, 55 + 9 * p), fill="#58b8ff")
    wall = sh("#c9b08f")
    d.rectangle((X, 108, X + 63, ROAD), fill=wall)
    d.rectangle((X, 106, X + 63, 110), fill=sh("#8f7a63"))
    d.rectangle((X + 44, 97, X + 56, 106), fill=sh("#8f7a63"))  # the stair hut
    for i, (wx, wy, ph, who, kind) in enumerate(state["homes"]):
        on = dusk > 0.1 and math.sin(t * 0.07 + ph) > 0.9 - 1.5 * dusk * (0.4 + 0.6 * who)
        pane = mix("#ffd98a" if kind > 0.3 else "#bfe3ff", "#fff3cf", 0.3) if on else mix("#8fb6d8", "#161b2e", 0.25 + 0.75 * night)
        d.rectangle((wx - 1, wy - 1, wx + 10, wy + 12), fill=mix(wall, DARK, 0.3))
        d.rectangle((wx, wy, wx + 9, wy + 11), fill=pane)
        d.rectangle((wx, wy, wx + (3 if kind > 0.5 else 1), wy + 11), fill=mix(pane, "#b0503c" if who > 0.5 else "#3f7f6a", 0.55))
        if on and who > 0.7:                                    # somebody is home
            bob = int(math.sin(t * 0.6 + ph) * 2)
            d.rectangle((wx + 5 + bob, wy + 5, wx + 7 + bob, wy + 11), fill="#2a2030")
            d.rectangle((wx + 5 + bob, wy + 3, wx + 7 + bob, wy + 4), fill="#2a2030")
        if kind < 0.35:                                         # a flower box
            d.rectangle((wx - 1, wy + 12, wx + 10, wy + 13), fill=sh("#6b4a35"))
            for fx in range(wx, wx + 10, 3):
                d.point((fx, wy + 11), fill=sh("#e85a71" if (fx + i) % 2 else "#ffd166", 0.4))
    d.line((X + 2, 110, X + 2, ROAD), fill=mix(wall, DARK, 0.35), width=2)      # a drainpipe
    d.line((X + 6, 130, X + 58, 131), fill=mix(wall, DARK, 0.5))                # washing on a line
    for i, col in enumerate(("#e85a71", "#f1ece0", "#5ec8ff", "#ffd166", "#f1ece0")):
        lx = X + 10 + i * 10
        sway = int(1.5 * math.sin(t * 1.7 + i))
        d.polygon(((lx, 131), (lx + 6, 131), (lx + 6 + sway, 137), (lx + sway, 137)), fill=sh(col, 0.5))
    d.rectangle((X + 22, ROAD - 14, X + 34, ROAD), fill=mix(wall, DARK, 0.65))
    for i in range(5):                                          # a striped awning over the door
        d.rectangle((X + 18 + i * 4, ROAD - 19, X + 21 + i * 4, ROAD - 15), fill=sh("#d6453d" if i % 2 else "#f1ece0", 0.4))
    cat = X + 6 + 34 * (0.5 + 0.5 * math.sin(t * 0.21))         # the cat on the roof
    d.rectangle((cat, 102, cat + 6, 105), fill="#15131c")
    d.rectangle((cat + (5 if math.cos(t * 0.21) > 0 else 0), 100, cat + (6 if math.cos(t * 0.21) > 0 else 1), 102), fill="#15131c")
    d.line((cat + (0 if math.cos(t * 0.21) > 0 else 6), 103, cat + (-2 if math.cos(t * 0.21) > 0 else 8), 99), fill="#15131c")

    # ---- 3. net: the telecom tower
    X = 188
    glass = sh("#3f6f93", 0.7)
    d.rectangle((X + 5, 52, X + 58, ROAD), fill=glass)
    d.rectangle((X + 5, 52, X + 58, 54), fill=sh("#9fc4dd"))
    for y in list(range(100, 156, 9)) + list(range(192, 226, 9)):   # ribbon windows, inside the keys
        d.rectangle((X + 8, y, X + 55, y + 4), fill=mix(mix("#9fd0ee", "#1a2c44", night), "#bfe9ff", dusk * (0.25 + 0.2 * math.sin(y + t * 0.3))))
    d.rectangle((X + 29, 56, X + 34, ROAD - 16), fill="#0d1626")
    for y in range(58, ROAD - 18, 5):                           # the light strip: data going up
        k = ((y / 5 + t * (1 + 9 * net)) % 6) / 6
        d.rectangle((X + 30, y, X + 33, y + 2), fill=mix("#5ef0c8", "#0d1626", min(1.0, k * 1.6)))
    tip = (X + 32, 22)
    for dx in (-7, 7):
        d.line((X + 32 + dx, 52, X + 32, 22), fill=sh("#c4c9d6", 0.4))
    for y in (44, 36, 29):
        w = (y - 22) * 7 / 30
        d.line((X + 32 - w, y, X + 32 + w, y), fill=sh("#c4c9d6", 0.4))
    d.ellipse((X + 16, 38, X + 26, 48), fill=sh("#e3e6ee", 0.4))
    d.ellipse((X + 39, 32, X + 47, 40), fill=sh("#e3e6ee", 0.4))
    d.ellipse((tip[0] - 2, tip[1] - 3, tip[0] + 2, tip[1] + 1), fill="#ff4d4d" if int(t * 2) % 2 else "#5a2030")
    for i in range(1 + int(net * 3.99)):                        # rings from the mast: more and faster with traffic
        p = (t * (0.25 + 1.2 * net) + i / (1 + int(net * 3.99))) % 1.0
        r, col = 5 + 24 * p, mix("#7df9ff", mix(top, low, 0.15), p)
        d.arc((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), 205, 250, fill=col, width=2)
        d.arc((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), 290, 335, fill=col, width=2)
    d.rectangle((X + 20, ROAD - 16, X + 43, ROAD), fill=mix("#bfe9ff", "#1a2c44", 0.75 - 0.5 * dusk))
    d.line((X + 31, ROAD - 16, X + 31, ROAD), fill=glass)
    d.rectangle((X + 18, ROAD - 21, X + 45, ROAD - 18), fill="#5ef0c8" if int(t * 1.5) % 4 else "#1d5d55")

    # ---- 4. disk: the container yard
    X = 282
    crane = sh("#e0a526", 0.45)
    for r in range(15):                                         # the stack: 3 wide, growing toward the beam
        for j in range(3):
            n = r * 3 + (j * 2) % 3
            if n < round(disk * 45):
                bx, by = X + 6 + j * 18, ROAD - 13 * (r + 1)
                col = sh(state["boxes"][n], 0.5)
                d.rectangle((bx, by, bx + 16, by + 11), fill=col)
                for cx in range(bx + 3, bx + 16, 4):
                    d.line((cx, by + 1, cx, by + 10), fill=mix(col, DARK, 0.25))
                d.line((bx, by, bx + 16, by), fill=mix(col, "#ffffff", 0.25))
    d.rectangle((X, 12, X + 3, ROAD), fill=crane)
    d.rectangle((X + 60, 12, X + 63, ROAD), fill=crane)
    d.rectangle((X, 10, X + 63, 16), fill=crane)
    for x in range(X + 2, X + 62, 8):
        d.line((x, 16, x + 4, 10), fill="#1a1626")
    tx = X + 12 + 38 * (0.5 + 0.5 * math.sin(t * 0.35))         # the trolley and its hook
    hy = 30 + 10 * math.sin(t * 0.5)
    d.rectangle((tx - 5, 16, tx + 5, 20), fill=sh("#3a3f52", 0.3))
    d.line((tx, 20, tx, hy), fill="#c4c9d6")
    d.arc((tx - 3, hy - 1, tx + 3, hy + 5), 0, 200, fill="#c4c9d6", width=2)
    d.ellipse((X + 30, 6, X + 34, 10), fill="#ff4d4d" if disk > 0.9 and int(t * 2) % 2 else "#5a2030")

    # ---- 5. time_of_day: the clock tower
    X = 376
    stone = sh("#9c95a8")
    d.rectangle((X + 11, 40, X + 52, ROAD), fill=stone)
    d.rectangle((X + 44, 40, X + 52, ROAD), fill=mix(stone, DARK, 0.2))
    d.rectangle((X + 5, 196, X + 58, ROAD), fill=stone)
    d.rectangle((X + 5, 194, X + 58, 197), fill=mix(stone, "#ffffff", 0.2))
    for y in range(100, 158, 12):                               # quoins up the corners
        d.rectangle((X + 11, y, X + 14, y + 5), fill=mix(stone, DARK, 0.25))
        d.rectangle((X + 49, y + 6, X + 52, y + 11), fill=mix(stone, DARK, 0.35))
    d.polygon(((X + 6, 40), (X + 32, 8), (X + 58, 40)), fill=sh("#4a4763"))
    d.line((X + 32, 8, X + 32, 1), fill=sh("#c4c9d6", 0.3))
    flag = 2 * math.sin(t * 3)
    d.polygon(((X + 32, 1), (X + 43, 3 + flag), (X + 32, 6)), fill=sh("#d6453d", 0.4))
    d.rectangle((X + 24, 46, X + 40, 63), fill="#141222")       # the belfry; the bell swings on the hour
    d.pieslice((X + 24, 40, X + 40, 54), 180, 360, fill="#141222")
    hour = (day * 24) % 1.0
    sw = 4 * math.sin(t * 5) * max(0.0, 1 - hour * 30)
    d.pieslice((X + 27 + sw, 48, X + 37 + sw, 62), 180, 360, fill="#e0b040")
    d.rectangle((X + 27 + sw, 55, X + 37 + sw, 57), fill="#e0b040")
    cx, cy = X + 32, 126                                        # the face fills key 10
    d.ellipse((cx - 25, cy - 25, cx + 25, cy + 25), fill=mix(stone, DARK, 0.45))
    d.ellipse((cx - 22, cy - 22, cx + 22, cy + 22), fill=mix("#f6ecc8", "#ffe9a8", dusk))
    for k in range(12):
        a = k * math.pi / 6
        d.line((cx + (15 if k % 3 == 0 else 18) * math.sin(a), cy - (15 if k % 3 == 0 else 18) * math.cos(a),
                cx + 20 * math.sin(a), cy - 20 * math.cos(a)), fill="#3a3144", width=2 if k % 3 == 0 else 1)
    for turn, ln, wd in ((day * 2, 11, 4), (day * 24, 17, 2)):  # hour hand, minute hand
        a = turn * 2 * math.pi
        d.line((cx, cy, cx + ln * math.sin(a), cy - ln * math.cos(a)), fill="#1a1626", width=wd)
    d.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill="#b0503c")
    d.rectangle((X + 24, 212, X + 40, ROAD), fill="#2a1c1a")    # the door, a lantern, slit windows, ivy
    d.pieslice((X + 24, 203, X + 40, 221), 180, 360, fill="#2a1c1a")
    d.line((X + 32, 205, X + 32, ROAD), fill="#4a332c")
    for wx in (X + 14, X + 47):
        d.rectangle((wx, 200, wx + 2, 209), fill=mix("#141222", "#ffd98a", dusk))
    for i in range(22):
        ix, iy = X + 6 + (i * 7) % 9, ROAD - 3 - i * 2
        d.rectangle((ix, iy, ix + 1, iy + 1), fill=sh("#4f9a5b" if i % 3 else "#2f6f3f", 0.5))

    # ---- the street: pavement, lamps, traffic
    d.rectangle((0, ROAD, W, H), fill="#0b0c14")
    d.line((0, ROAD, W, ROAD), fill=sh("#8a8fa3"))
    for x in range(int(-t * 30) % 24 - 24, W, 24):
        d.rectangle((x, ROAD + 8, x + 10, ROAD + 8), fill="#3a3c52")
    for c in range(5):                                          # one lamp per key, lit at dusk
        lx = c * 94 + 59
        d.line((lx, ROAD - 22, lx, ROAD), fill="#2a2d3c", width=2)
        d.line((lx - 5, ROAD - 22, lx, ROAD - 22), fill="#2a2d3c", width=2)
        if dusk > 0.05:                                         # a pool of light on the pavement
            d.ellipse((lx - 16, ROAD - 1, lx + 4, ROAD + 3), fill=mix("#0b0c14", "#ffe2a0", 0.55 * dusk))
            d.ellipse((lx - 9, ROAD - 23, lx - 2, ROAD - 16), fill=mix("#55586a", "#ffe2a0", 0.6 * dusk))
        d.rectangle((lx - 7, ROAD - 21, lx - 4, ROAD - 19), fill=mix("#55586a", "#fff7dc", dusk))
    n = 2 + int(net * (len(state["cars"]) - 2))                 # more traffic, more cars
    for ph, col, left in state["cars"][:n]:
        p = (ph + t / 5.0) % 1.0
        x, y = ((1 - p) if left else p) * (W + 30) - 15, ROAD + (10 if left else 2)
        d.rectangle((x, y, x + 11, y + 4), fill=col)
        d.rectangle((x + 3, y - 2, x + 8, y), fill=mix(col, DARK, 0.4))
        d.rectangle((x - 2 if left else x + 11, y + 1, x if left else x + 13, y + 3), fill="#fff7c2" if not left else "#ff3b3b")

    rain = max(0.0, (ram - 0.6) / 0.4)                          # starts at 60 % memory, full at 100 %
    for x, ph, ln in state["drops"][:int(rain * len(state["drops"]))]:
        y = ((ph + t / 1.4) % 1.0) * (H + 20) - 10
        d.line((x, y, x - 2, y + ln), fill="#8fa6d8")

    show = state.get("show")                                    # tap: the building's number over the tapped key
    if show and t < show[1]:
        x0, y0, x1, y1 = key_box(show[0])
        sig = getattr(s, COLUMN[(show[0] - 1) % 5])
        d.rectangle((x0, y0, x1, y1), fill="#0a0c18", outline="#ffc46b")
        text(d, (x0 + 32, y0 + 20), sig.label, 12, "#9aa3c7")
        text(d, (x0 + 32, y0 + 40), sig.text, 18)


def tap(key, x, y, s, t, state):
    state["show"] = (key, t + 3)
