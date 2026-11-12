"""Clipboard key: what is on the clipboard now, the last 10 clips, paste as plain text.

    3: {live: {plugin: clipboard.face, interval: 0.5}, action: {plugin: clipboard.open}}

Face: the kind on top (TEXT / LINK / CODE / IMAGE / FILES / COLOUR / HIDDEN), the content in the middle
(first word, the link's site, a thumbnail, a colour swatch, the first file name) and the size below.
Each tick costs one GetClipboardSequenceNumber call; the clipboard is read only when that number changes.
Kinds by simple rules (classify): files and images by clipboard format; text that is one #hex / rgb() /
hsl() value is a colour, one URL is a link, several lines that look like code (brackets, `;`, `=`, indents,
keywords) are code, the rest is text.
Privacy: a clip whose owner set ExcludeClipboardContentFromMonitorProcessing, "Clipboard Viewer Ignore" or
CanIncludeInClipboardHistory = 0 (password managers do) shows as HIDDEN, is never read and never stored.
History: the last 10 clips, in memory only (daemon plugin_state), never written to disk.
Press = takeover (30 s, re-armed per press; physical b1 ends it): keys 1-10 = clips, newest first (key 1 =
now, gold frame); press one to make it the clipboard again. Key 11 = PLAIN: put the current clip back as
plain text only and press ctrl+v. Key 15 = CLOSE.
"""
from __future__ import annotations

import hashlib
import io
import re
import time
from urllib.parse import urlparse

from PIL import Image, ImageDraw

from m18lib import tiles as T
from m18lib import winclip

HISTORY = 10
TIMEOUT = 30.0
KIND_COL = {"text": T.WHITE, "link": T.AQUA, "code": T.GREEN, "image": T.GOLD, "files": "#ffaa00",
            "colour": "#ff55ff", "hidden": T.GREY, "empty": T.GREY}
_COLOUR = re.compile(r"^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}|"
                     r"rgba?\(\s*\d{1,3}%?\s*[, ]\s*\d{1,3}%?\s*[, ]\s*\d{1,3}%?\s*([,/]\s*[\d.]+%?\s*)?\)|"
                     r"hsla?\(\s*[\d.]+(deg)?\s*[, ]\s*[\d.]+%\s*[, ]\s*[\d.]+%\s*([,/]\s*[\d.]+%?\s*)?\))$")
_URL = re.compile(r"^(https?://|www\.)[^\s]+$", re.I)
_CODE_START = re.compile(r"^\s*(def |class |import |from \S+ import |function |const |let |var |#include|"
                         r"public |private |package |using |SELECT |INSERT |UPDATE |CREATE |<\?php|<!DOCTYPE|"
                         r"<\w+[\s>]|\$ |PS> |if \(|for \(|while \(|fn |func |#!/)", re.I)


def classify(text: str) -> str:
    s = text.strip()
    if not s:
        return "text"
    if "\n" not in s and _COLOUR.match(s):
        return "colour"
    if "\n" not in s and _URL.match(s):
        return "link"
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if _CODE_START.match(lines[0]) and (len(lines) > 1 or re.search(r"[(){};=]", s)):
        return "code"
    if len(lines) >= 2:
        codeish = sum(1 for ln in lines if re.search(r"[{};]\s*$|^\s{2,}\S|^\t|=>|==|\)\s*\{|^\s*[})\]]", ln))
        if codeish / len(lines) >= 0.4:
            return "code"
    return "text"


def colour_rgb(s: str):
    s = s.strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    nums = [float(x) for x in re.findall(r"[\d.]+", s)[:3]]
    if s.lower().startswith("hsl"):
        import colorsys
        r, g, b = colorsys.hls_to_rgb(nums[0] / 360 % 1, nums[2] / 100, nums[1] / 100)
        return tuple(int(v * 255) for v in (r, g, b))
    return tuple(max(0, min(255, int(v))) for v in nums)


def _human(n, unit):
    if n >= 10000:
        return f"{n / 1000:.0f}k {unit}"
    if n >= 1000:
        return f"{n / 1000:.1f}k {unit}"
    return f"{n} {unit}"


def make_entry(raw: dict) -> dict:
    """A clipboard read -> {kind, size, hint, ...content, h (content hash)}. Hidden clips carry nothing."""
    if raw.get("hidden"):
        return {"kind": "hidden", "size": "", "hint": "", "h": "hidden"}
    if raw.get("files"):
        fs = raw["files"]
        n = raw.get("file_count", len(fs))
        name = fs[0].replace("\\", "/").rstrip("/").split("/")[-1]
        return {"kind": "files", "files": fs, "size": f"{n} file{'s' if n != 1 else ''}", "hint": name,
                "h": "f:" + hashlib.sha1("\n".join(fs).encode()).hexdigest()}
    if raw.get("image") is not None:
        im = raw["image"]
        b = io.BytesIO()
        im.save(b, "PNG", compress_level=1)
        th = im.convert("RGB")
        th.thumbnail((192, 192))
        return {"kind": "image", "png": b.getvalue(), "thumb": th, "size": f"{im.width}x{im.height}", "hint": "",
                "text": raw.get("text"), "h": "i:" + hashlib.sha1(b.getvalue()).hexdigest()}
    text = raw.get("text")
    if text is None:
        return {"kind": "empty", "size": "", "hint": "", "h": "empty"}
    kind = classify(text)
    s = text.strip()
    if kind == "link":
        u = urlparse(s if "://" in s else "http://" + s)
        hint = (u.hostname or s).removeprefix("www.")
    elif kind == "colour":
        hint = s
    else:
        ws = s.split()
        hint = ws[0] if ws else ""
    lines = len(text.splitlines()) or 1
    size = _human(lines, "ln") if kind == "code" else _human(len(text), "ch")
    return {"kind": kind, "text": text, "size": size, "hint": hint,
            "h": "t:" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()}


# ------------------------------------------------------------------ drawing
def render(pen: T.Pen, e: dict, now=False) -> dict:
    img = pen.canvas()
    pen.plate(img, (1, 1, 62, 62))
    kind = e["kind"]
    col = KIND_COL.get(kind, T.WHITE)
    k = pen.k
    label = "COLOUR" if kind == "colour" else kind.upper()
    pen.word(img, 32, 9, label, col, top=1)
    if kind == "image" and e.get("thumb") is not None:
        t = T.cover(e["thumb"], 54 * k, 32 * k)
        img.paste(t, (5 * k, 16 * k))
    elif kind == "colour":
        c = colour_rgb(e["text"])
        d = ImageDraw.Draw(img)
        d.rectangle((6 * k, 16 * k, 58 * k - 1, 46 * k - 1), fill=(0, 0, 0, 255))
        d.rectangle((7 * k, 17 * k, 57 * k - 1, 45 * k - 1), fill=c + (255,))
    elif kind == "hidden":
        _lock(pen, img)
    elif kind == "empty":
        pen.word(img, 32, 31, "-", T.GREY, top=2)
    else:
        hint = e.get("hint") or ""
        if kind == "files":
            hint = hint.rsplit(".", 1)[0] if "." in hint[1:] else hint
        pen.word(img, 32, 31, hint, T.WHITE if kind != "code" else T.GREEN, top=2, lines=2, maxh=32)
    if e.get("size"):
        pen.word(img, 32, 55, e["size"], T.GREY, top=1)
    if now:
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, 64 * k - 1, 64 * k - 1), outline=T.rgb(T.GOLD) + (255,), width=2 * k)
    return pen.face(img)


def _lock(pen, img):
    """A small padlock in the middle (hidden clips)."""
    k = pen.k
    d = ImageDraw.Draw(img)
    g = T.rgb("#8b8b8b") + (255,)
    d.rectangle((24 * k, 28 * k, 40 * k - 1, 44 * k - 1), fill=g)
    d.rectangle((27 * k, 19 * k, 37 * k - 1, 22 * k - 1), fill=g)
    d.rectangle((27 * k, 19 * k, 30 * k - 1, 28 * k - 1), fill=g)
    d.rectangle((34 * k, 19 * k, 37 * k - 1, 28 * k - 1), fill=g)
    d.rectangle((31 * k, 33 * k, 33 * k - 1, 39 * k - 1), fill=(16, 0, 16, 255))


# ------------------------------------------------------------------ monitor
def _st(ctx) -> dict:
    return ctx.daemon.plugin_state.setdefault("clipboard", {"seq": None, "cur": None, "hist": []})


def poll(ctx) -> dict:
    """Read the clipboard if its sequence number moved; returns the current entry."""
    st = _st(ctx)
    n = winclip.seq()
    if n == st["seq"] and st["cur"] is not None:
        return st["cur"]
    try:
        e = make_entry(winclip.read())
    except OSError:                                 # busy: try again next tick
        return st["cur"] or {"kind": "empty", "size": "", "hint": "", "h": "empty"}
    st["seq"], st["cur"] = n, e
    st["face"] = None
    if e["kind"] not in ("hidden", "empty"):
        hist = [x for x in st["hist"] if x["h"] != e["h"]]
        st["hist"] = ([e] + hist)[:HISTORY]
    return e


def face(ctx, **_):
    st = _st(ctx)
    e = poll(ctx)
    pen = T.Pen(ctx.daemon)
    look = (id(pen.theme), json_opts(pen.opts))
    if st.get("face") is None or st.get("look") != look:   # same dict object while idle: no redraw
        st["face"], st["look"] = render(pen, e), look
    return st["face"]


def json_opts(o):
    import json
    return json.dumps(o, sort_keys=True, default=str)


def restore(e: dict):
    """Make an entry the clipboard again."""
    if e["kind"] == "files":
        winclip.set_files(e["files"])
    elif e["kind"] == "image":
        winclip.set_image(Image.open(io.BytesIO(e["png"])))
    else:
        winclip.set_text(e["text"])


def plain_text(e: dict):
    if e["kind"] == "files":
        return "\n".join(e["files"])
    if e["kind"] in ("text", "link", "code", "colour"):
        return e["text"]
    return e.get("text") or None


def takeover_faces(pen, cur, hist) -> tuple[dict, dict]:
    faces, byk = {}, {}
    items = list(hist)
    if cur and cur["kind"] == "hidden":
        faces[1] = render(pen, cur, now=True)
        items = items[:9]
        start = 2
    else:
        start = 1
    for i, e in enumerate(items[:HISTORY]):
        k = start + i
        if k > 10:
            break
        faces[k] = render(pen, e, now=(k == 1 and cur is not None and e["h"] == cur["h"]))
        byk[k] = e
    can_plain = cur is not None and cur["kind"] != "hidden" and plain_text(cur) is not None
    img = pen.canvas()
    pen.slot(img, "#2c5fb8" if can_plain else "#3a3a46", (1, 1, 62, 62))
    pen.word(img, 32, 22, "PASTE", T.WHITE if can_plain else T.GREY, top=2)
    pen.word(img, 32, 43, "PLAIN", T.WHITE if can_plain else T.GREY, top=2)
    faces[11] = pen.face(img)
    img = pen.canvas()
    pen.slot(img, "#4a4a55", (1, 1, 62, 62))
    pen.word(img, 32, 24, "X", T.WHITE, top=3)
    pen.word(img, 32, 47, "CLOSE", T.WHITE, top=1)
    faces[15] = pen.face(img)
    for k in range(1, 16):
        faces.setdefault(k, {"label_pos": "none"})
    return faces, byk


def open(ctx, **_):                                    # noqa: A001 - the action name in YAML
    from m18lib import keys as K
    d = ctx.daemon
    poll(ctx)
    st = _st(ctx)
    view = {}

    def build():
        faces, view["byk"] = takeover_faces(T.Pen(d), st["cur"], st["hist"])
        return faces

    def on_key(c2, k, down, t):
        if not down:
            return
        if k == 15:
            d.modal_end("clipboard: closed", tok=view["tok"])
        elif k == 11:
            cur = st["cur"]
            txt = plain_text(cur) if cur and cur["kind"] != "hidden" else None
            if txt is None:
                return
            d.modal_end("clipboard: paste", tok=view["tok"])
            if K.DRY:
                K._log(f"clipboard plain text ({len(txt)} ch)")
            else:
                winclip.set_text(txt)
            c2.run({"hotkey": "ctrl+v"})
        elif k in view["byk"]:
            e = view["byk"][k]
            if K.DRY:
                K._log(f"clipboard restore {e['kind']} {e['size']}")
            else:
                restore(e)
                time.sleep(0.05)
                poll(c2)
            d.modal_update(view["tok"], build(), TIMEOUT)
        else:
            d.modal_update(view["tok"], None, TIMEOUT)

    view["tok"] = ctx.modal(build(), on_key=on_key, timeout=TIMEOUT)
