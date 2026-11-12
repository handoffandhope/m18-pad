"""HILLSIDE -- "Cross-section of a hillside" (an original blocky-world theme; every texture is
drawn here from scratch, nothing is copied from any game).

The pad is one slice of a blocky world: the top row is open sky (banded blue, flat clouds, a square
sun peeking into the corner, grass tufts and flowers along the bottom edge), the middle row is a
wall of grass blocks with a lit redstone lamp in the center (the Lights key), and the bottom row is
stone with ore veins. Everything sits on the LCD's own pixel grid: block textures are 16 texels
per key, icons are pixel-art items on a 2 px grid in an ore material (gold, diamond, emerald...)
with a dark outline, and labels are a pixel font in an item-tooltip box, coloured by the item's
"rarity".

Options (settings.theme: {name: hillside, ...}):
    clouds: true      flat clouds in the sky row
    flowers: true     grass tufts and flowers along the sky row's bottom edge
    ores: true        ore veins in the stone row's corners
    lamp: true        key 8 is a lit redstone lamp (its icon turns redstone)
    material: null    force one icon material everywhere (gold|diamond|emerald|iron|redstone|lapis|amethyst)
A face's `bg` colour picks the nearest material (red -> redstone, blue -> lapis, green -> emerald)
and adds a bevelled slot frame, so status still reads.
"""
from __future__ import annotations

import colorsys
import json
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

RESAMPLE = Image.BOX            # every element is on the 64 px grid: downscale without blur

PALETTE = {
    "fg": "#ffffff",
    "label_color": "#ffffff",
    "ink": "#100010",           # tooltip background
    "good": "#55ff55",
    "warn": "#ffff55",
    "bad": "#ff5555",
    "track": "#373737",
    "aqua": "#55ffff",
    "gold": "#ffaa00",
    "xp": "#80ff20",
    "font": r"C:\Windows\Fonts\consolab.ttf",   # fallback for plugins that draw text themselves
    "rgb": "#4caf2a",           # grass green ring
}

# name: (highlight, base, shade, outline); label colour is the item's "rarity" colour
MATERIALS = {
    "gold":     ("#fffbb0", "#fcd843", "#d29a1c", "#5a3a06"),
    "diamond":  ("#d8fffb", "#4fe8d8", "#1fa39b", "#0b3a37"),
    "emerald":  ("#c4ffd4", "#45d870", "#16943a", "#063a17"),
    "iron":     ("#ffffff", "#d8d8d8", "#9c9c9c", "#2e2e2e"),
    "redstone": ("#ff8a70", "#e5251f", "#8f0d0d", "#2e0303"),
    "lapis":    ("#a9c2ff", "#3f6ad8", "#213f94", "#0a153d"),
    "amethyst": ("#efc7ff", "#b77ae8", "#7642a8", "#28103f"),
}
RARITY = {"gold": "#ffff55", "diamond": "#55ffff", "emerald": "#55ff55", "iron": "#ffffff",
          "redstone": "#ff6b6b", "lapis": "#aab4ff", "amethyst": "#ff55ff"}
DISC_ICONS = {"mdi:record-rec", "mdi:record", "mdi:record-circle"}   # lettered discs become plain items
ROW_MATERIAL = {0: "gold", 1: "diamond", 2: "emerald"}       # sky, grass, stone


def _rgb(h):
    h = str(h).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    a, b = _rgb(a) if isinstance(a, str) else a, _rgb(b) if isinstance(b, str) else b
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ------------------------------------------------------------------ pixel font (5x7, own design)
_F = {
    "A": ".###.|#...#|#...#|#...#|#####|#...#|#...#", "B": "####.|#...#|#...#|####.|#...#|#...#|####.",
    "C": ".###.|#...#|#....|#....|#....|#...#|.###.", "D": "####.|#...#|#...#|#...#|#...#|#...#|####.",
    "E": "#####|#....|#....|####.|#....|#....|#####", "F": "#####|#....|#....|####.|#....|#....|#....",
    "G": ".####|#....|#....|#..##|#...#|#...#|.###.", "H": "#...#|#...#|#...#|#####|#...#|#...#|#...#",
    "I": "###|.#.|.#.|.#.|.#.|.#.|###", "J": "....#|....#|....#|....#|....#|#...#|.###.",
    "K": "#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#", "L": "#....|#....|#....|#....|#....|#....|#####",
    "M": "#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#", "N": "#...#|##..#|#.#.#|#..##|#...#|#...#|#...#",
    "O": ".###.|#...#|#...#|#...#|#...#|#...#|.###.", "P": "####.|#...#|#...#|####.|#....|#....|#....",
    "Q": ".###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#", "R": "####.|#...#|#...#|####.|#.#..|#..#.|#...#",
    "S": ".####|#....|#....|.###.|....#|....#|####.", "T": "#####|..#..|..#..|..#..|..#..|..#..|..#..",
    "U": "#...#|#...#|#...#|#...#|#...#|#...#|.###.", "V": "#...#|#...#|#...#|#...#|.#.#.|.#.#.|..#..",
    "W": "#...#|#...#|#...#|#.#.#|#.#.#|##.##|#...#", "X": "#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#",
    "Y": "#...#|#...#|.#.#.|..#..|..#..|..#..|..#..", "Z": "#####|....#|...#.|..#..|.#...|#....|#####",
    "a": ".....|.....|.###.|....#|.####|#...#|.####", "b": "#....|#....|#.##.|##..#|#...#|#...#|####.",
    "c": ".....|.....|.###.|#....|#....|#...#|.###.", "d": "....#|....#|.##.#|#..##|#...#|#...#|.####",
    "e": ".....|.....|.###.|#...#|#####|#....|.####", "f": "..##|.#..|####|.#..|.#..|.#..|.#..",
    "g": ".....|.....|.####|#...#|#...#|#...#|.####|....#|####.",
    "h": "#....|#....|#.##.|##..#|#...#|#...#|#...#", "i": "#|.|#|#|#|#|#",
    "j": "....#|.....|....#|....#|....#|....#|#...#|#...#|.###.",
    "k": "#...|#...|#..#|#.#.|##..|#.#.|#..#", "l": "#.|#.|#.|#.|#.|#.|.#",
    "m": ".....|.....|##.#.|#.#.#|#.#.#|#...#|#...#", "n": ".....|.....|####.|#...#|#...#|#...#|#...#",
    "o": ".....|.....|.###.|#...#|#...#|#...#|.###.",
    "p": ".....|.....|#.##.|##..#|#...#|#...#|####.|#....|#....",
    "q": ".....|.....|.##.#|#..##|#...#|#...#|.####|....#|....#",
    "r": ".....|.....|#.##.|##..#|#....|#....|#....", "s": ".....|.....|.####|#....|.###.|....#|####.",
    "t": ".#..|.#..|####|.#..|.#..|.#..|..##", "u": ".....|.....|#...#|#...#|#...#|#...#|.####",
    "v": ".....|.....|#...#|#...#|#...#|.#.#.|..#..", "w": ".....|.....|#...#|#...#|#.#.#|#.#.#|.####",
    "x": ".....|.....|#...#|.#.#.|..#..|.#.#.|#...#",
    "y": ".....|.....|#...#|#...#|#...#|#...#|.####|....#|####.",
    "z": ".....|.....|#####|...#.|..#..|.#...|#####",
    "0": ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.", "1": "..#..|.##..|..#..|..#..|..#..|..#..|#####",
    "2": ".###.|#...#|....#|..##.|.#...|#....|#####", "3": ".###.|#...#|....#|..##.|....#|#...#|.###.",
    "4": "...##|..#.#|.#..#|#...#|#####|....#|....#", "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.", "7": "#####|....#|...#.|..#..|..#..|..#..|..#..",
    "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.", "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    "+": ".....|..#..|..#..|#####|..#..|..#..|.....", "-": "....|....|....|####|....|....|....",
    ".": ".|.|.|.|.|.|#", ",": "..|..|..|..|..|.#|.#|#.", ":": ".|.|#|.|.|.|#",
    "%": "##..#|##..#|...#.|..#..|.#...|#..##|#..##", "/": "....#|...#.|...#.|..#..|.#...|.#...|#....",
    "'": "#|#|.|.|.|.|.", "!": "#|#|#|#|#|.|#", "?": ".###.|#...#|....#|...#.|..#..|.....|..#..",
    "(": "..#|.#.|#..|#..|#..|.#.|..#", ")": "#..|.#.|..#|..#|..#|.#.|#..",
    "&": ".##..|#..#.|.##..|.#...|#.#.#|#..#.|.##.#", "#": ".#.#.|#####|.#.#.|.#.#.|.#.#.|#####|.#.#.",
}
_GLYPHS = {c: [r for r in v.split("|")] for c, v in _F.items()}


def text_width(s: str) -> int:
    w = 0
    for ch in s:
        g = _GLYPHS.get(ch) or _GLYPHS.get(ch.upper())
        w += (3 if ch == " " else (len(g[0]) if g else 5)) + 1
    return max(0, w - 1)


def draw_text(img, x, y, s, fill, shadow=True, scale=1):
    """Pixel text at cap-top (x, y) on a 1-px grid image; MC-style shadow at fill/4."""
    d = ImageDraw.Draw(img)
    col = _rgb(fill) if isinstance(fill, str) else fill
    if img.mode == "L":                 # mask canvases take one channel; a tuple raised "color must be int"
        col = (max(col[:3]),) * 3
    shc = tuple(c // 4 for c in col[:3])
    for pass_, (dx, c) in enumerate((((scale, shc),) if shadow else ()) + ((0, col),)):
        cx = x
        for ch in s:
            if ch == " ":
                cx += 4 * scale
                continue
            g = _GLYPHS.get(ch) or _GLYPHS.get(ch.upper()) or _GLYPHS["?"]
            for ry, row in enumerate(g):
                for rx, b in enumerate(row):
                    if b == "#":
                        px, py = cx + rx * scale + dx, y + ry * scale + dx
                        d.rectangle((px, py, px + scale - 1, py + scale - 1), fill=c[0] if img.mode == "L" else c)
            cx += (len(g[0]) + 1) * scale


# ------------------------------------------------------------------ textures (16x16 texels per key)
SKY = ("#5b93ff", "#6aa0ff", "#7aabff", "#8bb7ff", "#9cc3ff", "#aaccff", "#b6d4ff", "#c0dbff")
GREENS = ("#5aa02f", "#6cb93c", "#4e8e28", "#7bc945", "#62ad35")
DIRT = ("#86603f", "#7a5537", "#95704d", "#6b4a30", "#a17b57")
STONE = ("#7f7f7f", "#757575", "#8b8b8b", "#6c6c6c", "#979797")
ORES = {"coal": ("#2b2b2b", "#474747"), "iron": ("#d9b096", "#ecd3bf"), "redstone": ("#c41515", "#ff3a2a"),
        "gold": ("#f7d443", "#fff39c"), "diamond": ("#3fd9d0", "#a6fff6"), "lapis": ("#2448a8", "#4f78e0"),
        "emerald": ("#17b04a", "#7cf09d")}
KEY_ORE = {11: "coal", 12: "iron", 13: "redstone", 14: "gold", 15: "diamond"}


def _pick(rng, cols, w=(5, 2, 2, 1, 1)):
    return _rgb(rng.choices(cols, weights=w[:len(cols)])[0])


def _sky(rng, opts, c):
    t = Image.new("RGB", (16, 16))
    px = t.load()
    for y in range(16):
        band = SKY[min(len(SKY) - 1, y // 2)]
        for x in range(16):
            px[x, y] = _rgb(band)
    if opts.get("clouds", True):
        clouds = {0: [(2, 0, 9, 0), (0, 1, 12, 1)], 2: [(5, 0, 10, 0), (3, 1, 15, 1)],
                  3: [(0, 1, 4, 1)], 1: [(11, 0, 15, 0), (9, 1, 15, 1)]}.get(c, [])
        for (x0, y0, x1, y1) in clouds:
            for y in range(y0, y1 + 1):
                for x in range(x0, x1 + 1):
                    px[x, y] = _rgb("#ffffff" if y == y0 else "#e3ecf8")
    if c == 4:                                    # the sun peeks into the top-right corner
        for y in range(0, 4):
            for x in range(11, 16):
                px[x, y] = _rgb("#fff9d2" if (12 <= x <= 14 and y <= 2) else "#ffe25a")
    if opts.get("flowers", True):                 # tufts and flowers along the bottom edge
        for x in range(16):
            r = rng.random()
            if r < 0.42:
                h = rng.choice((1, 1, 2, 2, 3))
                for y in range(16 - h, 16):
                    px[x, y] = _pick(rng, GREENS)
        for _ in range(2):
            x = rng.choice((0, 1, 2, 13, 14, 15))
            col = rng.choice(("#e8312a", "#ffd83a", "#f0f0ff", "#c85bff"))
            for y in (14, 15):
                px[x, y] = _rgb("#3f7d22")
            px[x, 13] = _rgb(col)
            if x < 15:
                px[x + 1, 13] = _mix(col, "#000000", 0.25)
            px[x, 12] = _mix(col, "#ffffff", 0.3)
    return t


def _grass(rng):
    t = Image.new("RGB", (16, 16))
    px = t.load()
    drip = [rng.random() < 0.55 for _ in range(16)]
    drip2 = [d and rng.random() < 0.35 for d in drip]
    for y in range(16):
        for x in range(16):
            if y < 3 or (y == 3 and drip[x]) or (y == 4 and drip2[x]):
                px[x, y] = _pick(rng, GREENS)
            else:
                calm = 4 <= x <= 11 and 5 <= y <= 12
                px[x, y] = _pick(rng, DIRT, (8, 2, 2, 1, 1) if calm else (5, 2, 2, 2, 1))
    return t


def _lamp(rng):
    t = Image.new("RGB", (16, 16))
    px = t.load()
    for y in range(16):
        for x in range(16):
            edge = x in (0, 15) or y in (0, 15)
            seam = x in (7, 8) or y in (7, 8)
            if edge:
                c = _rgb("#5e3a1c")
            elif seam:
                c = _rgb("#9a6428")
            else:
                lx, ly = x % 8, y % 8          # each quarter glows brighter toward its centre
                dist = abs(lx - 3.5) + abs(ly - 3.5)
                c = _mix("#fff0b8", "#e09a35", min(1.0, dist / 6.0))
                if rng.random() < 0.12:
                    c = _mix(c, "#ffffff", 0.35)
            px[x, y] = c
    return t


def _stone(rng, ore, opts):
    t = Image.new("RGB", (16, 16))
    px = t.load()
    for y in range(16):
        for x in range(16):
            px[x, y] = _pick(rng, STONE)
    for _ in range(3):                              # short dark cracks
        x, y = rng.randrange(16), rng.randrange(16)
        for _i in range(rng.choice((2, 3))):
            px[x % 16, y % 16] = _rgb("#5f5f5f")
            x += rng.choice((1, 0, 1))
            y += rng.choice((0, 1))
    if ore and opts.get("ores", True):
        dark, light = ORES[ore]
        shapes = [((0, 0), (1, 0), (0, 1), (1, 1), (2, 1)), ((1, 0), (0, 1), (1, 1), (2, 1), (1, 2)),
                  ((0, 0), (1, 0), (2, 0), (1, 1), (2, 1))]
        spots = [(1, 1), (12, 1)] if rng.random() < 0.5 else [rng.choice(((1, 1), (12, 1)))]
        for (ox, oy) in spots:
            cells = [(ox + x, oy + y) for (x, y) in rng.choice(shapes)]
            for (x, y) in cells:                       # shadow under each cell first
                if (x + 1, y + 1) not in cells and x < 15 and y < 15:
                    px[x + 1, y + 1] = _rgb("#4f4f4f")
            for (x, y) in cells:
                px[x, y] = _rgb(dark)
            px[cells[0]] = _rgb(light)
    return t


@lru_cache(maxsize=4)
def _tiles(opts_json):
    """Each key's 16x16 texture, seeded so the world never reshuffles."""
    opts = json.loads(opts_json)
    out = {}
    for k in range(1, 16):
        rng = random.Random(1000 + k)
        r, c = divmod(k - 1, 5)
        if r == 0:
            out[k] = _sky(rng, opts, c)
        elif r == 1:
            out[k] = _lamp(rng) if (k == 8 and opts.get("lamp", True)) else _grass(rng)
        else:
            out[k] = _stone(rng, KEY_ORE.get(k), opts)
    return out


# ------------------------------------------------------------------ materials
def _material(face, key, opts):
    if face.get("bg"):
        r, g, b = (v / 255 for v in _rgb(face["bg"]))
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        if s < 0.25:
            return "iron"
        for lim, name in ((0.05, "redstone"), (0.17, "gold"), (0.45, "emerald"), (0.55, "diamond"),
                          (0.75, "lapis"), (0.92, "amethyst"), (1.01, "redstone")):
            if h < lim:
                return name
    if opts.get("material") in MATERIALS:
        return opts["material"]
    if key == 8 and opts.get("lamp", True):
        return "redstone"
    return ROW_MATERIAL.get((max(1, key or 8) - 1) // 5, "iron")


def backdrop(key, face, S, opts):
    opts = opts or {}
    k = key if key and 1 <= key <= 15 else 8
    tile = _tiles(json.dumps(opts, sort_keys=True))[k]
    img = tile.resize((64, 64), Image.NEAREST)
    if face.get("bg"):                              # status: the key becomes an inventory slot
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, 63, 63), fill=_rgb("#8b8b8b"))
        d.rectangle((0, 0, 63, 2), fill=_rgb("#373737"))
        d.rectangle((0, 0, 2, 63), fill=_rgb("#373737"))
        d.rectangle((1, 61, 63, 63), fill=_rgb("#ffffff"))
        d.rectangle((61, 1, 63, 63), fill=_rgb("#ffffff"))
    return img.resize((S, S), Image.NEAREST)


def _paint_cells(img, cells, mat, grid):
    """cells: L image (n x n, 255 = inside). Draw the pixel-art item at `grid` px per cell."""
    hi, base, shade, out = (_rgb(c) for c in MATERIALS[mat])
    n = cells.size[0]
    inside = cells.load()
    ring = cells.filter(ImageFilter.MaxFilter(3))
    rp = ring.load()
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    lp = layer.load()
    for y in range(n):                              # soft drop shadow, one cell down-right
        for x in range(n):
            if x and y and rp[x - 1, y - 1] and not rp[x, y]:
                lp[x, y] = (0, 0, 0, 110)
    for y in range(n):
        for x in range(n):
            if rp[x, y] and not inside[x, y]:
                lp[x, y] = out + (255,)
            elif inside[x, y]:
                up = y == 0 or not inside[x, y - 1]
                left = x == 0 or not inside[x - 1, y]
                down = y == n - 1 or not inside[x, y + 1]
                right = x == n - 1 or not inside[x + 1, y]
                c = hi if (up or left) else shade if (down or right) else base
                lp[x, y] = c + (255,)
    S = img.size[0]
    big = layer.resize((n * grid, n * grid), Image.NEAREST)
    base_img = img.convert("RGBA")
    base_img.alpha_composite(big.crop((0, 0, S, S)))
    img.paste(base_img.convert("RGB"))


def _tooltip(d, x0, y0, x1, y1, notch=True):
    """Item-tooltip box on a 64 px grid: near-black plum, purple inner border, notched corners."""
    a = 238 if notch else 255
    d.rectangle((x0 + notch, y0, x1 - notch, y1), fill=(16, 0, 16, a))
    d.rectangle((x0, y0 + notch, x1, y1 - notch), fill=(16, 0, 16, a))
    d.line((x0 + 1, y0 + 1, x1 - 1, y0 + 1), fill=(80, 0, 255, 140))
    d.line((x0 + 1, y1 - 1, x1 - 1, y1 - 1), fill=(40, 0, 127, 140))
    d.line((x0 + 1, y0 + 2, x0 + 1, y1 - 2), fill=(60, 0, 190, 140))
    d.line((x1 - 1, y0 + 2, x1 - 1, y1 - 2), fill=(60, 0, 190, 140))


def ink(img, mask, role, fill, face, key, opts):
    opts = opts or {}
    S = img.size[0]
    mat = _material(face, key, opts)
    if role == "icon":
        n = 32
        if str(face.get("icon", "")) in DISC_ICONS:
            bb = mask.getbbox() or (0, 0, S, S)
            r = min(bb[2] - bb[0], bb[3] - bb[1]) * 0.42
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            mask = Image.new("L", mask.size, 0)
            ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        cells = mask.resize((n, n), Image.BOX).point(lambda v: 255 if v >= 96 else 0)
        _paint_cells(img, cells, mat, S // n)
        return
    if role == "glyph" or (role == "label" and not (face.get("icon") or face.get("glyph") or face.get("emoji"))):
        text = str(face.get("glyph") if role == "glyph" else face.get("label", ""))
        if role == "label":
            if img.info.get("mc_label"):
                return
            img.info["mc_label"] = True
        bb = mask.getbbox() or (0, 0, S, S)
        cy = (bb[1] + bb[3]) // 2 * 32 // S
        w = text_width(text)
        sc = max(1, min(4, 28 // max(1, w), 3 if role == "glyph" else 2))
        cells = Image.new("L", (32, 32), 0)
        draw_text(cells, (32 - w * sc) // 2, cy - (7 * sc) // 2, text, (255, 255, 255), shadow=False, scale=sc)
        _paint_cells(img, cells, mat, S // 32)
        return
    # bottom label: one tooltip with the whole label (drawn once, not per wrapped line)
    if img.info.get("mc_label"):
        return
    img.info["mc_label"] = True
    text = str(face.get("label", ""))
    while text_width(text) > 56 and len(text) > 1:
        text = text[:-1]
    w = text_width(text)
    lay = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    x0 = (64 - w) // 2 - 3
    y1 = 62
    y0 = y1 - 13
    _tooltip(d, x0, y0, x0 + w + 6, y1)
    draw_text(lay, x0 + 3, y0 + 2, text, RARITY.get(mat, "#ffffff"))
    base = img.convert("RGBA")
    base.alpha_composite(lay.resize((S, S), Image.NEAREST))
    img.paste(base.convert("RGB"))


# ------------------------------------------------------------------ for plugins
def meters(rows, key=None, opts=None):
    """CPU/RAM/GPU-style meters: tooltip plate, pixel names and values, 10-pip bars."""
    lay = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    _tooltip(d, 0, 0, 63, 63, notch=False)
    accents = [PALETTE["xp"], PALETTE["aqua"], PALETTE["gold"], "#ff55ff"]
    n = max(1, len(rows))
    rh = 58 // n
    for i, (name, v) in enumerate(rows):
        y = 4 + i * rh
        col = accents[i % len(accents)]
        if v is not None and v >= 85:
            col = PALETTE["bad"]
        draw_text(lay, 5, y, str(name)[:4], "#aaaaaa")
        val = "--" if v is None else f"{v:.0f}%"
        draw_text(lay, 59 - text_width(val), y, val, "#ffffff")
        py = y + 9
        lit = 0 if v is None else round(max(0.0, min(100.0, v)) / 10)
        for p in range(10):
            x = 5 + p * 5 + (1 if p >= 5 else 0)
            if p < lit:
                d.rectangle((x, py, x + 3, py + 2), fill=_rgb(col))
                d.line((x, py, x + 3, py), fill=_mix(col, "#ffffff", 0.45))
            else:
                d.rectangle((x, py, x + 3, py + 2), fill=(55, 55, 55, 255))
                d.line((x, py + 2, x + 3, py + 2), fill=(30, 30, 30, 255))
    return lay.resize((192, 192), Image.NEAREST)
