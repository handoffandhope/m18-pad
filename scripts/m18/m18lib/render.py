"""Render key faces (64x64 on the M18) and a full-pad preview PNG.

Faces are drawn at 3x and downsampled so small text stays crisp on the LCD.
A face is a dict: bg, fg, label, label_pos, glyph (big text), emoji, icon
(image path), border, font_size -- or `image` (a PIL Image from a live plugin).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NATIVE = 64
SS = 3                      # supersample factor
S = NATIVE * SS

THEME = {"bg": "#16161e", "fg": "#e6e6f0", "label_color": "#b8b8cc"}

_FONT_CANDIDATES = {
    "regular": [r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "bold": [r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "emoji": [r"C:\Windows\Fonts\seguiemj.ttf",
              "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"],
}
_font_cache: dict = {}
ASSETS = Path(__file__).resolve().parent.parent / "assets"
_mdi: dict | None = None


def mdi_names() -> dict:
    global _mdi
    if _mdi is None:
        _mdi = json.loads((ASSETS / "mdi-names.json").read_text())
    return _mdi


def mdi_codepoint(name: str):
    return mdi_names().get(name.strip().lower().removeprefix("mdi-"))


def font(kind: str, size: int, override: str | None = None):
    key = (kind, size, override)
    if key in _font_cache:
        return _font_cache[key]
    paths = ([override] if override else []) + _FONT_CANDIDATES.get(kind, [])
    f = None
    for p in paths:
        if p and os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                break
            except OSError:
                continue
    if f is None:
        f = ImageFont.load_default(size=size)
    _font_cache[key] = f
    return f


def _hex(c: str) -> str:
    c = c if c.startswith("#") else "#" + c
    if len(c) == 4:
        c = "#" + "".join(ch * 2 for ch in c[1:])
    return c


def _fit(draw, text, kind, max_w, start, minimum=10, override=None):
    size = start
    while size > minimum:
        f = font(kind, size, override)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font(kind, minimum, override)


def _wrap2(draw, text, kind, max_w, size, override=None):
    """Split a label onto two lines if it doesn't fit on one."""
    f = font(kind, size, override)
    if draw.textlength(text, font=f) <= max_w or " " not in text:
        return [text]
    words = text.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        w = max(draw.textlength(a, font=f), draw.textlength(b, font=f))
        if best is None or w < best[0]:
            best = (w, [a, b])
    return best[1]


_THEMES: dict = {}
_THEME_PATHS: dict = {}


def theme_path(settings: dict | None, base_dir: Path | None) -> tuple[Path | None, dict]:
    """settings.theme is a name or {name, ...options}; theme code is themes/<name>/theme.py."""
    t = (settings or {}).get("theme")
    if not t:
        return None, {}
    # Called once per key per redraw: the path lookup is cached for a second (it was 68 % of an
    # animated page's redraw time, profiled 2026-11-11).
    ck = (json.dumps(t, sort_keys=True, default=str), str(base_dir))
    hit = _THEME_PATHS.get(ck)
    now = time.monotonic()
    if hit and now - hit[0] < 1.0:
        return hit[1], dict(hit[2])
    if isinstance(t, str):
        name, opts = t, {}
    else:
        name, opts = t.get("name"), {k: v for k, v in t.items() if k != "name"}
    roots = ([base_dir / "themes"] if base_dir else []) + [Path(__file__).resolve().parent.parent / "themes"]
    found = None
    for root in roots:
        p = root / str(name) / "theme.py"
        if p.exists():
            found = p
            break
    _THEME_PATHS[ck] = (now, found, opts)
    return found, dict(opts)


def load_theme(settings: dict | None, base_dir: Path | None):
    """(module, options) for the configured theme, re-imported when theme.py changes."""
    p, opts = theme_path(settings, base_dir)
    if not p:
        return None, opts
    m = p.stat().st_mtime
    c = _THEMES.get(str(p))
    if not c or c[0] != m:
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"m18theme_{p.parent.name}", p)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"[render] theme {p}: {e}", file=sys.stderr)
            return None, opts
        c = _THEMES[str(p)] = (m, mod)
    return c[1], opts


def theme_stamp(settings: dict | None, base_dir: Path | None):
    """Changes whenever the theme, its options or its code change (part of every face hash)."""
    p, opts = theme_path(settings, base_dir)
    if not p:
        return None
    try:
        return [str(p), p.stat().st_mtime, json.dumps(opts, sort_keys=True, default=str)]
    except OSError:
        return None


def render_face(face: dict, base_dir: Path | None = None, settings: dict | None = None,
                size: int = NATIVE, key: int | None = None) -> Image.Image:
    """Draw one key. With a theme (settings.theme) its hooks take over:
    backdrop(key, face, S, opts) -> background image; ink(img, mask, role, fill, face, key, opts)
    paints icon/glyph/label shapes from a mask; finish(img, face, key, opts) adds final touches.
    Faces with `_plain` (dark mode) skip the theme."""
    settings = settings or {}
    theme, topts = (None, {}) if face.get("_plain") else load_theme(settings, base_dir)
    pal = getattr(theme, "PALETTE", {}) if theme else {}
    tfonts = getattr(theme, "FONTS", {}) if theme else {}
    rs = getattr(theme, "RESAMPLE", Image.LANCZOS) if theme else Image.LANCZOS  # BOX keeps pixel art crisp
    lfo = tfonts.get("label") or settings.get("font")
    gfo = tfonts.get("glyph") or settings.get("font")
    if face.get("image") is not None:
        im = face["image"]
        if theme and im.mode == "RGBA" and hasattr(theme, "backdrop"):
            img = theme.backdrop(key, face, S, topts).convert("RGBA")
            img.alpha_composite(im.resize((S, S), Image.LANCZOS))
            img = img.convert("RGB")
            if hasattr(theme, "finish"):
                img = theme.finish(img, face, key, topts) or img
            return img.resize((size, size), rs)
        img = im.convert("RGB")
        return img.resize((size, size), Image.LANCZOS)

    bg = _hex(face.get("bg", pal.get("bg", settings.get("default_bg", THEME["bg"]))))
    fg = _hex(face.get("fg", pal.get("fg", settings.get("default_fg", THEME["fg"]))))
    lc = _hex(face.get("label_color", fg if "fg" in face else pal.get("label_color", THEME["label_color"])))
    if theme and hasattr(theme, "backdrop"):
        img = theme.backdrop(key, face, S, topts).convert("RGB")
    else:
        img = Image.new("RGB", (S, S), bg)
    d = ImageDraw.Draw(img)

    def ink(xy, text, fnt, fill, anchor, role):
        if theme and hasattr(theme, "ink"):
            m = Image.new("L", (S, S), 0)
            ImageDraw.Draw(m).text(xy, text, font=fnt, fill=255, anchor=anchor)
            theme.ink(img, m, role, fill, face, key, topts)
        else:
            d.text(xy, text, font=fnt, fill=fill, anchor=anchor)
    label = str(face.get("label", "") or "")
    pos = face.get("label_pos", "bottom" if (face.get("glyph") or face.get("emoji")
                                             or face.get("icon")) else "center")
    if pos == "none":
        label = ""
    pad = 10
    label_h = 0
    lines = []
    if label and pos in ("bottom", "top"):
        lsize = int(face.get("font_size", 13)) * SS
        lines = _wrap2(d, label, "bold", S - 2 * pad, lsize, lfo)
        lf = _fit(d, max(lines, key=len), "bold", S - 2 * pad, lsize, override=lfo)
        lh = lf.size + 4
        label_h = lh * len(lines) + 8

    # main visual area
    top = label_h if pos == "top" else 0
    area = (pad, top + pad, S - pad, S - (label_h if pos == "bottom" else 0) - pad)
    aw, ah = area[2] - area[0], area[3] - area[1]
    cx, cy = (area[0] + area[2]) // 2, (area[1] + area[3]) // 2

    icon = str(face.get("icon") or "")
    if icon.startswith("mdi:"):
        cp = mdi_codepoint(icon[4:])
        if cp is None:
            d.text((cx, cy), "?", font=font("bold", 60), fill="#ff5555", anchor="mm")
            print(f"[render] unknown icon {icon}; try `m18ctl icons <word>`", file=sys.stderr)
        else:
            mf = ImageFont.truetype(str(ASSETS / "mdi.ttf"), int(min(aw, ah) * 1.0))
            ink((cx, cy), chr(cp), mf, fg, "mm", "icon")
    elif icon:
        p = Path(os.path.expandvars(os.path.expanduser(str(face["icon"]))))
        if not p.is_absolute() and base_dir:
            p = base_dir / p
        try:
            ic = Image.open(p)
            if face.get("_frame") and getattr(ic, "n_frames", 1) > 1:
                ic.seek(int(face["_frame"]) % ic.n_frames)
            ic = ic.convert("RGBA")
            ic.thumbnail((aw, ah), Image.LANCZOS)
            img.paste(ic, (cx - ic.width // 2, cy - ic.height // 2), ic)
        except Exception as e:  # missing icon shouldn't kill the pad
            d.text((cx, cy), "?", font=font("bold", 60), fill="#ff5555", anchor="mm")
            print(f"[render] icon {p}: {e}", file=sys.stderr)
    elif face.get("emoji"):
        e = str(face["emoji"])
        # color-emoji fonts often only render at fixed sizes (Noto: 109); if the
        # emoji font can't draw it at all, fall back to a plain font (monochrome)
        drawn = False
        for kind, sz in (("emoji", min(aw, ah) - 6), ("emoji", 109), ("regular", min(aw, ah) - 6)):
            try:
                ef = font(kind, sz, settings.get("emoji_font") if kind == "emoji" else None)
                tmp = Image.new("RGBA", (S * 2, S * 2), (0, 0, 0, 0))
                ImageDraw.Draw(tmp).text((S, S), e, font=ef, embedded_color=True, anchor="mm", fill=fg)
                bbox = tmp.getbbox()
                if not bbox:
                    continue
                tmp = tmp.crop(bbox)
                tmp.thumbnail((aw, ah), Image.LANCZOS)
                img.paste(tmp, (cx - tmp.width // 2, cy - tmp.height // 2), tmp)
                drawn = True
                break
            except Exception:
                continue
        if not drawn:
            print(f"[render] no font could draw emoji {e!r}; use glyph/icon instead",
                  file=sys.stderr)
    elif face.get("glyph"):
        g = str(face["glyph"])
        gf = _fit(d, g, "bold", aw, min(ah, int(ah * (1.0 if len(g) <= 2 else 0.7))), override=gfo)
        ink((cx, cy), g, gf, fg, "mm", "glyph")

    if label and pos == "center":
        lsize = int(face.get("font_size", 15)) * SS
        lines = _wrap2(d, label, "bold", S - 2 * pad, lsize, lfo)
        lf = _fit(d, max(lines, key=len), "bold", S - 2 * pad, lsize, override=lfo)
        lh = lf.size + 6
        y0 = S // 2 - lh * (len(lines) - 1) // 2
        for i, ln in enumerate(lines):
            ink((S // 2, y0 + i * lh), ln, lf, fg, "mm", "label")
    elif lines:
        lf = _fit(d, max(lines, key=len), "bold", S - 2 * pad,
                  int(face.get("font_size", 13)) * SS, override=lfo)
        lh = lf.size + 4
        y0 = (S - label_h + 4) if pos == "bottom" else 6
        for i, ln in enumerate(lines):
            ink((S // 2, y0 + i * lh), ln, lf, lc, "ma", "label")

    if face.get("border"):
        d.rounded_rectangle((3, 3, S - 4, S - 4), radius=18, outline=_hex(face["border"]), width=9)
    if theme and hasattr(theme, "finish"):
        img = theme.finish(img, face, key, topts) or img
    return img.resize((size, size), rs)


_GIF_CACHE: dict = {}


def gif_timeline(face: dict, base_dir: Path | None) -> list[float] | None:
    """Cumulative end times (s) of an animated GIF icon's frames, or None if not animated."""
    icon = str(face.get("icon") or "")
    if not icon.lower().endswith(".gif"):
        return None
    p = Path(os.path.expandvars(os.path.expanduser(icon)))
    if not p.is_absolute() and base_dir:
        p = base_dir / p
    try:
        key = (str(p), p.stat().st_mtime)
    except OSError:
        return None
    if key not in _GIF_CACHE:
        ends, t = [], 0.0
        try:
            with Image.open(p) as im:
                for i in range(getattr(im, "n_frames", 1)):
                    im.seek(i)
                    t += max(int(im.info.get("duration", 100) or 100), 20) / 1000
                    ends.append(t)
        except Exception:
            ends = []
        _GIF_CACHE[key] = ends if len(ends) > 1 else None
    return _GIF_CACHE[key]


def face_hash(face: dict) -> str:
    rest = {k: v for k, v in face.items() if k not in ("image", "frames")}
    h = hashlib.md5(json.dumps(rest, sort_keys=True, default=str).encode())
    if face.get("image") is not None and "_fid" not in face:   # frames hash by identity
        h.update(face["image"].tobytes())
    return h.hexdigest()


def render_preview(faces: dict[int, dict], page: str, base_dir: Path, settings: dict,
                   rgb: str | None, out: Path, pressed: int | None = None,
                   buttons: dict[int, str] | None = None) -> Path:
    """Full-pad picture: 5x3 LCD grid + 3 physical buttons, ring color as outline."""
    k, gap, margin = 128, 18, 40
    w = margin * 2 + 5 * k + 4 * gap
    h = margin * 2 + 3 * k + 2 * gap + 110 + 40
    img = Image.new("RGB", (w, h), "#0b0b10")
    d = ImageDraw.Draw(img)
    ring = _hex(rgb) if rgb and rgb != "off" else "#333344"
    d.rounded_rectangle((6, 6, w - 7, h - 7), radius=36, outline=ring, width=8)
    d.text((margin, 14), f"page: {page}", font=font("bold", 20), fill="#9aa0b5")
    for idx in range(1, 16):
        r, c = divmod(idx - 1, 5)
        x = margin + c * (k + gap)
        y = margin + 16 + r * (k + gap)
        face = faces.get(idx)
        if face is None and theme_path(settings, base_dir)[0]:
            face = {}                                   # themed: empty keys show the backdrop
        tile = render_face(face, base_dir, settings, size=k, key=idx) if face is not None \
            else Image.new("RGB", (k, k), "#050507")
        img.paste(tile, (x, y))
        d.rounded_rectangle((x - 2, y - 2, x + k + 1, y + k + 1), radius=10,
                            outline="#ffd400" if pressed == idx else "#2a2a36",
                            width=4 if pressed == idx else 2)
        d.text((x + 5, y + 3), str(idx), font=font("regular", 14), fill="#6b6b80")
    by = margin + 16 + 3 * k + 2 * gap + 24
    bw = (w - 2 * margin - 2 * gap) // 3
    for i, bk in enumerate((16, 17, 18)):
        x = margin + i * (bw + gap)
        lab = (buttons or {}).get(bk) or "(unset)"
        d.rounded_rectangle((x, by, x + bw, by + 70), radius=35,
                            fill="#20202a", outline="#ffd400" if pressed == bk else "#3a3a48", width=3)
        f = _fit(d, f"b{i+1}: {lab}", "bold", bw - 24, 20)
        d.text((x + bw // 2, by + 35), f"b{i+1}: {lab}", font=f, fill="#d0d0e0", anchor="mm")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
