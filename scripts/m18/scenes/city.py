"""A city seen through a window. The pad's fifteen screens are the panes and the bars between them are
the glazing bars, so nothing is lined up with them: you look down an avenue from a few floors up, in
perspective, and buildings, cars and smoke pass behind the bars the way they would behind a real window.

cpu          the power plant across the street: furnace windows glow hotter, the chimneys smoke harder,
             the cooling tower steams more.
ram          the water tank on the roof next door: its sight glass fills, red above 90 %. When memory is
             tight it rains, and the drops run down the glass, pane by pane.
net          traffic on the avenue, and the telecom tower on the skyline sends out more rings.
disk         the container yard below: the stacks grow.
time_of_day  the clock tower at the end of the avenue, the sky, the lamps and the lit windows at night.
             Pigeons leave the tower on the hour.

Inside, a cat sits on the sill. Tap a pane to read the number of what you see in it for 3 s."""
import math
import random

from PIL import Image, ImageDraw

from m18lib.scene import H, W, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram", "net", "disk", "time_of_day"]
FPS = 10
RING = {"signal": "cpu", "color": "#ffc46b"}

F, VX, HOR, CAM = 260.0, 220.0, 120.0, 14.0                     # lens, vanishing point, eye height (a 4th floor)
PANE = ["ram", "net", "time_of_day", "cpu", "cpu",              # what each key looks at, keys 1..15
        "ram", "ram", "net", "cpu", "cpu",
        "ram", "net", "net", "disk", "disk"]
CARS = ["#e8e8f0", "#ff6b6b", "#ffd166", "#5ec8ff", "#9be38a"]
BOXES = ["#c8553d", "#2f6f9f", "#e0a526", "#3f8f6b", "#8a5fbf", "#d9d2c0", "#b5462f", "#2b8c8c"]
DARK = "#0a0c1a"


def P(x, y, z):
    """World (across, up, away) to the glass."""
    k = F / z
    return (VX + x * k, HOR - (y - CAM) * k)


def setup(state):
    rnd = random.Random(7)
    state["far"] = [(x, rnd.randint(8, 46), rnd.random()) for x in range(-6, W, 13)]
    state["stars"] = [(rnd.randrange(W), rnd.randrange(110), rnd.random() * 6.28) for _ in range(60)]
    state["cars"] = [(((i // 2) * 5 % 6) / 6 + rnd.random() * 0.05, rnd.choice(CARS), i % 2) for i in range(12)]
    state["homes"] = [(rnd.random() * 6.28, rnd.random(), rnd.random()) for _ in range(32)]
    order = [(lv, i) for lv in range(4) for i in rnd.sample(range(9), 9)]       # stacks fill unevenly
    state["boxes"] = [(lv, i, rnd.choice(BOXES)) for lv, i in order]
    state["clouds"] = [(rnd.random(), 6 + rnd.randrange(34), 14 + rnd.randrange(12)) for _ in range(4)]
    state["beads"] = [(rnd.randrange(3, 61), rnd.random(), 0.5 + rnd.random()) for _ in range(15 * 7)]
    glare = Image.new("L", (W, H), 0)                           # daylight on each pane of glass
    g = ImageDraw.Draw(glare)
    for k in range(1, 16):
        x0, y0, _, _ = key_box(k)
        g.polygon(((x0 + 40, y0), (x0 + 52, y0), (x0 + 16, y0 + 64), (x0 + 4, y0 + 64)), fill=16)
        g.polygon(((x0 + 57, y0), (x0 + 61, y0), (x0 + 25, y0 + 64), (x0 + 21, y0 + 64)), fill=9)
    state["glare"] = glare
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
    top = ramp(["#3a6ea8", "#0b1030", "#060816"], night)
    low = ramp(["#b8d4ee", "#4a3566", "#221c40"], night)
    haze = mix(low, top, 0.25)

    def sh(col, k=0.62, z=0.0):                                 # a daytime colour now, and through the air
        return mix(mix(col, DARK, night * k), haze, min(0.6, max(0.0, (z - 40) / 150)))

    def rect(a, b, fill):
        d.rectangle((a[0], a[1], b[0], b[1]), fill=fill)

    def crate(x0, x1, y0, y1, z0, z1, col):                     # a box right of the avenue, seen from above
        d.polygon((P(x0, y1, z0), P(x0, y1, z1), P(x0, y0, z1), P(x0, y0, z0)), fill=mix(col, DARK, 0.35))
        d.polygon((P(x0, y1, z0), P(x1, y1, z0), P(x1, y1, z1), P(x0, y1, z1)), fill=mix(col, "#ffffff", 0.22))
        rect(P(x0, y1, z0), P(x1, y0, z0), col)

    # ---- the sky
    for y in range(0, int(HOR) + 2, 2):
        d.rectangle((0, y, W, y + 1), fill=mix(top, low, y / HOR))
    for x, y, ph in state["stars"]:
        a = night * (0.55 + 0.45 * math.sin(t * 1.3 + ph))
        if a > 0.15:
            d.point((x, y), fill=mix(top, "#ffffff", a))
    if night > 0.6 and t % 23 < 0.7:                            # now and then, a shooting star
        k = (t % 23) / 0.7
        x, y = 60 + (int(t / 23) * 97) % 300 + 40 * k, 6 + 18 * k
        d.line((x, y, x - 12, y - 5), fill=mix(top, "#ffffff", 1 - k))
    mx = ((day + 0.25) % 1.0) * (W + 40) - 20                   # one crossing a day: the moon by night, the sun by day
    my = 36 - 24 * math.sin(((day + 0.25) % 1.0) * math.pi)
    d.ellipse((mx - 10, my - 10, mx + 10, my + 10), fill=mix("#fff3c4", "#f4f1e6", night))
    if night > 0.5:
        d.ellipse((mx - 4, my - 12, mx + 12, my + 6), fill=mix(top, low, my / HOR))
    for ph, y, r in state["clouds"]:
        x = ((ph + t / 160) % 1.0) * (W + 80) - 40
        col = mix(mix(top, "#ffffff", 0.75), top, night * 0.85)
        d.ellipse((x - r, y - r * 0.45, x + r, y + r * 0.45), fill=col)
        d.ellipse((x - r * 0.3, y - r * 0.75, x + r * 0.6, y + r * 0.3), fill=col)
    for i, (x, h, lit) in enumerate(state["far"]):              # the far skyline
        d.rectangle((x, HOR - h, x + 11, HOR), fill=mix(haze, DARK, 0.3))
        if dusk > 0.3 and lit > 0.35:
            d.point((x + 3 + i % 5, HOR - h + 4 + (i * 7) % max(1, h - 6)), fill="#ffd98a")

    # ---- the ground: pavement, the avenue running to the clock tower, a crossing
    d.rectangle((0, HOR, W, H), fill=sh("#8b8f9c", 0.75))
    road = sh("#3b3e4b", 0.7)
    d.polygon((P(-8, 0, 20), P(8, 0, 20), P(8, 0, 90), P(-8, 0, 90)), fill=road)
    c = P(0, 0, 92)
    d.ellipse((c[0] - 34, c[1] - 7, c[0] + 34, c[1] + 7), fill=road)
    d.ellipse((c[0] - 20, c[1] - 4, c[0] + 20, c[1] + 4), fill=sh("#5f8f5a", 0.6))
    for z in range(26, 88, 7):
        d.polygon((P(-0.15, 0, z), P(0.15, 0, z), P(0.15, 0, z + 3), P(-0.15, 0, z + 3)), fill=sh("#d8d2a8", 0.5))
    for x in range(-7, 8, 2):
        d.polygon((P(x - 0.5, 0, 44), P(x + 0.5, 0, 44), P(x + 0.5, 0, 47.5), P(x - 0.5, 0, 47.5)), fill=sh("#d9dbe2", 0.5))
    for x in (-8, 8):
        d.line((*P(x, 0, 20), *P(x, 0, 90)), fill=sh("#c3c6d0", 0.6))

    # ---- net: the telecom tower on the skyline, left
    a, b = P(-34, 50, 100), P(-26, 0, 100)
    glass = sh("#3f6f93", 0.7, 100)
    rect(a, b, glass)
    d.rectangle((b[0] - 5, a[1], b[0], b[1]), fill=mix(glass, DARK, 0.25))
    for y in range(int(a[1]) + 4, 100, 5):
        d.line((a[0] + 2, y, b[0] - 2, y), fill=mix(glass, "#bfe9ff", 0.25 + 0.45 * dusk * (0.6 + 0.4 * math.sin(y))))
    cx = (a[0] + b[0]) / 2
    for y in range(int(a[1]) + 2, 100, 4):                      # the light strip: data going up
        k = ((y / 4 + t * (1 + 9 * net)) % 6) / 6
        d.rectangle((cx - 1, y, cx + 1, y + 1), fill=mix("#5ef0c8", "#0d1626", min(1.0, k * 1.6)))
    tip = (cx, a[1] - 17)
    d.line((cx, a[1], *tip), fill=sh("#c4c9d6", 0.4), width=2)
    d.line((cx - 5, a[1], cx, a[1] - 10), fill=sh("#c4c9d6", 0.4))
    d.line((cx + 5, a[1], cx, a[1] - 10), fill=sh("#c4c9d6", 0.4))
    d.ellipse((cx - 8, a[1] - 7, cx - 2, a[1] - 1), fill=sh("#e3e6ee", 0.4))
    d.ellipse((tip[0] - 2, tip[1] - 2, tip[0] + 2, tip[1] + 2), fill="#ff4d4d" if int(t * 2) % 2 else "#5a2030")
    for i in range(1 + int(net * 3.99)):                        # rings from the mast: more and faster with traffic
        p = (t * (0.25 + 1.2 * net) + i / (1 + int(net * 3.99))) % 1.0
        r, col = 4 + 15 * p, mix("#7df9ff", top, p)
        d.arc((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), 320, 40, fill=col, width=2)
        d.arc((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), 140, 220, fill=col, width=2)

    # ---- time_of_day: the clock tower closing the avenue
    stone = sh("#9c95a8", 0.62, 60)
    a, b = P(-5, 43.5, 90), P(5, 0, 90)
    rect(a, b, stone)
    d.rectangle((b[0] - 8, a[1], b[0], b[1]), fill=mix(stone, DARK, 0.22))
    for y in range(98, 150, 12):
        d.rectangle((a[0], y, a[0] + 3, y + 5), fill=mix(stone, DARK, 0.25))
        d.rectangle((VX - 1, y + 2, VX + 1, y + 9), fill=mix("#141222", "#ffd98a", dusk))
    d.pieslice((VX - 6, b[1] - 16, VX + 6, b[1] - 2), 180, 360, fill="#2a1c1a")
    d.rectangle((VX - 6, b[1] - 9, VX + 6, b[1]), fill="#2a1c1a")
    d.polygon(((a[0] - 3, a[1]), (VX, 6), (b[0] + 3, a[1])), fill=sh("#4a4763", 0.62, 60))
    d.line((VX, 6, VX, 0), fill=sh("#c4c9d6", 0.3))
    d.polygon(((VX, 0), (VX + 9, 2 + math.sin(t * 3)), (VX, 5)), fill=sh("#d6453d", 0.4))
    fx, fy = P(0, 37.9, 90)
    d.ellipse((fx - 14, fy - 14, fx + 14, fy + 14), fill=mix(stone, DARK, 0.45))
    d.ellipse((fx - 12, fy - 12, fx + 12, fy + 12), fill=mix("#f6ecc8", "#ffe9a8", dusk))
    for k in range(12):
        an = k * math.pi / 6
        r0 = 8 if k % 3 == 0 else 10
        d.line((fx + r0 * math.sin(an), fy - r0 * math.cos(an), fx + 11 * math.sin(an), fy - 11 * math.cos(an)), fill="#3a3144")
    for turn, ln, wd in ((day * 2, 6, 3), (day * 24, 10, 2)):   # hour hand, minute hand
        an = turn * 2 * math.pi
        d.line((fx, fy, fx + ln * math.sin(an), fy - ln * math.cos(an)), fill="#1a1626", width=wd)
    d.ellipse((fx - 1, fy - 1, fx + 1, fy + 1), fill="#b0503c")
    since = ((day * 24) % 1.0) * 3600                           # pigeons leave on the hour
    if since < 22:
        for i in range(7):
            an = -0.4 - i * 0.38
            bx, by = VX + math.cos(an) * since * (5 + i), 30 + math.sin(an) * since * 2.2 + 3 * math.sin(i)
            fl = 2 if int(t * 6 + i) % 2 else -1
            d.line((bx - 3, by - fl, bx, by, bx + 3, by - fl), fill=mix("#20222e", top, since / 30))

    # ---- cpu: the power plant across the street, behind the container yard
    for cx0 in (21, 29):                                        # two chimneys
        a, b = P(cx0, 34, 66), P(cx0 + 2.2, 16, 66)
        rect(a, b, sh("#a3a3ae", 0.62, 66))
        d.rectangle((b[0] - 2, a[1], b[0], b[1]), fill=sh("#7d7d8a", 0.62, 66))
        for band in (0, 9):
            d.rectangle((a[0], a[1] + band, b[0], a[1] + band + 3), fill=sh("#d6453d", 0.45, 66))
    cx, base, rim = P(51, 0, 75)[0], P(51, 0, 75)[1], P(51, 36, 75)[1]          # the cooling tower
    prof = [(0.0, 21), (0.3, 16), (0.65, 12.5), (0.85, 13), (1.0, 15)]
    cool = sh("#b9bcc6", 0.62, 75)
    d.polygon([(cx - w, base + (rim - base) * f) for f, w in prof] + [(cx + w, base + (rim - base) * f) for f, w in reversed(prof)], fill=cool)
    d.polygon([(cx + w * 0.35, base + (rim - base) * f) for f, w in prof] + [(cx + w, base + (rim - base) * f) for f, w in reversed(prof)], fill=mix(cool, DARK, 0.18))
    d.ellipse((cx - 15, rim - 3, cx + 15, rim + 3), fill=mix(cool, DARK, 0.5))
    brick = sh("#8a4b3a", 0.62, 30)
    a, b = P(12, 18, 60), P(54, 0, 60)
    d.polygon((P(12, 18, 60), P(12, 18, 90), P(12, 0, 90), P(12, 0, 60)), fill=mix(brick, DARK, 0.35))
    rect(a, b, brick)
    for y in range(int(a[1]) + 6, int(b[1]), 6):
        d.line((a[0], y, W, y), fill=mix(brick, DARK, 0.22))
    d.rectangle((a[0] - 1, a[1] - 2, W, a[1] + 2), fill=sh("#c9b8a8", 0.62, 30))
    fire = ramp(["#2a1614", "#a3300a", "#ff8a1f", "#fff2b0"], min(1.0, cpu * 1.05))
    for i in range(8):                                          # furnace windows: hotter with load
        p0, p1 = P(14.6 + i * 4.8, 12.6, 60), P(17.6 + i * 4.8, 6, 60)
        glow = mix(fire, "#fff7d0", 0.25 * cpu * (0.5 + 0.5 * math.sin(t * 9 + i * 2)))
        d.rectangle((p0[0] - 1, p0[1], p1[0] + 1, p1[1]), fill=mix(brick, DARK, 0.45))
        d.pieslice((p0[0] - 1, p0[1] - 8, p1[0] + 1, p0[1] + 8), 180, 360, fill=mix(brick, DARK, 0.45))
        d.rectangle((p0[0] + 1, p0[1], p1[0] - 1, p1[1]), fill=glow)
        d.pieslice((p0[0] + 1, p0[1] - 6, p1[0] - 1, p0[1] + 6), 180, 360, fill=glow)
        mid = (p0[0] + p1[0]) / 2
        d.line((mid, p0[1] - 5, mid, p1[1]), fill=mix(glow, DARK, 0.5))
        d.line((p0[0] + 1, p0[1] + 10, p1[0] - 1, p0[1] + 10), fill=mix(glow, DARK, 0.5))
    for cx0 in (21, 29):                                        # smoke
        sx, sy = P(cx0 + 1.1, 34, 66)
        n = 1 + int(cpu * 5)
        for i in range(n):
            p = (t * (0.07 + 0.45 * cpu) + i / n + cx0) % 1.0
            px, py, r = sx + 18 * p + 2 * math.sin(t + i), sy - 3 - 40 * p, 3 + 6 * p
            d.ellipse((px - r, py - r, px + r, py + r), fill=mix(mix("#d8d8e0", "#55586a", cpu), mix(top, low, 0.2), p * 0.8))
    n = 2 + int(cpu * 6)                                        # steam
    for i in range(n):
        p = (t * (0.05 + 0.3 * cpu) + i / n) % 1.0
        px, py, r = cx - 10 * p + 3 * math.sin(t * 0.7 + i * 2), rim - 4 - 46 * p, 6 + 8 * p
        d.ellipse((px - r, py - r, px + r, py + r), fill=mix(mix("#f4f6fa", top, night * 0.6), mix(top, low, 0.3), p * 0.85))

    # ---- disk: the container yard below, and its crane
    d.polygon((P(11.5, 0, 28), P(60, 0, 28), P(60, 0, 46), P(11.5, 0, 46)), fill=sh("#62656f", 0.7))
    crane = sh("#e0a526", 0.45)
    for x in (12.4, 34):
        d.line((*P(x, 0, 45), *P(x, 17.5, 45)), fill=mix(crane, DARK, 0.3), width=2)
    count = round(disk * 36)
    live = {(lv, i): col for n, (lv, i, col) in enumerate(state["boxes"]) if n < count}
    for zi, z0 in enumerate((41, 36, 31)):                      # far rows first, far columns first, bottom up
        for xi in (2, 1, 0):
            for lv in range(4):
                col = live.get((lv, zi * 3 + xi))
                if col:
                    crate(13.5 + xi * 6.1, 19.1 + xi * 6.1, lv * 2.6, lv * 2.6 + 2.5, z0, z0 + 2.6, sh(col, 0.5))
    for x in (12.4, 34):
        d.line((*P(x, 0, 30), *P(x, 17.5, 30)), fill=crane, width=3)
        d.line((*P(x, 17.5, 30), *P(x, 17.5, 45)), fill=crane, width=2)
    a, b = P(12.4, 17.5, 30), P(34, 17.5, 30)
    d.rectangle((a[0], a[1] - 2, W, a[1] + 3), fill=crane)
    for x in range(int(a[0]), W, 9):
        d.line((x, a[1] + 3, x + 5, a[1] - 2), fill="#1a1626")
    tx = a[0] + 20 + 90 * (0.5 + 0.5 * math.sin(t * 0.35))      # the trolley and its hook
    hy = a[1] + 22 + 10 * math.sin(t * 0.5)
    d.rectangle((tx - 6, a[1] + 3, tx + 6, a[1] + 8), fill=sh("#3a3f52", 0.3))
    d.line((tx, a[1] + 8, tx, hy), fill="#c4c9d6")
    d.arc((tx - 3, hy - 1, tx + 3, hy + 5), 0, 200, fill="#c4c9d6", width=2)
    d.ellipse((a[0] - 2, a[1] - 7, a[0] + 3, a[1] - 2), fill="#ff4d4d" if disk > 0.9 and int(t * 2) % 2 else "#5a2030")

    # ---- ram: the apartments next door, close, with the water tank on the roof
    wood, steel = sh("#7a5a3c"), sh("#5b6170")
    tl, br = P(-29, 30, 40), P(-25, 25.5, 40)
    for x in (tl[0] + 3, br[0] - 3):
        d.line((x, br[1], x - (2 if x < 44 else -2), 62), fill=steel, width=2)
    d.line((tl[0] + 3, br[1] + 3, br[0] - 3, 60), fill=steel)
    d.line((br[0] - 3, br[1] + 3, tl[0] + 3, 60), fill=steel)
    rect(tl, br, wood)
    for x in range(int(tl[0]) + 4, int(br[0]), 5):
        d.line((x, tl[1], x, br[1]), fill=mix(wood, DARK, 0.3))
    for y in (tl[1] + 5, br[1] - 5):
        d.line((tl[0], y, br[0], y), fill=steel)
    d.polygon(((tl[0] - 3, tl[1]), ((tl[0] + br[0]) / 2, tl[1] - 12), (br[0] + 3, tl[1])), fill=sh("#4d5364"))
    gx = (tl[0] + br[0]) / 2
    d.rectangle((gx - 4, tl[1] + 3, gx + 4, br[1] - 2), fill="#0e1322")     # the sight glass: memory in use
    full = (br[1] - tl[1] - 7) * ram
    water = "#ff6b5e" if ram > 0.9 else "#58b8ff"
    d.rectangle((gx - 3, br[1] - 3 - full, gx + 3, br[1] - 3), fill=water)
    d.line((gx - 3, br[1] - 3 - full, gx + 3, br[1] - 3 - full), fill=mix(water, "#ffffff", 0.6))
    wall = sh("#c9a88a")
    side = mix(wall, DARK, 0.3)
    homes = state["homes"]

    def pane_of(n):
        ph, who, kind = homes[n]
        on = dusk > 0.1 and math.sin(t * 0.07 + ph) > 0.9 - 1.5 * dusk * (0.4 + 0.6 * who)
        col = mix("#ffd98a" if kind > 0.3 else "#bfe3ff", "#fff3cf", 0.3) if on else mix("#8fb6d8", "#161b2e", 0.25 + 0.75 * night)
        return on, col, ph, who, kind

    d.polygon((P(-12, 22, 34), P(-12, 22, 60), P(-12, 0, 60), P(-12, 0, 34)), fill=side)
    for r in range(4):                                          # the side that faces the avenue
        y0 = 5.6 + 3.95 * r
        for j, z in enumerate((37, 43, 49, 55)):
            on, col, *_ = pane_of(16 + r * 4 + j)
            d.polygon((P(-12, y0 + 3.2, z), P(-12, y0 + 3.2, z + 3.2), P(-12, y0, z + 3.2), P(-12, y0, z)), fill=mix(col, side, 0.2))
    a, b = P(-30, 22, 34), P(-12, 0, 34)
    rect(a, b, wall)
    d.rectangle((0, a[1] - 3, b[0] + 2, a[1] + 2), fill=sh("#8f7a63"))
    for r in range(4):
        y0 = 5.6 + 3.95 * r
        fl = P(0, y0 - 0.5, 34)[1]
        d.line((0, fl, b[0], fl), fill=mix(wall, DARK, 0.18))
        for c in range(4):
            on, pane, ph, who, kind = pane_of(r * 4 + c)
            p0, p1 = P(-28.1 + 4.3 * c, y0 + 3.2, 34), P(-25.7 + 4.3 * c, y0, 34)
            x0, y0s, x1, y1s = int(p0[0]), int(p0[1]), int(p1[0]), int(p1[1])
            d.rectangle((x0 - 2, y0s - 2, x1 + 2, y1s + 2), fill=mix(wall, DARK, 0.3))
            d.rectangle((x0, y0s, x1, y1s), fill=pane)
            d.rectangle((x0, y0s, x0 + (6 if kind > 0.5 else 3), y1s), fill=mix(pane, "#b0503c" if who > 0.5 else "#3f7f6a", 0.55))
            d.line(((x0 + x1) // 2, y0s, (x0 + x1) // 2, y1s), fill=mix(wall, DARK, 0.3))
            if on and who > 0.6:                                # somebody is home
                bob = int(math.sin(t * 0.6 + ph) * 3)
                d.rectangle((x0 + 10 + bob, y0s + 9, x0 + 15 + bob, y1s), fill="#2a2030")
                d.ellipse((x0 + 10 + bob, y0s + 4, x0 + 15 + bob, y0s + 9), fill="#2a2030")
            if kind < 0.4:                                      # a flower box
                d.rectangle((x0 - 2, y1s + 1, x1 + 2, y1s + 4), fill=sh("#6b4a35"))
                for fx2 in range(x0, x1, 4):
                    d.rectangle((fx2, y1s - 1, fx2 + 1, y1s), fill=sh("#e85a71" if (fx2 + c) % 3 else "#ffd166", 0.4))
    a = P(-30, 4.7, 34)
    d.rectangle((0, a[1], b[0], b[1]), fill=sh("#6d4f43"))      # the shops at street level
    shop = mix("#26314a", "#ffe2a0", 0.15 + 0.75 * dusk)
    rect(P(-16.6, 3.4, 34), P(-12.6, 0.5, 34), shop)
    rect(P(-29, 3.4, 34), P(-25, 0.5, 34), shop)
    rect(P(-23.4, 3.7, 34), P(-21.6, 0, 34), mix(sh("#6d4f43"), DARK, 0.5))
    aw0, aw1 = P(-17, 4.6, 34), P(-12.2, 3.5, 34)
    for i in range(8):                                          # a striped awning
        x = aw0[0] + i * (aw1[0] - aw0[0]) / 8
        d.rectangle((x, aw0[1], x + (aw1[0] - aw0[0]) / 8, aw1[1] + 1), fill=sh("#d6453d" if i % 2 else "#f1ece0", 0.4))
    sg = P(-20.6, 4.2, 34)
    d.rectangle((sg[0], sg[1], sg[0] + 9, sg[1] + 5), fill="#ff5fa2" if int(t * 1.5) % 5 else "#5a2440")

    # ---- the street: lamps and traffic, far to near
    things = [(z, "lamp", x) for z in (30, 40, 52, 68, 86) for x in (-10, 10)]
    n = 2 + int(net * (len(state["cars"]) - 2))                 # more traffic, more cars
    for ph, col, away in state["cars"][:n]:
        p = (ph + t / 9.0) % 1.0
        things.append((27 + p * 61 if away else 88 - p * 61, "car", (col, away)))
    for z, kind, v in sorted(things, key=lambda th: -th[0]):
        k = F / z
        if kind == "lamp":
            base, head = P(v, 0, z), P(v, 6, z)
            arm = head[0] + (k * 1.4 if v < 0 else -k * 1.4)
            if dusk > 0.05:
                d.ellipse((arm - k * 2.2, base[1] - k * 0.5, arm + k * 2.2, base[1] + k * 0.5), fill=mix(sh("#8b8f9c", 0.75), "#ffe2a0", 0.4 * dusk))
            d.line((*base, *head), fill="#2a2d3c", width=2 if z < 60 else 1)
            d.line((*head, arm, head[1]), fill="#2a2d3c", width=2 if z < 60 else 1)
            r = max(1.0, k * 0.35)
            d.ellipse((arm - r, head[1] - r, arm + r, head[1] + r), fill=mix("#55586a", "#fff1c0", dusk))
        else:
            col, away = v
            x = 3.6 if away else -3.6
            col = sh(col, 0.45)
            d.polygon((P(x - 1.1, 1.5, z), P(x + 1.1, 1.5, z), P(x + 1.1, 1.5, z + 4.4), P(x - 1.1, 1.5, z + 4.4)), fill=mix(col, "#ffffff", 0.2))
            d.polygon((P(x - 0.9, 1.6, z + 1), P(x + 0.9, 1.6, z + 1), P(x + 0.9, 1.6, z + 3.3), P(x - 0.9, 1.6, z + 3.3)), fill="#1c2233")
            a, b = P(x - 1.1, 1.5, z), P(x + 1.1, 0.2, z)
            rect(a, b, col)
            lamp = "#ff3b3b" if away else "#fff7c2"
            w = max(1.0, k * 0.4)
            d.rectangle((a[0], a[1] + 1, a[0] + w, a[1] + 1 + w), fill=lamp)
            d.rectangle((b[0] - w, a[1] + 1, b[0], a[1] + 1 + w), fill=lamp)

    # ---- this side of the glass: rain on the panes, daylight on them, the sill, the cat, a plant
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
    fur = "#0d0b12"                                             # the cat watches the street
    tail = math.sin(t * 0.9) * (1 if int(t / 7) % 2 else 0.3)
    d.ellipse((30, 214, 56, 248), fill=fur)
    d.ellipse((36, 198, 54, 216), fill=fur)
    twitch = 2 if t % 11 < 0.3 else 0
    d.polygon(((37, 204), (38, 193 + twitch), (44, 200)), fill=fur)
    d.polygon(((47, 200), (53, 193), (54, 205)), fill=fur)
    d.line((34, 244, 20, 245, 12 + 3 * tail, 240 - 5 * abs(tail), 9 + 6 * tail, 234), fill=fur, width=3)
    d.rectangle((417, 232, 433, 243), fill=mix("#b5623f", DARK, 0.25 + 0.5 * night))
    d.rectangle((415, 230, 435, 233), fill=mix("#c9764f", DARK, 0.25 + 0.5 * night))
    for i, (dx, dy) in enumerate(((-9, -14), (-4, -19), (2, -21), (8, -16), (12, -10))):
        sway = math.sin(t * 0.8 + i) * 0.8
        d.line((425, 231, 425 + dx + sway, 231 + dy), fill=mix("#4f9a5b", DARK, 0.2 + 0.5 * night), width=2)
        d.ellipse((423 + dx + sway, 229 + dy, 427 + dx + sway, 233 + dy), fill=mix("#6fc27a", DARK, 0.2 + 0.5 * night))

    show = state.get("show")                                    # tap: the number of what this pane looks at
    if show and t < show[1]:
        x0, y0, x1, y1 = key_box(show[0])
        sig = getattr(s, PANE[show[0] - 1])
        d.rectangle((x0, y0, x1, y1), fill="#0a0c18", outline="#ffc46b")
        text(d, (x0 + 32, y0 + 20), sig.label, 12, "#9aa3c7")
        text(d, (x0 + 32, y0 + 40), sig.text, 18)


def tap(key, x, y, s, t, state):
    state["show"] = (key, t + 3)
