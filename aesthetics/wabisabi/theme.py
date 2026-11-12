"""WABI-SABI -- "The mended tea bowl" (侘寂).

The pad is the side of one hand-thrown tea bowl, seen up close. The top row is its lip: an uneven
iron-brown rim over cream shino glaze with fire blush, crackle and pinholes. The middle row is where
the ash glaze runs and pools grey-green. The bottom row is the bare clay foot with grit and throwing
rings, where the glaze stopped in drips. One break, mended with gold (kintsugi), runs through the
margins: the flaw is the most valued part.

Nothing is perfectly straight or centred. Icons are sumi ink brushed by hand: each key's icon sits a
little rotated and offset, its edges bleed, and its ink is uneven. Ink is dark on light glaze and
white slip on dark clay. Labels are quiet lowercase. The power icon becomes an enso, the brushed
circle left open. A face's `bg` colour becomes a small hand-stamped seal (hanko) in a natural pigment.
One empty key holds a seasonal accent, chosen when the theme loads (autumn: a fallen maple leaf);
when that key gets a function the accent quietly goes.

Options (settings.theme: {name: wabisabi, ...}):
    kintsugi: true    the gold-mended break
    crackle: true     crackle and pinholes in the cream glaze
    season: auto      auto|spring|summer|autumn|winter|none
    accent_key: 10    the empty key that may hold the seasonal accent
    grain: true       faint paper/clay grain over everything
"""
from __future__ import annotations

import datetime
import json
import math
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

SUMI = "#26221e"        # ink on light glaze
GOFUN = "#f1eadb"       # white slip on dark clay
PALETTE = {
    "fg": SUMI,
    "label_color": SUMI,
    "ink": "#26221e",
    "slip": GOFUN,
    "gold": "#c9a24a",
    "shu": "#c0462c",       # vermilion seal
    "ai": "#2f4f86",        # indigo
    "moss": "#5f7a3c",
    "kaki": "#c4682f",      # persimmon
    "good": "#5f7a3c",
    "warn": "#c49a3a",
    "bad": "#c0462c",
    "track": "#8d8370",
    "font": r"C:\Windows\Fonts\palab.ttf",
    "rgb": "#c4682f",
}
FONTS = {"label": r"C:\Windows\Fonts\palab.ttf", "glyph": r"C:\Windows\Fonts\palab.ttf"}  # Palatino: Zapf's calligraphic roman


def _rgb(h):
    h = str(h).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _font(px):
    try:
        return ImageFont.truetype(FONTS["label"], int(px))
    except OSError:
        return ImageFont.truetype(r"C:\Windows\Fonts\georgiab.ttf", int(px))


def _noise(w, h, cw, ch, rng):
    """Smooth value noise: a cw x ch grid of random values, upscaled bicubic to w x h."""
    small = Image.new("L", (cw, ch))
    small.putdata([rng.randrange(256) for _ in range(cw * ch)])
    return small.resize((w, h), Image.BICUBIC)


def _vgrad(w, h, stops):
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t <= t1:
                u = 0 if t1 == t0 else (t - t0) / (t1 - t0)
                a, b = _rgb(c0), _rgb(c1)
                d.line((0, y, w, y), fill=tuple(int(a[i] + (b[i] - a[i]) * u) for i in range(3)))
                break
    return im


def _season(opts):
    s = (opts or {}).get("season", "auto")
    if s != "auto":
        return s
    m = datetime.date.today().month
    return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer"}.get(m, "autumn")


# ------------------------------------------------------------------ the bowl
def _geom(S):
    G = round(S * 0.28)
    return G, 5 * S + 4 * G, 3 * S + 2 * G


def _K(k, fx, fy, S, G):
    r, c = divmod(k - 1, 5)
    return c * (S + G) + fx * S, r * (S + G) + fy * S


@lru_cache(maxsize=4)
def _scene(S, opts_json):
    opts = json.loads(opts_json)
    rng = random.Random(1588)                  # Raku, Chojiro's first tea bowls
    G, W, H = _geom(S)
    row3 = 2 * (S + G)

    # clay foot: warm body, mottled, gritty, with throwing rings
    clay = Image.new("RGB", (W, H), _rgb("#7a4c33"))
    clay.paste(_rgb("#5f3a26"), (0, 0), _noise(W, H, 14, 8, rng).point(lambda v: max(0, v - 120)))
    d = ImageDraw.Draw(clay)
    for i in range(int(S * 0.62)):
        y = row3 - G + i * 3 + rng.randint(-1, 1)
        if i % 14 == 0:
            pts = [(x, y + 3.5 * math.sin(x / (S * 0.37) + i)) for x in range(0, W + 8, 8)]
            d.line(pts, fill=_rgb("#84553a") if i % 28 else _rgb("#6a412c"), width=2)
    for _ in range(int(W * H / 2400)):
        x, y = rng.randrange(W), rng.randrange(row3 - G, H)
        r = rng.choice((1, 1, 2, 2, 3))
        d.ellipse((x - r, y - r, x + r, y + r), fill=_rgb(rng.choice(("#a07858", "#b08a69", "#55331f"))))

    # glaze: cream shino at the lip, ash grey-green where it runs and pools
    glaze = _vgrad(W, H, [(0.0, "#ece3cf"), (0.28, "#e6dac1"), (0.40, "#d3cbb0"),
                          (0.52, "#b6b396"), (0.64, "#a2a487"), (1.0, "#8c9374")])
    blush = _noise(W, H, 9, 5, rng).point(lambda v: max(0, (v - 135) * 2))
    upper = _vgrad(W, H, [(0, "#ffffff"), (0.35, "#bbbbbb"), (0.5, "#000000"), (1, "#000000")]).convert("L")
    blush = ImageChops.multiply(blush, upper)            # fire blush only in the cream upper glaze
    glaze.paste(_rgb("#d79d70"), (0, 0), blush.point(lambda v: int(v * 0.55)))
    mott = _noise(W, H, 30, 14, rng)
    glaze.paste(_rgb("#f4ecdc"), (0, 0), mott.point(lambda v: max(0, v - 170)))
    runs = Image.new("L", (W, H), 0)          # vertical runs of darker glaze down the middle row
    rd = ImageDraw.Draw(runs)
    for _ in range(26):
        x = rng.randrange(W)
        y0 = rng.uniform(0.3, 0.45) * H
        y1 = y0 + rng.uniform(0.12, 0.32) * H
        w = rng.uniform(0.02, 0.06) * S
        rd.rounded_rectangle((x - w, y0, x + w, y1), radius=w, fill=rng.randint(40, 90))
    glaze.paste(_rgb("#6f7a5b"), (0, 0), runs.filter(ImageFilter.GaussianBlur(S * 0.03)))
    gd = ImageDraw.Draw(glaze)
    tears = Image.new("L", (W, H), 0)
    td = ImageDraw.Draw(tears)
    for _ in range(22):
        x = rng.uniform(0, W)
        y = rng.uniform(0.33, 0.45) * H
        L = rng.uniform(0.12, 0.3) * H
        w = rng.uniform(0.025, 0.05) * S
        poly_l, poly_r = [], []
        for i in range(13):
            t = i / 12
            xx = x + S * 0.012 * math.sin(t * 5 + x)
            ww = w * (0.35 + 0.65 * (1 - t))           # taper toward the tip
            poly_l.append((xx - ww, y + L * t))
            poly_r.append((xx + ww, y + L * t))
        td.polygon(poly_l + poly_r[::-1], fill=rng.randint(110, 170))
        b = w * 0.75                                    # the bead where the run stopped
        td.ellipse((x - b, y + L - b * 0.6, x + b, y + L + b * 1.2), fill=210)
    glaze.paste(_rgb("#76815f"), (0, 0), tears.filter(ImageFilter.GaussianBlur(2.5)))
    glaze.paste(_rgb("#c3c5aa"), (0, 0), _shift(tears, -3, -2).filter(ImageFilter.GaussianBlur(2)).point(lambda v: v // 5))

    # where the glaze stops: a wavy edge in the gap above the foot, short drips into the bottom row
    cover = Image.new("L", (W, H), 0)
    cd = ImageDraw.Draw(cover)
    base = row3 - G * 0.45
    edge = [(x, base + G * 0.35 * math.sin(x / (S * 0.21)) + rng.uniform(-3, 3)) for x in range(0, W + 6, 6)]
    cd.polygon([(0, 0), (W, 0)] + edge[::-1], fill=255)
    drips = []
    for _ in range(16):
        x = rng.randrange(W)
        L = rng.uniform(0.04, 0.12) * S + G * 0.5
        w = rng.uniform(0.025, 0.045) * S
        drips.append((x, base, L, w))
    drips.append((_K(11, 0.07, 0, S, G)[0], base, G * 0.5 + S * 0.62, S * 0.035))    # one long run
    for (x, y, L, w) in drips:
        cd.rounded_rectangle((x - w, y - 4, x + w, y + L), radius=w, fill=255)
        cd.ellipse((x - w * 1.3, y + L - w * 1.6, x + w * 1.3, y + L + w), fill=255)
    cover = cover.filter(ImageFilter.GaussianBlur(2))
    pool = ImageChops.subtract(cover, cover.filter(ImageFilter.MinFilter(int(S * 0.06) | 1)))
    glaze.paste(_rgb("#5e6b4b"), (0, 0), pool.filter(ImageFilter.GaussianBlur(S * 0.02)).point(lambda v: int(v * 0.8)))
    img = Image.composite(glaze, clay, cover)
    d = ImageDraw.Draw(img)
    for (x, y, L, w) in drips:                 # glassy highlight where each drip gathered
        d.ellipse((x - w * 0.45, y + L - w * 0.9, x + w * 0.1, y + L - w * 0.4), fill=_rgb("#c8ccb4"))

    if opts.get("crackle", True):              # fine crackle and pinholes in the cream glaze
        cr = Image.new("L", (W, H), 0)
        cdd = ImageDraw.Draw(cr)
        for _ in range(140):
            x, y = rng.randrange(W), rng.uniform(0.02, 0.42) * H
            a = rng.uniform(0, math.tau)
            for _s in range(rng.randint(2, 5)):
                l = rng.uniform(0.05, 0.13) * S
                nx, ny = x + l * math.cos(a), y + l * math.sin(a)
                cdd.line((x, y, nx, ny), fill=70, width=3)
                x, y, a = nx, ny, a + rng.uniform(-0.9, 0.9)
        img.paste(_rgb("#8c7a60"), (0, 0), cr)
        for _ in range(45):
            x, y = rng.randrange(W), rng.uniform(0.05, 0.45) * H
            r = rng.choice((2, 3, 3, 4))
            d.ellipse((x - r, y - r, x + r, y + r), fill=_rgb("#7a6650"))

    # the lip: an uneven iron rim
    def thick(x):
        v = (0.5 + 0.25 * math.sin(x / (S * 0.43) + 1.3) + 0.15 * math.sin(x / (S * 0.13) + 0.4)
             + 0.1 * math.sin(x / (S * 0.051)))
        return S * (0.03 + 0.1 * max(0.0, min(1.0, v)) ** 1.6)
    lip = [(x, thick(x) + rng.uniform(0, 0.006) * S) for x in range(0, W + 6, 6)]
    rim = Image.new("L", (W, H), 0)
    rd2 = ImageDraw.Draw(rim)
    rd2.polygon([(0, 0), (W, 0)] + lip[::-1], fill=255)
    for (k, fx, L) in ((1, 0.1, 0.24), (4, 0.9, 0.17)):   # iron running down from the lip
        x0 = _K(k, fx, 0, S, G)[0]
        y0 = thick(x0)
        pts_l = [(x0 - S * 0.022 * (1 - t * 0.6), y0 + S * L * t) for t in (0, 0.5, 1)]
        pts_r = [(x0 + S * 0.022 * (1 - t * 0.6), y0 + S * L * t) for t in (1, 0.5, 0)]
        rd2.polygon([(x0 - S * 0.03, y0 - 4)] + pts_l + pts_r + [(x0 + S * 0.03, y0 - 4)], fill=255)
        rd2.ellipse((x0 - S * 0.02, y0 + S * L - S * 0.016, x0 + S * 0.02, y0 + S * L + S * 0.022), fill=255)
    img.paste(_rgb("#5b3a27"), (0, 0), rim.filter(ImageFilter.GaussianBlur(3)))
    for _ in range(26):                          # darker iron where the rim pooled
        x = rng.randrange(W)
        y = thick(x) * rng.uniform(0.3, 0.8)
        r = rng.choice((3, 4, 6))
        d.ellipse((x - r * 1.6, y - r, x + r * 1.6, y + r), fill=_rgb("#3f2618"))
    if _season(opts) == "winter":               # first snow caught on the lip
        for _ in range(60):
            x = rng.randrange(W)
            y = S * rng.uniform(0.05, 0.1)
            r = rng.choice((2, 3, 4))
            d.ellipse((x - r, y - r, x + r, y + r), fill=_rgb("#fbfaf6"))

    if opts.get("kintsugi", True):
        _kintsugi(img, S, G, rng)
    return img


def _shift(m, dx, dy):
    out = Image.new("L", m.size, 0)
    out.paste(m, (dx, dy))
    return out


def _jag(a, b, depth, rng, amp=0.28):
    if depth == 0:
        return [a]
    (x0, y0), (x1, y1) = a, b
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    L = math.hypot(x1 - x0, y1 - y0)
    nx, ny = -(y1 - y0) / (L or 1), (x1 - x0) / (L or 1)
    o = rng.uniform(-amp, amp) * L
    m = (mx + nx * o, my + ny * o)
    return _jag(a, m, depth - 1, rng, amp) + _jag(m, b, depth - 1, rng, amp)


def _kintsugi(img, S, G, rng):
    """One break, mended in gold: from the lip down the right margins of keys 3, 8 and 13."""
    K = lambda k, fx, fy: _K(k, fx, fy, S, G)
    main = [K(3, 0.97, 0.0), K(3, 0.91, 0.3), K(3, 0.94, 0.66), K(3, 0.9, 1.0), K(8, 0.9, 0.0),
            K(8, 0.93, 0.38), K(8, 0.89, 0.72), K(8, 0.93, 1.0), K(13, 0.92, 0.0), K(13, 0.88, 0.4),
            K(13, 0.95, 0.75), K(13, 1.12, 0.85)]
    d = ImageDraw.Draw(img)
    pts = []
    for a, b in zip(main, main[1:]):
        pts += _jag(a, b, 3, rng, amp=0.22)
    pts.append(main[-1])
    ws = [rng.randint(6, 9) for _ in pts]
    for (a, b), w in zip(zip(pts, pts[1:]), ws):
        d.line((a, b), fill=_rgb("#5e4519"), width=w + 4)
    for (a, b), w in zip(zip(pts, pts[1:]), ws):
        d.line((a, b), fill=_rgb("#b8923a"), width=w)
    for (a, b) in zip(pts, pts[1:]):
        d.line(((a[0] - 2, a[1] - 1), (b[0] - 2, b[1] - 1)), fill=_rgb("#e8cf86"), width=3)


# ------------------------------------------------------------------ per key
def _lum(img, box=None):
    region = img.crop(box) if box else img
    return ImageStat.Stat(region.convert("L")).mean[0]


def _seal_color(bg):
    import colorsys
    r, g, b = (v / 255 for v in _rgb(bg))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.25:
        return SUMI
    for lim, c in ((0.06, PALETTE["shu"]), (0.17, PALETTE["warn"]), (0.45, PALETTE["moss"]),
                   (0.80, PALETTE["ai"]), (1.01, PALETTE["shu"])):
        if h < lim:
            return c


def _hanko(tile, color, S, seed):
    rng = random.Random(seed)
    sz = int(S * 0.2)
    m = Image.new("L", (sz * 2, sz * 2), 0)
    d = ImageDraw.Draw(m)
    o = sz // 2
    d.rectangle((o, o, o + sz, o + sz), fill=255)
    t = 3                                          # one LCD pixel
    i = sz // 4
    d.rectangle((o + i, o + i, o + sz - i, o + sz - i), outline=0, width=t)
    for _ in range(3):                             # where the paste didn't take
        x, y = rng.randint(o, o + sz), rng.choice((o + 2, o + sz - 2))
        d.ellipse((x - 3, y - 2, x + 3, y + 2), fill=0)
    m = m.effect_spread(2).filter(ImageFilter.MedianFilter(5)).rotate(rng.uniform(-6, 6), Image.BICUBIC)
    lay = Image.new("RGBA", m.size, _rgb(color) + (0,))
    lay.putalpha(m.point(lambda v: int(v * 0.92)))
    tile.alpha_composite(lay, (int(S * 0.76) - o - sz // 2, int(S * 0.13) - o - sz // 2))


def _maple(d, cx, cy, R, rot, Z):
    pts = []
    for i in range(90):
        a = rot + i / 90 * math.tau
        lobe = abs(math.cos(2.5 * (a - rot)))
        r = R * (0.42 + 0.58 * lobe ** 0.6) * (0.55 if math.cos(a - rot - math.pi) > 0.9 else 1)
        pts.append((cx + r * math.sin(a), cy - r * math.cos(a)))
    d.polygon(pts, fill=_rgb("#9c4a2c") + (235,))
    for i in range(5):
        a = rot + i * math.tau / 5
        d.line((cx, cy, cx + R * 0.75 * math.sin(a), cy - R * 0.75 * math.cos(a)), fill=_rgb("#6e3420") + (255,), width=2 * Z)
    d.line((cx, cy, cx - R * 0.9 * math.sin(rot), cy + R * 1.1 * math.cos(rot)), fill=_rgb("#6e3420") + (255,), width=3 * Z)


def _leaf(tile, S, rng, season):
    d = ImageDraw.Draw(tile)
    if season == "autumn":                        # one fallen maple leaf, drawn 4x and scaled down
        Z = 4
        big = Image.new("RGBA", (S * Z, S * Z), (0, 0, 0, 0))
        _maple(ImageDraw.Draw(big), S * 0.86 * Z, S * 0.84 * Z, S * 0.12 * Z, rng.uniform(-0.6, 0.4), Z)
        tile.alpha_composite(big.resize((S, S), Image.LANCZOS))
    elif season == "spring":                      # three plum petals
        for _ in range(3):
            x, y = S * rng.uniform(0.35, 0.8), S * rng.uniform(0.4, 0.8)
            r = S * 0.045
            d.ellipse((x - r, y - r * 0.7, x + r, y + r * 0.7), fill=_rgb("#e9b9bf"))
    elif season == "summer":                      # moss taking hold in a corner
        for _ in range(140):
            a, rr = rng.uniform(0, math.pi / 2), S * 0.28 * rng.random() ** 0.6
            x, y = S - 4 - rr * math.cos(a), S - 4 - rr * math.sin(a)
            r = rng.choice((2, 3, 4))
            d.ellipse((x - r, y - r, x + r, y + r), fill=_rgb(rng.choice(("#5f7a3c", "#7c9446", "#4a5f2c"))))


def backdrop(key, face, S, opts):
    opts = opts or {}
    G, W, H = _geom(S)
    scene = _scene(S, json.dumps(opts, sort_keys=True))
    k = key if key and 1 <= key <= 15 else 8
    x, y = _K(k, 0, 0, S, G)
    tile = scene.crop((int(x), int(y), int(x) + S, int(y) + S)).convert("RGBA")
    season = _season(opts)
    if not face and k == int(opts.get("accent_key", 10)) and season != "none":
        _leaf(tile, S, random.Random(k), season)
    if face.get("bg"):
        _hanko(tile, _seal_color(face["bg"]), S, k)
    return tile.convert("RGB")


def _brush(mask, seed, rough=3, kasure=True):
    """Hand-brushed ink from a clean mask: a little rotated and offset, bled edges, uneven ink."""
    rng = random.Random(seed)
    bb = mask.getbbox()
    if not bb:
        return mask
    S = mask.size[0]
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    lean = rng.uniform(2.5, 5.5) * rng.choice((-1, 1))
    m = mask.rotate(lean, Image.BICUBIC, center=(cx, cy),
                    translate=(rng.randint(-5, 5), rng.randint(-3, 3)))
    if rough:                                      # brush pressure: strokes swell and thin
        press = _noise(S, S, 4, 4, rng)
        m = Image.composite(m.filter(ImageFilter.MaxFilter(5)), m.filter(ImageFilter.MinFilter(3)), press)
    if rough:
        m = m.effect_spread(rough).filter(ImageFilter.GaussianBlur(1.4)).point(
            lambda v: 255 if v > 118 else (v * 2 if v > 60 else 0))
    if kasure:                                     # dry-brush streaks thin the ink in places
        streak = _noise(S, S, 5, 30, rng).point(lambda v: 120 + v * 135 // 255)
        m = ImageChops.multiply(m, streak)
    return m


def _put(img, m, color, bleed=True):
    base = img.convert("RGBA")
    if bleed:                                      # nijimi: ink soaking softly into the glaze
        halo = Image.new("RGBA", img.size, _rgb(color) + (0,))
        halo.putalpha(m.filter(ImageFilter.GaussianBlur(4)).point(lambda v: v * 30 // 255))
        base.alpha_composite(halo)
    lay = Image.new("RGBA", img.size, _rgb(color) + (0,))
    lay.putalpha(m)
    base.alpha_composite(lay)
    img.paste(base.convert("RGB"))


def _ink_for(img, box):
    return SUMI if _lum(img, box) > 120 else GOFUN


def _enso(S, cy, seed):
    """The power symbol as an enso: a brushed circle left open at the top, and one falling stroke."""
    rng = random.Random(seed)
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    cx, R, w = S / 2, S * 0.255, S * 0.06
    a0, sweep = math.radians(28), math.radians(305)
    n = 260
    for i in range(n):
        t = i / (n - 1)
        a = a0 + sweep * t
        rr = R * (1 + 0.035 * math.sin(t * 5.1 + 0.7))
        ww = w * (1.15 - 0.75 * t ** 1.4) * rng.uniform(0.92, 1.05)
        if t > 0.78 and rng.random() < (t - 0.78) * 2.4:
            continue                                   # the brush running dry
        x, y = cx + rr * math.sin(a), cy - rr * math.cos(a)
        d.ellipse((x - ww, y - ww, x + ww, y + ww), fill=255)
    for i in range(60):
        t = i / 59
        x = cx + S * 0.008 * math.sin(t * 3)
        y = cy - R * 1.12 + R * 1.02 * t
        ww = w * (0.95 - 0.55 * t)
        d.ellipse((x - ww, y - ww, x + ww, y + ww), fill=255)
    return m


def ink(img, mask, role, fill, face, key, opts):
    opts = opts or {}
    S = img.size[0]
    seed = (key or 0) * 7 + 3
    if role == "icon" and str(face.get("icon", "")) == "mdi:power":
        bb = mask.getbbox() or (0, 0, S, S)
        m = _enso(S, (bb[1] + bb[3]) / 2, seed)
        _put(img, _brush(m, seed, rough=2, kasure=False), _ink_for(img, m.getbbox()))
        return
    if role in ("icon", "glyph"):
        bb = mask.getbbox()
        if not bb:
            return
        if role == "icon" and str(face.get("icon", "")) in ("mdi:record-rec", "mdi:record", "mdi:record-circle"):
            r = min(bb[2] - bb[0], bb[3] - bb[1]) * 0.4
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            mask = Image.new("L", mask.size, 0)
            ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        m = _brush(mask, seed, rough=3 if role == "icon" else 2)
        _put(img, m, _ink_for(img, bb))
        return
    # labels: drawn once, whole, quiet lowercase, slightly off true
    if img.info.get("ws_label"):
        return
    img.info["ws_label"] = True
    text = str(face.get("label", "")).lower()
    centered = not (face.get("icon") or face.get("glyph") or face.get("emoji"))
    size = S * (0.2 if centered else 0.155)
    f = _font(size)
    while f.getlength(text) > S * 0.86 and size > S * 0.1:
        size -= 2
        f = _font(size)
    rng = random.Random(seed + 1)
    m = Image.new("L", (S, S), 0)
    y = S * 0.5 if centered else S * 0.845
    ImageDraw.Draw(m).text((S / 2 + rng.randint(-3, 3), y), text, font=f, fill=255, anchor="mm")
    m = m.rotate(rng.uniform(-1.6, 1.6), Image.BICUBIC, center=(S / 2, y))
    _put(img, m, _ink_for(img, m.getbbox()), bleed=False)


@lru_cache(maxsize=2)
def _grain(S):
    rng = random.Random(7)
    g = Image.new("L", (S, S))
    g.putdata([rng.randrange(256) for _ in range(S * S)])
    return g.filter(ImageFilter.GaussianBlur(0.8))


def finish(img, face, key, opts):
    if not (opts or {}).get("grain", True):
        return img
    g = _grain(img.size[0])
    dark = Image.new("RGB", img.size, (40, 30, 20))
    return Image.composite(dark, img, g.point(lambda v: max(0, v - 150) // 6))


# ------------------------------------------------------------------ for plugins
def meters(rows, key=None, opts=None):
    """Meters as brush strokes: a faint hairline is the whole, the ink stroke is how much is used."""
    S = 192
    k = key if key and 1 <= key <= 15 else 2
    ground = backdrop(k, {"label": "x"}, S, opts or {})
    col = SUMI if _lum(ground, (0, 0, S, S)) > 120 else GOFUN
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    n = max(1, len(rows))
    rh = (S - 20) / n
    f = _font(S * 0.19)
    rng = random.Random(k)
    for i, (name, v) in enumerate(rows):
        y = 14 + i * rh
        txt = Image.new("L", (S, S), 0)
        td = ImageDraw.Draw(txt)
        td.text((S * 0.07, y + rh * 0.32), str(name).lower(), font=f, fill=255, anchor="lm")
        td.text((S * 0.93, y + rh * 0.32), "–" if v is None else f"{v:.0f}", font=f, fill=255, anchor="rm")
        stroke = Image.new("L", (S, S), 0)
        sd = ImageDraw.Draw(stroke)
        by = y + rh * 0.8
        sd.line((S * 0.07, by, S * 0.93, by), fill=80, width=3)
        if v is not None:
            L = max(S * 0.07, S * 0.86 * max(0.0, min(100.0, v)) / 100)
            steps = max(2, int(L / 2))
            for j in range(steps):
                t = j / steps
                w = S * 0.035 * (1.1 - 0.7 * t) * rng.uniform(0.9, 1.1)
                if t > 0.7 and rng.random() < (t - 0.7) * 2:
                    continue
                x = S * 0.07 + L * t
                sd.ellipse((x - w, by - w * 0.8, x + w, by + w * 0.8), fill=255)
        c = PALETTE["shu"] if (v is not None and v >= 85) else col
        for m, colr in ((txt, col), (stroke, c)):
            lay = Image.new("RGBA", (S, S), _rgb(colr) + (0,))
            lay.putalpha(m)
            out.alpha_composite(lay)
    return out
