"""A city seen through a window, from about twenty floors up. The pad's fifteen screens are the panes and
the bars between them are the glazing bars, so nothing is lined up with them: a district runs to the
horizon in perspective, and towers, traffic and smoke pass behind the bars as they would behind a window.

cpu          the power plant, right: furnace doors glow hotter and its two chimneys smoke harder.
ram          the water tank on a roof, left: its sight glass fills, red above 90 %. When memory is tight
             it rains, and the drops run down the glass, pane by pane.
net          traffic on the boulevard, and the rings from the tower downtown.
disk         the container yard below: painted bays fill up, then stack.
time_of_day  the clock tower, the sky, and the city's lights at night. Pigeons leave the tower on the hour.

Inside, a cat sits on the sill. Tap a pane to read the number of what you see in it for 3 s.

The city itself never moves, so it is drawn once and kept, and redrawn every five minutes of the day
as the light changes; only smoke, traffic, water, hands and rain are drawn each frame."""
import math
import random

from PIL import Image, ImageDraw

from m18lib.scene import H, W, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram", "net", "disk", "time_of_day"]
FPS = 10
RING = {"signal": "cpu", "color": "#ffc46b"}

F, VX, HOR, CAM = 240.0, 314.0, 66.0, 70.0                      # lens, vanishing point, horizon, eye height
PANE = ["time_of_day", "time_of_day", "time_of_day", "net", "cpu",      # what each key looks at, keys 1..15
        "ram", "ram", "time_of_day", "net", "cpu",
        "disk", "disk", "disk", "net", "cpu"]
DARK = "#0a0c1a"
PAL = ["#c9a88a", "#8a4b3a", "#b9a58c", "#9c95a8", "#a3856b", "#7d8597", "#c7b8a0", "#96705a", "#b0b5c0"]
GLASS = ["#3f6f93", "#4f86a8", "#2f5f86", "#5a8fa0"]
CARS = ["#e8e8f0", "#ff6b6b", "#ffd166", "#5ec8ff", "#9be38a"]
BOXES = ["#c8553d", "#2f6f9f", "#e0a526", "#3f8f6b", "#8a5fbf", "#d9d2c0", "#b5462f", "#2b8c8c"]
CELL, BLOCK, ROWS, COLS, RIVER = 44, 34, 20, 22, 5
KEEP_CLEAR = [(-94, -14, 88, 122), (-134, -96, 88, 122), (-60, -39, 124, 142), (-125, -75, 126, 160), (12, 60, 95, 128)]
BAYS = [(x, z) for z in (108, 93) for x in (-94, -78, -62, -46, -30)]   # the yard: far row first
CHIMNEYS = (28, 40)


def P(x, y, z):
    """World (across, up, away) to the glass."""
    k = F / z
    return (VX + x * k, HOR - (y - CAM) * k)


def setup(state):
    rnd = random.Random(11)
    items = []
    for j in range(ROWS):
        z0 = 88 + CELL * j
        if j == RIVER:
            continue
        for i in range(-COLS, COLS):
            x0 = 12 + CELL * i if i >= 0 else -12 - CELL * (-i - 1) - BLOCK
            if any(x0 < c[1] and x0 + BLOCK > c[0] and z0 < c[3] and z0 + BLOCK > c[2] for c in KEEP_CLEAR):
                continue
            cx, cz = x0 + 17, z0 + 17
            tall = 115 * math.exp(-(math.hypot(cx + 40, cz - 470) / 150) ** 2)
            lots = []
            for a in ((1, 0) if x0 > 0 else (0, 1)):            # far from the boulevard first, far in depth first
                for b in (1, 0):
                    h = 7 + rnd.random() * 14 + tall * (0.35 + rnd.random() * 0.9)
                    col = rnd.choice(GLASS) if tall > 35 and rnd.random() < 0.6 else rnd.choice(PAL)
                    lots.append((x0 + 1 + a * 17, x0 + 16 + a * 17, z0 + 1 + b * 17, z0 + 16 + b * 17, h, col, rnd.random()))
            park = rnd.random() < 0.07 and tall < 30
            items.append((z0, abs(cx), "park" if park else "lots", x0, lots))
    items += [(88, 115, "park", -134, [(-134 + a * 17, 0, 88 + b * 17, 0, 0, "", rnd.random()) for a in (0, 1) for b in (0, 1)]),
              (126, 100, "flats", 0, []), (126, 49.5, "clock", 0, []), (95, 36, "plant", 0, []),
              (88, 54, "yard", 0, []), (430, 20, "mast", 0, [])]
    state["items"] = sorted(items, key=lambda it: (-it[0], -it[1]))
    state["stars"] = [(rnd.randrange(W), rnd.randrange(int(HOR)), rnd.random()) for _ in range(40)]
    state["clouds"] = [(rnd.random(), 6 + rnd.randrange(34), 18 + rnd.randrange(14)) for _ in range(5)]
    state["cars"] = [(rnd.random(), rnd.choice(CARS)) for _ in range(44)]
    order = [(lv, i) for lv in range(2) for i in rnd.sample(range(10), 10)]     # bays fill in no order
    state["boxes"] = [(lv, i, rnd.choice(BOXES)) for lv, i in order]
    state["tv"] = [(rnd.uniform(-170, 150), rnd.uniform(4, 16), rnd.choice((132, 176, 220)), rnd.random() * 6.28) for _ in range(14)]
    state["beads"] = [(rnd.randrange(3, 61), rnd.random(), 0.5 + rnd.random()) for _ in range(15 * 7)]
    glare = Image.new("L", (W, H), 0)                           # daylight on each pane of glass
    g = ImageDraw.Draw(glare)
    for k in range(1, 16):
        x0, y0, _, _ = key_box(k)
        g.polygon(((x0 + 40, y0), (x0 + 52, y0), (x0 + 16, y0 + 64), (x0 + 4, y0 + 64)), fill=16)
        g.polygon(((x0 + 57, y0), (x0 + 61, y0), (x0 + 25, y0 + 64), (x0 + 21, y0 + 64)), fill=9)
    state.update(glare=glare, ease={}, bg=None, bg_key=None)


def level(s, name, state=None, k=0.3):
    v = getattr(s, name).value
    v = 0.0 if v is None or v != v else min(1.0, max(0.0, float(v)))
    if state is not None:                                       # eased, so nothing jumps
        e = state["ease"]
        v = e[name] = e.get(name, v) + (v - e.get(name, v)) * k
    return v


def light(day):
    night = 0.5 + 0.5 * math.cos(day * 2 * math.pi)             # 1 at midnight, 0 at noon
    dusk = min(1.0, max(0.0, (night - 0.35) * 3))               # lamps and windows come on
    top = ramp(["#3a6ea8", "#0b1030", "#060816"], night)
    low = ramp(["#b8d4ee", "#4a3566", "#221c40"], night)
    haze = mix(low, top, 0.25)

    def sh(col, z=0.0, k=0.5):                                 # a daytime colour now, and through the air
        return mix(mix(col, DARK, night * k), haze, min(0.75, max(0.0, (z - 90) / 900)))
    return night, dusk, top, low, haze, sh


def boxer(d, sh, dusk):
    def box(x0, x1, z0, z1, h, col, seed=0.0, y0=0.0, plain=False):
        k = F / z0
        edge = P(x1 if x1 < 0 else x0, 0, z1)[0]
        if (x1 < 0 and edge < 0) or (x0 > 0 and edge > W) or P(0, h, z1)[1] > H:
            return
        c = sh(col, z0)
        a, b = P(x0, h, z0), P(x1, y0, z0)
        if x1 < 0 or x0 > 0:
            x = x1 if x1 < 0 else x0
            d.polygon((P(x, h, z0), P(x, h, z1), P(x, y0, z1), P(x, y0, z0)), fill=mix(c, DARK, 0.3))
        if h < CAM:
            d.polygon((P(x0, h, z0), P(x1, h, z0), P(x1, h, z1), P(x0, h, z1)), fill=mix(c, sh("#8d919c", z0), 0.55))
        d.rectangle((a[0], a[1], b[0], b[1]), fill=c)
        if plain:
            return
        if k >= 1.3:                                            # floors
            for fl in range(1, int(h / 3.6) + 1):
                y = HOR - (fl * 3.6 - 1.6 - CAM) * k
                d.rectangle((a[0] + 1, y, b[0] - 1, y + max(1, k * 0.9)), fill=mix(c, "#1c2233", 0.45))
        if dusk > 0.2:                                          # lights
            sz = max(1.0, k * 1.1)
            for q in range(max(1, int(h / 4))):
                u, v = (seed * 37.1 + q * 0.618) % 1.0, (seed * 91.7 + q * 0.414) % 1.0
                x, y = a[0] + 1 + u * (b[0] - a[0] - 2 - sz), a[1] + 1 + v * (b[1] - a[1] - 2 - sz)
                d.rectangle((x, y, x + sz, y + sz), fill="#ffd98a" if q % 4 else "#bfe3ff")
    return box


def city(state, day):
    """Everything that stands still, for this light."""
    night, dusk, top, low, haze, sh = light(day)
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    box = boxer(d, sh, dusk)
    for y in range(0, int(HOR) + 2, 2):
        d.rectangle((0, y, W, y + 1), fill=mix(top, low, y / HOR))
    for x, y, b in state["stars"]:
        if night * b > 0.3:
            d.point((x, y), fill=mix(top, "#ffffff", night * b))
    mx = ((day + 0.25) % 1.0) * (W + 40) - 20                   # one crossing a day: the moon by night, the sun by day
    my = 30 - 20 * math.sin(((day + 0.25) % 1.0) * math.pi)
    d.ellipse((mx - 11, my - 11, mx + 11, my + 11), fill=mix("#fff3c4", "#f4f1e6", night))
    if night > 0.5:
        d.ellipse((mx - 5, my - 13, mx + 13, my + 7), fill=mix(top, low, my / HOR))
    d.rectangle((0, HOR, W, H), fill=mix(haze, sh("#5f8f5a"), 0.4))     # hills beyond the city
    edge = P(0, 0, 88 + CELL * ROWS)[1]
    for y in range(int(edge), H, 2):                            # streets
        d.rectangle((0, y, W, y + 1), fill=mix(haze, sh("#454853", 0, 0.7), min(1.0, 0.25 + (y - edge) / 50)))
    zr = 88 + CELL * RIVER
    d.polygon((P(-2000, 0, zr - 4), P(2000, 0, zr - 4), P(2000, 0, zr + 38), P(-2000, 0, zr + 38)), fill=sh("#3f7fb8", zr, 0.75))
    for bx in range(-8 * CELL - 17, 8 * CELL, 2 * CELL):        # bridges
        d.polygon((P(bx - 4, 0, zr - 4), P(bx + 4, 0, zr - 4), P(bx + 4, 0, zr + 38), P(bx - 4, 0, zr + 38)), fill=sh("#8d919c", zr))
    d.polygon((P(-12, 0, 60), P(12, 0, 60), P(12, 0, 960), P(-12, 0, 960)), fill=sh("#3b3e4b", 0, 0.7))    # the boulevard
    d.polygon((P(-1, 0, 60), P(1, 0, 60), P(1, 0, 960), P(-1, 0, 960)), fill=sh("#6f8f62", 0, 0.6))
    for z in range(90, 400, 14):
        c, r = P(0, 3, z), max(1.0, F / z * 1.6)
        d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=sh("#3f8a4a", z, 0.6))

    for z0, _, kind, x0, lots in state["items"]:
        k = F / z0
        if kind in ("lots", "park"):
            near = P(x0 if x0 > 0 else x0 + BLOCK, 0, z0 + BLOCK)[0]
            if (x0 > 0 and near > W + 40) or (x0 < 0 and near < -40):
                continue
            d.polygon((P(x0, 0, z0), P(x0 + BLOCK, 0, z0), P(x0 + BLOCK, 0, z0 + BLOCK), P(x0, 0, z0 + BLOCK)),
                      fill=sh("#9a9daa" if kind == "lots" else "#5f9a5b", z0, 0.75))
        if kind == "park":
            for lx0, _, lz0, _, _, _, seed in lots:
                for q in range(3):
                    c, r = P(lx0 + 3 + q * 5, 4, lz0 + 3 + (q * 7 + seed * 9) % 10), max(1.0, k * 3.2)
                    d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r * 0.8), fill=sh("#2f7f3f" if q % 2 else "#3f9a4f", z0))
        elif kind == "lots" and k > 1.0:
            for lx0, lx1, lz0, lz1, h, col, seed in lots:
                box(lx0, lx1, lz0, lz1, h, col, seed)
        elif kind == "lots":
            box(x0 + 1, x0 + BLOCK - 1, z0 + 1, z0 + BLOCK - 1, max(l[4] for l in lots) * 0.8, lots[0][5], lots[0][6])
        elif kind == "mast":                                    # the tower downtown, at the end of the boulevard
            box(15, 25, 430, 440, 150, "#7d8597", plain=True)
            c, r = P(20, 140, 430), 7
            d.ellipse((c[0] - r, c[1] - r * 0.7, c[0] + r, c[1] + r * 0.7), fill=sh("#c4c9d6", 300))
            d.line((*c, c[0], c[1] - 16), fill=sh("#c4c9d6", 300), width=2)
        elif kind == "flats":                                   # apartments, and the water tank on their roof
            box(-125, -75, 126, 160, 34, "#c9a88a", seed=0.37)
            a, b = P(-113, 53, 132), P(-93, 38, 132)
            for x in (a[0] + 4, b[0] - 4):
                d.line((x, b[1], x, P(0, 34, 132)[1]), fill=sh("#5b6170"), width=2)
            d.rectangle((a[0], a[1], b[0], b[1]), fill=sh("#7a5a3c"))
            for x in range(int(a[0]) + 5, int(b[0]), 6):
                d.line((x, a[1], x, b[1]), fill=mix(sh("#7a5a3c"), DARK, 0.3))
            d.polygon(((a[0] - 3, a[1] + 1), ((a[0] + b[0]) / 2, a[1] - 13), (b[0] + 3, a[1] + 1)), fill=sh("#4d5364"))
            for y in (a[1] + 4, b[1] - 3):
                d.line((a[0], y, b[0], y), fill=sh("#3a3f4c"), width=2)
        elif kind == "clock":
            box(-58, -41, 126, 140, 48, "#9c95a8", plain=True)
            a, b = P(-59.5, 48, 126), P(-39.5, 48, 126)
            d.polygon((a, P(-49.5, 61, 126), b), fill=sh("#4a4763"))
            fx, fy = P(-49.5, 38.4, 126)
            d.ellipse((fx - 19, fy - 19, fx + 19, fy + 19), fill=mix(sh("#9c95a8"), DARK, 0.55))
        elif kind == "plant":
            for cx in CHIMNEYS:
                box(cx, cx + 7, 110, 117, 80, "#a3a3ae", plain=True)
                for y in (70, 54):
                    a, b = P(cx, y + 6, 110), P(cx + 7, y, 110)
                    d.rectangle((a[0], a[1], b[0], b[1]), fill=sh("#d6453d", 0, 0.45))
            box(12, 60, 95, 128, 20, "#8a4b3a", plain=True)
            a, b = P(12, 20, 95), P(60, 0, 95)
            for y in range(int(a[1]) + 6, int(b[1]), 6):
                d.line((a[0], y, W, y), fill=mix(sh("#8a4b3a"), DARK, 0.22))
        elif kind == "yard":
            d.polygon((P(-96, 0, 90), P(-14, 0, 90), P(-14, 0, 118), P(-96, 0, 118)), fill=sh("#4a4d58", 0, 0.7))
            for x, z in BAYS:
                d.polygon((P(x, 0, z), P(x + 14, 0, z), P(x + 14, 0, z + 6), P(x, 0, z + 6)), outline=sh("#d4d7e0", 0, 0.5))
    return im


def draw(im, d, s, t, state):
    day = level(s, "time_of_day")
    cpu, ram, net, disk = (level(s, n, state) for n in ("cpu", "ram", "net", "disk"))
    night, dusk, top, low, haze, sh = light(day)
    key = int(day * 288)
    if state["bg_key"] != key:
        state["bg"], state["bg_key"] = city(state, key / 288), key
    im.paste(state["bg"])
    box = boxer(d, sh, 0.0)

    for ph, y, r in state["clouds"]:
        x = ((ph + t / 200) % 1.0) * (W + 80) - 40
        col = mix(mix(top, "#ffffff", 0.75), top, night * 0.85)
        d.ellipse((x - r, y - r * 0.4, x + r, y + r * 0.4), fill=col)
        d.ellipse((x - r * 0.3, y - r * 0.7, x + r * 0.6, y + r * 0.25), fill=col)
    if dusk > 0.3:                                              # a few televisions, far off
        for x, y, z, ph in state["tv"]:
            c = P(x, y, z)
            d.rectangle((c[0], c[1], c[0] + 2, c[1] + 2), fill=mix("#4f8fe0", "#cfe6ff", 0.5 + 0.5 * math.sin(t * 7 + ph) * math.sin(t * 2.3 + ph)))

    wx, wy, wr = 30, 111, 18                                    # the big wheel, turning
    spoke = mix(sh("#e8e8f0", 200), "#ffd98a", dusk * 0.7)
    d.line((wx - 9, wy + 22, wx, wy, wx + 9, wy + 22), fill=sh("#5b6170", 200), width=3)
    d.ellipse((wx - wr, wy - wr, wx + wr, wy + wr), outline=spoke, width=2)
    for i in range(8):
        an = t * 0.12 + i * math.pi / 4
        ex, ey = wx + wr * math.cos(an), wy + wr * math.sin(an)
        d.line((wx, wy, ex, ey), fill=spoke)
        d.rectangle((ex - 2, ey - 1, ex + 2, ey + 3), fill=mix(sh(CARS[1 + i % 4], 200, 0.3), CARS[1 + i % 4], dusk))
    ax = ((t / 150.0 + 0.3) % 1.0) * (W + 80) - 40              # an airship crosses the sky
    ay = 34 + 3 * math.sin(t * 0.2)
    d.polygon(((ax - 20, ay), (ax - 27, ay - 7), (ax - 27, ay + 7)), fill=sh("#c8553d", 0, 0.4))
    d.ellipse((ax - 22, ay - 9, ax + 22, ay + 9), fill=sh("#e9e4d6", 0, 0.4))
    d.rectangle((ax - 22, ay - 1, ax + 22, ay + 1), fill=sh("#c8553d", 0, 0.4))
    d.rectangle((ax - 6, ay + 9, ax + 6, ay + 12), fill=sh("#3a3f4c", 0, 0.4))
    if dusk > 0.3 and int(t * 1.5) % 2:
        d.rectangle((ax + 20, ay - 1, ax + 22, ay + 1), fill="#ff4d4d")

    # ---- net: the tower's rings, and traffic on the boulevard
    tip = P(20, 140, 430)
    tip = (tip[0], tip[1] - 16)
    d.ellipse((tip[0] - 3, tip[1] - 3, tip[0] + 3, tip[1] + 3), fill="#ff4d4d" if int(t * 2) % 2 else "#5a2030")
    for i in range(1 + int(net * 3.99)):                        # more and faster with traffic
        p = (t * (0.25 + 1.2 * net) + i / (1 + int(net * 3.99))) % 1.0
        r, col = 6 + 22 * p, mix(mix("#0b4fd0", "#7df9ff", night), top, p * 0.8)
        d.arc((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), 315, 45, fill=col, width=3)
        d.arc((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), 135, 225, fill=col, width=3)
    n = 6 + int(net * (len(state["cars"]) - 6))
    cars = []
    for q, (ph, col) in enumerate(state["cars"][:n]):
        away = q % 2
        u = (ph + t / 40.0 * (1 + q % 3 * 0.15)) % 1.0
        cars.append((70 * (880 / 70) ** (u if away else 1 - u), away, col, 5 + (q // 2 % 2) * 4))     # even to the eye
    for z, away, col, lane in sorted(cars, reverse=True):
        k, x = F / z, (lane if away else -lane)
        a, b = P(x - 1.7, 2.4, z), P(x + 1.7, 0, z)
        w = max(1.0, k * 0.9)
        if k > 0.8:
            d.polygon((P(x - 1.7, 2.4, z), P(x + 1.7, 2.4, z), P(x + 1.7, 2.4, z + 6), P(x - 1.7, 2.4, z + 6)), fill=mix(sh(col, z, 0.45), "#ffffff", 0.2))
            d.rectangle((a[0], a[1], b[0], b[1]), fill=sh(col, z, 0.45))
        if dusk > 0.2 or k <= 0.8:                              # at night and far off, a string of lamps
            lamp = "#ff3b3b" if away else "#fff7c2"
            d.rectangle((a[0], a[1], a[0] + w, a[1] + w), fill=lamp if dusk > 0.2 else sh(col, z, 0.45))
            d.rectangle((b[0] - w, a[1], b[0], a[1] + w), fill=lamp if dusk > 0.2 else sh(col, z, 0.45))

    # ---- time_of_day: the clock
    fx, fy = P(-49.5, 38.4, 126)
    d.ellipse((fx - 16, fy - 16, fx + 16, fy + 16), fill=mix("#f6ecc8", "#ffe9a8", dusk))
    for k in range(4):                                          # quarter marks only: they read from the chair
        an = k * math.pi / 2
        d.line((fx + 11 * math.sin(an), fy - 11 * math.cos(an), fx + 15 * math.sin(an), fy - 15 * math.cos(an)), fill="#3a3144", width=3)
    for turn, ln, wd in ((day * 2, 8, 5), (day * 24, 13, 3)):   # hour hand, minute hand
        an = turn * 2 * math.pi
        d.line((fx, fy, fx + ln * math.sin(an), fy - ln * math.cos(an)), fill="#1a1626", width=wd)
    d.ellipse((fx - 2, fy - 2, fx + 2, fy + 2), fill="#b0503c")
    since = ((day * 24) % 1.0) * 3600                           # pigeons leave on the hour
    if since < 22:
        for i in range(7):
            an = -0.4 - i * 0.38
            bx, by = fx + math.cos(an) * since * (5 + i), fy - 24 + math.sin(an) * since * 2.2 + 3 * math.sin(i)
            fl = 2 if int(t * 6 + i) % 2 else -1
            d.line((bx - 3, by - fl, bx, by, bx + 3, by - fl), fill=mix("#20222e", top, since / 30))

    # ---- cpu: furnace doors and smoke
    fire = ramp(["#2a1614", "#a3300a", "#ff8a1f", "#fff2b0"], min(1.0, cpu * 1.05))
    for i in range(4):
        a, b = P(14 + i * 12, 12, 95), P(24 + i * 12, 3, 95)
        glow = mix(fire, "#fff7d0", 0.25 * cpu * (0.5 + 0.5 * math.sin(t * 9 + i * 2)))
        r = (b[0] - a[0]) / 2
        d.rectangle((a[0] - 2, a[1], b[0] + 2, b[1]), fill=mix(sh("#8a4b3a"), DARK, 0.5))
        d.pieslice((a[0] - 2, a[1] - r - 2, b[0] + 2, a[1] + r + 2), 180, 360, fill=mix(sh("#8a4b3a"), DARK, 0.5))
        d.rectangle((a[0], a[1], b[0], b[1]), fill=glow)
        d.pieslice((a[0], a[1] - r, b[0], a[1] + r), 180, 360, fill=glow)
        d.rectangle((a[0] + r - 1, a[1] - r, a[0] + r, b[1]), fill=mix(glow, DARK, 0.5))
    for cx in CHIMNEYS:
        sx, sy = P(cx + 3.5, 80, 110)
        n = 2 + int(cpu * 5)
        for i in range(n):
            p = (t * (0.06 + 0.4 * cpu) + i / n + cx) % 1.0
            px, py, r = sx - 16 * p + 3 * math.sin(t + i), sy - 4 - (20 + 30 * cpu) * p, 3 + 5 * cpu + (3 + 8 * cpu) * p
            d.ellipse((px - r, py - r, px + r, py + r), fill=mix(mix("#e2e2ea", "#4a4d5e", cpu), mix(top, low, 0.2), p * 0.8))

    # ---- ram: the sight glass on the tank
    a, b = P(-113, 53, 132), P(-93, 38, 132)
    gx = (a[0] + b[0]) / 2
    d.rectangle((gx - 8, a[1] + 3, gx + 8, b[1] - 2), fill="#0e1322")
    full = (b[1] - a[1] - 8) * ram
    water = "#ff6b5e" if ram > 0.9 else "#58b8ff"
    d.rectangle((gx - 6, b[1] - 4 - full, gx + 6, b[1] - 4), fill=water)
    d.rectangle((gx - 6, b[1] - 4 - full, gx + 6, b[1] - 3 - full), fill=mix(water, "#ffffff", 0.6))

    # ---- disk: containers in the yard's bays, two high
    count = round(disk * 20)
    live = {(lv, i): col for n, (lv, i, col) in enumerate(state["boxes"]) if n < count}
    for i, (x, z) in enumerate(BAYS):
        for lv in (0, 1):
            col = live.get((lv, i))
            if col:
                box(x, x + 14, z, z + 6, lv * 5 + 5, col, y0=lv * 5, plain=True)
                d.rectangle((*P(x, lv * 5 + 5, z), *P(x + 14, lv * 5, z)), outline=mix(sh(col), DARK, 0.6))

    crane = sh("#f2b632", 0, 0.4)                               # the gantry crane works the near row
    cx = -62 + 30 * math.sin(t * 0.15)
    for x in (-97, -13):
        d.line((*P(x, 0, 92), *P(x, 19, 92)), fill=crane, width=3)
    d.line((*P(-97, 19, 92), *P(-13, 19, 92)), fill=crane, width=4)
    hook = 15 - 4 * (0.5 + 0.5 * math.sin(t * 0.4))
    d.line((*P(cx, 19, 92), *P(cx, hook, 92)), fill="#20222e", width=2)
    d.rectangle((P(cx - 3, 21, 92)[0], P(0, 21, 92)[1], P(cx + 3, 0, 92)[0], P(0, 18, 92)[1]), fill=mix(crane, DARK, 0.35))

    # ---- this side of the glass: rain on the panes, daylight on them, the sill, the cat
    rain = max(0.0, (ram - 0.6) / 0.4)                          # starts at 60 % memory, full at 100 %
    if rain > 0:
        for i in range(int(rain * 26)):                         # falling outside
            x, ph = (i * 97) % W, (i * 0.37) % 1.0
            y = ((ph + t / 1.2) % 1.0) * (H + 20) - 10
            d.line((x, y, x - 2, y + 8), fill=mix(haze, "#b9c8ee", 0.6))
        for k in range(15):                                     # and running down each pane
            x0, y0, _, _ = key_box(k + 1)
            for bx, ph, sp in state["beads"][k * 7:k * 7 + int(rain * 4.99) + 1]:
                p = (ph + t * 0.05 * sp) % 1.0
                y = y0 + (p + 0.04 * math.sin(t * sp + bx)) * 66 - 2
                x = x0 + bx + math.sin(p * 9 + bx)
                d.line((x, y - 5 - 4 * sp, x, y), fill=mix(haze, "#dbe7ff", 0.22))
                d.rectangle((x, y, x + 1, y + 2), fill=mix(haze, "#e8f1ff", 0.75))
    im.paste((255, 255, 255), (0, 0), state["glare"].point(lambda v: int(v * (1 - 0.7 * night))))
    d.rectangle((0, H - 9, W, H), fill=mix("#4a342a", DARK, 0.3 + 0.4 * night))
    d.line((0, H - 9, W, H - 9), fill=mix("#7a5a48", DARK, 0.3 + 0.4 * night))
    fur = "#0d0b12"                                             # the cat watches the city
    tail = math.sin(t * 0.9) * (1 if int(t / 7) % 2 else 0.3)
    twitch = 3 if t % 11 < 0.3 else 0
    for g, col in ((2, mix("#b9a48f", "#ffd9a0", dusk)), (0, fur)):     # a rim of light, then the cat
        d.ellipse((20 - g, 216 - g, 58 + g, 256), fill=col)
        d.ellipse((28 - g, 198 - g, 54 + g, 222 + g), fill=col)
        d.polygon(((29 - g, 208), (31, 191 + twitch - g), (40, 201 - g)), fill=col)
        d.polygon(((43, 201 - g), (52, 191 - g), (54 + g, 208)), fill=col)
        d.line((26, 245, 12, 245, 6 + 3 * tail, 238 - 6 * abs(tail), 4 + 6 * tail, 228), fill=col, width=4 + g)

    show = state.get("show")                                    # tap: the number of what this pane looks at
    if show and t < show[1]:
        x0, y0, x1, y1 = key_box(show[0])
        sig = getattr(s, PANE[show[0] - 1])
        d.rectangle((x0, y0, x1, y1), fill="#0a0c18", outline="#ffc46b")
        text(d, (x0 + 32, y0 + 20), sig.label, 12, "#9aa3c7")
        text(d, (x0 + 32, y0 + 40), sig.text, 18)


def tap(key, x, y, s, t, state):
    state["show"] = (key, t + 3)
