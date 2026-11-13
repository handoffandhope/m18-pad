"""A city seen through a window, from about twenty floors up. The pad's fifteen screens are the panes and
the bars between them are the glazing bars, so nothing is lined up with them: a district runs to the
horizon in perspective, and towers, traffic and smoke pass behind the bars as they would behind a window.

cpu          the power plant, right: furnace doors glow hotter, roof fans spin faster, its chimneys smoke
             harder. A stoker walks the doors faster. The smoke stays in the sky for four and a half
             minutes and drifts one pane a minute: a spike is a pale wisp, a long job a brown band with
             a red sun behind it. After half a minute above 85 % the chimneys flare.
ram          the water tank on a roof, left: its sight glass fills, red above 90 %. Above 60 % it rains
             and the drops run down the glass; after half a minute above 90 % there is lightning. When
             the rain stops by day there is a rainbow, away from the sun, while the streets dry.
net          the rings from the tower downtown, the lamps lit up its shaft and how fast the searchlights
             sweep; traffic on the boulevard (light trails at night) follows the last twenty seconds.
disk         the container yard below: a gantry crane sets a container down for every 5 %, and waits a
             moment for the lens before it does.
gpu          the big wheel, left of the tank: it turns faster the harder the card works.
time_of_day  the clock tower, the sun and moon, the light on the walls, how many windows are still lit.
             Pigeons leave the tower on the hour; at night there are fireworks instead.

Each reading also says what is inside it (`s.cpu.data`, see m18lib/signals.py), and the same places show
that for whoever knows where to look. `inside` gathers it; every part falls back to the plain reading
when the daemon has nothing to say.

cores        the plant's four furnace doors are the cores, a quarter of them each, busiest in the middle
             two (the ones the wide view shows): one hot door beside a dark one is one thread flat
             out, which the total alone calls "6 %". Up close every core has its own flame, as tall as
             the core is busy. The left chimney smokes brown for time spent in programs, the right
             one steams white for time spent in the kernel (drivers, disk and network handling).
cache        the dim hatched band above the water is memory the system holds as file cache and gives
             back when asked: water plus band is what is occupied, water alone is what is needed. The
             amber tube beside the glass fills as the pagefile does.
down and up  cars driving toward the window are the download, cars driving away the upload. The tower's
             rings on the right close in on the mast (arriving), those on the left spread out (leaving).
disk I/O     the lamp post in the yard blinks blue for reads and orange for writes, faster for more.
             A goods train crosses the viaduct, behind the roofs, while the disk is busy: longer for
             more MB/s, blue and orange wagons in proportion, running right for reads, left for writes.
gpu          a green arc inside the wheel's rim is the card's memory in use, and the hub goes from blue
             through yellow to red between 40 and 85 degrees. Without a card the wheel just turns.

A close-up hangs a second, blue-grey plate with those numbers: the busiest program and its share, cache
and swap, down and up, read and write, VRAM and temperature.

A cat sits on the sill. It is the one who notices things: when a number has stayed over its line for
half a minute (page option `patience: 30`; a spike does not count), or the hour strikes, the cat turns its
head and the view zooms in to what it is looking at, with the number on a plate and how long it has
lasted. Tap a pane to zoom to what you see in it; tap again to come back. Left alone, the view visits
one place a minute (page option `tour: 90` changes the pause, `tour: 0` stops it).

A zoom is the same picture through a longer lens, so every place is drawn once in the wide picture's
own pixels and a View scales it. What stands still is drawn once per place and per five minutes of
daylight, in three layers (sky, far city, near city) so the train can run between them."""
import math
import random

from PIL import Image, ImageColor, ImageDraw

from m18lib.scene import H, W, key_box, mix, ramp, text

SIGNALS = ["cpu", "ram", "net", "disk", "gpu", "time_of_day"]
FPS = 10
RING = {"signal": "cpu", "color": "#ffc46b"}

F, VX, HOR, CAM = 240.0, 314.0, 66.0, 70.0                      # lens, vanishing point, horizon, eye height
SHOTS = {"wide": (1.0, 220.0, 126.0),                           # magnification, and the wide-picture point at centre
         "cpu": (2.0, 351.0, 180.0), "ram": (2.5, 127.0, 110.5), "net": (2.4, 325.0, 36.0),
         "disk": (3.0, 175.0, 212.0), "time_of_day": (6.2, 219.7, 126.2), "gpu": (1.6, 147.5, 112.0)}
PLATE = {"cpu": 6, "ram": 7, "net": 6, "disk": 4, "time_of_day": 10, "gpu": 1}     # the key the number plate hangs in
MORE = {"cpu": 1, "ram": 2, "net": 1, "disk": 5, "gpu": 7}      # and the key for the plate that says what is inside it
TOUR = ["cpu", "net", "ram", "gpu", "time_of_day", "disk"]
PANE = ["time_of_day", "time_of_day", "time_of_day", "net", "cpu",      # what each key looks at, keys 1..15
        "gpu", "ram", "time_of_day", "net", "cpu",
        "disk", "disk", "disk", "net", "cpu"]
ALARM = {"cpu": 0.85, "ram": 0.9, "net": 0.8, "gpu": 0.9}       # crossing one of these gets the cat's attention
FIRE = ["#2a1614", "#a3300a", "#ff8a1f", "#fff2b0"]
READ, WRITE = "#4f9be8", "#f08a2e"                              # what the disk gives is blue, what it takes is orange
MOVE, NOTICE = 0.9, 0.5                                         # seconds: a zoom, and the cat's look before it
SOOT = "#54402f"                                                # the plant's smoke is brown, the clouds are not
DRIFT = 94 / 60                                                 # old smoke crosses one pane a minute
DARK = "#0a0c1a"
PAL = ["#c9a88a", "#8a4b3a", "#b9a58c", "#9c95a8", "#a3856b", "#7d8597", "#c7b8a0", "#96705a", "#b0b5c0"]
GLASS = ["#3f6f93", "#4f86a8", "#2f5f86", "#5a8fa0"]
CARS = ["#e8e8f0", "#ff6b6b", "#ffd166", "#5ec8ff", "#9be38a"]
BOXES = ["#c8553d", "#2f6f9f", "#e0a526", "#3f8f6b", "#8a5fbf", "#d9d2c0", "#b5462f", "#2b8c8c"]
SPARK = ["#ffd166", "#ff6b6b", "#7df9ff", "#c9a0ff", "#9be38a", "#ffffff"]
CELL, BLOCK, ROWS, COLS, RIVER = 44, 34, 20, 22, 5
KEEP_CLEAR = [(-94, -14, 88, 122), (-134, -96, 88, 122), (-60, -39, 124, 142), (-125, -75, 126, 160), (12, 60, 95, 128)]
BAYS = [(x, z) for z in (108, 93) for x in (-94, -78, -62, -46, -30)]   # the yard: far row first
CHIMNEYS = (28, 40)
RAIL = 300                                                      # the viaduct; nearer than this is the near layer


class View:
    """The wide picture magnified m times: a longer lens from the same window."""

    def __init__(self, m, cx, cy):
        self.m, self.ox, self.oy = m, W / 2 - cx * m, H / 2 - cy * m

    def at(self, x, y):                                         # a pixel of the wide picture
        return (self.ox + x * self.m, self.oy + y * self.m)

    def P(self, x, y, z):                                       # the world: across, up, away
        k = F / z
        return (self.ox + (VX + x * k) * self.m, self.oy + (HOR - (y - CAM) * k) * self.m)

    def k(self, z):                                             # pixels per metre at that distance
        return F * self.m / z


class Light:
    def __init__(self, day, wet=0.0):
        self.day, self.wet = day, wet
        self.night = n = 0.5 + 0.5 * math.cos(day * 2 * math.pi)       # 1 at midnight, 0 at noon
        self.dusk = min(1.0, max(0.0, (n - 0.35) * 3))                 # lamps and windows come on
        self.gold = g = math.exp(-((n - 0.5) / 0.13) ** 2)             # sunrise and sunset
        self.top = mix(ramp(["#3a6ea8", "#1d2a5c", "#060816"], n), "#5b3f7a", g * 0.35)
        self.low = mix(ramp(["#b8d4ee", "#6a4a78", "#221c40"], n), "#ff9a5a", g * 0.8)
        grey = 0.65 * wet * (1 - 0.6 * n)                              # rain clouds close the sky over
        self.top, self.low = mix(self.top, "#5f6878", grey), mix(self.low, "#a4acb8", grey)
        self.haze = mix(self.low, self.top, 0.25)
        self.east = math.sin(day * 2 * math.pi) * min(1.0, (1 - n) * 1.6)     # the sun on east or west walls
        h = day * 24
        self.awake = 1.0 if h >= 12 else 0.3 + 0.7 * max(0.0, 1 - h / 2.5)    # windows still lit

    def sh(self, col, z=0.0, k=0.5):                            # a daytime colour now, and through the air
        c = mix(mix(mix(col, DARK, self.night * k), "#ffb27a", self.gold * 0.16 * (1 - self.wet)), "#6b7280", self.wet * 0.22)
        return mix(c, self.haze, min(0.75, max(0.0, (z - 90) / 900)))


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
              (88, 54, "yard", 0, []), (430, 20, "mast", 0, []), (RAIL, 0, "rail", 0, []), (250, 296, "wheel", 0, [])]
    state["items"] = sorted(items, key=lambda it: (-it[0], -it[1]))
    state["stars"] = [(rnd.randrange(W), rnd.randrange(int(HOR)), rnd.random()) for _ in range(40)]
    state["clouds"] = [(rnd.random(), 6 + rnd.randrange(34), 18 + rnd.randrange(14)) for _ in range(5)]
    state["cars"] = [(rnd.random(), rnd.choice(CARS)) for _ in range(44)]
    order = [(lv, i) for lv in range(2) for i in rnd.sample(range(10), 10)]     # bays fill in no order
    state["boxes"] = [(lv, i, rnd.choice(BOXES)) for lv, i in order]
    state["tv"] = [(rnd.uniform(-170, 150), rnd.uniform(4, 16), rnd.choice((132, 176, 220)), rnd.random() * 6.28) for _ in range(14)]
    state["glints"] = [(rnd.uniform(-420, 300), rnd.uniform(310, 340), rnd.random()) for _ in range(46)]
    state["beads"] = [(rnd.randrange(3, 61), rnd.random(), 0.5 + rnd.random()) for _ in range(15 * 7)]
    glare = Image.new("L", (W, H), 0)                           # daylight on each pane of glass
    g = ImageDraw.Draw(glare)
    for k in range(1, 16):
        x0, y0, _, _ = key_box(k)
        g.polygon(((x0 + 40, y0), (x0 + 52, y0), (x0 + 16, y0 + 64), (x0 + 4, y0 + 64)), fill=16)
        g.polygon(((x0 + 57, y0), (x0 + 61, y0), (x0 + 25, y0 + 64), (x0 + 21, y0 + 64)), fill=9)
    state.update(glare=glare, ease={}, cache={}, cam=None, tick=(None, 0.0), past=None, cat=0.0, airship=-99.0, train=None, wheel=0.0,
                 yard={"have": None, "job": None, "gx": -55.0, "gz": 93.0, "news": None})


def level(s, name, state=None, k=0.3):
    v = getattr(s, name).value
    v = 0.0 if v is None or v != v else min(1.0, max(0.0, float(v)))
    if state is not None:                                       # eased, so nothing jumps
        e = state["ease"]
        v = e[name] = e.get(name, v) + (v - e.get(name, v)) * k
    return v


def lerp(a, b, u):
    return a + (b - a) * u


def inside(s, vals, state, t):
    """What is inside each number, from the `data` of its reading, and a plain guess where a reading has none:
    the cores behind the load, the cache above the memory in use, which way the traffic goes, what the
    disk is doing, and the graphics card's memory and heat."""
    e = state["ease"]

    def num(sig, key, default):
        x = getattr(s, sig).data.get(key)
        return default if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x else float(x)

    def eased(key, x, k=0.2):
        x = min(1.0, max(0.0, x))
        e[key] = e.get(key, x) + (x - e.get(key, x)) * k
        return e[key]
    raw = s.cpu.data.get("cores")
    cores = sorted((min(1.0, max(0.0, float(c))) for c in (raw if isinstance(raw, (list, tuple)) else [])
                    if isinstance(c, (int, float)) and not isinstance(c, bool) and c == c), reverse=True)
    if len(cores) > 32:                                         # a door has room for eight flames: past that, the busiest of each group
        cores = [cores[i * len(cores) // 32] for i in range(32)]
    n, doors = len(cores), []
    for i in range(4):                                          # a door for each quarter of the cores, busiest first
        part = [vals["cpu"]] * 4 if not n else cores[i:i + 1] if n < 4 else cores[i * n // 4:(i + 1) * n // 4]     # under four cores, doors stay shut
        doors.append([eased(f"core{i}.{j}", c, 0.3) for j, c in enumerate(part)])
    timetable = 0.5 if t % 34 < 2 else 0.0                      # no word from the disk: a train now and then all the same
    gpu = bool(s.gpu.live and s.gpu.value is not None)
    return {"doors": doors,
            "kernel": eased("kernel", num("cpu", "kernel", 0.25), 0.1),
            "top": str(s.cpu.data.get("top") or ""), "top_share": min(1.0, max(0.0, num("cpu", "top_share", 0.0))),
            "cache": eased("cache", num("ram", "cache", 0.0)), "swap": eased("swap", num("ram", "swap", 0.0)),
            "down": eased("down", num("net", "down", vals["net"]), 0.3), "up": eased("up", num("net", "up", vals["net"]), 0.3),
            "read": eased("read", num("disk", "read", timetable), 0.15), "write": eased("write", num("disk", "write", 0.0), 0.15),
            "gpu": gpu, "vram": eased("vram", num("gpu", "vram", 0.0)), "temp": num("gpu", "temp", 50.0)}


def more(shot, s, ins):
    """The second plate of a close-up: what is inside the number, as rows of (text, size, colour)."""
    grey, white, amber = "#aab3d6", "#ffffff", "#ffc46b"

    def fit(txt, size, col):                                    # as large as the pane's width lets it be
        return (txt, max(10, min(size, int(96 / max(1, len(txt))))), col)

    def short(rate):                                            # "682.4K/s" -> "682K"
        r = str(rate)[:-2] if str(rate).endswith("/s") else str(rate)
        return (r[:-1].split(".")[0] if len(r) > 4 else r[:-1]) + r[-1:]
    if shot == "cpu":                                           # who does the work, and how much of it is the system's own
        sys_ = fit(f"sys {ins['kernel'] * 100:.0f}%", 13, amber)
        name, share = ins["top"], f"{ins['top_share'] * 100:.0f}%"
        if len(name) > 8:                                       # a long name takes two rows
            return [fit(name[:7], 13, grey), fit(name[7:14], 13, grey), (share, 17, white), (sys_[0], 12, amber)]
        return [fit(name, 14, grey), (share, 20, white), sys_] if name else [sys_]
    if shot == "ram" and "cache" in s.ram.data:
        return [("cache", 13, grey), (f"{ins['cache'] * 100:.0f}%", 22, white), fit(f"swap {ins['swap'] * 100:.0f}%", 13, amber)]
    if shot == "net" and "down_text" in s.net.data:
        return [fit("dn " + short(s.net.data["down_text"]), 15, "#fff7c2"), fit("up " + short(s.net.data.get("up_text", "")), 15, "#ff6b6b")]
    if shot == "disk" and "read_text" in s.disk.data:
        return [fit("rd " + short(s.disk.data["read_text"]), 15, READ), fit("wr " + short(s.disk.data.get("write_text", "")), 15, WRITE)]
    if shot == "gpu" and ins["gpu"] and "vram" in s.gpu.data:
        hot = ramp(["#58b8ff", "#ffd166", "#ff4d2e"], min(1.0, max(0.0, (ins["temp"] - 40) / 45)))
        return [("VRAM", 13, grey), fit(str(s.gpu.data.get("vram_text") or f"{ins['vram'] * 100:.0f}%"), 22, white), (f"{ins['temp']:.0f}\u00b0C", 15, hot)]
    return None


# ---------------------------------------------------------------- what stands still

def boxer(d, v, L, fine=True):
    def box(x0, x1, z0, z1, h, col, seed=0.0, y0=0.0, plain=False):
        k = v.k(z0)
        a, b = v.P(x0, h, z0), v.P(x1, y0, z0)
        back = v.P(x1 if x1 < 0 else x0, h, z1)
        if max(b[0], back[0]) < 0 or min(a[0], back[0]) > W or b[1] < 0 or min(a[1], back[1]) > H:
            return
        c = L.sh(col, z0)
        if x1 < 0 or x0 > 0:                                    # the wall that faces the boulevard
            x = x1 if x1 < 0 else x0
            shade = 0.3 - (0.2 if x1 < 0 else -0.2) * L.east
            d.polygon((v.P(x, h, z0), v.P(x, h, z1), v.P(x, y0, z1), v.P(x, y0, z0)), fill=mix(c, DARK, shade))
        if h < CAM:
            d.polygon((a, v.P(x1, h, z0), v.P(x1, h, z1), v.P(x0, h, z1)), fill=mix(c, L.sh("#8d919c", z0), 0.55))
        d.rectangle((a[0], a[1], b[0], b[1]), fill=c)
        if plain:
            return
        lit = L.dusk * L.awake
        if k >= 3.2 and fine:                                   # close enough for windows
            pane = mix(mix(c, "#1c2233", 0.55), L.top, 0.25 * (1 - L.night))
            for fl in range(int((h - y0) / 3.6)):
                yb = fl * 3.6 + 1.0
                top, bot = b[1] - (yb + 2.0) * k, b[1] - yb * k
                if bot < 0 or top > H:
                    continue
                for cl in range(int((x1 - x0 - 1) / 3.0)):
                    xa = a[0] + (1 + cl * 3.0) * k
                    u = (seed * 97.3 + fl * 0.618 + cl * 0.381) % 1.0
                    d.rectangle((xa, top, xa + 1.8 * k, bot), fill=("#ffd98a" if u < lit * 0.4 else "#bfe3ff") if u < lit * 0.5 else pane)
            return
        if k >= 1.3:                                            # floors
            for fl in range(1, int(h / 3.6) + 1):
                y = b[1] - (fl * 3.6 - 1.6 - y0) * k
                d.rectangle((a[0] + 1, y, b[0] - 1, y + max(1, k * 0.9)), fill=mix(c, "#1c2233", 0.45))
        if lit > 0.15:                                          # lights
            sz = max(1.0, k * 1.1)
            for q in range(max(1, int(h / 4 * L.awake))):
                u, w = (seed * 37.1 + q * 0.618) % 1.0, (seed * 91.7 + q * 0.414) % 1.0
                x, y = a[0] + 1 + u * (b[0] - a[0] - 2 - sz), a[1] + 1 + w * (b[1] - a[1] - 2 - sz)
                d.rectangle((x, y, x + sz, y + sz), fill="#ffd98a" if q % 4 else "#bfe3ff")
    return box


def paint(d, v, L, box, item):
    z0, _, kind, x0, lots = item
    k, M = v.k(z0), v.m
    if kind in ("lots", "park"):
        g = (v.P(x0, 0, z0), v.P(x0 + BLOCK, 0, z0), v.P(x0 + BLOCK, 0, z0 + BLOCK), v.P(x0, 0, z0 + BLOCK))
        if max(p[0] for p in g) < -60 * M or min(p[0] for p in g) > W + 60 * M or g[0][1] < -150 * M or g[2][1] > H:
            return
        d.polygon(g, fill=L.sh("#9a9daa" if kind == "lots" else "#5f9a5b", z0, 0.75))
    if kind == "park":
        for lx0, _, lz0, _, _, _, seed in lots:
            for q in range(3):
                c, r = v.P(lx0 + 3 + q * 5, 4, lz0 + 3 + (q * 7 + seed * 9) % 10), max(1.0, k * 3.2)
                d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r * 0.8), fill=L.sh("#2f7f3f" if q % 2 else "#3f9a4f", z0))
    elif kind == "lots" and k > 1.0:
        for lx0, lx1, lz0, lz1, h, col, seed in lots:
            box(lx0, lx1, lz0, lz1, h, col, seed)
    elif kind == "lots":
        box(x0 + 1, x0 + BLOCK - 1, z0 + 1, z0 + BLOCK - 1, max(l[4] for l in lots) * 0.8, lots[0][5], lots[0][6])
    elif kind == "rail":                                        # the railway viaduct along the river
        col = L.sh("#b9a58c", RAIL)
        for x in range(-700, 700, 30):
            a, b = v.P(x, 24, RAIL), v.P(x + 2.5, 0, RAIL)
            d.rectangle((a[0], a[1], max(b[0], a[0] + 1), b[1]), fill=mix(col, DARK, 0.2))
        a, b = v.P(-700, 26, RAIL), v.P(700, 24, RAIL)
        d.rectangle((a[0], a[1], b[0], max(b[1], a[1] + 1)), fill=col)
    elif kind == "wheel":                                       # the big wheel's legs
        hub = v.P(-296, 22, 250)
        d.line((*v.P(-306, 0, 250), *hub, *v.P(-286, 0, 250)), fill=L.sh("#5b6170", 200), width=max(2, round(2.5 * M)))
    elif kind == "mast":                                        # the tower downtown, at the end of the boulevard
        box(15, 25, 430, 440, 150, "#7d8597", plain=True)
        c, r, col = v.P(20, 140, 430), 7 * M, L.sh("#c4c9d6", 300)
        d.ellipse((c[0] - r, c[1] - r * 0.7, c[0] + r, c[1] + r * 0.7), fill=col)
        d.line((*c, c[0], c[1] - 16 * M), fill=col, width=max(2, round(1.6 * M)))
        if M >= 2:                                              # the deck's windows
            d.rectangle((c[0] - r * 0.85, c[1] - M, c[0] + r * 0.85, c[1] + M), fill=mix("#22304a", "#ffd98a", L.dusk))
    elif kind == "flats":                                       # apartments, and the water tank on their roof
        box(-125, -75, 126, 160, 34, "#c9a88a", seed=0.37)
        wood, iron = L.sh("#7a5a3c"), L.sh("#4d5364")
        a, b = v.P(-113, 53, 132), v.P(-93, 38, 132)
        for x in (-110.8, -95.2):
            d.line((*v.P(x, 38, 132), *v.P(x, 34, 132)), fill=L.sh("#5b6170"), width=max(2, round(1.1 * k)))
        d.rectangle((a[0], a[1], b[0], b[1]), fill=wood)
        for i in range(1, 6):
            x = a[0] + i * 3.33 * k
            d.line((x, a[1], x, b[1]), fill=mix(wood, DARK, 0.3), width=max(1, round(k * 0.2)))
        d.polygon(((a[0] - 1.6 * k, a[1] + 0.5 * k), ((a[0] + b[0]) / 2, a[1] - 7 * k), (b[0] + 1.6 * k, a[1] + 0.5 * k)), fill=iron)
        for y in (a[1] + 2.2 * k, b[1] - 1.6 * k):
            d.line((a[0], y, b[0], y), fill=L.sh("#3a3f4c"), width=max(2, round(k * 0.8)))
    elif kind == "clock":
        box(-58, -41, 126, 140, 48, "#9c95a8", plain=True)
        stone = L.sh("#9c95a8")
        if k >= 4:                                              # courses of stone
            for y in range(4, 48, 4):
                d.line((*v.P(-58, y, 126), *v.P(-41, y, 126)), fill=mix(stone, DARK, 0.14), width=max(1, round(k * 0.12)))
        d.polygon((v.P(-59.5, 48, 126), v.P(-49.5, 61, 126), v.P(-39.5, 48, 126)), fill=L.sh("#4a4763"))
        c, r = v.P(-49.5, 38.4, 126), 19 * M
        d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=mix(stone, DARK, 0.55))
    elif kind == "plant":
        box(12, 60, 95, 128, 20, "#8a4b3a", plain=True)
        brick = mix(L.sh("#8a4b3a"), DARK, 0.22)
        for i in range(1, 8):                                   # courses of brick
            d.line((*v.P(12, 20 - i * 2.4, 95), *v.P(60, 20 - i * 2.4, 95)), fill=brick, width=max(1, round(k * 0.2)))
        for cx in CHIMNEYS:                                     # the chimneys stand on its roof
            box(cx, cx + 7, 110, 117, 80, "#a3a3ae", y0=20, plain=True)
            for y in (70, 54):
                a, b = v.P(cx, y + 6, 110), v.P(cx + 7, y, 110)
                d.rectangle((a[0], a[1], b[0], b[1]), fill=L.sh("#d6453d", 0, 0.45))
    elif kind == "yard":
        d.polygon((v.P(-98, 0, 90), v.P(-12, 0, 90), v.P(-12, 0, 118), v.P(-98, 0, 118)), fill=L.sh("#4a4d58", 0, 0.7))
        for x, z in BAYS:
            d.polygon((v.P(x, 0, z), v.P(x + 14, 0, z), v.P(x + 14, 0, z + 6), v.P(x, 0, z + 6)), outline=L.sh("#d4d7e0", 0, 0.5))
        for x in (-97, -13):                                    # the crane's rails
            d.line((*v.P(x, 0, 91), *v.P(x, 0, 116)), fill=L.sh("#22242c"), width=max(1, round(k * 0.3)))


def layers(state, v, L, fine=True):
    """Everything that stands still, for this lens and this light: the sky, the far city, the near city.
    While the lens is moving it is drawn every frame, without the windows nobody could see."""
    M = v.m
    sky = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(sky)

    def tone(Y):
        return mix(L.top, L.low, min(1.0, max(0.0, (Y - v.oy) / M / HOR)))
    for Y in range(0, H, 2):
        d.rectangle((0, Y, W, Y + 1), fill=tone(Y))
    for x, y, b in state["stars"]:
        if L.night * b > 0.3:
            p = v.at(x, y)
            d.rectangle((p[0], p[1], p[0] + M * 0.5, p[1] + M * 0.5), fill=mix(L.top, "#ffffff", L.night * b))
    h = L.day * 24                                              # both rise on the right and set on the left
    sun = 5.6 < h < 18.4
    u = min(1.0, max(0.0, ((h - 6) if sun else (h - 18) % 24) / 12))
    c, r = v.at(W * (0.93 - 0.86 * u), HOR + 4 - 60 * math.sin(u * math.pi)), (11 + 3 * L.gold) * M
    if sun:
        d.ellipse((c[0] - r * 1.8, c[1] - r * 1.8, c[0] + r * 1.8, c[1] + r * 1.8), fill=mix(tone(c[1]), "#ffc27a", 0.15 + 0.3 * L.gold))
        d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=mix(mix("#fff3c4", "#ff7a3c", L.gold), tone(c[1]), L.wet * 0.8))
    else:
        d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill="#f4f1e6")
        d.ellipse((c[0] - r * 0.45, c[1] - r * 1.2, c[0] + r * 1.2, c[1] + r * 0.65), fill=tone(c[1]))

    land = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(land)
    hill = mix(L.haze, L.sh("#5f8f5a"), 0.4)                    # hills beyond the city
    ridge = [v.at(x, HOR - 3 - 3 * math.sin(x * 0.021) - 2 * math.sin(x * 0.05 + 1)) for x in range(-40, W + 61, 20)]
    d.polygon(ridge + [v.at(W + 60, HOR + 2), v.at(-40, HOR + 2)], fill=hill)
    hy = v.at(0, HOR)[1]
    if hy < H:
        d.rectangle((0, max(0, hy), W, H), fill=hill)
    ew = HOR + CAM * F / (88 + CELL * ROWS)
    street = L.sh("#454853", 0, 0.7)
    for Y in range(max(0, int(v.at(0, ew)[1])), H, 2):          # streets
        d.rectangle((0, Y, W, Y + 1), fill=mix(L.haze, street, min(1.0, 0.25 + ((Y - v.oy) / M - ew) / 50)))
    zr = 88 + CELL * RIVER
    river = mix(L.sh("#3f7fb8", zr, 0.75), L.low, 0.3 * L.gold)
    d.polygon((v.P(-2000, 0, zr - 4), v.P(2000, 0, zr - 4), v.P(2000, 0, zr + 38), v.P(-2000, 0, zr + 38)), fill=river)
    if L.dusk > 0.3:                                            # the city's lights in the water
        for x, z, b in state["glints"]:
            p = v.P(x, 0, z)
            d.rectangle((p[0], p[1], p[0] + max(1, M * 0.6), p[1] + (2 + 3 * b) * M), fill=mix("#ffd98a", river, 0.35 + 0.4 * b))
    for bx in range(-8 * CELL - 17, 8 * CELL, 2 * CELL):        # bridges
        d.polygon((v.P(bx - 4, 0, zr - 4), v.P(bx + 4, 0, zr - 4), v.P(bx + 4, 0, zr + 38), v.P(bx - 4, 0, zr + 38)), fill=L.sh("#8d919c", zr))
    d.polygon((v.P(-12, 0, 60), v.P(12, 0, 60), v.P(12, 0, 960), v.P(-12, 0, 960)), fill=L.sh("#3b3e4b", 0, 0.7))    # the boulevard
    d.polygon((v.P(-1, 0, 60), v.P(1, 0, 60), v.P(1, 0, 960), v.P(-1, 0, 960)), fill=L.sh("#6f8f62", 0, 0.6))
    if M >= 1.8:                                                # lane marks
        for z in range(64, 400, 8):
            for x in (-7, 7):
                d.line((*v.P(x, 0, z), *v.P(x, 0, z + 3)), fill=L.sh("#b8bcc8", z, 0.6), width=max(1, round(v.k(z) * 0.25)))
    for z in range(90, 400, 14):
        c, r = v.P(0, 3, z), max(1.0, v.k(z) * 1.6)
        d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=L.sh("#3f8a4a", z, 0.6))
    box = boxer(d, v, L, fine)
    for it in state["items"]:
        if it[0] >= RAIL:
            paint(d, v, L, box, it)

    near = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(near)
    box = boxer(d, v, L, fine)
    for it in state["items"]:
        if it[0] < RAIL:
            paint(d, v, L, box, it)
    return sky, land, near


# ---------------------------------------------------------------- how long it has been going on

def remember(t, state, vals):
    """A spike and a long peak show the same number, so the city keeps time: how many seconds each number
    has stayed over its line, how the traffic has run lately, how wet the streets are, and a puff of the
    plant's smoke for every three seconds of the last four and a half minutes."""
    m = state["past"]
    if m is None or not 0 <= t - m["t"] < 5:                    # first frame, or another page was showing
        keep = m or {"held": dict.fromkeys(ALARM, 0.0), "dip": dict.fromkeys(ALARM, 0.0), "down": vals["down"], "up": vals["up"], "burn": vals["cpu"],
                     "wet": 0.0, "smoke": [], "puff": -9.0}
        m = state["past"] = {**keep, "t": t}
    dt, m["t"] = t - m["t"], t
    m["dt"] = dt
    for n, th in ALARM.items():
        if vals[n] > th:
            m["held"][n], m["dip"][n] = m["held"][n] + dt, 0.0
        else:                                                   # a dip of a few seconds does not end a peak; eight do
            m["dip"][n] += dt
            if m["dip"][n] > 8:
                m["held"][n] = 0.0
    for way in ("down", "up"):
        m[way] += (vals[way] - m[way]) * min(1.0, dt / 20)
    m["burn"] += (vals["cpu"] - m["burn"]) * min(1.0, dt / 15)       # what goes up the chimney: a spike is a pale wisp
    m["wet"] = min(1.0, m["wet"] + dt / 40) if vals["ram"] > 0.6 else max(0.0, m["wet"] - dt / 90)
    if not 0 <= t - m["puff"] < 3:
        m["puff"] = t
        m["smoke"] = [q for q in m["smoke"] if 0 <= t - q[0] < 270] + [(t, m["burn"])]
    return m


# ---------------------------------------------------------------- who decides where to look

def camera(t, state, vals, since):
    """Where the lens is now. Returns (m, cx, cy, moving, noticing, arrived)."""
    c = state["cam"]
    if c is None or t < c["seen"] or t - c["seen"] > 30:
        c = state["cam"] = {"shot": "wide", "frm": SHOTS["wide"], "t0": t - 9, "until": 0.0, "home": t,
                            "armed": {}, "hour": since < 60, "n": 0, "seen": t}
    c["seen"] = t

    def now():
        u = min(1.0, max(0.0, (t - c["t0"]) / MOVE))
        e = u * u * (3 - 2 * u)
        (m0, x0, y0), (m1, x1, y1) = c["frm"], SHOTS[c["shot"]]
        if abs(m1 - m0) < 1e-3:
            return m1, lerp(x0, x1, e), lerp(y0, y1, e), u
        m = m0 * (m1 / m0) ** e
        f = (1 / m - 1 / m1) / (1 / m0 - 1 / m1)                # one point of the picture holds still
        return m, x1 + (x0 - x1) * f, y1 + (y0 - y1) * f, u

    def go(name, hold):
        m, x, y, _ = now()
        wait = NOTICE if c["shot"] == "wide" and name != "wide" else 0.0
        c.update(frm=(m, x, y), shot=name, t0=t + wait, until=t + wait + MOVE + hold)
        if name == "wide":
            c["home"] = t + MOVE

    held, patience = state["past"]["held"], float(state["opts"].get("patience", 30) or 0)
    for n in ALARM:
        if held[n] == 0:
            c["armed"][n] = True
    if since > 60:
        c["hour"] = False
    tap = state.pop("tapped", None)
    u = now()[3]
    if tap:
        go("wide", 0) if c["shot"] != "wide" else go(PANE[tap - 1], 12)
    elif c["shot"] != "wide":
        if u >= 1 and t > c["until"]:
            go("wide", 0)
    elif u >= 1 and t - c["home"] > 3:
        tour = float(state["opts"].get("tour", 60) or 0)
        if since < 30 and not c["hour"]:                        # the hour struck (the clock is read every few seconds)
            want, c["hour"] = "time_of_day", True
        elif state["yard"]["news"] is not None:
            want = "disk"
        else:
            want = next((n for n in ALARM if held[n] > patience and c["armed"].get(n)), None)   # not a spike: it stayed
        if want in ALARM:
            c["armed"][want] = False
        if want:
            go(want, 8)
        elif tour and t - c["home"] > tour:
            name = TOUR[c["n"] % len(TOUR)]
            c["n"] += 1
            if name != "gpu" or vals["has_gpu"]:                # nothing to visit at the wheel without a graphics card to read
                go(name, 6)
    m, x, y, u = now()
    return m, x, y, 0 < u < 1, c["shot"] != "wide" and u < 1, c["shot"] != "wide" and u >= 1


def crane(t, state, disk):
    """The yard's crane: one container set down or lifted away at a time. Returns (hook height, what hangs on it)."""
    y = state["yard"]
    want = round(disk * 20)
    if y["have"] is None or abs(want - y["have"]) > 3:
        y["have"], y["job"] = want, None
    job, cam = y["job"], state["cam"] or {}
    if job is None and want != y["have"]:
        if y["news"] is None:
            y["news"] = t                                       # the cat hears it first
        coming = cam.get("shot") == "disk"
        there = coming and t > cam.get("t0", 0) + MOVE + 0.4
        if there or (not coming and not 0 <= t - y["news"] < 3.5):      # it starts once the lens is on it, or nobody is coming
            add = want > y["have"]
            job = y["job"] = {"n": y["have"] if add else y["have"] - 1, "add": add, "t0": t, "gx": y["gx"], "gz": y["gz"], "done": False}
            y["news"] = None
    elif want == y["have"]:
        y["news"] = None
    if job is None:
        return None
    lv, i, col = state["boxes"][job["n"]]
    bx, bz = BAYS[i][0] + 7, BAYS[i][1]
    p = t - job["t0"]
    go = min(1.0, max(0.0, p / 1.4))
    go = go * go * (3 - 2 * go)
    y["gx"], y["gz"] = lerp(job["gx"], bx, go), lerp(job["gz"], bz, go)
    down = min(1.0, max(0.0, (p - 1.4) / 0.9))
    up = min(1.0, max(0.0, (p - 2.3) / 0.7))
    hook = lerp(lerp(14, lv * 5, down), 14, up)
    if p >= 2.3 and not job["done"]:                            # the moment it lets go, or takes hold
        job["done"] = True
        y["have"] += 1 if job["add"] else -1
    carrying = (p < 2.3) if job["add"] else (p >= 2.3)
    if not job["add"] and p > 3.0:                              # carried off to the end of the yard
        y["gx"] = lerp(bx, -22, min(1.0, (p - 3.0) / 1.2))
    if p > (3.0 if job["add"] else 4.2) or p < 0:
        y["job"] = None
    return hook, col if carrying else None


# ---------------------------------------------------------------- the frame

def draw(im, d, s, t, state):
    day = level(s, "time_of_day")
    vals = {n: level(s, n, state) for n in ("cpu", "ram", "net", "disk")}
    cpu, ram, net, disk = vals["cpu"], vals["ram"], vals["net"], vals["disk"]
    ins = inside(s, vals, state, t)
    vals.update(gpu=level(s, "gpu", state) if ins["gpu"] else 0.0, has_gpu=ins["gpu"], down=ins["down"], up=ins["up"])
    since = ((day * 24) % 1.0) * 3600                           # seconds past the hour
    load = crane(t, state, disk)
    past = remember(t, state, vals)
    held, patience = past["held"], float(state["opts"].get("patience", 30) or 0)
    m, cx, cy, moving, noticing, arrived = camera(t, state, vals, since)
    cam = state["cam"]
    v, M = View(m, cx, cy), m
    P, at = v.P, v.at
    rain = max(0.0, (ram - 0.6) / 0.4)                          # starts at 60 % memory, full at 100 %
    wet = round(rain * 5) / 5
    L = Light(day, wet)
    sh = L.sh
    key = (round(m, 3), round(cx, 1), round(cy, 1), int(day * 288), wet)
    if moving:
        sky, land, near = layers(state, v, L, fine=False)
    else:
        if key not in state["cache"]:
            if len(state["cache"]) > 2:
                state["cache"].clear()
            state["cache"][key] = layers(state, v, Light(key[3] / 288, wet))
        sky, land, near = state["cache"][key]
    wind = 0.6 + 0.4 * math.sin(t / 40)

    def px(n):
        return max(1, round(n * M))

    # ---- the sky
    im.paste(sky)
    for ph, y, r in state["clouds"]:
        c, R = at(((ph + t / 200) % 1.0) * (W + 80) - 40, y), r * M
        col = mix(mix(mix(mix(L.top, "#ffffff", 0.75), "#ffb89a", L.gold * 0.6), "#59606e", L.wet * 0.6), L.top, L.night * 0.85)
        R *= 1 + L.wet
        d.ellipse((c[0] - R, c[1] - R * 0.4, c[0] + R, c[1] + R * 0.4), fill=col)
        d.ellipse((c[0] - R * 0.3, c[1] - R * 0.7, c[0] + R * 0.6, c[1] + R * 0.25), fill=col)
    bow = past["wet"] * (1 - L.dusk * 2.5) if rain == 0 else 0.0
    trail = [(t - t0, c) for t0, c in past["smoke"] if c > 0.3]
    if trail or bow > 0.02:                                     # soft things: drawn at half size and laid over the sky
        veil = Image.new("RGBA", (W // 2, H // 2), (0, 0, 0, 0))
        dv = ImageDraw.Draw(veil)
        u = (L.day * 24 - 6) / 12                               # where the sun is, when it is up
        sun, sr = at(W * (0.93 - 0.86 * u), HOR + 4 - 60 * math.sin(u * math.pi)) if 0 < u < 1 else None, (11 + 3 * L.gold) * M
        for age, c in trail:                                    # the plant's last minutes: the oldest smoke is furthest left
            x, y = at(400 - age * DRIFT, 21 - 7 * (1 - math.exp(-age / 25)) + 4 * math.sin(age / 31 + c * 40))
            r, dark = (8 + 9 * c + age * 0.02) * M, (c - 0.3) / 0.7
            a = 230 * min(1.0, dark * 1.6) * min(1.0, (270 - age) / 40)
            dv.ellipse((x / 2 - r / 2, y / 2 - r * 0.26, x / 2 + r / 2, y / 2 + r * 0.26),    # flat and brown: no cloud
                       fill=(*mix(mix("#dcd8d6", SOOT, dark), L.top, 0.1)[:3], int(a)))
        over = [(sun[0] + i * sr / 2, sun[1] + j * sr / 2) for i, j in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))] if sun else []
        thick = sum(veil.getpixel((int(x / 2), int(y / 2)))[3] for x, y in over if 0 <= x < W - 1 and 0 <= y < H - 1) / 1275
        if bow > 0.02:                                          # the rain stopped and the streets are still wet:
            c = at(150 if u < 0.5 else 290, 128)                # a rainbow, on the side away from the sun
            for i, col in enumerate(("#ff5a5a", "#ffb04a", "#ffe95a", "#6fdc7a", "#5aa8ff", "#a57aff")):
                r = (118 - i * 3) * M
                dv.arc((c[0] / 2 - r / 2, c[1] / 2 - r / 2, c[0] / 2 + r / 2, c[1] / 2 + r / 2), 180, 360,
                       fill=(*ImageColor.getrgb(col), int(150 * min(1.0, bow))), width=max(2, round(1.6 * M)))
        veil = veil.resize((W, H), Image.BILINEAR)
        im.paste(veil, (0, 0), veil)
        if thick > 0.1 and L.wet < 0.5:                         # the sun behind smoke is a red disc, as over a fire
            d.ellipse((sun[0] - sr, sun[1] - sr, sun[0] + sr, sun[1] + sr),
                      fill=mix(mix(mix("#fff3c4", "#ff7a3c", L.gold), "#ff4d2e", min(1.0, thick * 1.5)), SOOT, 0.3 * thick))
    if L.night > 0.7 and t % 37 < 0.5:                          # a shooting star
        p, x0 = (t % 37) / 0.5, 110 + int(t / 37) * 53 % 200
        d.line((*at(x0 + p * 60, 6 + p * 20), *at(x0 + p * 60 - 20, p * 20)), fill=mix(L.top, "#ffffff", 1 - p), width=px(1))
    if L.dusk > 0.5 and since < 26:                             # the hour, at night: fireworks behind the skyline
        for i in range(16):                                     # each one opens in a pane of the top row
            a = since - i * 1.5
            ox, oy, col = (126, 220, 32, 314, 173, 79, 267)[i % 7] + i * 5 % 9, 24 + i * 11 % 16, SPARK[i % len(SPARK)]
            if -0.8 < a <= 0:                                   # the rocket
                y = HOR - (HOR - oy) * (1 + a / 0.8)
                d.line((*at(ox, y), *at(ox, y + 7)), fill="#ffe9b0", width=px(2))
            elif 0 < a < 2.8:
                R, fade = 31 * (1 - math.exp(-a * 2.4)), min(1.0, a / 2.8) ** 2
                for q in range(14):
                    an = q * math.pi / 7 + i
                    for ring, wd, cl in ((1.0, 2.2, col), (0.52, 1.5, SPARK[(i + 3) % len(SPARK)])):
                        e = at(ox + R * ring * math.cos(an), oy + R * ring * math.sin(an) * 0.92 + a * a * 2.0)
                        b = at(ox + R * ring * 0.66 * math.cos(an), oy + R * ring * 0.66 * math.sin(an) * 0.92 + a * a * 1.5)
                        d.line((*b, *e), fill=mix(cl, L.top, fade), width=px(wd))
                        if ring == 1.0 and a > 0.5:             # the tips glitter
                            d.rectangle((e[0] - px(1), e[1] - px(1), e[0] + px(1), e[1] + px(1)),
                                        fill=mix("#ffffff", L.top, fade if int(t * 10 + q) % 2 else 1.0))
                if a < 0.3:
                    c = at(ox, oy)
                    d.ellipse((c[0] - 7 * M, c[1] - 7 * M, c[0] + 7 * M, c[1] + 7 * M), fill="#ffffff")
    storm = held["ram"] > patience and (int(t / 1.3) * 7919 % 5 == 0) and t % 1.3 < 0.3      # a storm takes a while to build
    if storm:                                                   # lightning, somewhere over the city
        rnd = random.Random(int(t / 1.3))
        x, pts = rnd.randrange(40, W - 40), []
        for i in range(7):
            pts.append(at(x + rnd.randrange(-9, 10) * (i > 0), i * (HOR + 6) / 6))
        d.line([q for p in pts for q in p], fill="#f4f6ff", width=px(3))
        d.line((*pts[3], *at(x + 16, 3.6 * (HOR + 6) / 6), *at(x + 13, 4.6 * (HOR + 6) / 6)), fill="#f4f6ff", width=px(2))

    # ---- the far city, and what runs between it and the near one
    im.paste(land, (0, 0), land)
    c = P(-560 + ((t / 150.0 + 0.3) % 1.0) * 850, 118 + 4 * math.sin(t * 0.2), 350)      # an airship crosses the sky
    hull, trim = sh("#e9e4d6", 0, 0.4), sh("#c8553d", 0, 0.4)
    d.polygon(((c[0] - 20 * M, c[1]), (c[0] - 27 * M, c[1] - 7 * M), (c[0] - 27 * M, c[1] + 7 * M)), fill=trim)
    d.ellipse((c[0] - 22 * M, c[1] - 9 * M, c[0] + 22 * M, c[1] + 9 * M), fill=hull)
    d.rectangle((c[0] - 22 * M, c[1] - M, c[0] + 22 * M, c[1] + M), fill=trim)
    d.rectangle((c[0] - 6 * M, c[1] + 9 * M, c[0] + 6 * M, c[1] + 12 * M), fill=sh("#3a3f4c", 0, 0.4))
    if L.dusk > 0.3 and int(t * 1.5) % 2:
        d.rectangle((c[0] + 20 * M, c[1] - M, c[0] + 22 * M, c[1] + M), fill="#ff4d4d")
    state["airship"] = c[0]
    zb = 88 + CELL * RIVER + 17
    kr = v.k(zb)
    water = sh("#3f7fb8", zb, 0.75)
    for i, (ph, col) in enumerate(((0.1, "#d9d2c0"), (0.62, "#c8553d"))):       # barges on the river
        x = ((ph + t / (140.0 + 60 * i)) % 1.0) * 900 - 560
        x = x if i else -x - 100                                # one each way
        a, b = P(x, 2.5, zb), P(x + 22, 0, zb)
        w0, w1 = (x + 24, x + 52) if i == 0 else (x - 30, x - 2)
        d.line((*P(w0, 0, zb), *P(w1, 0, zb)), fill=mix(water, "#ffffff", 0.35), width=max(1, round(kr * 0.6)))
        d.rectangle((a[0], a[1], b[0], b[1]), fill=sh("#3a3f4c", zb))
        d.rectangle((a[0] + 2 * kr, a[1] - 2.5 * kr, a[0] + 16 * kr, a[1]), fill=sh(col, zb))
        if L.dusk > 0.3:
            d.rectangle((a[0] + 18 * kr, a[1] - 3 * kr, a[0] + 18 * kr + px(1), a[1] - 3 * kr + px(1)), fill="#fff7c2")
    tr = state["train"]                                         # the viaduct carries what the disk moves
    if tr and not 0 <= t - tr["t0"] < 12.5:
        tr = state["train"] = None
    busy = max(ins["read"], ins["write"])
    if tr is None and busy > 0.08:                              # a wagon or six, by how much: blue for reads, orange for writes
        n = 1 + round(5 * busy)
        wr = round(n * ins["write"] / (ins["read"] + ins["write"]))
        tr = state["train"] = {"t0": t, "way": -1 if wr * 2 > n else 1, "cars": [READ] * (n - wr) + [WRITE] * wr}
    if tr:
        way, kt = tr["way"], v.k(RAIL)
        head = lerp(*((-410, 290) if way > 0 else (165, -535)), (t - tr["t0"]) / 11.5)
        for car, col in enumerate(["#f2e8d0"] + tr["cars"]):
            x = head - way * car * 17
            a, b = P(min(x, x - way * 16), 33.5, RAIL), P(max(x, x - way * 16), 26, RAIL)
            col = mix(col, sh(col, RAIL, 0.3), 0.35)            # freight is painted to be seen: less of the distance on it
            d.rectangle((a[0], a[1], b[0], b[1]), fill=col)
            d.line((a[0], b[1], b[0], b[1]), fill=mix("#1a1c26", L.haze, 0.2), width=px(1))     # and it rides on something dark
            if car and b[1] - a[1] >= 5:                        # the wagons carry containers two high
                d.line((a[0], (a[1] + b[1]) / 2, b[0], (a[1] + b[1]) / 2), fill=mix(col, DARK, 0.35), width=px(0.5))
            if car == 0:
                d.rectangle((a[0] + kt, a[1] + kt, max(b[0] - kt, a[0] + kt), a[1] + 2.2 * kt), fill=mix("#22304a", "#ffe9a8", L.dusk))
        tail = P(head - way * (len(tr["cars"]) + 1) * 17, 29.7, RAIL)   # and the blur it leaves
        d.line((*tail, tail[0] - way * 26 * kt, tail[1]), fill=mix(sh(tr["cars"][-1], RAIL, 0.3), L.haze, 0.6), width=max(1, round(kt * 4)))

    # ---- the near city
    im.paste(near, (0, 0), near)
    if L.night < 0.6:                                           # cloud shadows cross the roofs
        mask = Image.new("L", (W // 8, H // 8), 0)             # drawn small and stretched, so they are soft
        g = ImageDraw.Draw(mask)
        for i, (ph, _, r) in enumerate(state["clouds"][:3]):
            yw = 110 + i * 50
            c, R = at(((ph + t / 90) % 1.0) * (W + 300) - 150, yw), r * M * (yw / 50)
            g.ellipse(((c[0] - R) / 8, (c[1] - R * 0.3) / 8, (c[0] + R) / 8, (c[1] + R * 0.3) / 8), fill=int(44 * (1 - L.night / 0.6)))
        im.paste((10, 14, 34), (0, 0), mask.resize((W, H), Image.BILINEAR))
    hub, R = P(-296, 22, 250), 18.2 * M                         # the big wheel: the graphics card turns it
    state["wheel"] += min(0.5, max(0.0, past["dt"])) * ((0.05 + 1.3 * vals["gpu"]) if ins["gpu"] else 0.12)
    spoke = mix(sh("#e8e8f0", 200), "#ffd98a", L.dusk * 0.7)
    d.ellipse((hub[0] - R, hub[1] - R, hub[0] + R, hub[1] + R), outline=spoke, width=px(2))
    for i in range(8):
        an = state["wheel"] + i * math.pi / 4
        e = (hub[0] + R * math.cos(an), hub[1] + R * math.sin(an))
        d.line((*hub, *e), fill=spoke, width=px(0.7))
    if ins["gpu"]:                                              # a green ring inside the rim fills with its memory,
        r = R - 4.2 * M                                         # and the hub is as hot as the card
        d.arc((hub[0] - r, hub[1] - r, hub[0] + r, hub[1] + r), -90, -90 + 360 * ins["vram"], fill=mix("#63f07a", DARK, L.night * 0.2), width=px(2.8))
        r = 4 * M
        d.ellipse((hub[0] - r, hub[1] - r, hub[0] + r, hub[1] + r), fill=ramp(["#58b8ff", "#ffd166", "#ff4d2e"], min(1.0, max(0.0, (ins["temp"] - 40) / 45))))
    for i in range(8):
        an = state["wheel"] + i * math.pi / 4
        e = (hub[0] + R * math.cos(an), hub[1] + R * math.sin(an))
        col = CARS[1 + i % 4] if L.dusk < 0.3 or int(t * 3 + i) % 8 else "#ffffff"      # a light runs round it at night
        d.rectangle((e[0] - 2 * M, e[1] - M, e[0] + 2 * M, e[1] + 3 * M), fill=mix(sh(col, 200, 0.3), col, L.dusk))
    if L.dusk > 0.3:                                            # a few televisions, far off
        for x, y, z, ph in state["tv"]:
            c, sz = P(x, y, z), max(2, v.k(z) * 1.2)
            d.rectangle((c[0], c[1], c[0] + sz, c[1] + sz), fill=mix("#4f8fe0", "#cfe6ff", 0.5 + 0.5 * math.sin(t * 7 + ph) * math.sin(t * 2.3 + ph)))
    if L.dusk > 0.5:                                            # searchlights downtown, faster with the traffic
        mask = Image.new("L", (W, H), 0)
        g = ImageDraw.Draw(mask)
        for i, (bx, by) in enumerate(((268, 52), (366, 58))):
            an = -math.pi / 2 + 0.75 * math.sin(t * (0.25 + 0.9 * net) + i * 2.1)
            g.polygon((at(bx, by), at(bx + 95 * math.cos(an - 0.07), by + 95 * math.sin(an - 0.07)), at(bx + 95 * math.cos(an + 0.07), by + 95 * math.sin(an + 0.07))), fill=int(70 * L.dusk))
        im.paste((207, 224, 255), (0, 0), mask)

    # ---- net: the tower's rings, and traffic on the boulevard
    tip = P(20, 140, 430)
    tip = (tip[0], tip[1] - 16 * M)
    d.ellipse((tip[0] - 3 * M, tip[1] - 3 * M, tip[0] + 3 * M, tip[1] + 3 * M), fill="#ff4d4d" if int(t * 2) % 2 else "#5a2030")
    for i in range(8):                                          # lamps down its shaft: lit as far up as the traffic goes
        a, b = P(13, 102 + i * 4, 430), P(27, 99 + i * 4, 430)
        on = min(1.0, max(0.0, net * 8 - i))
        d.rectangle((a[0], a[1], b[0], max(b[1], a[1] + 1)), fill=mix(mix(sh("#7d8597", 300), DARK, 0.55), "#7df9ff", on))
    for flow, arc in ((ins["down"], (315, 45)), (ins["up"], (135, 225))):
        rings = 1 + int(flow * 3.99)                            # more and faster with traffic. On the right, what comes
        for i in range(rings):                                  # down closes on the mast; on the left, what goes up leaves it
            p = (t * (0.25 + 1.2 * flow) + i / rings) % 1.0
            p = 1 - p if arc[0] == 315 else p
            r, col = (6 + 22 * p) * M, mix(mix("#0b4fd0", "#7df9ff", L.night), L.top, p * 0.8)
            d.arc((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), *arc, fill=col, width=px(3))
    half = len(state["cars"]) // 2                              # a road fills slowly: the last twenty seconds or so.
    lanes = (3 + int(past["down"] * (half - 3)), 3 + int(past["up"] * (half - 3)))     # towards us comes down, away goes up
    cars = []
    for q, (ph, col) in enumerate(state["cars"]):
        away = q % 2
        if q // 2 >= lanes[away]:
            continue
        u = (ph + t / 40.0 * (1 + q % 3 * 0.15)) % 1.0
        cars.append((70 * (880 / 70) ** (u if away else 1 - u), away, col, 5 + (q // 2 % 2) * 4))     # even to the eye
    road = sh("#3b3e4b", 0, 0.7)
    for z, away, col, lane in sorted(cars, reverse=True):
        k, x = v.k(z), (lane if away else -lane)
        a, b = P(x - 1.7, 2.4, z), P(x + 1.7, 0, z)
        if b[1] < 0 or a[1] > H:
            continue
        w, lamp = max(1.0, k * 0.9), "#ff3b3b" if away else "#fff7c2"
        if L.dusk > 0.2:                                        # a long exposure: the lamps leave trails
            z1 = z - z * 0.11 * (1 + net) if away else z + z * 0.11 * (1 + net)
            for side in (-1.2, 1.2):
                d.line((*P(x + side, 1.2, z), *P(x + side, 1.2, max(62, z1))), fill=mix(lamp, road, 0.55), width=max(1, round(w)))
        if k > 0.8:
            d.polygon((P(x - 1.7, 2.4, z), P(x + 1.7, 2.4, z), P(x + 1.7, 2.4, z + 6), P(x - 1.7, 2.4, z + 6)), fill=mix(sh(col, z, 0.45), "#ffffff", 0.2))
            d.rectangle((a[0], a[1], b[0], b[1]), fill=sh(col, z, 0.45))
            if k > 3:                                           # a rear window
                d.polygon((P(x - 1.3, 2.4, z + 1), P(x + 1.3, 2.4, z + 1), P(x + 1.3, 2.4, z + 2.4), P(x - 1.3, 2.4, z + 2.4)), fill=mix("#22304a", L.top, 0.3))
        if L.dusk > 0.2 or k <= 0.8:                            # at night and far off, a string of lamps
            c = lamp if L.dusk > 0.2 else sh(col, z, 0.45)
            d.rectangle((a[0], a[1], a[0] + w, a[1] + w), fill=c)
            d.rectangle((b[0] - w, a[1], b[0], a[1] + w), fill=c)

    # ---- ram: the flats' evening, and the sight glass on their tank
    kf = v.k(126)
    if kf >= 3.2:
        def win(cl, fl):
            return (*P(-124 + cl * 3.0, fl * 3.6 + 3.0, 126), *P(-122.2 + cl * 3.0, fl * 3.6 + 1.0, 126))
        if L.dusk > 0.3:
            d.rectangle(win(3, 8), fill=mix("#4f8fe0", "#cfe6ff", 0.5 + 0.5 * math.sin(t * 7) * math.sin(t * 2.3)))   # television
            for cl in (9, 10, 11):                              # and someone who cannot sit still
                d.rectangle(win(cl, 8), fill="#ffd98a")
            x = -96.4 + 6.4 * (0.5 + 0.5 * math.sin(t * 0.5))
            if (x + 124) % 3.0 < 1.2:
                a, b = P(x, 32.6, 126), P(x + 0.7, 29.8, 126)
                d.rectangle((a[0], a[1], b[0], b[1]), fill="#3a2a22")
        for i in range(4):                                      # washing on the roof, in the wind
            a, fl = P(-90 + i * 3.2, 37.5, 140), wind * math.sin(t * 2 + i) * kf
            d.polygon((a, (a[0] + 2.4 * kf, a[1]), (a[0] + 2.4 * kf + fl, a[1] + 3 * kf), (a[0] + fl, a[1] + 3 * kf)), fill=sh(BOXES[i * 2 + 1], 0, 0.4))
        d.line((*P(-91, 37.6, 140), *P(-77, 37.6, 140)), fill=sh("#3a3f4c"), width=1)
    kt = v.k(132)
    a, b = P(-107, 51.5, 132), P(-99, 39.5, 132)
    d.rectangle((a[0] - px(1), a[1] - px(1), b[0] + px(1), b[1] + px(1)), fill="#0e1322")
    full = (b[1] - a[1]) * ram
    wet = "#ff6b5e" if ram > 0.9 else "#58b8ff"
    slosh = math.sin(t * 2.2) * 0.25 * kt * min(1.0, ram * 8, (1 - ram) * 8)
    kept = (b[1] - a[1]) * min(ins["cache"], 1 - ram)           # above the water, memory that only holds files:
    if kept >= 1:                                               # dimmer, and given back the moment it is wanted
        d.rectangle((a[0], b[1] - full - kept, b[0], b[1] - full), fill=mix(wet, "#0e1322", 0.58))
        for i in range(1, int(kept / (1.3 * kt)) + 1) if kt >= 4 else ():
            d.line((a[0], b[1] - full - i * 1.3 * kt, b[0], b[1] - full - i * 1.3 * kt), fill=mix(wet, "#0e1322", 0.35), width=1)
    d.polygon(((a[0], b[1]), (b[0], b[1]), (b[0], b[1] - full - slosh), (a[0], b[1] - full + slosh)), fill=wet)
    d.line((a[0], b[1] - full + slosh, b[0], b[1] - full - slosh), fill=mix(wet, "#ffffff", 0.6), width=px(1))
    if kt >= 4:                                                 # marks at the quarters
        for q in (0.25, 0.5, 0.75):
            y = b[1] - (b[1] - a[1]) * q
            d.line((a[0], y, a[0] + 1.6 * kt, y), fill="#c4c9d6", width=px(0.5))
    a2, b2 = P(-98.3, 51.5, 132), P(-95.5, 39.5, 132)           # an amber tube beside it: how full the pagefile is
    d.rectangle((a2[0], a2[1] - px(1), b2[0] + px(1), b2[1] + px(1)), fill="#0e1322")
    if ins["swap"] > 0.004:
        d.rectangle((a2[0], min(b2[1] - 1, b2[1] - (b2[1] - a2[1]) * ins["swap"]), b2[0], b2[1]), fill="#ffb648")
    if ram > 0.9:                                               # and it overflows
        for i in range(3):
            c = P(-92.4, 52 - ((t * 1.4 + i / 3) % 1.0) * 18, 132)
            d.rectangle((c[0], c[1], c[0] + px(1), c[1] + px(1.6)), fill="#9ad4ff")

    # ---- time_of_day: the clock
    c, R = P(-49.5, 38.4, 126), 16 * M
    d.ellipse((c[0] - R, c[1] - R, c[0] + R, c[1] + R), fill=mix("#f6ecc8", "#ffe9a8", L.dusk))
    marks = 12 if M >= 2.5 else 4                               # quarter marks read from the chair; the rest need the lens
    for q in range(marks):
        an, big = q * 2 * math.pi / marks, M < 2.5 or q % 3 == 0
        r0 = R * (0.7 if big else 0.8)
        d.line((c[0] + r0 * math.sin(an), c[1] - r0 * math.cos(an), c[0] + R * 0.94 * math.sin(an), c[1] - R * 0.94 * math.cos(an)),
               fill="#3a3144", width=round(2 + R * (0.07 if big else 0.03)))
    for turn, ln, wd in ((day * 2, 0.5, 3 + R * 0.12), (day * 24, 0.8, 2 + R * 0.07)):       # hour hand, minute hand
        an = turn * 2 * math.pi
        d.line((c[0], c[1], c[0] + R * ln * math.sin(an), c[1] - R * ln * math.cos(an)), fill="#1a1626", width=round(wd))
    if M >= 2.5:                                                # a second hand, for whoever comes this close
        if state["tick"][0] != day:                             # the time arrives every few seconds; count on from it
            state["tick"] = (day, t)
        an = int(day * 86400 + min(10.0, max(0.0, t - state["tick"][1]))) % 60 / 60 * 2 * math.pi
        d.line((c[0] - R * 0.15 * math.sin(an), c[1] + R * 0.15 * math.cos(an), c[0] + R * 0.86 * math.sin(an), c[1] - R * 0.86 * math.cos(an)), fill="#b0503c", width=px(0.5))
    d.ellipse((c[0] - R * 0.12, c[1] - R * 0.12, c[0] + R * 0.12, c[1] + R * 0.12), fill="#b0503c")
    if since < 22 and L.dusk <= 0.5:                            # pigeons leave on the hour
        for i in range(7):
            an = -0.4 - i * 0.38
            bx, by = c[0] + math.cos(an) * since * (5 + i) * M, c[1] + (-24 + math.sin(an) * since * 2.2 + 3 * math.sin(i)) * M
            fl = (2 if int(t * 6 + i) % 2 else -1) * M
            d.line((bx - 3 * M, by - fl, bx, by, bx + 3 * M, by - fl), fill=mix("#20222e", L.top, since / 30), width=px(1))
    if L.dusk < 0.5:                                            # a flock, most of the day
        fx, fy = 150 + 120 * math.sin(t / 23), 58 + 26 * math.sin(t / 17 + 1)
        for i in range(8):
            b = at(fx + 9 * math.sin(i * 2.4 + t * 0.7) + i * 4 - 14, fy + 6 * math.cos(i * 1.7 + t * 0.9))
            fl = (1.6 if int(t * 5 + i) % 2 else -0.8) * M
            d.line((b[0] - 2.5 * M, b[1] - fl, b[0], b[1], b[0] + 2.5 * M, b[1] - fl), fill=sh("#20222e", 200), width=px(1))

    # ---- cpu: furnace doors, the stoker, smoke
    kp = v.k(95)
    fire = ramp(FIRE, min(1.0, cpu * 1.05))
    frame = mix(sh("#8a4b3a"), DARK, 0.5)
    for i in range(4):                                          # a door for each quarter of the cores. The fire starts in the two
        a, b = P(14 + i * 12, 12, 95), P(24 + i * 12, 3, 95)    # middle doors (the two the wide view shows) and spreads outward
        hot = ins["doors"][(2, 0, 1, 3)[i]]
        lead = hot[0] if hot else 0.0
        glow = mix(ramp(FIRE, min(1.0, lead * 1.05)), "#fff7d0", 0.25 * lead * (0.5 + 0.5 * math.sin(t * 9 + i * 2)))
        r, g = (b[0] - a[0]) / 2, max(2, kp * 0.8)
        d.rectangle((a[0] - g, a[1], b[0] + g, b[1]), fill=frame)
        d.pieslice((a[0] - g, a[1] - r - g, b[0] + g, a[1] + r + g), 180, 360, fill=frame)
        d.rectangle((a[0], a[1], b[0], b[1]), fill=glow)
        d.pieslice((a[0], a[1] - r, b[0], a[1] + r), 180, 360, fill=glow)
        if kp >= 4:                                             # close up there is a fire in there: a flame for each core,
            m = len(hot)                                        # as tall as the core is busy, orange against the glow
            fw = min(1.2 * kp, 0.42 * 2 * r / max(1, m))
            for j in range(m):
                fx = a[0] + (j + 0.5) / m * 2 * r
                room = (b[1] - a[1]) + math.sqrt(max(0.0, r * r - (fx - a[0] - r) ** 2))        # up to the arch
                fh = min(room * 0.94, (1.2 + 11 * hot[j]) * kp * (0.8 + 0.2 * math.sin(t * (7 + j % 5) + i * 1.7 + j)))
                lean = 0.4 * fw * math.sin(t * 5 + j)
                d.polygon(((fx - fw, b[1]), (fx + lean, b[1] - fh), (fx + fw, b[1])), fill=mix("#e8501a", "#c2330c", 0.5 + 0.5 * math.sin(t * 11 + j * 2)))
                d.polygon(((fx - fw * 0.45, b[1]), (fx + lean * 0.5, b[1] - fh * 0.55), (fx + fw * 0.45, b[1])), fill="#ffb02e")
            d.rectangle((a[0], b[1] - 0.8 * kp, b[0], b[1]), fill="#1a0f0c")
        d.rectangle((a[0] + r - g / 4, a[1] - r, a[0] + r + g / 4, b[1]), fill=mix(glow, DARK, 0.5))
    for fx in (16, 56):                                         # roof fans, as fast as the load
        fc, fe, fn = P(fx, 20, 104), P(fx + 4, 20, 104), P(fx, 20, 108)
        rx, ry = fe[0] - fc[0], fc[1] - fn[1]
        d.ellipse((fc[0] - rx, fc[1] - ry, fc[0] + rx, fc[1] + ry), fill="#1d1f28", outline=sh("#c4c9d6"), width=px(0.6))
        spin = t * (0.6 + 9 * cpu) + fx
        for j in range(3):
            an = spin + j * 2.094
            d.polygon((fc, (fc[0] + rx * 0.86 * math.cos(an - 0.42), fc[1] + ry * 0.86 * math.sin(an - 0.42)),
                       (fc[0] + rx * 0.86 * math.cos(an + 0.42), fc[1] + ry * 0.86 * math.sin(an + 0.42))), fill=sh("#aeb4c2"))
        d.ellipse((fc[0] - rx * 0.2, fc[1] - ry * 0.2, fc[0] + rx * 0.2, fc[1] + ry * 0.2), fill=fire)
    pace = t * (0.12 + 0.5 * cpu)                               # the stoker and his barrow
    wx, way = 15 + 42 * (0.5 + 0.5 * math.sin(pace)), 1 if math.cos(pace) > 0 else -1
    a, hd = P(wx, 1.9, 93), max(1.0, kp * 0.45)
    d.rectangle((a[0] - kp * 0.4, a[1], a[0] + kp * 0.4, a[1] + 1.9 * kp), fill="#1c1a22")
    d.ellipse((a[0] - hd, a[1] - 2 * hd, a[0] + hd, a[1]), fill=mix("#e0b089", DARK, L.night * 0.4))
    d.rectangle((a[0] + way * kp * 1.3 - kp * 0.8, a[1] + 0.9 * kp, a[0] + way * kp * 1.3 + kp * 0.8, a[1] + 1.7 * kp), fill="#3a3f4c")
    for ci, cx in enumerate(CHIMNEYS):                          # brown smoke for the programs' work, white steam for the kernel's
        top = P(cx + 3.5, 80, 110)
        amt = min(1.0, cpu * ((1 - ins["kernel"]) * 1.4, ins["kernel"] * 2.5)[ci])
        n = 2 + int(amt * 5)
        for i in range(n):
            p = (t * (0.06 + 0.4 * amt) + i / n + cx) % 1.0
            c = (top[0] + (-(8 + 12 * wind) * p + 3 * math.sin(t + i)) * M, top[1] - (4 + (20 + 30 * amt) * p) * M)
            r = (3 + 5 * amt + (3 + 8 * amt) * p) * M
            d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=mix(mix("#e2e2ea", "#fbfcff" if ci else SOOT, amt), mix(L.top, L.low, 0.2), p * 0.8))
        if held["cpu"] > patience:                                  # run hot this long and they flare
            fh = (9 + 5 * math.sin(t * 13 + cx)) * M
            d.polygon(((top[0] - 3.5 * M, top[1]), (top[0] + 2 * M * math.sin(t * 9), top[1] - fh), (top[0] + 3.5 * M, top[1])), fill="#ff8a1f")
            d.polygon(((top[0] - 1.8 * M, top[1]), (top[0], top[1] - fh * 0.55), (top[0] + 1.8 * M, top[1])), fill="#fff2b0")
            for i in range(5):
                p = (t * 1.7 + i * 0.2) % 1.0
                sx = top[0] + math.sin(i * 9 + int(t * 1.7)) * 9 * p * M
                d.rectangle((sx, top[1] - (6 + 24 * p) * M, sx + px(1), top[1] - (6 + 24 * p) * M + px(1)), fill="#ffd166")

    # ---- disk: containers in the yard's bays, two high, and the crane that moves them
    yd = state["yard"]
    box = boxer(d, v, L)
    ky = v.k(100)

    def crate(x, z, y0, col):
        box(x, x + 14, z, z + 6, y0 + 5, col, y0=y0, plain=True)
        a, b = P(x, y0 + 5, z), P(x + 14, y0, z)
        if ky >= 4:                                             # ribs
            for q in range(1, 7):
                d.line((a[0] + q * 2 * v.k(z), a[1], a[0] + q * 2 * v.k(z), b[1]), fill=mix(sh(col), DARK, 0.22), width=max(1, round(ky * 0.2)))
        d.rectangle((a[0], a[1], b[0], b[1]), outline=mix(sh(col), DARK, 0.6))

    def gantry():
        z, col = yd["gz"] + 3, sh("#eef1f6", 0, 0.4)             # white, so no container can be mistaken for it
        for x in (-97, -13):
            d.line((*P(x, 0, z), *P(x, 22, z)), fill=col, width=max(3, round(ky * 1.2)))
        d.line((*P(-97, 22, z), *P(-13, 22, z)), fill=col, width=max(4, round(ky * 1.6)))
        hook, hung = load if load else (14, None)
        gx = yd["gx"]
        if hung:
            crate(gx - 7, yd["gz"], hook, hung)
        d.line((*P(gx, 22, z), *P(gx, hook + 5.6, z)), fill=sh("#c4c9d6", 0, 0.4), width=max(2, round(ky * 0.4)))
        a, b = P(gx - 5, hook + 5.8, z), P(gx + 5, hook + 5, z)
        d.rectangle((a[0], a[1], b[0], max(b[1], a[1] + 2)), fill="#20222e")
        a, b = P(gx - 2.5, 24, z), P(gx + 2.5, 21, z)
        d.rectangle((a[0], a[1], b[0], b[1]), fill=sh("#e0483c", 0, 0.4))

    live = {(lv, i): col for n, (lv, i, col) in enumerate(state["boxes"]) if n < yd["have"]}
    for row in (0, 1):
        if row == 1 and yd["gz"] > 100:
            gantry()
        for i in range(row * 5, row * 5 + 5):
            for lv in (0, 1):
                if (lv, i) in live:
                    crate(*BAYS[i], lv * 5, live[(lv, i)])
    if yd["gz"] <= 100:
        gantry()
    foot, top, kl = P(-63, 0, 89), P(-63, 17.6, 89), v.k(89)    # the yard's lamp post is the drive's activity light:
    d.line((*foot, *top), fill=sh("#3a3f4c", 0, 0.5), width=max(1, round(kl * 0.35)))
    d.rectangle((top[0] - 2.9 * kl, top[1] - 2.6 * kl, top[0] + 2.9 * kl, top[1]), fill="#12141e")
    for side, col, amt in ((-1, READ, ins["read"]), (1, WRITE, ins["write"])):       # blue blinks for reads, orange for writes
        dice = math.sin(int(t * 10) * 12.9898 + side * 7.1) * 43758.5453 % 1.0
        c = (top[0] + side * 1.45 * kl, top[1] - 1.3 * kl)
        d.rectangle((c[0] - kl, c[1] - kl, c[0] + kl, c[1] + kl),
                    fill=mix(col, "#12141e", 0.82) if amt <= 0.03 else col if dice < 0.2 + 0.8 * amt else mix(col, "#12141e", 0.45))

    # ---- a zoom smears: the picture again, a little larger each time, about the point that holds still
    if moving and abs(SHOTS[cam["shot"]][0] - cam["frm"][0]) > 1e-3:
        (m0, x0, y0), (m1, x1, y1) = cam["frm"], SHOTS[cam["shot"]]
        u = (t - cam["t0"]) / MOVE
        step = abs(math.log(m1 / m0)) * 6 * u * (1 - u) / MOVE * 0.07       # how far the lens travels in one exposure
        fx = W / 2 - x0 * m0 + (x1 * m1 - x0 * m0) / (m1 - m0) * m0
        fy = H / 2 - y0 * m0 + (y1 * m1 - y0 * m0) / (m1 - m0) * m0
        acc = im.copy()
        for j in range(1, 6):
            sc = math.exp(step * (j / 5 if m1 > m0 else -j / 5))
            smear = im.transform((W, H), Image.AFFINE, (1 / sc, 0, fx * (1 - 1 / sc), 0, 1 / sc, fy * (1 - 1 / sc)), Image.BILINEAR)
            acc = Image.blend(acc, smear, 1 / (j + 1))
        im.paste(acc)

    # ---- this side of the glass: rain on the panes, daylight on them, the plate, the sill, the cat
    if rain > 0:
        for i in range(int(rain * 26)):                         # falling outside
            x, ph = (i * 97) % W, (i * 0.37) % 1.0
            y = ((ph + t / 1.2) % 1.0) * (H + 20) - 10
            d.line((x, y, x - 2 - 3 * wind, y + 8), fill=mix(L.haze, "#b9c8ee", 0.6))
        for k in range(15):                                     # and running down each pane
            x0, y0, _, _ = key_box(k + 1)
            for bx, ph, sp in state["beads"][k * 7:k * 7 + int(rain * 4.99) + 1]:
                p = (ph + t * 0.05 * sp) % 1.0
                y = y0 + (p + 0.04 * math.sin(t * sp + bx)) * 66 - 2
                x = x0 + bx + math.sin(p * 9 + bx)
                d.line((x, y - 5 - 4 * sp, x, y), fill=mix(L.haze, "#dbe7ff", 0.22))
                d.rectangle((x, y, x + 1, y + 2), fill=mix(L.haze, "#e8f1ff", 0.75))
    if storm and t % 1.3 < 0.12:                                # the flash
        im.paste(Image.blend(im, Image.new("RGB", (W, H), "#dfe6ff"), 0.3))
    im.paste((255, 255, 255), (0, 0), state["glare"].point(lambda g: int(g * (1 - 0.7 * L.night))))
    if arrived:                                                 # the number of what the lens is on, and what is inside it
        grow, shot = min(1.0, max(0.0, (t - cam["t0"] - MOVE) / 0.25)), cam["shot"]
        sig = getattr(s, shot)
        value = sig.text if shot != "time_of_day" else f"{int(day * 24):02d}:{int(day * 1440) % 60:02d}"
        value = "--" if shot == "gpu" and not ins["gpu"] else value
        value = value[:-2] if len(value) > 6 and value.endswith("/s") else value
        fit = int(100 / max(1, len(value)))
        over = held.get(shot, 0.0)                              # and for how long, once it is no spike
        rows = [(sig.label, 13, "#aab3d6"), (value, min(fit, 26 if len(value) <= 3 else 22 if len(value) == 4 else 21), "#ffffff")]
        if over >= 10:
            rows = [(sig.label, 12, "#aab3d6"), (value, min(fit, 23 if len(value) <= 3 else 20 if len(value) == 4 else 19), "#ffffff"),
                    (f"{int(over)}s" if over < 60 else f"{int(over // 60)}m{int(over % 60):02d}s", 15, "#ffc46b")]
        for key, rows, edge in ((PLATE[shot], rows, "#ffc46b"), (MORE.get(shot), more(shot, s, ins), "#7d8fc4")):
            if not (key and rows):
                continue
            x0, y0, _, _ = key_box(key)
            if grow < 0.5:                                      # it opens from a line
                d.line((x0 + 4, y0 + 32, x0 + 59, y0 + 32), fill=edge, width=3)
                continue
            d.rounded_rectangle((x0 + 1, y0 + 1, x0 + 62, y0 + 62), 7, fill="#0a0c18", outline=edge, width=2)   # the whole pane: it reads from the chair
            gap = (60 - sum(size for _, size, _ in rows)) / (len(rows) + 1)
            y = y0 + 2 + gap
            for txt, size, col in rows:
                text(d, (x0 + 32, y + size / 2), txt, size, col)
                y += size + gap
    d.rectangle((0, H - 9, W, H), fill=mix("#4a342a", DARK, 0.3 + 0.4 * L.night))
    d.line((0, H - 9, W, H - 9), fill=mix("#7a5a48", DARK, 0.3 + 0.4 * L.night))
    asleep = 1 <= day * 24 < 5.5 and cam["shot"] == "wide" and rain < 0.75
    if cam["shot"] != "wide":
        look = 1.0                                              # at what the lens is on
    elif since < 20:
        look = 0.9                                              # at the clock, on the hour
    else:                                                       # at the airship while it is in sight
        ax = state["airship"]
        look = min(1.0, max(-1.0, (ax - 40) / 200)) if -30 * M < ax < W + 30 * M else 0.5 * math.sin(t / 9)
    state["cat"] += (look - state["cat"]) * (0.5 if noticing else 0.12)
    alert = 1.0 if noticing or (arrived and t - cam["t0"] < MOVE + 1) else 0.0
    cat(d, t, L, state["cat"], alert, asleep, rain > 0 and t % 9 < 0.8)


def cat(d, t, L, look, alert, asleep, paw):
    """From behind, on the sill. It turns its head to what it watches and sleeps through the small hours."""
    rim, fur = mix("#b9a48f", "#ffd9a0", L.dusk), "#0d0b12"
    if asleep:
        br = math.sin(t * 1.2)
        for g, col in ((2, rim), (0, fur)):
            d.ellipse((12 - g, 229 - g - br, 60 + g, 258), fill=col)
            d.ellipse((10 - g, 224 - g, 34 + g, 246 + g), fill=col)
            d.polygon(((12 - g, 232), (14, 219 - g), (22, 226 - g)), fill=col)
            d.polygon(((24, 225 - g), (31, 219 - g), (33 + g, 232)), fill=col)
            d.line((60, 246, 46, 252, 26, 250), fill=col, width=4 + g)
        return
    hx, ear = look * 5, 3 * alert
    side = 1 if look > 0 else -1
    mx = 41 + hx + side * 13
    tail = math.sin(t * 0.9) * (1 if int(t / 7) % 2 else 0.3) * (1 - alert)
    twitch = 3 if t % 11 < 0.3 else 0
    for g, col in ((2, rim), (0, fur)):                         # a rim of light, then the cat
        d.ellipse((20 - g, 216 - g, 58 + g, 256), fill=col)
        d.ellipse((28 + hx - g, 198 - g, 54 + hx + g, 222 + g), fill=col)
        d.polygon(((29 + hx - g, 208), (31 + hx * 1.3, 191 - ear + twitch - g), (40 + hx, 201 - g)), fill=col)
        d.polygon(((43 + hx, 201 - g), (52 + hx * 1.3, 191 - ear - g), (54 + hx + g, 208)), fill=col)
        if abs(look) > 0.45:                                    # far enough round to see its muzzle
            d.ellipse((mx - 5 - g, 209 - g, mx + 5 + g, 219 + g), fill=col)
        d.line((26, 245, 12, 245, 6 + 3 * tail, 238 - 6 * abs(tail), 4 + 6 * tail, 228), fill=col, width=4 + g)
        if paw:                                                 # a paw at a raindrop
            d.line((52, 238, 59, 226 - 4 * math.sin(t * 8)), fill=col, width=4 + g)
    if abs(look) > 0.45:
        d.line((mx + side * 4, 214, mx + side * 11, 212), fill=rim)
        d.line((mx + side * 4, 216, mx + side * 11, 217), fill=rim)
        d.rectangle((mx - side * 3, 207, mx - side * 3 + 1, 209), fill=mix("#b6ff7a", fur, 0.5 - 0.5 * L.dusk))


def tap(key, x, y, s, t, state):
    state["tapped"] = key
