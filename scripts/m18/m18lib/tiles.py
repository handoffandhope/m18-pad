"""Drawing kit for plugin tiles that must read at 64x64 in any look.

Pen(daemon) picks one of two modes:
  pixel   the theme offers a pixel font (hillside): draw on the 64 px grid, return RGBA at 192 (NEAREST)
          so the theme's backdrop shows through transparent pixels and edges stay crisp;
  smooth  no theme, or a theme without a pixel font: draw at 192 with TrueType, return RGBA (themed)
          or RGB on the plain background.
Coordinates are always in 64-px key units. Used by plugins/cards.py, clip.py and clipboard.py.
"""
from __future__ import annotations

import re

from PIL import Image, ImageDraw

from m18lib import render as R

PLAIN_BG = "#16161e"
TEXT_SIZES = {1: 10, 2: 18, 3: 26}          # smooth-mode font sizes (key px) standing in for pixel scales 1-3

# accents shared by both looks (the hillside tooltip colours read well on the plain dark tiles too)
GOLD, WHITE, AQUA, GREY, RED, GREEN, BLUE = "#ffff55", "#ffffff", "#55ffff", "#aaaaaa", "#ff5555", "#55ff55", "#5599ff"


def rgb(h):
    h = str(h).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    a, b = rgb(a) if isinstance(a, str) else a, rgb(b) if isinstance(b, str) else b
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Pen:
    def __init__(self, daemon):
        s = daemon.cfg["settings"] if daemon.cfg else {}
        self.base, self.settings = daemon.base, s
        mod, self.opts = R.load_theme(s, daemon.base)
        self.theme = mod
        self.pixel = bool(mod and hasattr(mod, "draw_text") and hasattr(mod, "text_width"))
        self.k = 1 if self.pixel else 3
        self.pal = dict(getattr(mod, "PALETTE", {}) or {}) if mod else {}

    # ------------------------------------------------------------ canvas
    def canvas(self, fill=None):
        n = 64 * self.k
        if fill is not None:
            return Image.new("RGBA", (n, n), rgb(fill) + (255,))
        if self.theme:
            return Image.new("RGBA", (n, n), (0, 0, 0, 0))
        return Image.new("RGBA", (n, n), rgb(PLAIN_BG) + (255,))

    def face(self, img) -> dict:
        """Face dict for a live key or a modal key."""
        if self.k == 1:
            img = img.resize((192, 192), Image.NEAREST)
        if not self.theme:
            img = img.convert("RGB")
        return {"image": img}

    def _r(self, box):
        k = self.k
        return tuple(int(round(v * k)) + (k - 1 if i >= 2 else 0) for i, v in enumerate(box))

    def rect(self, img, box, fill, outline=None):
        ImageDraw.Draw(img).rectangle(self._r(box), fill=fill if not isinstance(fill, str) else rgb(fill),
                                      outline=rgb(outline) if isinstance(outline, str) else outline)

    # ------------------------------------------------------------ plates
    def plate(self, img, box=(0, 0, 63, 63), tint=None):
        """Dark text plate: the hillside item tooltip in pixel mode, a rounded dark card otherwise."""
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = box
        base = rgb(tint) if tint else (16, 0, 16)
        if self.pixel:
            d.rectangle((x0 + 1, y0, x1 - 1, y1), fill=base + (240,))
            d.rectangle((x0, y0 + 1, x1, y1 - 1), fill=base + (240,))
            d.line((x0 + 1, y0 + 1, x1 - 1, y0 + 1), fill=(80, 0, 255, 160))
            d.line((x0 + 1, y1 - 1, x1 - 1, y1 - 1), fill=(40, 0, 127, 160))
            d.line((x0 + 1, y0 + 2, x0 + 1, y1 - 2), fill=(60, 0, 190, 160))
            d.line((x1 - 1, y0 + 2, x1 - 1, y1 - 2), fill=(60, 0, 190, 160))
        else:
            k = self.k
            fill = base + (255,) if tint else ((30, 30, 42, 255) if not self.theme else (16, 16, 24, 225))
            d.rounded_rectangle((x0 * k + 3, y0 * k + 3, x1 * k + k - 4, y1 * k + k - 4), radius=8 * k // 3,
                                fill=fill)

    def slot(self, img, fill, box=(0, 0, 63, 63)):
        """A solid key: bevelled inventory slot (pixel) or a filled rounded tile (smooth)."""
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = box
        c = rgb(fill)
        if self.pixel:
            d.rectangle((x0, y0, x1, y1), fill=c + (255,))
            hi, lo = mix(c, "#ffffff", 0.45) + (255,), mix(c, "#000000", 0.55) + (255,)
            d.rectangle((x0, y0, x1, y0 + 2), fill=hi)
            d.rectangle((x0, y0, x0 + 2, y1), fill=hi)
            d.rectangle((x0, y1 - 2, x1, y1), fill=lo)
            d.rectangle((x1 - 2, y0, x1, y1), fill=lo)
        else:
            k = self.k
            d.rounded_rectangle((x0 * k + 3, y0 * k + 3, x1 * k + k - 4, y1 * k + k - 4), radius=10 * k // 3,
                                fill=c + (255,), outline=mix(c, "#ffffff", 0.35) + (255,), width=k * 2)

    # ------------------------------------------------------------ text
    def width(self, s, scale):
        return self._sw(s, scale if self.pixel else TEXT_SIZES[scale])

    def _sw(self, s, size):
        """Width in key units at `size`: a pixel scale (pixel mode) or a font height in key px (smooth)."""
        if self.pixel:
            return self.theme.text_width(s) * size
        f = R.font("bold", int(size * self.k))
        return ImageDraw.Draw(Image.new("L", (1, 1))).textlength(s, font=f) / self.k

    def fit(self, s, maxw=56, top=3):
        """Largest scale (top..1) at which s fits maxw, else None."""
        for sc in range(top, 0, -1):
            if self.width(s, sc) <= maxw:
                return sc
        return None

    def text(self, img, cx, cy, s, color, scale=2, shadow=True):
        """Text centred on (cx, cy) in key units."""
        self._st(img, cx, cy, s, color, scale if self.pixel else TEXT_SIZES[scale], shadow)

    def _st(self, img, cx, cy, s, color, size, shadow=True):
        col = rgb(color) if isinstance(color, str) else color
        if self.pixel:
            w = self.theme.text_width(s) * size
            self.theme.draw_text(img, int(round(cx - w / 2)), int(round(cy - 7 * size / 2)), s, col,
                                 shadow=shadow, scale=size)
            return
        k = self.k
        f = R.font("bold", int(size * k))
        d = ImageDraw.Draw(img)
        if shadow:
            d.text((cx * k + 2, cy * k + 2), s, font=f, fill=(0, 0, 0, 200), anchor="mm")
        d.text((cx * k, cy * k), s, font=f, fill=col + (255,), anchor="mm")

    def word(self, img, cx, cy, s, color, maxw=58, top=3, lines=1, maxh=58):
        """One word as large as fits, centred on (cx, cy). Sizes run from `top` (pixel scale 3/2/1; in
        smooth mode any font size down to scale 1's). Order: one line at a large size; two lines split at
        punctuation (m18-/macropad, github./com) at a large size; one line at the smallest size; two lines
        split anywhere at the smallest size; truncated with '.'. lines=1 forbids splitting."""
        mw = maxw if self.pixel else maxw - 4          # TrueType has no side bearing to spare
        if self.pixel:
            sizes = [sc for sc in (3, 2, 1) if sc <= top]
            hgt = lambda z: 7 * z                      # noqa: E731
        else:
            sizes = list(range(TEXT_SIZES[top], TEXT_SIZES[1] - 1, -1))
            hgt = lambda z: 0.72 * z                   # noqa: E731
        small, large = sizes[-1], [z for z in sizes if z > sizes[-1]]

        def two(z, splits):
            gap = (hgt(z) + 3) / 2
            if lines < 2 or 2 * gap + hgt(z) > maxh:
                return False
            for a, b in splits:
                if self._sw(a, z) <= mw and self._sw(b, z) <= mw:
                    self._st(img, cx, cy - gap, a, color, z)
                    self._st(img, cx, cy + gap, b, color, z)
                    return True
            return False
        for z in large:
            if self._sw(s, z) <= mw:
                self._st(img, cx, cy, s, color, z)
                return z
        punct = [(a, b) for a, b in _splits(s) if a[-1] in "-./_:@\\"]
        for z in large:
            if two(z, punct):
                return z
        if self._sw(s, small) <= mw:
            self._st(img, cx, cy, s, color, small)
            return small
        if two(small, punct + _splits(s)):
            return small
        while len(s) > 1 and self._sw(s + ".", small) > mw:
            s = s[:-1]
        self._st(img, cx, cy, s + ".", color, small)
        return small

    # ------------------------------------------------------------ icons and images
    def icon_face(self, icon, label="", key=8, bg=None) -> Image.Image:
        """A standard themed face (icon + label) as a 192 px RGB image, to draw on top of."""
        face = {"icon": icon}
        if label:
            face["label"] = label
        if bg:
            face["bg"] = bg
        return R.render_face(face, self.base, self.settings, size=192, key=key).convert("RGBA")

    def badge(self, img192, n, color=RED):
        """Count badge, top-right, on a 192 px image (any mode)."""
        txt = str(n) if n < 100 else "99"
        d = ImageDraw.Draw(img192)
        if self.pixel:
            w = self.theme.text_width(txt) + 5
            lay = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay)
            ld.rectangle((63 - w - 1, 0, 63, 12), fill=(0, 0, 0, 255))
            ld.rectangle((63 - w, 1, 62, 11), fill=rgb(color) + (255,))
            self.theme.draw_text(lay, 63 - w + 3, 3, txt, (255, 255, 255), shadow=True)
            big = lay.resize((192, 192), Image.NEAREST)
            base = img192.convert("RGBA")
            base.alpha_composite(big)
            return base
        f = R.font("bold", 30)
        w = int(d.textlength(txt, font=f)) + 22
        d.rounded_rectangle((192 - w - 4, 4, 188, 46), radius=20, fill=rgb(color), outline=(0, 0, 0), width=3)
        d.text((192 - 4 - w / 2, 25), txt, font=f, fill=(255, 255, 255), anchor="mm")
        return img192


_VOW = set("aeiouy")


def _splits(s: str) -> list[tuple[str, str]]:
    """Ways to break one word over two lines, best first: after punctuation, between two consonants,
    after a vowel, all weighted toward the middle."""
    out = []
    n = len(s)
    for i in range(1, n):
        a, b = s[i - 1], s[i]
        score = abs(i - n / 2)
        if a in "-./_:@\\":
            score -= 3
        elif a.isalpha() and b.isalpha() and a.lower() not in _VOW and b.lower() not in _VOW:
            score -= 0.6
        elif a.lower() in _VOW and b.isalpha() and b.lower() not in _VOW:
            score -= 0.3
        out.append((score, s[:i], s[i:]))
    return [(x, y) for _, x, y in sorted(out)]


def contain(im, w, h, bg=(0, 0, 0)):
    """Fit `im` inside w x h, letterboxed."""
    im = im.convert("RGB")
    s = min(w / im.width, h / im.height)
    nw, nh = max(1, int(im.width * s)), max(1, int(im.height * s))
    out = Image.new("RGB", (w, h), bg)
    out.paste(im.resize((nw, nh), Image.LANCZOS), ((w - nw) // 2, (h - nh) // 2))
    return out


def cover(im, w, h):
    """Fill w x h, centre-cropped."""
    im = im.convert("RGB")
    s = max(w / im.width, h / im.height)
    nw, nh = max(w, int(im.width * s + 0.5)), max(h, int(im.height * s + 0.5))
    im = im.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - w) // 2, (nh - h) // 2
    return im.crop((x, y, x + w, y + h))


# The pad has a black bar between neighbouring screens. A picture spread over several keys must lose the
# strip that falls behind each bar, or straight lines look broken where they cross it. GAP is that bar in
# key pixels. Measured from the user's LIDAR scan of the front (2026-11-11): lit screen ~13 mm, key pitch
# ~19.2 mm both ways, so a bar is ~30 px (range 26-38; screen glow blurs the edges). A by-eye test with
# diagonal lines drawn for 20-38 px could not tell them apart, so a few px either way does not matter.
GAP = 30


def block_size(cols, rows, gap=None):
    """Pixel size of a picture that spans cols x rows keys, bars included."""
    g = GAP if gap is None else gap
    return cols * 64 + (cols - 1) * g, rows * 64 + (rows - 1) * g


def split(im, cols, rows, gap=None):
    """A block_size(cols, rows) image -> {(col, row): 64 px tile}; the pixels behind the bars are dropped."""
    g = GAP if gap is None else gap
    p = 64 + g
    return {(c, r): im.crop((c * p, r * p, c * p + 64, r * p + 64)) for r in range(rows) for c in range(cols)}


_UNIT = re.compile(r"^(?:[kKMG]?i?B|KB|MB|GB|TB|s|ms|min|h|px|fps|%|x)$")


def tokens(text: str) -> list[str]:
    """Words for one-word-per-key display. A number keeps its unit ("48 MB" -> "48MB")."""
    out: list[str] = []
    for w in str(text).split():
        if out and _UNIT.match(w) and re.match(r"^[\d.,:]+$", out[-1]):
            out[-1] += w
        else:
            out.append(w)
    return out


def is_number(w: str) -> bool:
    return bool(re.match(r"^[+\-]?[\d.,:/]+[a-zA-Z%]{0,3}$", w))
