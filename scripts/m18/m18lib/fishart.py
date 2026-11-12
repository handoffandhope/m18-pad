"""Pixel art for the fish tank (plugins/fishbowl.py): real side-view fish, a moon jelly, a sunfish, their
front views at the glass, fry, the tank fixtures (lamp, buoy, filter gauge, treasure chest) and a 5x7 pixel font.

Everything is drawn at 1 px = 1 key pixel and never scaled smoothly, so it stays crisp on the 64 px screens.
Sprites face RIGHT; the tank mirrors them. Every function is pure (inputs -> RGBA image) and cached.

Identity: identity(sid, grid) turns a session's 16x16 sprite (or its id) into body / pattern / fin colours and a
pattern kind, so the same session is always the same fish ("the orange striped one").
"""
from __future__ import annotations

import colorsys
import hashlib
import math
from functools import lru_cache

from PIL import Image, ImageDraw

SPRITE_PAL = {"K": "#161616", "W": "#f4f4f4", "G": "#b4b4b4", "S": "#7c7c7c", "D": "#454545", "R": "#cc3a30",
              "O": "#ee8c2c", "Y": "#f2d33a", "L": "#72cc40", "E": "#2f7d2c", "C": "#3fcbdc", "B": "#3a5bd2",
              "P": "#8e3fca", "N": "#7d4c2a", "T": "#dbb47e"}
WATER_MID = (36, 83, 170)
PATTERNS = ("stripes", "spots", "band", "saddle", "plain")
BODY_HUES = ("#f08a2c", "#f2c53a", "#e0483c", "#f07aa8", "#a066e0", "#8fd84a", "#3fd0c0", "#ff7f6a", "#f4f1ea",
             "#c58bf0", "#ffb347", "#5fe08a")
PATTERN_WORD = {"stripes": "striped", "spots": "spotted", "band": "banded", "saddle": "patched", "plain": "plain"}
GOLD = (255, 221, 64)
INK = (22, 16, 30)


# ------------------------------------------------------------------ colour helpers
def rgb(h):
    if isinstance(h, tuple):
        return tuple(int(v) for v in h[:3])
    h = str(h).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    a, b = rgb(a), rgb(b)
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def hexc(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb(c))


def _hsv(c):
    r, g, b = (v / 255 for v in rgb(c))
    return colorsys.rgb_to_hsv(r, g, b)


def _from_hsv(h, s, v):
    return tuple(int(round(x * 255)) for x in colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v))))


def _seed(s) -> int:
    return int(hashlib.sha1(str(s).encode()).hexdigest()[:8], 16)


def readable_on_water(c):
    """A body colour that stands out on the blue water: blues get lighter and brighter, dark colours lifted."""
    h, s, v = _hsv(c)
    if 0.5 <= h <= 0.72 and s > 0.25:                 # blue-ish: would vanish into the water
        return _from_hsv(h - 0.04, s * 0.75, max(v, 0.95))
    if v < 0.45:
        return _from_hsv(h, s, 0.55)
    return rgb(c)


def identity(sid, grid=None) -> dict:
    """{body, pattern, fin, kind}: colours from the sprite's two most used colours (else from the id), the pattern
    kind from the id. Deterministic: the same session id and sprite always give the same fish."""
    cnt = {}
    for row in grid or []:
        for ch in row:
            if ch in SPRITE_PAL and ch not in "K":
                cnt[ch] = cnt.get(ch, 0) + 1
    hs = _seed(sid)
    chroma = sorted((c for c in cnt if c not in "WGSD" and cnt[c] >= 3), key=lambda c: (-cnt[c], c))
    greys = sorted((c for c in cnt if c in "WGS"), key=lambda c: (-cnt[c], c))
    if chroma:                                    # the sprite's own colours
        body = rgb(SPRITE_PAL[chroma[0]])
        pat = rgb(SPRITE_PAL[chroma[1]]) if len(chroma) > 1 else (rgb(SPRITE_PAL[greys[0]]) if greys else None)
    else:                                         # a grey sprite: body hue from the id, its greys as the pattern
        body = rgb(BODY_HUES[hs % len(BODY_HUES)])
        pat = rgb(SPRITE_PAL[greys[0]]) if greys else None
    body = readable_on_water(body)
    if pat is None or _dist(pat, body) < 60:
        h, s, v = _hsv(body)
        pat = _from_hsv(h + 0.08, s * 0.5, 1.0) if v < 0.75 else _from_hsv(h, min(1, s + 0.3), v * 0.55)
    kind = PATTERNS[(hs >> 8) % len(PATTERNS)]
    fin = mix(body, pat, 0.5) if kind == "plain" else mix(body, (255, 255, 255), 0.35)
    if kind == "plain":
        fin = pat
    return {"body": body, "pattern": pat, "fin": fin, "kind": kind}


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(rgb(a), rgb(b))) ** 0.5


def colour_name(c) -> str:
    h, s, v = _hsv(c)
    if s < 0.18:
        return "white" if v > 0.85 else "silver" if v > 0.5 else "grey"
    names = ((0.04, "red"), (0.11, "orange"), (0.18, "yellow"), (0.42, "green"), (0.53, "teal"), (0.70, "blue"),
             (0.83, "purple"), (0.95, "pink"), (1.01, "red"))
    for edge, n in names:
        if h < edge:
            return "brown" if n in ("orange", "red") and v < 0.6 else n
    return "red"


# ------------------------------------------------------------------ species shapes
# L, H body; c = fattest point (0 tail end .. 1 snout); ped = half-height where the tail joins; tail kind,
# tl/th tail length and height; dorsal/anal (kind, from, to, height) as fractions of L; eye px; pec = pectoral size
SPEC = {
    "opus":   dict(L=20, H=17, c=0.52, ped=2.0, tail="fan", tl=7, th=14, dorsal=("sail", 0.18, 0.72, 4),
                   anal=("round", 0.18, 0.5, 3), eye=3, pec=4, blush=True, front=(24, 26)),
    "sonnet": dict(L=17, H=10, c=0.6, ped=1.6, tail="fork", tl=6, th=12, dorsal=("tri", 0.3, 0.68, 3),
                   anal=("tri", 0.22, 0.42, 2), eye=2, pec=3, blush=False, front=(17, 18)),
    "haiku":  dict(L=10, H=6, c=0.6, ped=1.0, tail="fork", tl=4, th=8, dorsal=("tri", 0.35, 0.62, 2),
                   anal=None, eye=2, pec=0, blush=False, front=(12, 12)),
    "fish":   dict(L=12, H=12, c=0.45, ped=2.0, tail="veil", tl=11, th=16, dorsal=("tri", 0.3, 0.75, 3),
                   anal=("round", 0.25, 0.5, 2), eye=2, pec=3, blush=True, front=(17, 18)),
    "sol":    dict(L=18, H=17, c=0.45, ped=6.5, tail="clavus", tl=3, th=15, dorsal=("mola", 0.02, 0.3, 9),
                   anal=("mola", 0.02, 0.3, 9), eye=2, pec=3, blush=False, front=(18, 30)),
}
SPEED = {"opus": 9.0, "sonnet": 16.0, "haiku": 26.0, "fish": 13.0, "luna": 7.0, "sol": 8.0}
LABEL = {"opus": "OPUS", "sonnet": "SONNET", "haiku": "HAIKU", "fish": "GOLDFISH", "luna": "LUNA", "sol": "SOL"}


def _body_hh(sp, u):
    c, H, ped = sp["c"], sp["H"], sp["ped"]
    if u >= c:
        v = (u - c) / (1 - c)
        return (H / 2) * max(0.0, 1 - v ** 2.4) ** 0.5
    v = (c - u) / c
    return ped + (H / 2 - ped) * max(0.0, 1 - v ** 1.9) ** 0.65


def _shape(species, wag=0, flap=0):
    """Role map {(x, y): role} for a side-view fish facing right, plus anchor points.
    roles: b body, f fin, F fin ray, P pectoral, e eye, w eye highlight, m mouth"""
    sp = SPEC[species]
    L, H = sp["L"], sp["H"]
    cy = H / 2
    px = {}
    top, bot = {}, {}
    for x in range(L):
        u = (x + 0.5) / L
        hh = _body_hh(sp, u)
        for y in range(-2, H + 2):
            if abs((y + 0.5) - cy) <= hh + 0.2:
                px[(x, y)] = "b"
                top[x] = min(top.get(x, 99), y)
                bot[x] = max(bot.get(x, -99), y)
    # tail, attached at the left end, swinging with `wag` (-2..2)
    tl, th = sp["tl"], sp["th"]
    kind = sp["tail"]
    for d in range(1, tl + 1):
        x = -d
        f = d / tl
        if kind == "clavus":                                 # the sunfish's scalloped rudder
            half = (th / 2) * (1 - 0.15 * f)
            off = 0
        elif kind == "veil":                                  # long, drooping, flowing double tail
            half = sp["ped"] + (th / 2 - sp["ped"]) * min(1.0, f * 1.6) ** 0.8
            off = wag * f ** 1.2 * 2.0 + f * 2.5
        else:
            half = sp["ped"] + (th / 2 - sp["ped"]) * f ** 0.8
            off = wag * f ** 1.4 * 1.5
        c0 = cy + off
        for y in range(-6, H + 8):
            dy = (y + 0.5) - c0
            if abs(dy) > half + 0.2:
                continue
            if kind == "fork" and f > 0.45 and abs(dy) < (f - 0.45) / 0.55 * half * 0.75:
                continue
            if kind == "veil" and f > 0.55 and abs(dy) < (f - 0.55) / 0.45 * half * 0.35:
                continue
            if kind == "veil" and d >= tl - 1 and (y + d) % 3 == 0:
                continue
            if kind == "fan" and d == tl and abs(dy) > half - 1.2:
                continue
            if kind == "clavus" and d == tl and (y % 3 == 0):
                continue
            px[(x, y)] = "F" if (kind != "clavus" and d >= tl - 1 and d > 2) else "f"
    # dorsal and anal fins
    for which, spec in (("dorsal", sp["dorsal"]), ("anal", sp["anal"])):
        if not spec:
            continue
        fk, a, b, h = spec
        xa, xb = int(round(a * L)), int(round(b * L))
        for x in range(xa, max(xa + 1, xb)):
            t = (x - xa) / max(1, xb - xa - 1)
            if fk == "tri":
                hh = h * (t / 0.35 if t < 0.35 else (1 - t) / 0.65 * 0.9 + 0.1)
            elif fk == "sail":
                hh = h * math.sin(math.pi * min(1, t * 0.85 + 0.1)) ** 0.7
            elif fk == "mola":                                # tall blade, swept back
                hh = h * (1 - abs(t - 0.45) / 0.55) ** 0.6
            else:
                hh = h * math.sin(math.pi * t) ** 0.8
            n = int(round(hh))
            for i in range(1, n + 1):
                y = top.get(x, 0) - i if which == "dorsal" else bot.get(x, H) + i
                if (x, y) not in px:
                    px[(x, y)] = "F" if (i == n and n > 1 and fk != "mola") else "f"
    # pectoral fin behind the gill, flapping
    pec = sp["pec"]
    if pec:
        gx = int(round(L * (0.62 if species != "sol" else 0.66)))
        gy = int(round(cy + (0 if species == "sol" else 0.5)))
        for i in range(pec):
            for j in range(max(1, pec - i - (1 if flap else 0))):
                y = gy + j - (1 if flap else 0) + (i // 2 if not flap else 0)
                x = gx - i
                if (x, y) in px and px[(x, y)] == "b":
                    px[(x, y)] = "P"
    # eye, mouth
    e = sp["eye"]
    ex = L - 1 - max(2, int(round(L * 0.17))) - (e - 2)
    ey = int(round(cy - H * 0.2)) - (1 if e == 3 else 0)
    if species == "haiku":
        ey = int(round(cy - 1.5))
        ex = L - 3
    eye = [(ex + i, ey + j) for i in range(e) for j in range(e)]
    my = int(math.floor(cy)) + (1 if H > 7 else 0)
    if species == "sol":
        my = int(math.floor(cy)) + 1
    mouth = (L - 1, my)
    return px, {"eye": eye, "eye_xy": (ex, ey), "e": e, "mouth": mouth, "top": top, "bot": bot, "L": L, "H": H,
                "cy": cy}


def _jelly_shape(phase):
    """Moon jelly: dome bell, four horseshoe rings, a fringe of short tentacles and frilly oral arms."""
    w = 18 if phase == 0 else 16
    h = 9 if phase == 0 else 11
    px = {}
    cx = w / 2
    for y in range(h):
        v = (h - y - 0.5) / h
        half = (w / 2) * max(0.0, 1 - v ** 2.2) ** 0.5
        for x in range(w):
            if abs((x + 0.5) - cx) <= half + 0.15:
                px[(x, y)] = "b"
    rings = []
    for k, rx in enumerate((0.25, 0.42, 0.58, 0.75)):        # the four gonad rings seen through the bell
        x0 = int(round(rx * w)) - 1
        y0 = h - 5 if k in (0, 3) else h - 6
        for (dx, dy) in ((0, 0), (1, -1), (2, 0), (0, 1), (2, 1)):
            rings.append((x0 + dx, y0 + dy))
    for p in rings:
        if p in px:
            px[p] = "p"
    for x in range(1, w - 1, 2):                             # fringe tentacles
        ln = 3 + ((x * 7) % 3)
        for i in range(ln):
            sway = int(round(math.sin((i + x) * 0.9 + phase * 2.0) * 0.6))
            px.setdefault((x + sway, h + i), "t")
    for a, off in ((-2, 0.0), (1, 1.6)):                     # oral arms, frilly
        for i in range(9 if phase == 0 else 8):
            x = int(round(cx + a + math.sin(i * 0.8 + off + phase * 1.5) * 1.2))
            px[(x, h + i)] = "a"
            if i % 3 == 1:
                px[(x + 1, h + i)] = "a"
    ey = h - 4
    eye = [(int(cx) - 4, ey), (int(cx) + 3, ey)]
    return px, {"eye": eye, "e": 1, "mouth": (int(cx), ey + 2), "w": w, "h": h, "L": w, "H": h, "cy": h / 2}


# ------------------------------------------------------------------ colouring
def _palette(ident, look):
    body, pat, fin = ident["body"], ident["pattern"], ident["fin"]
    if look == "pale":
        g = lambda c: mix(mix((sum(rgb(c)) // 3,) * 3, c, 0.3), (216, 190, 255), 0.4)   # noqa: E731
        body, pat, fin = g(body), g(pat), g(fin)
    elif look == "belly":
        g = lambda c: mix(c, (200, 200, 205), 0.55)          # noqa: E731
        body, pat, fin = g(body), g(pat), g(fin)
    elif look == "frost":
        g = lambda c: mix(c, (223, 244, 255), 0.45)          # noqa: E731
        body, pat, fin = g(body), g(pat), g(fin)
    return {
        "b": body, "u": mix(body, (255, 255, 255), 0.42), "d": mix(body, (0, 0, 0), 0.16),
        "h": mix(body, (255, 255, 255), 0.7), "p": pat, "f": fin, "F": mix(fin, (0, 0, 0), 0.18),
        "P": mix(fin, (255, 255, 255), 0.2), "o": mix(body, (10, 6, 24), 0.72), "of": mix(fin, (10, 6, 24), 0.7),
        "e": INK, "w": (255, 255, 255), "m": mix(body, (10, 6, 24), 0.75), "c": (255, 140, 170),
    }


def _pattern_mask(kind, x, y, L, top, bot, seed):
    """Is body pixel (x, y) in the session's pattern?"""
    t, b = top.get(x, 0), bot.get(x, 0)
    if kind == "stripes":
        per = 4 if L > 12 else 3
        return (x + seed % per) % per == 0 and 0.15 * L < x < 0.8 * L
    if kind == "spots":
        if y > (t + b) / 2 + 1:
            return False
        h = _seed(f"{seed}:{x // 3}:{y // 3}")
        return h % 3 == 0 and x % 3 == 1 and y % 3 == (h >> 4) % 2
    if kind == "band":
        bx = int(L * 0.6)
        return bx <= x <= bx + (1 if L < 12 else 2) or (L > 15 and int(L * 0.25) <= x <= int(L * 0.25) + 1)
    if kind == "saddle":
        return 0.2 * L <= x <= 0.65 * L and y <= t + max(1, (b - t) * 0.45)
    return False


def _render(px, anc, ident, look, species, pattern=True, closed=False, xeye=False):
    pal = _palette(ident, look)
    top, bot = anc.get("top", {}), anc.get("bot", {})
    L = anc["L"]
    seed = _seed(ident.get("sid", "")) if ident.get("sid") else 0
    col = {}
    for (x, y), r in px.items():
        if r == "b" and species == "luna":                 # translucent milky bell, faint tint of its colour
            col[(x, y)] = mix((236, 230, 250), pal["b"], 0.16) if y < anc["h"] - 2 else mix((214, 206, 236), pal["b"], 0.2)
        elif r == "p" and species == "luna":
            col[(x, y)] = pal["b"]
        elif r == "b":
            t, b = top.get(x, y), bot.get(x, y)
            span = max(1, b - t)
            rel = (y - t) / span
            c = pal["d"] if rel < 0.22 else pal["u"] if rel > 0.68 else pal["b"]
            if species == "haiku":                         # a tetra: one bright lateral line
                if y == int(anc["cy"]) - 1 and 1 <= x <= L - 3:
                    c = mix(pal["p"], (255, 255, 255), 0.25)
            elif pattern and _pattern_mask(ident["kind"], x, y, L, top, bot, seed):
                c = pal["p"] if rel <= 0.68 or ident["kind"] in ("stripes", "band") else mix(pal["p"], pal["u"], 0.4)
            col[(x, y)] = c
        elif r in "fFP":
            col[(x, y)] = pal[r]
        elif r == "p":
            col[(x, y)] = pal["p"]
        elif r == "t":
            col[(x, y)] = (226, 220, 244)
        elif r == "a":
            col[(x, y)] = mix(pal["b"], (255, 255, 255), 0.55)
    if species not in ("luna",):
        # a highlight streak on the upper front body
        for x in range(int(L * 0.45), int(L * 0.8)):
            t = top.get(x)
            if t is not None and (x, t + 1) in col and px.get((x, t + 1)) == "b" and (x % 3 != 0):
                col[(x, t + 1)] = mix(col[(x, t + 1)], (255, 255, 255), 0.45)
    # eye(s)
    e = anc["e"]
    if species == "luna":
        for (x, y) in anc["eye"]:
            if closed:
                col[(x - 1, y)] = INK
                col[(x, y - 1)] = INK
                col[(x + 1, y)] = INK
            elif xeye:
                for p in ((x - 1, y - 1), (x + 1, y + 1), (x - 1, y + 1), (x + 1, y - 1), (x, y)):
                    col[p] = INK
            else:
                col[(x, y)] = INK
                col[(x, y + 1)] = INK
        mx, my = anc["mouth"]
        col[(mx, my)] = pal["m"] if not xeye else INK
        if not xeye:
            col[(anc["eye"][0][0] - 1, my)] = pal["c"]
            col[(anc["eye"][1][0] + 1, my)] = pal["c"]
    else:
        ex, ey = anc["eye_xy"]
        if closed:                                        # content: a little ^ arc
            for i in range(e + 1):
                yy = ey + (1 if i in (0, e) else 0) + (1 if e == 2 else 0)
                col[(ex + i - (1 if e == 3 else 0), yy)] = INK
        elif xeye:
            n = max(3, e)
            for i in range(n):
                col[(ex - 1 + i, ey - 1 + i)] = INK
                col[(ex - 1 + i, ey - 1 + n - 1 - i)] = INK
        else:
            if e >= 3:                                   # a white ring makes the big eye read as an eye
                for i in range(-1, e + 1):
                    for j in range(-1, e + 1):
                        if (ex + i, ey + j) in col and not (i in (-1, e) and j in (-1, e)):
                            col[(ex + i, ey + j)] = (250, 250, 250)
            for (x, y) in anc["eye"]:
                col[(x, y)] = INK
            col[(ex + e - 1, ey)] = (255, 255, 255)        # the 1 px highlight, upper front
        mx, my = anc["mouth"]
        col[(mx, my)] = pal["m"]
        if closed and (mx - 1, my) in col:
            col[(mx - 1, my + 1 if (mx - 1, my + 1) in col else my)] = pal["m"]   # a small smile
        if SPEC.get(species, {}).get("blush") and not xeye:
            bx, by = ex - 1, ey + e + (0 if e == 3 else 1)
            if (bx, by) in col:
                col[(bx, by)] = pal["c"]
    # outline: dark rim around everything
    filled = set(col)
    out = {}
    for (x, y) in filled:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (x + dx, y + dy)
            if q not in filled:
                fin = px.get((x, y)) in ("f", "F", "t", "a")
                out[q] = pal["of"] if fin else pal["o"]
    if species == "luna":                                  # a jelly has no hard outline below the bell
        out = {q: c for q, c in out.items() if px.get((q[0], q[1] - 1)) in (None, "b", "p") and q[1] <= anc["h"]}
    col.update(out)
    xs = [p[0] for p in col]
    ys = [p[1] for p in col]
    x0, y0 = min(xs), min(ys)
    img = Image.new("RGBA", (max(xs) - x0 + 1, max(ys) - y0 + 1), (0, 0, 0, 0))
    pp = img.load()
    for (x, y), c in col.items():
        a = 255
        if species == "luna":
            r = px.get((x, y))
            a = 215 if r in ("b",) else 235 if r in ("t", "a") else 255
        pp[x - x0, y - y0] = tuple(rgb(c)) + (a,)
    shift = (-x0, -y0)
    return img, shift


def _shift_pts(pts, s):
    return [(x + s[0], y + s[1]) for x, y in pts]


@lru_cache(maxsize=1024)
def _side_cached(species, body, pat, fin, kind, sid, wag, flap, look, closed, xeye):
    ident = {"body": body, "pattern": pat, "fin": fin, "kind": kind, "sid": sid}
    if species == "luna":
        px, anc = _jelly_shape(wag % 2)
    else:
        px, anc = _shape(species, wag, flap)
    img, s = _render(px, anc, ident, look, species, closed=closed, xeye=xeye)
    eye = _shift_pts(anc["eye"], s)
    mouth = (anc["mouth"][0] + s[0], anc["mouth"][1] + s[1])
    if species == "luna":
        head = (img.width // 2, 0)
    else:
        head = (int(round(anc["L"] * 0.55)) + s[0], min(anc["top"].values()) + s[1])
    return img, {"eye": eye, "mouth": mouth, "head": head}


def side(species, ident, frame=0, look="side", closed=False, xeye=False):
    """Side view facing right. frame 0-3 is the swim cycle (tail wag and pectoral flap); a jelly pulses on 0/1.
    look: side | pale | belly | frost. Returns (RGBA, anchors {eye, mouth, head})."""
    if species not in SPEC and species != "luna":
        species = "fish"
    wag = (0, 1, 0, -1)[frame % 4] if species != "luna" else frame % 2
    if species == "opus":
        wag = (0, 1, 1, 0, -1, -1)[frame % 6] if frame >= 0 else 0
    flap = (frame // 2) % 2 if species != "luna" else 0
    img, anc = _side_cached(species, ident["body"], ident["pattern"], ident["fin"], ident["kind"],
                            ident.get("sid", ""), wag, flap, look, closed, xeye)
    if look == "belly":
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        h = img.height
        anc = {k: ([(x, h - 1 - y) for x, y in v] if isinstance(v, list) else (v[0], h - 1 - v[1]))
               for k, v in anc.items()}
    return img, anc


@lru_cache(maxsize=256)
def _front_cached(species, body, pat, fin, kind, sid, flap, look, blink):
    """Face-on: round face, two big eyes looking at you, a little 'o' mouth, side fins out, dorsal on top."""
    ident = {"body": body, "pattern": pat, "fin": fin, "kind": kind, "sid": sid}
    pal = _palette(ident, look)
    if species == "luna":
        px, anc = _jelly_shape(flap % 2)
        img, s = _render(px, anc, ident, look, "luna", closed=bool(blink))
        img = img.resize((img.width * 3 // 2, img.height * 3 // 2), Image.NEAREST)
        return img
    W, H = SPEC.get(species, SPEC["fish"])["front"]
    col = {}
    cx, cy = (W - 1) / 2, (H - 1) / 2
    body_h = H * (0.62 if species == "sol" else 0.82)
    body_w = W * (0.62 if species != "sol" else 0.55)
    oy = (H - body_h) / 2 + (1 if species != "sol" else 0)
    for y in range(H):
        for x in range(W):
            dx = (x - cx) / (body_w / 2)
            dy = (y - (oy + body_h / 2 - 0.5)) / (body_h / 2)
            if dx * dx + dy * dy <= 1.0:
                rel = (y - oy) / body_h
                c = pal["d"] if rel < 0.18 else pal["u"] if rel > 0.62 else pal["b"]
                col[(x, y)] = c
    # identity pattern on the face: stripes = a forehead band, spots = freckles, band = a ring, saddle = cap
    k = kind
    xs = [p[0] for p in col]
    for (x, y) in list(col):
        rel = (y - oy) / body_h
        if k == "stripes" and 0.12 < rel < 0.22:
            col[(x, y)] = pal["p"]
        elif k == "band" and abs(x - cx) > body_w / 2 - 2.2 and 0.2 < rel < 0.8:
            col[(x, y)] = pal["p"]
        elif k == "saddle" and rel < 0.2:
            col[(x, y)] = pal["p"]
        elif k == "spots" and 0.08 < rel < 0.3 and (x * 7 + y * 3) % 5 == 0:
            col[(x, y)] = pal["p"]
    # side fins (pectorals) and dorsal
    fy = int(oy + body_h * 0.6)
    left, right = min(xs), max(xs)
    for i in range(1, 4 if W > 14 else 3):
        for j in range(3 - (i > 2)):
            yy = fy + j - (i if flap else 0) // 2 + (0 if flap else i // 2)
            col.setdefault((left - i, yy), pal["f"])
            col.setdefault((right + i, yy), pal["f"])
    dh = {"sol": 6, "opus": 5}.get(species, 3)
    for i in range(1, dh + 1):
        col.setdefault((int(cx), int(oy) - i + 1), pal["F"] if i % 2 else pal["f"])
        if W % 2 == 0:
            col.setdefault((int(cx) + 1, int(oy) - i + 1), pal["f"])
    if species == "sol":                                      # anal fin below too
        for i in range(1, dh + 1):
            col.setdefault((int(cx), int(oy + body_h) + i - 1), pal["f"])
            col.setdefault((int(cx) + 1, int(oy + body_h) + i - 1), pal["f"])
    # eyes: big, white-ringed, looking at you, each with a 1 px highlight
    e = 3 if W >= 17 else 2
    ey = int(oy + body_h * 0.32)
    gap = max(2, int(body_w * 0.18))
    for side_, ex in ((-1, int(cx - gap - e + 1)), (1, int(cx + gap))):
        if blink:
            for i in range(e):
                col[(ex + i, ey + e - 1)] = INK
            continue
        for i in range(-1, e + 1):
            for j in range(-1, e + 1):
                if not (i in (-1, e) and j in (-1, e)) and (ex + i, ey + j) in col:
                    col[(ex + i, ey + j)] = (250, 250, 250)
        for i in range(e):
            for j in range(e):
                col[(ex + i, ey + j)] = INK
        col[(ex + (e - 1 if side_ < 0 else 0), ey)] = (255, 255, 255)
    # mouth: a small round 'o'
    my = int(oy + body_h * 0.7)
    mw = 2 if W >= 16 else 1
    for i in range(mw):
        col[(int(cx) - (mw // 2) + i + (0 if W % 2 else 1), my)] = pal["m"]
        if mw == 2:
            col[(int(cx) - (mw // 2) + i + (0 if W % 2 else 1), my + 1)] = mix(pal["m"], (255, 120, 140), 0.5)
    if SPEC.get(species, {}).get("blush"):
        for bx in (int(cx - gap - e), int(cx + gap + e)):
            if (bx, my - 1) in col:
                col[(bx, my - 1)] = pal["c"]
    filled = set(col)
    for (x, y) in list(filled):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (x + dx, y + dy)
            if q not in filled and q not in col:
                col[q] = pal["o"]
    xs = [p[0] for p in col]
    ys = [p[1] for p in col]
    x0, y0 = min(xs), min(ys)
    img = Image.new("RGBA", (max(xs) - x0 + 1, max(ys) - y0 + 1), (0, 0, 0, 0))
    pp = img.load()
    for (x, y), c in col.items():
        pp[x - x0, y - y0] = tuple(rgb(c)) + (255,)
    return img


def front(species, ident, frame=0, look="side", blink=False):
    return _front_cached(species if species in SPEC or species == "luna" else "fish", ident["body"],
                         ident["pattern"], ident["fin"], ident["kind"], ident.get("sid", ""), (frame // 2) % 2,
                         look, blink)


def squash(img, f=0.55):
    """A turn frame: the fish seen end-on for an instant."""
    return img.resize((max(3, int(img.width * f)), img.height), Image.NEAREST)


_FRY: dict = {}


def fry(ident, frame=0):
    """A tiny fish (subagent): 6x4 with a tail and an eye. One image per colour and tail frame, shared."""
    k = (str(ident["body"]), frame % 2)
    im = _FRY.get(k)
    if im is None:
        if len(_FRY) > 400:
            _FRY.clear()
        im = _FRY[k] = _fry(ident, frame)
    return im


def _fry(ident, frame=0):
    c = rgb(ident["body"])
    lt = mix(c, (255, 255, 255), 0.35)
    o = mix(c, (10, 6, 24), 0.7)
    im = Image.new("RGBA", (8, 5), (0, 0, 0, 0))
    p = im.load()
    for x, y in ((2, 1), (3, 1), (4, 1), (5, 1), (1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 2), (2, 3), (3, 3),
                 (4, 3), (5, 3)):
        p[x, y] = lt + (255,) if y == 3 else c + (255,)
    for x, y in ((0, 1 + frame % 2), (0, 2 + frame % 2)):
        p[x, y] = c + (255,)
    p[5, 1] = INK + (255,)
    for x, y in ((2, 0), (3, 0), (4, 0), (5, 0), (6, 1), (7, 2), (6, 3), (1, 1), (1, 3), (2, 4), (3, 4), (4, 4),
                 (5, 4)):
        if p[x, y][3] == 0:
            p[x, y] = o + (255,)
    return im


# ------------------------------------------------------------------ 5x7 pixel font (A-Z 0-9 and a few signs)
_F = {
    "A": ".###.|#...#|#...#|#####|#...#|#...#|#...#", "B": "####.|#...#|####.|#...#|#...#|#...#|####.",
    "C": ".###.|#...#|#....|#....|#....|#...#|.###.", "D": "####.|#...#|#...#|#...#|#...#|#...#|####.",
    "E": "#####|#....|####.|#....|#....|#....|#####", "F": "#####|#....|####.|#....|#....|#....|#....",
    "G": ".###.|#....|#....|#..##|#...#|#...#|.###.", "H": "#...#|#...#|#####|#...#|#...#|#...#|#...#",
    "I": "###|.#.|.#.|.#.|.#.|.#.|###", "J": "....#|....#|....#|....#|#...#|#...#|.###.",
    "K": "#...#|#..#.|###..|#..#.|#...#|#...#|#...#", "L": "#....|#....|#....|#....|#....|#....|#####",
    "M": "#...#|##.##|#.#.#|#...#|#...#|#...#|#...#", "N": "#...#|##..#|#.#.#|#..##|#...#|#...#|#...#",
    "O": ".###.|#...#|#...#|#...#|#...#|#...#|.###.", "P": "####.|#...#|####.|#....|#....|#....|#....",
    "Q": ".###.|#...#|#...#|#...#|#...#|#..#.|.##.#", "R": "####.|#...#|####.|#...#|#...#|#...#|#...#",
    "S": ".####|#....|.###.|....#|....#|#...#|.###.", "T": "#####|..#..|..#..|..#..|..#..|..#..|..#..",
    "U": "#...#|#...#|#...#|#...#|#...#|#...#|.###.", "V": "#...#|#...#|#...#|#...#|#...#|.#.#.|..#..",
    "W": "#...#|#...#|#...#|#...#|#.#.#|##.##|#...#", "X": "#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#",
    "Y": "#...#|#...#|.#.#.|..#..|..#..|..#..|..#..", "Z": "#####|....#|...#.|..#..|.#...|#....|#####",
    "0": ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.", "1": "..#..|.##..|..#..|..#..|..#..|..#..|.###.",
    "2": ".###.|#...#|....#|..##.|.#...|#....|#####", "3": ".###.|#...#|....#|..##.|....#|#...#|.###.",
    "4": "...#.|..##.|.#.#.|#..#.|#####|...#.|...#.", "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.", "7": "#####|....#|...#.|..#..|..#..|..#..|..#..",
    "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.", "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    "%": "##..#|##.#.|...#.|..#..|.#...|.#.##|#..##", "!": "#|#|#|#|#|.|#", "?": ".###.|#...#|....#|...#.|..#..|.....|..#..",
    "-": ".....|.....|.....|####.|.....|.....|.....", "+": ".....|..#..|..#..|#####|..#..|..#..|.....",
    ".": ".|.|.|.|.|.|#", "/": "....#|...#.|...#.|..#..|.#...|.#...|#....", ":": ".|#|.|.|.|#|.",
    " ": "...|...|...|...|...|...|...",
}
FONT = {k: v.split("|") for k, v in _F.items()}
# a 3x5 digit font for tight spots
_F3 = {"0": "###|#.#|#.#|#.#|###", "1": ".#.|##.|.#.|.#.|###", "2": "###|..#|###|#..|###", "3": "###|..#|.##|..#|###",
       "4": "#.#|#.#|###|..#|..#", "5": "###|#..|###|..#|###", "6": "###|#..|###|#.#|###", "7": "###|..#|.#.|.#.|.#.",
       "8": "###|#.#|###|#.#|###", "9": "###|#.#|###|..#|###", "%": "#.#|..#|.#.|#..|#.#", "C": "###|#..|#..|#..|###",
       "P": "##.|#.#|##.|#..|#..", "U": "#.#|#.#|#.#|#.#|###", "R": "##.|#.#|##.|#.#|#.#", "A": ".#.|#.#|###|#.#|#.#",
       "M": "#.#|###|###|#.#|#.#", "G": "###|#..|#.#|#.#|###", "-": "...|...|###|...|...", " ": "...|...|...|...|..."}
FONT3 = {k: v.split("|") for k, v in _F3.items()}


def text_width(s, scale=1, small=False):
    f = FONT3 if small else FONT
    w = 0
    for ch in str(s).upper():
        g = f.get(ch) or f.get(" ")
        w += (len(g[0]) + 1) * scale
    return max(0, w - scale)


def draw_text(img, x, y, s, color, scale=1, shadow=None, small=False):
    """Pixel text with its top-left at (x, y). shadow: a colour drawn 1 px (x scale) down-right first."""
    f = FONT3 if small else FONT
    d = ImageDraw.Draw(img)
    a = (255,) if img.mode == "RGBA" else ()
    for pass_, col in (((1, shadow),) if shadow is not None else ()) + ((0, color),):
        cx = x
        for ch in str(s).upper():
            g = f.get(ch) or f.get(" ")
            for ry, row in enumerate(g):
                for rx, b in enumerate(row):
                    if b == "#":
                        px0, py0 = cx + rx * scale + pass_ * scale, y + ry * scale + pass_ * scale
                        d.rectangle((px0, py0, px0 + scale - 1, py0 + scale - 1), fill=tuple(rgb(col)) + a)
            cx += (len(g[0]) + 1) * scale


# ------------------------------------------------------------------ speech bubble, progress, marking flag
def bubble(text, color=(20, 20, 30), fill=(250, 250, 245), tail="left"):
    """A small speech bubble with one word (5x7 font), tail at the bottom left or right."""
    tw = text_width(text)
    w, h = tw + 6, 11
    im = Image.new("RGBA", (w + 2, h + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    o = (20, 16, 30, 255)
    d.rectangle((1, 0, w, h - 1), fill=o)
    d.rectangle((0, 1, w + 1, h - 2), fill=o)
    d.rectangle((1, 1, w, h - 2), fill=tuple(rgb(fill)) + (255,))
    tx = 3 if tail == "left" else w - 4
    for i, (a, b) in enumerate(((0, 2), (0, 1), (0, 0))):
        yy = h - 1 + i
        xs = (tx + a, tx + b) if tail == "left" else (tx - b, tx - a)
        d.rectangle((xs[0] - 1, yy, xs[1] + 1, yy), fill=o)
        if i < 2:
            d.rectangle((xs[0], yy - 1 if i == 0 else yy, xs[1], yy - 1 if i == 0 else yy),
                        fill=tuple(rgb(fill)) + (255,))
    draw_text(im, 4, 2, text, color)
    return im


def progress_bar(value, color, width=18):
    """A little health-bar style progress strip that floats under a fish."""
    im = Image.new("RGBA", (width, 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, width - 1, 3), fill=(16, 12, 24, 255))
    n = int(round((width - 2) * max(0.0, min(1.0, value))))
    if n:
        d.rectangle((1, 1, n, 2), fill=tuple(rgb(color)) + (255,))
    return im


def marking(grid, palette=None, base_palette=None):
    """An agent's own 16x16 face as an 8x8 flag (every other cell), with a pole: its marking, not its body."""
    pal = dict(base_palette or SPRITE_PAL, **(palette or {}))
    im = Image.new("RGBA", (11, 13), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 0, 12), fill=(120, 86, 50, 255))
    d.rectangle((1, 0, 10, 9), fill=(20, 16, 30, 255))
    for y in range(8):
        for x in range(8):
            ch = (grid[y * 2] if y * 2 < len(grid) else "")[x * 2:x * 2 + 1] or "."
            if ch == "." and y * 2 + 1 < len(grid):
                ch = grid[y * 2 + 1][x * 2 + 1:x * 2 + 2] or "."
            c = pal.get(ch)
            d.point((2 + x, 1 + y), fill=tuple(rgb(c)) + (255,) if c else (240, 240, 235, 255))
    return im


# ------------------------------------------------------------------ fixtures (64x64 RGBA overlays on the tank tile)
def _rect(d, box, c, a=255):
    d.rectangle(box, fill=tuple(rgb(c)) + (a,))


LAMP_HOT = (32, 27)          # bulb centre in the lamp key (key px); the tank lights the water in a cone below it


@lru_cache(maxsize=16)
def lamp(glow_phase=0):
    """The tank lamp (key 1): a cord, a green enamel shade, a warm bulb and its halo. The light cone in the water
    is drawn by the tank so it continues through the bar into the next screen."""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _rect(d, (30, 0, 33, 9), "#23232c")
    _rect(d, (31, 0, 31, 9), "#4a4a58")
    _rect(d, (28, 8, 35, 11), "#3c3c48")
    halo = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    for r, a in ((19, 40), (14, 70), (10, 110)):
        hd.ellipse((32 - r, 27 - r * 0.7, 32 + r, 27 + r * 0.9), fill=(255, 236, 160, a + glow_phase * 6))
    im.alpha_composite(halo)
    d = ImageDraw.Draw(im)
    for y in range(11, 25):                      # the shade: a cone, darker inside the rim
        t = (y - 11) / 13
        half = int(round(6 + t * 12))
        _rect(d, (32 - half, y, 31 + half, y), "#2f6b4a")
        _rect(d, (32 - half, y, 32 - half, y), "#1b3f2c")
        _rect(d, (31 + half, y, 31 + half, y), "#1b3f2c")
        if y < 18:
            _rect(d, (33 - half + 2, y, 33 - half + 3, y), "#6fb88a")
    _rect(d, (14, 24, 49, 25), "#8fd3a6")
    _rect(d, (13, 25, 50, 25), "#1b3f2c")
    for x in range(24, 40):                      # the bulb peeking under the rim
        h = int(round(4 * math.sqrt(max(0.0, 1 - ((x - 31.5) / 8) ** 2))))
        _rect(d, (x, 26, x, 26 + h), (255, 246, 196))
    _rect(d, (29, 26, 34, 27), (255, 255, 255))
    return im


def buoy(surf_y, bob=0, frozen=False):
    """Scrawl (key 5): a red record float on the surface, its line hanging into the water, REC underneath."""
    return _buoy(int(surf_y), int(bob), bool(frozen))


@lru_cache(maxsize=64)
def _buoy(surf_y, bob, frozen):
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cy = max(14, surf_y + 1) + bob
    r = 12
    _rect(d, (31, cy + 6, 31, 50), (220, 228, 236), 200)            # the line
    _rect(d, (30, 50, 32, 51), (200, 200, 210))
    _rect(d, (32, 51, 32, 53), (200, 200, 210))
    _rect(d, (30, 53, 31, 53), (200, 200, 210))
    _rect(d, (31, cy - r - 6, 32, cy - r), "#f4f4f4")                # antenna
    _rect(d, (31, cy - r - 8, 32, cy - r - 6), "#e0302a")
    d.ellipse((32 - r - 1, cy - r - 1, 31 + r + 1, cy + 7), fill=(40, 10, 14, 255))
    d.chord((32 - r, cy - r, 31 + r, cy + r), 180, 360, fill=(224, 48, 42, 255))      # red dome = record
    d.rectangle((32 - r, cy - 1, 31 + r, cy + 2), fill=(250, 250, 250, 255))           # white band
    d.chord((32 - r + 2, cy - 4, 31 + r - 2, cy + 6), 0, 180, fill=(196, 206, 222, 255))   # below the water
    d.ellipse((24, cy - 9, 29, cy - 6), fill=(255, 150, 140, 255))   # gloss
    _rect(d, (25, cy - 8, 26, cy - 8), (255, 255, 255))
    if not frozen:                                                   # little ripples where it floats
        for dx in (-r - 5, -r - 3, r + 2, r + 4):
            _rect(d, (32 + dx, surf_y + 1, 33 + dx, surf_y + 1), (210, 236, 255), 220)
    lab = "REC"
    w = text_width(lab) + 6
    x0 = 32 - w // 2
    _rect(d, (x0, 55, x0 + w - 1, 63), (20, 12, 16), 235)
    _rect(d, (x0 + 1, 56, x0 + 2, 57), "#e0302a")
    draw_text(im, x0 + 4, 56, lab, (255, 255, 255))
    return im


GAUGE_ROWS = (("C", "#4fd8ff"), ("R", "#c38bff"), ("G", "#6fe36a"))


def _level_col(base, v):
    if v is None:
        return (90, 90, 100)
    if v >= 90:
        return (255, 80, 70)
    if v >= 75:
        return (255, 176, 48)
    return rgb(base)


@lru_cache(maxsize=256)
def gauge(cpu, ram, gpu):
    """PC resources (key 11) as the tank's filter: an intake pipe, a slate box and three sight glasses whose
    liquid level is the load, with the number on each. Values are ints (percent) or None."""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _rect(d, (44, 0, 50, 9), "#59646f")                               # intake pipe up into the water
    _rect(d, (45, 0, 46, 9), "#8c99a5")
    _rect(d, (42, 0, 52, 1), "#3a434c")
    _rect(d, (12, 3, 16, 9), "#59646f")                               # outlet nozzle (bubbles come out here)
    _rect(d, (13, 3, 13, 9), "#8c99a5")
    _rect(d, (11, 2, 17, 3), "#3a434c")
    _rect(d, (1, 8, 62, 61), "#1a1f26")                               # box
    _rect(d, (2, 9, 61, 60), "#3a4552")
    _rect(d, (2, 9, 61, 9), "#6a7886")
    _rect(d, (2, 9, 2, 60), "#55626f")
    for bx, by in ((4, 11), (59, 11), (4, 58), (59, 58)):
        _rect(d, (bx, by, bx, by), "#a7b3be")
    for i, (lab, base) in enumerate(GAUGE_ROWS):
        v = (cpu, ram, gpu)[i]
        y0 = 12 + i * 16
        c = _level_col(base, v)
        draw_text(im, 4, y0 + 4, lab, c, shadow=(10, 10, 14))
        _rect(d, (11, y0, 58, y0 + 13), "#0b1218")
        _rect(d, (12, y0 + 1, 57, y0 + 12), "#13212b")
        if v is not None:
            w = int(round(46 * max(0, min(100, v)) / 100))
            if w > 0:
                _rect(d, (12, y0 + 1, 11 + w, y0 + 12), mix(c, (0, 0, 0), 0.25))
                _rect(d, (12, y0 + 1, 11 + w, y0 + 2), mix(c, (255, 255, 255), 0.2))
        txt = "--" if v is None else str(int(round(v)))
        _num(im, 56 - text_width(txt, 2), y0, txt)
    return im


def _num(im, x, y, txt):
    """Big digits (5x7 at 2x = 10x14) with a 1 px dark rim so they read over any liquid."""
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw_text(lay, x, y, txt, (255, 255, 255), scale=2)
    a = lay.split()[3]
    from PIL import ImageFilter
    rim = a.filter(ImageFilter.MaxFilter(3))
    dark = Image.new("RGBA", im.size, (8, 10, 16, 255))
    dark.putalpha(rim)
    im.alpha_composite(dark)
    im.alpha_composite(lay)


def chest(n, prio=1, phase=0):
    return _chest(int(n), int(prio), int(phase) % 4)


@lru_cache(maxsize=64)
def _chest(n, prio, phase):
    """The card key (key 15): a treasure chest on the sand. Closed and quiet at 0 cards; open and glowing with the
    count above it when there are cards (a red gem when one is urgent)."""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    wood, dark, band, edge = (138, 90, 43), (92, 56, 26), (232, 184, 48), (40, 24, 14)
    if n <= 0:
        wood, band = mix(wood, (70, 80, 110), 0.25), mix(band, (70, 80, 110), 0.35)
    x0, x1, yb, yt = 11, 52, 57, 40
    if n > 0:                                                    # light pouring out of the open chest
        glow = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.polygon([(x0 + 4, yt), (x1 - 4, yt), (x1 + 8, 0), (x0 - 8, 0)], fill=(255, 226, 120, 46 + 10 * phase))
        im.alpha_composite(glow)
        d = ImageDraw.Draw(im)
        # the lid, swung back
        _rect(d, (x0 + 1, yt - 12, x1 - 1, yt - 2), edge)
        _rect(d, (x0 + 2, yt - 11, x1 - 2, yt - 3), (64, 30, 22))
        _rect(d, (x0 + 2, yt - 11, x1 - 2, yt - 10), dark)
        _rect(d, (x0 + 6, yt - 11, x0 + 8, yt - 3), band)
        _rect(d, (x1 - 8, yt - 11, x1 - 6, yt - 3), band)
        # coins
        for i in range(x0 + 2, x1 - 1):
            h = 2 + ((i * 7) % 3)
            _rect(d, (i, yt - h + 1, i, yt), (255, 214, 64) if (i + phase) % 4 else (255, 250, 200))
        _rect(d, (x0 + 1, yt, x1 - 1, yt + 1), edge)
    else:
        for y in range(yt - 9, yt + 1):                           # the domed lid, shut
            t = (y - (yt - 9)) / 9
            inset = int(round(4 * (1 - t) ** 2))
            _rect(d, (x0 + inset, y, x1 - inset, y), edge if y == yt - 9 else wood)
            _rect(d, (x0 + inset, y, x0 + inset, y), edge)
            _rect(d, (x1 - inset, y, x1 - inset, y), edge)
        _rect(d, (x0 + 6, yt - 8, x0 + 8, yt), band)
        _rect(d, (x1 - 8, yt - 8, x1 - 6, yt), band)
        _rect(d, (x0 + 1, yt, x1 - 1, yt), dark)
    _rect(d, (x0, yt + 1, x1, yb), edge)                          # the box
    _rect(d, (x0 + 1, yt + 2, x1 - 1, yb - 1), wood)
    for yy in (yt + 7, yt + 12):
        _rect(d, (x0 + 1, yy, x1 - 1, yy), dark)
    _rect(d, (x0 + 6, yt + 2, x0 + 8, yb - 1), band)
    _rect(d, (x1 - 8, yt + 2, x1 - 6, yb - 1), band)
    _rect(d, (28, yt + 1, 35, yt + 9), edge)                       # lock
    _rect(d, (29, yt + 2, 34, yt + 8), band)
    gem = (230, 40, 50) if prio >= 2 else (40, 24, 14)
    _rect(d, (31, yt + 4, 32, yt + 6), gem)
    if n > 0:
        txt = str(min(n, 99))
        sc = 3 if n < 10 else 2
        tw = text_width(txt, sc)
        lay = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw_text(lay, 32 - tw // 2, 4 if sc == 3 else 8, txt, (255, 221, 64) if prio < 2 else (255, 120, 90), scale=sc)
        from PIL import ImageFilter
        rim = lay.split()[3].filter(ImageFilter.MaxFilter(3))
        dk = Image.new("RGBA", (64, 64), (40, 18, 6, 255))
        dk.putalpha(rim)
        im.alpha_composite(dk)
        im.alpha_composite(lay)
        for sx, sy in (((8 + phase * 11) % 52 + 6, 30 - phase * 3), ((40 + phase * 7) % 50 + 7, 26 - phase * 2)):
            _rect(d, (sx, sy - 1, sx, sy + 1), (255, 250, 210))
            _rect(d, (sx - 1, sy, sx + 1, sy), (255, 250, 210))
    return im


# Where each fixture's must-read pixels are (key px, x0 y0 x1 y1). Creatures and their bubbles never draw over these.
READABLE = {"lamp": (13, 0, 50, 31), "buoy": (18, 2, 46, 63), "gauge": (1, 8, 62, 61), "chest": (0, 0, 63, 63)}
