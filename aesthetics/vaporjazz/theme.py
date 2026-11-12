"""VAPORJAZZ -- "Late Night Mall, 1994".

The 15 keys are one window onto a single scene: a striped sun setting into a neon floor grid,
under a dusk sky scattered with early-90s jazz-pattern confetti (teal brush swashes, purple
squiggles, Memphis triangles and dots). Icons are pastel-chrome stickers with a dark ink outline
and a hard hot-pink drop shadow; labels are chunky black italic.

Options (settings.theme: {name: vaporjazz, ...}):
    confetti: true    jazz confetti in the sky
    palms: true       palm silhouettes at the far left and right of the middle row
    scanlines: true   faint CRT/VHS scanlines
    halo: true        soft dark pool behind each icon for legibility over the bright sun
A face's `bg` color is drawn as a neon rim (red = recording stays red) instead of a flat fill.
"""
from __future__ import annotations

import json
import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

PALETTE = {
    "fg": "#fff4dc",          # cream: default ink fill
    "label_color": "#fff4dc",
    "ink": "#1b0b36",         # outlines, palm silhouettes
    "shadow": "#ff3fa4",      # hard drop shadow
    "teal": "#2ee6d6",
    "pink": "#ff6ec7",
    "purple": "#8a3ffc",
    "peach": "#ffb38a",
    "yellow": "#ffe66d",
    "sky_top": "#140a33",
    "floor": "#1a0933",
    # for plugins drawing their own tiles (ctx.theme): bars, tracks, states
    "good": "#2ee6d6",
    "warn": "#ffb38a",
    "bad": "#ff3f6c",
    "track": "#4a2a6e",       # empty part of meters (drawn on an ink plate)
    "font": r"C:\Windows\Fonts\seguibli.ttf",   # chunky black italic, for plugin text
    "rgb": "#ff4fa0",          # suggested ring color
}
FONTS = {"label": r"C:\Windows\Fonts\seguibli.ttf", "glyph": r"C:\Windows\Fonts\seguibli.ttf"}

ICON_GRADIENT = [(0.0, "#fffbea"), (0.45, "#ffe3f3"), (0.75, "#ffb6e1"), (1.0, "#a9f4ff")]


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lerp(stops, t):
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            u = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            a, b = _rgb(c0), _rgb(c1)
            return tuple(int(a[i] + (b[i] - a[i]) * u) for i in range(3))
    return _rgb(stops[-1][1])


def _vgrad(w, h, stops):
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line((0, y, w, y), fill=_lerp(stops, y / max(1, h - 1)))
    return im


# ------------------------------------------------------------------ the scene
def _geom(S):
    G = round(S * 0.28)                      # virtual bezel between keys keeps the scene continuous
    W, H = 5 * S + 4 * G, 3 * S + 2 * G
    horizon = (S + G) + int(S * 0.78)       # low in the middle row
    return G, W, H, horizon


@lru_cache(maxsize=4)
def _scene(S, opts_json):
    o = json.loads(opts_json)
    G, W, H, hz = _geom(S)
    cx = W // 2
    img = _vgrad(W, H, [(0.0, "#140a33"), (0.30, "#341260"), (0.52, "#7a2380"),
                        (hz / H, "#ff7f6e"), (1.0, "#ff7f6e")]).convert("RGBA")

    # sun glow + striped sun, cut at the horizon
    r = int(S * 1.12)
    cy = hz
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((cx - r * 1.35, cy - r * 1.35, cx + r * 1.35, cy + r * 1.35),
                                 fill=(255, 90, 170, 90))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.35)))
    sun = _vgrad(2 * r, r, [(0.0, "#ffe66d"), (0.55, "#ff9a62"), (1.0, "#ff3f9a")]).convert("RGBA")
    mask = Image.new("L", (2 * r, r), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, 2 * r, 2 * r), fill=255)
    # slits: thin near the top, thicker toward the horizon
    y, band = int(r * 0.42), 0
    while y < r:
        th = int(S * 0.025 + band * S * 0.018)
        md.rectangle((0, y, 2 * r, y + th), fill=0)
        y += th + int(S * 0.11 - band * S * 0.008)
        band += 1
    sun.putalpha(mask)
    img.alpha_composite(sun, (cx - r, cy - r))

    # floor + neon grid
    fl = _vgrad(W, H - hz, [(0.0, "#2a0d4a"), (1.0, "#0c0420")]).convert("RGBA")
    img.alpha_composite(fl, (0, hz))
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    teal = _rgb(PALETTE["teal"])
    lw = max(2, S // 64)
    for i in range(-14, 15):                       # radiating lines
        x_bot = cx + i * S * 0.42
        gd.line((cx + i * 2, hz, x_bot, H), fill=teal + (230,), width=lw)
    D = (H - hz)
    for z in (1.0, 1.32, 1.75, 2.35, 3.2, 4.5, 6.6, 10.0, 16.0):   # receding cross lines
        y = hz + int(D / z)
        a = int(240 * min(1.0, 1.6 / z + 0.15))
        gd.line((0, y, W, y), fill=teal + (a,), width=lw)
    gd.line((0, hz, W, hz), fill=_rgb(PALETTE["pink"]) + (255,), width=lw + 1)
    img.alpha_composite(grid.filter(ImageFilter.GaussianBlur(S * 0.03)))
    img.alpha_composite(grid)

    if o.get("palms", True):
        _palms(img, S, G, W, hz)
    if o.get("confetti", True):
        _confetti(img, S, G, W, hz)
    return img.convert("RGB")


def _palms(img, S, G, W, hz):
    d = ImageDraw.Draw(img)
    ink = _rgb(PALETTE["ink"]) + (235,)
    for base_x, lean in ((int(S * 0.55), 1), (W - int(S * 0.55), -1)):
        top = (base_x + lean * S * 0.22, hz - S * 0.66)
        pts = []
        for i in range(21):                    # curved trunk, tapering
            t = i / 20
            x = base_x + lean * S * 0.22 * (t ** 1.6)
            y = hz - S * 0.66 * t
            pts.append((x, y))
        for (x0, y0), (x1, y1), i in zip(pts, pts[1:], range(20)):
            d.line((x0, y0, x1, y1), fill=ink, width=max(2, int(S * (0.06 - 0.0022 * i))))
        tx, ty = top
        for ang, ln in ((-165, 0.42), (-135, 0.4), (-95, 0.26), (-50, 0.4), (-15, 0.45), (20, 0.34), (160, 0.34)):
            a = math.radians(ang)
            L = S * ln
            spine = [(tx + math.cos(a) * L * u, ty + math.sin(a) * L * u + (L * 0.35) * u * u)
                     for u in (i / 8 for i in range(9))]
            for j, (x, y) in enumerate(spine):
                rr = S * 0.05 * math.sin(math.pi * j / 8) + 1
                d.ellipse((x - rr, y - rr * 0.6, x + rr, y + rr * 0.6), fill=ink)


def _swash(d, x, y, s, color, rot):
    """Teal brush swash: a thick tapered arc drawn as overlapping dabs."""
    for i in range(28):
        t = i / 27
        a = rot + (t - 0.5) * 2.2
        px = x + math.cos(a) * s * 0.55
        py = y + math.sin(a) * s * 0.55 * 0.6
        rr = s * 0.16 * (math.sin(math.pi * t) ** 0.7) + 1
        d.ellipse((px - rr, py - rr, px + rr, py + rr), fill=color)


def _squiggle(d, x, y, s, color, rot):
    pts = []
    for i in range(30):
        t = i / 29
        u = (t - 0.5) * s
        v = math.sin(t * math.pi * 3) * s * 0.14
        pts.append((x + u * math.cos(rot) - v * math.sin(rot), y + u * math.sin(rot) + v * math.cos(rot)))
    d.line(pts, fill=color, width=max(4, int(s * 0.16)), joint="curve")


def _confetti(img, S, G, W, hz):
    """Early-90s jazz/Memphis confetti at a size that survives 64 px: fat brush swashes,
    squiggles, zigzags, triangles, rings and speckle clusters, kept to key corners."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rnd = random.Random(1994)
    teal = _rgb(PALETTE["teal"]) + (255,)
    purple = (164, 92, 255, 255)
    pink = (255, 70, 170, 255)
    yellow = _rgb(PALETTE["yellow"]) + (255,)
    cream = (255, 244, 220, 210)
    lw = max(4, int(S * 0.055))
    slots = []
    for row in (0, 1):
        for col in range(5):
            x0, y0 = col * (S + G), row * (S + G)
            if row == 0:
                cs = [(0.15, 0.15), (0.85, 0.15), (0.12, 0.5), (0.88, 0.5)]
            elif col in (0, 4):                 # palm keys: inner top corner only
                cs = [(0.8, 0.16)] if col == 0 else [(0.2, 0.16)]
            elif col == 2:                      # center key (power): keep clear
                cs = []
            else:                               # sun keys: outer top corners
                cs = [(0.14, 0.15), (0.86, 0.15)]
            for fx, fy in cs:
                slots.append((x0 + fx * S, y0 + fy * S, fy > 0.3))
    rnd.shuffle(slots)
    big = ["swash", "squiggle", "zig", "swash", "squiggle", "zig"]      # top corners only
    small = ["tri", "ring", "specks"]                                      # side slots beside icons
    nb = ns = 0
    for x, y, side in slots:
        if side:
            k, ns = small[ns % len(small)], ns + 1
        else:
            k, nb = big[nb % len(big)], nb + 1
        s = S * (rnd.uniform(0.22, 0.27) if side else rnd.uniform(0.3, 0.36))
        if k == "swash":            # keep the arc above the icon: open downward, flatter
            rot = rnd.uniform(-0.5, 0.5) + (math.pi if rnd.random() < 0.5 else 0)
            _swash(d, x, y, s * 1.1, teal, rot)
            continue
        rot = rnd.uniform(-0.6, 0.6)
        if k == "squiggle":
            _squiggle(d, x, y, s, purple, rot)
        elif k == "tri":
            pts = [(x + math.cos(rot + j * 2.094) * s * 0.34, y + math.sin(rot + j * 2.094) * s * 0.34) for j in range(3)]
            d.polygon(pts, fill=pink)
        elif k == "ring":
            rr = s * 0.2
            d.ellipse((x - rr, y - rr, x + rr, y + rr), outline=yellow, width=lw)
        elif k == "zig":
            pts = [(x - s * 0.42 + j * s * 0.17, y + (s * 0.13 if j % 2 else -s * 0.13)) for j in range(6)]
            pts = [(x + (px - x) * math.cos(rot * 0.5) - (py - y) * math.sin(rot * 0.5),
                    y + (px - x) * math.sin(rot * 0.5) + (py - y) * math.cos(rot * 0.5)) for px, py in pts]
            d.line(pts, fill=yellow, width=lw, joint="curve")
        else:
            for _ in range(6):
                px, py = x + rnd.uniform(-s * 0.4, s * 0.4), y + rnd.uniform(-s * 0.3, s * 0.3)
                rr = S * 0.022
                d.ellipse((px - rr, py - rr, px + rr, py + rr), fill=rnd.choice([teal, pink, yellow]))
    # sparkles
    for _ in range(18):
        x, y = rnd.uniform(0, W), rnd.uniform(0, hz - S * 0.9)
        rr = S * rnd.uniform(0.022, 0.04)
        d.polygon([(x, y - rr * 2), (x + rr * 0.5, y), (x, y + rr * 2), (x - rr * 0.5, y)], fill=cream)
        d.polygon([(x - rr * 2, y), (x, y + rr * 0.5), (x + rr * 2, y), (x, y - rr * 0.5)], fill=cream)
    img.alpha_composite(layer)


# ------------------------------------------------------------------ hooks
def backdrop(key, face, S, opts):
    G, W, H, hz = _geom(S)
    scene = _scene(S, json.dumps(opts or {}, sort_keys=True))
    if not key or key > 15:
        return scene.crop((2 * (S + G), 0, 2 * (S + G) + S, S))
    r, c = divmod(key - 1, 5)
    tile = scene.crop((c * (S + G), r * (S + G), c * (S + G) + S, r * (S + G) + S)).convert("RGBA")
    if (opts or {}).get("halo", True) and (face.get("icon") or face.get("glyph")):
        halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        hy = S * 0.42 if face.get("label") else S * 0.5
        ImageDraw.Draw(halo).ellipse((S * 0.18, hy - S * 0.3, S * 0.82, hy + S * 0.3),
                                     fill=_rgb(PALETTE["ink"]) + (120,))
        tile.alpha_composite(halo.filter(ImageFilter.GaussianBlur(S * 0.09)))
    if face.get("label"):                    # a darker band under the label
        band = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(band).rectangle((0, S * 0.72, S, S), fill=_rgb(PALETTE["ink"]) + (175,))
        tile.alpha_composite(band.filter(ImageFilter.GaussianBlur(S * 0.05)))
    if face.get("bg"):                        # a key's color becomes a neon rim, under the ink
        col = _neon(str(face["bg"]))
        rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(rim).rounded_rectangle((S * 0.035, S * 0.035, S * 0.965, S * 0.965), radius=S * 0.12,
                                              outline=col + (255,), width=max(5, S // 26))
        glow = rim.filter(ImageFilter.GaussianBlur(S * 0.05))
        tile.alpha_composite(glow)
        tile.alpha_composite(glow)
        tile.alpha_composite(rim)
    return tile.convert("RGB")


def _neon(hexcol):
    """Same hue, full saturation and brightness: brick red -> neon red, dull blue -> electric blue."""
    import colorsys
    r, g, b = (c / 255 for c in _rgb(hexcol))
    h, _s, _v = colorsys.rgb_to_hsv(r, g, b)
    return tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, 0.85, 1.0))


def _shift(mask, dx, dy):
    out = Image.new("L", mask.size, 0)
    out.paste(mask, (dx, dy))
    return out


def ink(img, mask, role, fill, face, key, opts):
    S = img.size[0]
    if role == "label":
        mask = _shift(mask, 0, -int(S * 0.035))
        bb = mask.getbbox()
        if bb:                                # dark tag behind the label: covers rims and busy sun/grid
            px, py = S * 0.05, S * 0.03
            plate = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            ImageDraw.Draw(plate).rounded_rectangle(
                (max(0, bb[0] - px), bb[1] - py, min(S - 1, bb[2] + px), min(S - 1, bb[3] + py)),
                radius=S * 0.06, fill=_rgb(PALETTE["ink"]) + (200,))
            base = img.convert("RGBA")
            base.alpha_composite(plate)
            img.paste(base.convert("RGB"))
    o = max(3, S // 42) if role != "label" else max(2, S // 60)
    sh = max(3, S // 34) if role != "label" else max(2, S // 70)
    outline = mask.filter(ImageFilter.MaxFilter(2 * o + 1))
    img.paste(_rgb(PALETTE["shadow"]), (0, 0), _shift(outline, sh, sh))
    img.paste(_rgb(PALETTE["ink"]), (0, 0), outline)
    if role == "label":
        img.paste(_rgb(PALETTE["label_color"]), (0, 0), mask)
    else:
        box = mask.getbbox() or (0, 0, S, S)
        h = max(1, box[3] - box[1])
        grad = Image.new("RGB", (S, S))
        g = _vgrad(S, h, ICON_GRADIENT)
        grad.paste(g, (0, box[1]))
        img.paste(grad, (0, 0), mask)


@lru_cache(maxsize=2)
def _scan(S):
    sc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(sc)
    for y in range(0, S, 6):
        d.line((0, y, S, y), fill=(10, 0, 30, 22), width=2)
    return sc


def finish(img, face, key, opts):
    S = img.size[0]
    out = img.convert("RGBA")
    if (opts or {}).get("scanlines", True):
        out.alpha_composite(_scan(S))
    return out.convert("RGB")
