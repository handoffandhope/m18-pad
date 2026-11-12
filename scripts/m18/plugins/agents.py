"""Agent keys: each AI session's own key, drawn and used by the agent itself (references/agents.md).

Drop-in for the session keys (same args as sessions.slot / .press / .nudge):

    6: {live: {plugin: agents.slot, interval: 1, args: {zone: [6, 7, 9], slot: 0}},
        action: {plugin: agents.press, args: {zone: [6, 7, 9], slot: 0}},
        hold: {plugin: agents.hold, args: {zone: [6, 7, 9], slot: 0}}}
    10: {..., hold: {plugin: agents.deck}}        # optional: every agent with something posted, one key each

With nothing posted by the agent, a key looks and acts exactly like sessions.slot. Agents post through
scripts/m18/pad.py into <config dir>/state/agents/ (m18lib/agentpad.py); this plugin is the only thing
that draws it, and it applies the fair-use rules (agentpad.QUOTA) again as it reads:

  face       the agent's own 16x16 sprite (up to 4 frames, 4 fps, 6 extra colours) and a 6-letter label,
             in place of the Haiku face; the session state colours and badges stay as sessions.py draws them
  plate      the bottom strip shows, in this order: a parked button's label (gold, key gets a border in the
             agent's colour), else a progress bar with the percent (and a short status word if it fits),
             else the status word (tone colour), else the label
  tap        one parked button: sends the press to the agent (an event, nothing else), a SENT flash;
             2-3 buttons: a small takeover (face, status, the buttons on 11-13, OPEN on 15; b1 closes);
             attention pulsing: tap = seen (the agent gets a "seen" event) and the session opens;
             nothing posted: sessions.press (opens the session)
  empty slots  agents that are not a session on the pad (subagents, `--as` ids) fill empty zone slots
  "+N" key   if a hidden agent has a button or wants attention, a gold corner; tap = the deck
  ring       one agent's progress arc (priority 1, yields to any non-ambient owner, 600 s per agent per
             hour), and a 2.4 s pulse in the agent's colour for an accepted attention flash
  cleanup    an agent that is no session in sessions.scan(), has no live `pad wait` and wrote nothing for
             15 min loses everything: its file, its inbox, its cards; expired parts drop at their ttl
"""
from __future__ import annotations

import math
import os
import shutil
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

from m18lib import agentpad as A
from m18lib import ringmap as RM
from m18lib import tiles as T

_VERSION = os.path.getmtime(__file__)
OWNER = "agents"
AMBIENT = ("ring.status",)
MODAL_S = 30.0
FLASH_RING_S = 2.4
_EXTRA_GLYPHS = {"%": "##..#|##.#.|...#.|..#..|.#...|.#.##|#..##", "?": ".###.|#...#|....#|...#.|..#..|.....|..#.."}


# ------------------------------------------------------------------ plumbing
def _P(d) -> dict:
    return d.plugin_state.setdefault("agents", {"docs": {}, "mt": {}, "attn": {}, "flash_seen": None,
                                                 "ledger": None, "shown": {}, "img": {}, "dirty": False,
                                                 "ring_cur": None, "ring_flash": None, "feeds": set(),
                                                 "dead_since": {}})


def _S(d):
    """The sessions plugin's module globals (same instance the daemon runs), or None."""
    try:
        return d.plugin_fn("sessions.slot").__globals__
    except Exception:                                        # noqa: BLE001
        return None


def _base(d) -> Path:
    return Path(d.base)


feed_of = A.feed_of


def _ledger(d) -> dict:
    P = _P(d)
    if P["ledger"] is None:
        lg = A.read_json(A.ledger_path(_base(d))) or {}
        P["ledger"] = {k: lg.get(k) or {} for k in ("flashes", "ring", "flash_results", "purged")}
        P["ledger"]["refused"] = lg.get("refused") or []
        P["flash_seen"] = dict(lg.get("flash_seen") or {})
    return P["ledger"]


def _refuse(d, sid, what, why):
    lg = _ledger(d)
    lg["refused"] = (lg["refused"] + [{"t": round(time.time(), 1), "agent": sid, "what": what, "why": why}])[-50:]
    _P(d)["dirty"] = True
    d.log(f"agents: refused {what} for {sid[:8]}: {why}")


def _save_ledger(d):
    P = _P(d)
    lg = _ledger(d)
    now = time.time()
    for sid in list(lg["flashes"]):
        lg["flashes"][sid] = [t for t in lg["flashes"][sid] if now - t < 3600]
        if not lg["flashes"][sid]:
            del lg["flashes"][sid]
    for sid in list(lg["ring"]):
        lg["ring"][sid] = [s for s in lg["ring"][sid] if s[1] > now - 3600]
        if not lg["ring"][sid]:
            del lg["ring"][sid]
    lg["purged"] = {k: v for k, v in lg["purged"].items() if now - v < 86400}
    shown = {k: v for k, v in P["shown"].items() if now - v.get("t", 0) < 10}
    out = dict(lg, t=round(now, 2), flash_seen=P["flash_seen"] or {}, shown=shown, version=_VERSION)
    try:
        A.write_json(A.ledger_path(_base(d)), out)
    except OSError:
        pass


def load_docs(d) -> dict:
    """{sid: effective doc} from the store, re-read only when a file changes."""
    P = _P(d)
    root = A.store_dir(_base(d))
    now = time.time()
    seen = set()
    try:
        with os.scandir(root) as it:                         # one listing; on Windows it carries each file's mtime
            files = [e for e in it if e.name.endswith(".json") and not e.name.startswith(("_", "."))]
    except OSError:
        files = []
    for e in files:
        sid = e.name[:-5]
        if not A.ID_RE.match(sid):
            continue
        seen.add(sid)
        try:
            mt = e.stat().st_mtime
        except OSError:
            continue
        p = Path(e.path)
        if P["mt"].get(sid) != mt:
            P["mt"][sid] = mt
            raw = A.read_json(p, limit=A.QUOTA["doc_bytes"])
            P.setdefault("raw", {})[sid] = raw if isinstance(raw, dict) and raw.get("id") == sid else None
            P["dirty_keys"] = True
    docs = {}
    for sid in seen:
        raw = P.setdefault("raw", {}).get(sid)
        e = A.effective(raw, now) if raw else None
        if e:
            docs[sid] = e
    for sid in list(P["mt"]):
        if sid not in seen:
            P["mt"].pop(sid, None)
            P["raw"].pop(sid, None)
    P["docs"] = docs
    return docs


# ------------------------------------------------------------------ who is where
def view(d, zone, S=None) -> dict:
    """{"slots": {i: {"s": session|None, "doc": doc|None}}, "overflow_slot": i|None, "hidden": [docs]}"""
    S = S if S is not None else _S(d)
    z = list(zone) if zone else [6, 7, 9, 10]
    n = len(z)
    docs = _P(d)["docs"]
    slots, over_slot, pad_ids, overflow = {}, None, set(), []
    if S is not None:
        snap = S["snapshot"](n)
        pad_ids = {s["id"] for s in snap["pad"]}
        overflow = snap["overflow"]
        for i, s in snap["slots"].items():
            slots[i] = {"s": s, "doc": docs.get(s["id"])}
        if overflow:
            over_slot = n - 1
    cap = n - 1 if over_slot is not None else n
    guests = sorted((x for sid, x in docs.items() if sid not in pad_ids), key=lambda x: x["created"])
    hidden = [docs[s["id"]] for s in overflow if s["id"] in docs]
    for i in range(cap):
        if i not in slots:
            if guests:
                slots[i] = {"s": None, "doc": guests.pop(0)}
    hidden += guests
    return {"slots": slots, "overflow_slot": over_slot, "hidden": hidden, "zone": z}


def wants_eyes(doc, P) -> bool:
    return bool(doc and (doc["buttons"] or P["attn"].get(doc["id"], 0) > time.time()))


# ------------------------------------------------------------------ drawing (64 px, the session-key look)
def _glyphs(S):
    g = dict(S["_G"]) if S and "_G" in S else {}
    g.update({c: v.split("|") for c, v in _EXTRA_GLYPHS.items()})
    return g


def text_w(G, s):
    w = 0
    for ch in s.upper():
        g = G.get(ch)
        w += (len(g[0]) if g else 3) + 1
    return max(0, w - 1)


def draw_text(G, dr, x, y, s, col, shadow=True):
    sh = tuple(c // 4 for c in col)
    for off, c in (((1, sh),) if shadow else ()) + ((0, col),):
        cx = x
        for ch in s.upper():
            g = G.get(ch)
            if not g:
                cx += 4
                continue
            for ry, row in enumerate(g):
                for rx, b in enumerate(row):
                    if b == "#":
                        dr.point((cx + rx + off, y + ry + off), fill=c)
            cx += len(g[0]) + 1


def paint(img, grid, pal, dim=1.0):
    px = img.load()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            c = T.rgb(pal.get(ch) or A.BASE_PALETTE.get(ch, "#ff00ff"))
            if dim != 1.0:
                c = tuple(int(v * dim) for v in c)
            for dy in range(3):
                for dx in range(3):
                    px[8 + x * 3 + dx, 3 + y * 3 + dy] = c


def _plate(G, dr, text, col, outline="#3a00a8"):
    w = min(60, text_w(G, text) + 6)
    x0 = (64 - w) // 2
    dr.rectangle((x0, 52, x0 + w - 1, 62), fill="#100010", outline=outline)
    for cx, cy in ((x0, 52), (x0 + w - 1, 52), (x0, 62), (x0 + w - 1, 62)):
        dr.point((cx, cy), fill="#100010")
    draw_text(G, dr, x0 + 3, 54, text, T.rgb(col))


def _bar(G, dr, value, text, fill):
    dr.rectangle((2, 51, 61, 62), fill="#100010", outline="#3a00a8")
    x1 = 3 + int(round(57 * value))
    if x1 > 3:
        dr.rectangle((3, 52, x1 - 1, 61), fill=fill)
    tw = text_w(G, text)
    draw_text(G, dr, (64 - tw) // 2, 54, text, (255, 255, 255))


def _fit(G, s, w=56):
    while s and text_w(G, s) > w:
        s = s[:-1]
    return s


def compose(S, state, phase, kind, quiet, grid, pal, plate, border=None, count=0, corner=False):
    """One 64 px RGB frame. state: waiting|working|finished|guest. plate: ("label"|"status"|"button", text, col)
    or ("bar", value, text)."""
    G = _glyphs(S)
    silent = state == "working" and quiet is not None
    look = "silent" if silent else ("working" if state == "guest" else state)
    if S and "_slot" in S:
        img, dr = S["_slot"](look, phase)
    else:
        img = Image.new("RGB", (64, 64), "#27345f")
        dr = ImageDraw.Draw(img)
    paint(img, grid, pal, 0.55 if silent else 0.78 if state == "working" else 1.0)
    dr = ImageDraw.Draw(img)
    if S and state != "guest":
        try:
            if silent:
                S["_age_badge"](dr, S["age_text"](quiet))
            else:
                S["_badge"](dr, state, phase, kind)
        except Exception:                                    # noqa: BLE001 - a renamed helper only loses the badge
            pass
    if plate[0] == "bar":
        _bar(G, dr, plate[1], plate[2], "#2f7d2c")
    else:
        _plate(G, dr, _fit(G, plate[1]), plate[2], outline="#a88a1c" if plate[0] == "button" else "#3a00a8")
    if border:
        dr.rectangle((0, 0, 63, 63), outline=border, width=2)
    if count >= 2:
        dr.rectangle((2, 2, 11, 12), fill="#161616")
        dr.rectangle((3, 3, 10, 11), fill="#a88a1c")
        draw_text(G, dr, 5, 4, str(count), (22, 22, 22), shadow=False)
    if corner:
        dr.polygon([(44, 0), (63, 0), (63, 19)], fill="#f6d03c")
        draw_text(G, dr, 57, 1, "!", (22, 22, 22), shadow=False)
    return img


def _out(img, themed):
    return img.resize((192, 192), Image.NEAREST).convert("RGBA") if themed else img


def _themed(d) -> bool:
    try:
        return bool(T.Pen(d).theme)
    except Exception:                                        # noqa: BLE001
        return False


def _plate_for(doc, label):
    if doc and doc["buttons"]:
        b = doc["buttons"][0]
        return ("button", b["label"], T.GOLD)
    if doc and doc.get("progress"):
        pct = f"{int(round(doc['progress']['value'] * 100))}%"
        st = (doc.get("status") or {}).get("text")
        txt = f"{st} {pct}" if st and len(st) + len(pct) + 1 <= 9 else pct
        return ("bar", doc["progress"]["value"], txt)
    if doc and doc.get("status"):
        return ("status", doc["status"]["text"], A.TONES[doc["status"]["tone"]])
    return ("label", label, "#ffffff")


def agent_face(d, S, doc, s=None, sessions=None, corner=False) -> dict:
    """Face dict (image or frames) for a session and/or an agent doc."""
    P = _P(d)
    th = _themed(d)
    sid = (doc or {}).get("id") or (s or {}).get("id") or "x"
    if doc and doc.get("face"):
        frames, fps, pal = doc["face"]["frames"], doc["face"]["fps"], doc["face"]["palette"]
        label = doc["face"].get("label")
    else:
        frames, fps, pal, label = None, 2.0, {}, None
    if frames is None or label is None:
        if s is not None and S is not None:
            lab, grid = S["face_for"](s, sessions or [])
        else:
            lab = (doc or {}).get("title") or "AGENT"
            grid = S["fallback_grid"](sid) if S and "fallback_grid" in S else ["." * 16] * 16
        frames = frames or [grid]
        label = label or lab
    state = s["state"] if s else "guest"
    kind = s.get("wkind") if s else None
    quiet = (S["_quiet"](s) if S and "_quiet" in S else None) if s else None
    plate = _plate_for(doc, label.upper())
    attn = P["attn"].get(sid, 0) > time.time()
    border_c = A.color_for(sid, doc) if doc and doc["buttons"] else None
    count = len(doc["buttons"]) if doc else 0
    pulse = state == "waiting" or attn
    key = (sid, state, kind, None if quiet is None else S["age_text"](quiet) if S else 0, tuple("".join(f) for f in frames),
           tuple(sorted(pal.items())), plate, border_c, count, attn, corner, th, fps)
    cache = P["img"]
    if key in cache:
        return cache[key]
    if len(cache) > 400:
        cache.clear()
    if pulse:
        imgs = []
        for i in range(6):
            ph = 0.5 - 0.5 * math.cos(2 * math.pi * i / 6)
            bc = border_c
            if attn:
                bc = T.mix(A.color_for(sid, doc), "#f6d03c", ph)
            imgs.append(_out(compose(S, state, ph if state == "waiting" else 0.0, kind, quiet, frames[i % len(frames)],
                                     pal, plate, bc, count, corner), th))
        face = {"frames": imgs, "fps": 3}
    elif len(frames) > 1:
        face = {"frames": [_out(compose(S, state, 0.0, kind, quiet, f, pal, plate, border_c, count, corner), th)
                           for f in frames], "fps": fps}
    else:
        face = {"image": _out(compose(S, state, 0.0, kind, quiet, frames[0], pal, plate, border_c, count, corner), th)}
    cache[key] = face
    return face


# ------------------------------------------------------------------ live keys
def slot(ctx, zone=None, slot=0, silent_min=None, **_):
    """Live face: the session key with the agent's own face, plate, buttons and attention on it."""
    d = ctx.daemon
    _ensure(ctx)
    S = _S(d)
    base = {}
    if S is not None:
        try:
            base = S["slot"](ctx, zone=zone, slot=slot, silent_min=silent_min) or {}
        except Exception as e:                               # noqa: BLE001
            ctx.log(f"agents: sessions.slot failed: {e}")
            S = None
    if not _P(d)["docs"]:
        load_docs(d)
    v = view(d, zone, S)
    P = _P(d)
    if slot == v["overflow_slot"]:
        if any(wants_eyes(x, P) for x in v["hidden"]):
            return _corner(base)
        return base
    e = v["slots"].get(slot)
    if not e or (e["doc"] is None):
        return base
    doc, s = e["doc"], e["s"]
    old = P["shown"].get(doc["id"]) or {}
    if (old.get("key"), old.get("page")) != (ctx.key, ctx.page):
        P["dirty"] = True
    P["shown"][doc["id"]] = {"key": ctx.key, "page": ctx.page, "slot": slot, "guest": s is None, "t": time.time()}
    return agent_face(d, S, doc, s, S["scan"]() if S else [])


def _corner(face):
    """The '+N' face with a gold corner: someone hidden wants the user."""
    def mark(im):
        im = im.copy()
        k = im.width // 64
        dr = ImageDraw.Draw(im)
        dr.polygon([(44 * k, 0), (im.width - 1, 0), (im.width - 1, 19 * k)], fill=(246, 208, 60, 255) if im.mode == "RGBA" else (246, 208, 60))
        return im
    if "frames" in face:
        return dict(face, frames=[mark(f) for f in face["frames"]])
    if "image" in face:
        return dict(face, image=mark(face["image"]))
    return face


# ------------------------------------------------------------------ presses
def fire(d, ctx_source, key, sid, bid) -> dict | None:
    """The user pressed an agent's button: an event to that agent, nothing else is run."""
    base = _base(d)
    with A.locked(base, sid):
        raw = A.read_json(A.doc_path(base, sid)) or {}
        btns = raw.get("buttons") or []
        b = next((x for x in btns if isinstance(x, dict) and x.get("id") == bid), None)
        if b is None:
            return None
        ev = {"type": "agent_button", "agent": sid, "button": bid, "label": str(b.get("label", ""))[:12],
              "source": ctx_source, "key": key, "t": round(time.time(), 3)}
        ev["inbox"] = A.inbox_put(base, sid, ev)            # before the button goes, so a waiter never sees "gone" first
        if not b.get("sticky") and ctx_source == "device":       # software can't use up the user's button
            raw["buttons"] = [x for x in btns if x is not b]
            A.write_json(A.doc_path(base, sid), raw)
    d.publish_event(ev)
    d.log(f"agents: button {bid!r} of {sid[:8]} pressed (source={ctx_source})")
    load_docs(d)
    _refresh_keys(d)
    return ev


def _seen(d, ctx, sid):
    P = _P(d)
    P["attn"].pop(sid, None)
    ev = {"type": "agent_seen", "agent": sid, "source": ctx.source, "key": ctx.key, "t": round(time.time(), 3)}
    ev["inbox"] = A.inbox_put(_base(d), sid, ev)
    d.publish_event(ev)
    _refresh_keys(d)


def press(ctx, zone=None, slot=0, **_):
    d = ctx.daemon
    S = _S(d)
    load_docs(d)
    v = view(d, zone, S)
    P = _P(d)
    if slot == v["overflow_slot"]:
        if any(wants_eyes(x, P) for x in v["hidden"]):
            return deck(ctx)
        return S["press"](ctx, zone=zone, slot=slot) if S else None
    e = v["slots"].get(slot)
    doc = e["doc"] if e else None
    if doc is None:
        return S["press"](ctx, zone=zone, slot=slot) if S else None
    sid = doc["id"]
    if P["attn"].get(sid, 0) > time.time():
        _seen(d, ctx, sid)                                   # any tap on a flashing key counts as seen
        if not doc["buttons"]:
            return S["press"](ctx, zone=zone, slot=slot) if (S and e["s"] is not None) else None
    if len(doc["buttons"]) == 1:
        if fire(d, ctx.source, ctx.key, sid, doc["buttons"][0]["id"]):
            ctx.flash({"icon": "mdi:check-bold", "label": "SENT", "bg": "#1d6b2a"}, 0.8)
        return None
    if len(doc["buttons"]) > 1:
        return _takeover(ctx, ("agent", sid, e["s"]))
    if e["s"] is not None and S is not None:
        return S["press"](ctx, zone=zone, slot=slot)
    ctx.flash({"glyph": "-", "label": "no window", "bg": "#2a2a33"}, 0.8)


def hold(ctx, zone=None, slot=0, **kw):
    """Hold = the session key's hold (sessions.nudge); for a guest, its buttons."""
    d = ctx.daemon
    S = _S(d)
    v = view(d, zone, S)
    e = v["slots"].get(slot)
    if e and e["s"] is None and e["doc"] and e["doc"]["buttons"]:
        return _takeover(ctx, ("agent", e["doc"]["id"], None))
    return S["nudge"](ctx, zone=zone, slot=slot, **kw) if S else None


def deck(ctx, **_):
    """Action: every agent with something posted, one key each (1-14), key 15 closes; b1 too."""
    _ensure(ctx)
    load_docs(ctx.daemon)
    if not _P(ctx.daemon)["docs"]:
        ctx.flash({"glyph": "0", "label": "no agents"}, 0.8)
        return None
    return _takeover(ctx, ("deck",))


def _btn_face(pen, label, color):
    img = pen.canvas()
    pen.slot(img, color, (1, 1, 62, 62))
    pen.word(img, 32, 32, label, T.WHITE, maxw=58, top=2, lines=2)
    return pen.face(img)


def _word_face(pen, w, color):
    img = pen.canvas()
    pen.plate(img, (1, 1, 62, 62))
    pen.word(img, 32, 32, w, color, top=3, lines=2)
    return pen.face(img)


def _takeover(ctx, start):
    d = ctx.daemon
    S = _S(d)
    st = {"view": start, "tok": None, "map": {}}

    def sess_of(sid):
        if S is None:
            return None
        return next((x for x in S["scan"]() if x["id"] == sid and x.get("on_pad")), None)

    def build():
        load_docs(d)
        docs = _P(d)["docs"]
        pen = T.Pen(d)
        faces, mp = {}, {}
        if st["view"][0] == "deck":
            order = sorted(docs.values(), key=lambda x: (not wants_eyes(x, _P(d)), -x["updated"]))[:14]
            for i, doc in enumerate(order):
                k = 1 + i
                faces[k] = agent_face(d, S, doc, sess_of(doc["id"]), S["scan"]() if S else [])
                mp[k] = ("agent", doc["id"])
            faces[15] = _btn_face(pen, "DONE", "#4a4a55")
            mp[15] = ("close",)
        else:
            sid = st["view"][1]
            doc = docs.get(sid)
            if doc is None:
                return None
            s = sess_of(sid)
            faces[1] = agent_face(d, S, dict(doc, buttons=[]), s, S["scan"]() if S else [])
            words = []
            if doc.get("status"):
                words.append((doc["status"]["text"], A.TONES[doc["status"]["tone"]]))
            if doc.get("progress"):
                words.append((f"{int(round(doc['progress']['value'] * 100))}%", T.AQUA))
            title = (s or {}).get("title") or doc.get("title") or ""
            if title:  # the whole title on one key (up to two lines), never split across keys
                words.append((title, T.GOLD))
            for i, (w, c) in enumerate(words[:3]):
                faces[2 + i] = _word_face(pen, w, c)
            for i, b in enumerate(doc["buttons"][:3]):
                faces[11 + i] = _btn_face(pen, b["label"], b.get("color") or "#2c5fb8")
                mp[11 + i] = ("fire", sid, b["id"])
            if s is not None:
                faces[15] = _btn_face(pen, "OPEN", "#2f8f3a")
                mp[15] = ("open", sid)
            else:
                faces[15] = _btn_face(pen, "DONE", "#4a4a55")
                mp[15] = ("close",)
        for k in range(1, 16):
            faces.setdefault(k, {"label_pos": "none"})
        st["map"] = mp
        return faces

    def on_key(c2, k, down, t):
        if not down:
            return
        act = st["map"].get(k)
        if not act:
            d.modal_update(st["tok"], None, MODAL_S)
            return
        if act[0] == "close":
            d.modal_end("agents: closed", tok=st["tok"])
            return
        if act[0] == "agent":
            doc = _P(d)["docs"].get(act[1])
            if doc and doc["buttons"]:
                st["view"] = ("agent", act[1])
            else:
                if doc and _P(d)["attn"].get(act[1], 0) > time.time():
                    _seen(d, c2, act[1])
                s = sess_of(act[1])
                d.modal_end("agents: open", tok=st["tok"])
                if s is not None and S is not None:
                    S["open_session"](c2, s)
                return
        elif act[0] == "fire":
            fire(d, c2.source, k, act[1], act[2])
            doc = load_docs(d).get(act[1])
            if not doc or not doc["buttons"]:
                d.modal_end("agents: done", tok=st["tok"])
                return
        elif act[0] == "open":
            s = sess_of(act[1])
            d.modal_end("agents: open", tok=st["tok"])
            if s is not None and S is not None:
                S["open_session"](c2, s)
            return
        faces = build()
        if faces is None:
            d.modal_end("agents: gone", tok=st["tok"])
        else:
            d.modal_update(st["tok"], faces, MODAL_S)

    faces = build()
    if faces is None:
        return None
    st["tok"] = ctx.modal(faces, on_key=on_key, timeout=MODAL_S)
    return st["tok"]


# ------------------------------------------------------------------ background: rules, ring, cleanup
def _refresh_keys(d):
    if not d.cfg:
        return
    from m18lib import config as C
    for k, kd in C.resolved_keys(d.cfg, d.page).items():
        lv = kd.get("live")
        if lv and str(lv.get("plugin", "")).startswith("agents."):
            d.live_next.pop((d.page, k), None)


def _alive(d, S, sid, doc, sessions_by_id) -> bool:
    now = time.time()
    s = sessions_by_id.get(sid)
    if s is not None and s.get("state") != "stale":
        return True
    if doc and now - doc["updated"] < A.QUOTA["dead_grace_s"]:
        return True
    raw = _P(d).get("raw", {}).get(sid) or {}
    for pid, until in (raw.get("waiters") or {}).items():
        try:
            if float(until) > now and A.pid_alive(int(pid)):
                return True
        except (TypeError, ValueError):
            continue
    return False


def purge(d, sid, why):
    base = _base(d)
    try:
        with A.locked(base, sid, timeout=0.5):
            A.doc_path(base, sid).unlink(missing_ok=True)
    except A.PadError:
        return False
    shutil.rmtree(A.inbox_dir(base, sid), ignore_errors=True)
    for c in d.cards.ordered(feed_of(sid)):
        d.cards.remove(c["id"])
    P = _P(d)
    P["attn"].pop(sid, None)
    _ledger(d)["purged"][sid] = round(time.time(), 1)
    P["dirty"] = True
    d.log(f"agents: purged {sid[:8]} ({why})")
    return True


def _card_mirror(fd):
    def on_card(ctx, card, action):
        sid = A.sid_of_feed(card.get("feed", ""), _P(ctx.daemon)["docs"])
        if not sid:
            return
        ev = {"type": "card_action", "agent": sid, "card": card["id"], "action": action,
              "source": ctx.source, "t": round(time.time(), 3)}
        A.inbox_put(ctx.daemon.base, sid, ev)
    return on_card


def housekeeping(d):
    """Purge dead agents and expired files, enforce the card quota, mirror card answers to the inbox."""
    S = _S(d)
    P = _P(d)
    try:
        sessions = {s["id"]: s for s in (S["scan"]() if S else [])}
    except Exception:                                        # noqa: BLE001
        sessions = {}
    docs = load_docs(d)
    for sid in list(P.get("raw", {})):
        if sid in docs:
            if not _alive(d, S, sid, docs[sid], sessions):
                purge(d, sid, "session ended")
        else:
            raw = P["raw"].get(sid) or {}
            if time.time() - float(raw.get("updated") or 0) > 60:   # nothing left to show, and not just written
                purge(d, sid, "expired")
    by_feed: dict = {}
    for c in d.cards.ordered():
        if c.get("feed", "").startswith("ag:"):
            by_feed.setdefault(c["feed"], []).append(c)
    feeds = {feed_of(sid): sid for sid in docs}
    for fd, cs in by_feed.items():
        sid = feeds.get(fd) or A.sid_of_feed(fd, sessions) or fd[3:]
        if fd not in feeds and not (sid in sessions and sessions[sid].get("state") != "stale"):
            for c in cs:
                d.cards.remove(c["id"])
            d.log(f"agents: removed {len(cs)} card(s) of gone agent {fd}")
            continue
        cs.sort(key=lambda c: c.get("created", 0))
        for c in cs[A.QUOTA["cards"]:]:
            d.cards.remove(c["id"])
            _refuse(d, sid, "card", f"quota: {A.QUOTA['cards']} open cards per agent")
        if fd not in P["feeds"]:
            d.cards.callbacks[fd] = _card_mirror(fd)
            P["feeds"].add(fd)
    for fd in list(P["feeds"]):
        if fd not in feeds and fd not in by_feed:
            d.cards.callbacks.pop(fd, None)
            P["feeds"].discard(fd)


def handle_flashes(d):
    P = _P(d)
    lg = _ledger(d)
    if P["flash_seen"] is None:
        P["flash_seen"] = {}
    now = time.time()
    for sid, doc in P["docs"].items():
        fl = doc.get("flash")
        if not fl:
            continue
        seen = P["flash_seen"].get(sid)
        if seen is not None and fl["n"] <= seen:
            continue
        P["flash_seen"][sid] = fl["n"]
        P["dirty"] = True
        if seen is None and now - fl["at"] > 30:
            continue                                         # an old request from before a restart
        why = A.flash_refusal(lg, sid, now)
        if why:
            lg["flash_results"][sid] = {"n": fl["n"], "ok": False, "why": why, "t": round(now, 2)}
            _refuse(d, sid, "flash", why)
            continue
        lg["flashes"].setdefault(sid, []).append(round(now, 2))
        lg["flash_results"][sid] = {"n": fl["n"], "ok": True, "t": round(now, 2)}
        P["attn"][sid] = now + A.QUOTA["attention_s"]
        P["ring_flash"] = (sid, now, A.color_for(sid, doc))
        _refresh_keys(d)
    for sid in [k for k, v in P["attn"].items() if v < now or k not in P["docs"]]:
        P["attn"].pop(sid, None)
        _refresh_keys(d)


def _ring_free(d) -> bool:
    o = d.leds_owner
    return o is None or o.get("owner") in (OWNER,) + AMBIENT or (o.get("until") and o["until"] < time.time())


def drive_ring(d):
    P = _P(d)
    lg = _ledger(d)
    now = time.time()
    frame, sid = None, None
    if not _ring_free(d):
        _close_span(d, now)
        return
    rm = RM.load(_base(d))
    rf = P.get("ring_flash")
    if rf and now - rf[1] < FLASH_RING_S:
        sid = rf[0]
        f = abs(math.sin(math.pi * (now - rf[1]) / (FLASH_RING_S / 3)))
        frame = [RM.scaled(rf[2], max(0.08, f))] * RM.N_LEDS
    else:
        P["ring_flash"] = None
        cands = []
        for x in P["docs"].values():
            pg = x.get("progress")
            if not (pg and pg["ring"]):
                continue
            used = A.ring_used(lg, x["id"], now)
            if used >= A.QUOTA["ring_s_per_hour"]:
                if not P.setdefault("ring_over", {}).get(x["id"]):
                    P["ring_over"][x["id"]] = True
                    _refuse(d, x["id"], "ring", f"quota: {A.QUOTA['ring_s_per_hour']} s of ring time per hour")
                continue
            P.setdefault("ring_over", {}).pop(x["id"], None)
            cands.append(x)
        if cands:
            x = max(cands, key=lambda x: x["progress"]["set"])
            sid = x["id"]
            frame = RM.meter_frame(rm, x["progress"]["value"], A.color_for(sid, x), None, "#0c0c0c")
    if frame is None:
        _close_span(d, now)
        if d.leds_owner and d.leds_owner.get("owner") == OWNER:
            d.leds_release(OWNER, "agents: nothing to show")
        return
    try:
        ok = d.leds_set(frame, 2.0, None, OWNER, 1)
    except Exception:                                        # noqa: BLE001 - dark mode refuses frames
        ok = False
    if not ok:
        _close_span(d, now)
        return
    cur = P.get("ring_cur")                                  # [sid, last draw]: ring time is counted per span
    spans = lg["ring"].setdefault(sid, [])
    if cur and cur[0] == sid and now - cur[1] < 3 and spans:
        spans[-1][1] = round(now, 2)
    else:
        spans.append([round(now, 2), round(now, 2)])
    P["ring_cur"] = [sid, now]


def _close_span(d, now):
    _P(d)["ring_cur"] = None


def _loop(d, st, stop):
    last_h = last_s = 0.0
    while not stop.is_set():
        try:
            now = time.time()
            P = _P(d)
            P.pop("dirty_keys", None)
            load_docs(d)
            if P.pop("dirty_keys", None):
                _refresh_keys(d)
            handle_flashes(d)
            drive_ring(d)
            if now - last_h >= 2.0:
                last_h = now
                housekeeping(d)
            if now - last_s >= 1.0 and (P["dirty"] or now - last_s >= 5.0):   # pad.py wants a beat under 10 s
                last_s = now
                P["dirty"] = False
                _save_ledger(d)
        except Exception as e:                               # noqa: BLE001
            try:
                d.log("agents loop:", e)
            except Exception:                                # noqa: BLE001
                pass
        stop.wait(0.1 if _P(d).get("ring_flash") else 0.25)


def _ensure(ctx):
    d = ctx.daemon
    st = d.plugin_state.setdefault("agents.thread", {})
    th = st.get("thread")
    if th and th.is_alive() and st.get("version") == _VERSION:
        return
    if st.get("stop"):
        st["stop"].set()
    st["stop"] = stop = threading.Event()
    st["version"] = _VERSION
    st["thread"] = threading.Thread(target=_loop, args=(d, st, stop), daemon=True, name="agents")
    st["thread"].start()


def tick(ctx, **_):
    """Optional live function for a page without agent keys: keeps the rules and the ring running. Returns {}."""
    _ensure(ctx)
    return {}
