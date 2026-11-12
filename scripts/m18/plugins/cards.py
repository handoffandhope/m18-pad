"""The card key: one key shows the top card, pressing it takes the pad over to show that card.

    5: {live: {plugin: cards.key, interval: 2}, action: {plugin: cards.open}}

Cards come from feeds (POST /cards, ctx.cards.post; see references/cards.md). The key face:
  no cards     {} (the theme backdrop, or the plain background)
  one card     its image (cover-cropped) or its icon, with the title's first word as the label
  2+ cards     the same plus a red count badge top-right; priority 2 adds a red frame
Takeover (ctx.modal, 60 s, re-armed on every press; physical b1 always ends it):
  rows 1-2     the card: an image fills keys 1-3 + 6-8 (3x2 block) and the words go on 4, 5, 9, 10;
               with an icon, key 1 is the icon and the words fill the other nine keys;
               otherwise the words fill all ten. One word or number per key, as large as fits:
               title gold, lines white, numbers aqua. Too many words: the last key shows "...".
  row 3        the card's actions on keys 11-14 (green / red / blue by colour, or by label), key 15 =
               NEXT (n/m) with 2+ cards, else CLOSE. An action resolves the card (removed, event on
               /events, feed handler runs) and the takeover moves to the next card or closes.
               NEXT and CLOSE leave cards alone.
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from m18lib import tiles as T

TIMEOUT = 60.0
GOOD = ("keep", "ok", "yes", "approve", "accept", "open", "allow", "done", "save", "go")
BAD = ("delete", "remove", "discard", "deny", "no", "reject", "trash", "stop", "kill", "cancel")
COL_GOOD, COL_BAD, COL_OTHER, COL_NAV = "#2f8f3a", "#b3261e", "#2c5fb8", "#4a4a55"
_IMG: dict = {}


def card_image(c):
    b = c.get("image_png_b64")
    if not b:
        return None
    k = (c["id"], c.get("updated"))
    if k not in _IMG:
        if len(_IMG) > 40:
            _IMG.clear()
        try:
            im = Image.open(io.BytesIO(base64.b64decode(b)))
            im.load()
            _IMG[k] = im.convert("RGB")
        except Exception:                                    # noqa: BLE001
            _IMG[k] = None
    return _IMG[k]


def action_color(a: dict) -> str:
    if a.get("color"):
        return a["color"]
    w = (a.get("id", "") + " " + a.get("label", "")).lower()
    if any(x in w.split() or w.startswith(x) for x in BAD):
        return COL_BAD
    if any(x in w.split() or w.startswith(x) for x in GOOD):
        return COL_GOOD
    return COL_OTHER


def first_word(s: str) -> str:
    ws = T.tokens(s)
    return ws[0] if ws else ""


# ------------------------------------------------------------------ the key face
def render_key(pen: T.Pen, cards: list[dict], key: int = 8) -> dict:
    if not cards:
        return {}
    c, n = cards[0], len(cards)
    im = card_image(c)
    if im is not None:
        img = pen.canvas()
        thumb = T.cover(im, 60 * pen.k, 60 * pen.k)
        img.paste(thumb, (2 * pen.k, 2 * pen.k))
        pen.plate(img, (0, 49, 63, 63))
        pen.word(img, 32, 56, first_word(c["title"]).upper(), T.GOLD, maxw=58, top=1)
        out = pen.face(img)["image"].convert("RGBA")
    elif c.get("icon"):
        out = pen.icon_face(c["icon"], first_word(c["title"]), key=key)
    else:
        img = pen.canvas()
        pen.plate(img, (1, 1, 62, 62))
        ws = T.tokens(c["title"])
        pen.word(img, 32, 24 if len(ws) > 1 else 32, ws[0] if ws else "?", T.GOLD, maxw=58)
        if len(ws) > 1:
            pen.word(img, 32, 46, ws[1] + (" .." if len(ws) > 2 else ""), T.WHITE, maxw=58, top=2)
        out = pen.face(img)["image"].convert("RGBA")
    if c.get("priority", 1) >= 2:
        from PIL import ImageDraw
        ImageDraw.Draw(out).rectangle((0, 0, 191, 191), outline=T.rgb(T.RED) + (255,), width=6)
    if n >= 2:
        out = pen.badge(out, n)
    return {"image": out if pen.theme else out.convert("RGB")}


def key(ctx, **_):
    """Live face of the card key."""
    return render_key(T.Pen(ctx.daemon), ctx.cards.list(), ctx.key or 8)


# ------------------------------------------------------------------ the takeover
def _word_face(pen, w, color):
    img = pen.canvas()
    pen.plate(img, (1, 1, 62, 62))
    pen.word(img, 32, 32, w, color, top=3, lines=2)
    return pen.face(img)


def _action_face(pen, label, color):
    img = pen.canvas()
    pen.slot(img, color, (1, 1, 62, 62))
    ws = label.split()
    if len(ws) == 1 or pen.fit(label, 54, 2) == 2:
        pen.word(img, 32, 32, label.upper(), T.WHITE, maxw=58, top=2)
    else:
        a, b = ws[0], " ".join(ws[1:])
        pen.word(img, 32, 22, a.upper(), T.WHITE, maxw=58, top=2)
        pen.word(img, 32, 43, b.upper(), T.WHITE, maxw=58, top=2)
    return pen.face(img)


def _nav_face(pen, i, n):
    img = pen.canvas()
    pen.slot(img, COL_NAV, (1, 1, 62, 62))
    if n > 1:
        pen.word(img, 32, 22, f"{i + 1}/{n}", T.AQUA, maxw=58, top=2)
        pen.word(img, 32, 44, "NEXT", T.WHITE, maxw=58, top=2)
    else:
        pen.word(img, 32, 24, "X", T.WHITE, maxw=58, top=3)
        pen.word(img, 32, 47, "CLOSE", T.WHITE, maxw=58, top=1)
    return pen.face(img)


def layout_words(c: dict, slots: list[int]) -> dict[int, tuple[str, str]]:
    """{key: (word, colour)}. The lines start on the next row when that still fits."""
    title = [(w, T.GOLD) for w in T.tokens(c["title"])]
    body = [(w, T.AQUA if T.is_number(w) else T.WHITE) for ln in c.get("lines", []) for w in T.tokens(ln)]
    row1 = [k for k in slots if k <= 5]
    seq = title + body
    gap = len(row1) - len(title)
    if body and 0 < gap and len(title) + gap + len(body) <= len(slots):
        seq = title + [None] * gap + body
    if len(seq) > len(slots):
        seq = seq[:len(slots) - 1] + [("...", T.GREY)]
    return {k: w for k, w in zip(slots, seq) if w}


def render_takeover(pen: T.Pen, c: dict, i: int, n: int) -> tuple[dict, dict]:
    """(faces {key: face}, actions {key: action id}) for card c, the i-th of n."""
    faces: dict[int, dict] = {}
    im = card_image(c)
    if im is not None:
        block = T.contain(im, *T.block_size(3, 2))
        for (col, row), t in T.split(block, 3, 2).items():
            faces[1 + row * 5 + col] = {"image": t}
        slots = [4, 5, 9, 10]
    elif c.get("icon"):
        faces[1] = {"image": pen.icon_face(c["icon"], c["feed"][:8], key=1).convert("RGB")}
        slots = [2, 3, 4, 5, 6, 7, 8, 9, 10]
    else:
        slots = list(range(1, 11))
    for k, (w, col) in layout_words(c, slots).items():
        faces[k] = _word_face(pen, w, col)
    acts = {}
    for j, a in enumerate(c.get("actions", [])[:4]):
        faces[11 + j] = _action_face(pen, a["label"], action_color(a))
        acts[11 + j] = a["id"]
    faces[15] = _nav_face(pen, i, n)
    for k in range(1, 16):
        faces.setdefault(k, {"label_pos": "none"})             # the backdrop, not a black hole
    return faces, acts


def open(ctx, **_):                                            # noqa: A001 - the action name in YAML
    """Take the pad over with the top card. No cards: a short flash."""
    d = ctx.daemon
    if not ctx.cards.list():
        ctx.flash({"glyph": "0", "label": "no cards"}, 0.8)
        return
    st = {"i": 0, "id": None, "acts": {}, "tok": None, "n": 0}

    def build():
        cs = ctx.cards.list()
        if not cs:
            return None
        ids = [c["id"] for c in cs]
        if st["id"] in ids:
            st["i"] = ids.index(st["id"])
        st["i"] %= len(cs)
        c = cs[st["i"]]
        st["id"], st["n"] = c["id"], len(cs)
        faces, st["acts"] = render_takeover(T.Pen(d), c, st["i"], len(cs))
        return faces

    def refresh():
        if st["tok"] is None:
            return
        faces = build()
        if faces is None:
            d.modal_end("cards: none left", tok=st["tok"])
            ok = False
        else:
            ok = d.modal_update(st["tok"], faces, TIMEOUT)
        if not ok:
            st["tok"] = None
            try:
                d.cards.listeners.remove(refresh)
            except ValueError:
                pass

    def on_key(c2, k, down, t):
        if not down:
            return
        if k in st["acts"]:
            cid = st["id"]
            st["id"] = None                    # the next card takes this index
            c2.cards.act(cid, st["acts"][k])   # store change -> refresh() redraws or closes
        elif k == 15:
            if st["n"] > 1:
                st["i"], st["id"] = st["i"] + 1, None
                refresh()
            else:
                d.modal_end("cards: closed", tok=st["tok"])
        elif st["tok"] is not None:
            d.modal_update(st["tok"], None, TIMEOUT)

    faces = build()
    st["tok"] = ctx.modal(faces, on_key=on_key, timeout=TIMEOUT)
    d.cards.listeners.append(refresh)
