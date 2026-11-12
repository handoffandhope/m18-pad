"""Tap-to-expand volume mixer.

    Vol key:  action: {plugin: mixer.open}

Takes over the pad (ctx.modal), shows what is making sound right now, and closes itself 2.5 s after the
last press (b1 closes at once). Needs `pip install pycaw comtypes` in the daemon's venv.

What counts as an active app: a Windows audio session whose state is Active AND whose output peak is above
0.002 now, or was above it within the last 8 s (kept in ctx.state while the daemon runs). State alone is not
enough: on the test PC java.exe and a streaming host hold Active sessions that are silent, and a paused player can
keep one too. Sessions of one process name (Chrome has several) are one app. Order: loudest right now
first, then most recently audible. The mixer has no background sampler, so "recently audible" only covers
the moments it was opened.

Layout (3 rows x 5 keys; 4 levels per row = 25/50/75/100 %, press sets that level):
  no active app      master volume as one 15-step bar over all 15 keys (key N = N/15; pressing the top lit
                     key steps down one, so key 1 twice reaches silence)
  1..3 active apps   one row per app: key 1 of the row = the app (name, percent; press = mute toggle), keys 2-5
                     = levels. Spare rows are master (key 1 = VOL, press = mute master)
  more than 3        the three most audible; the last row's first key becomes "+N" (N apps hidden; it shows the
                     third app's name small); press = move the window two apps along (wraps), so every app
                     gets its own mute key on some page
Drawing follows the theme: if it offers pixel-text helpers (hillside) levels are hearts for apps and XP bars
for master; otherwise plain bars.
"""
from __future__ import annotations

import threading
import time

from PIL import Image, ImageDraw

CLOSE_S = 2.5
ACTIVE_PEAK = 0.002
RECENT_S = 8.0
LEVELS = 4
BACKEND = None            # tests inject a fake with the PycawBackend interface

ACCENTS = ("#ffaa00", "#55ffff", "#ff55ff")      # one per app row
MASTER_COL = "#80ff20"
KNOWN = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "spotify": "Spotify",
         "discord": "Discord", "vlc": "VLC", "obs64": "OBS", "obs32": "OBS", "steam": "Steam",
         "steamwebhelper": "Steam", "javaw": "Java", "java": "Java", "brave": "Brave",
         "teams": "Teams", "ms-teams": "Teams", "zoom": "Zoom", "wmplayer": "Media"}


def short(name: str) -> str:
    n = str(name).strip()
    if n.lower().endswith(".exe"):
        n = n[:-4]
    if n.lower() in KNOWN:
        return KNOWN[n.lower()]
    n = n.replace("_", " ").replace("-", " ").split(" ")[0] or n
    return (n[:1].upper() + n[1:])[:7]


# ------------------------------------------------------------------------------ audio backend
_tl = threading.local()


def _com():
    """COM must be initialised on every thread that touches it (daemon workers are plain threads)."""
    if not getattr(_tl, "ok", False):
        import comtypes
        comtypes.CoInitialize()
        _tl.ok = True


class PycawBackend:
    """Windows Core Audio through pycaw. Fresh COM objects on every call, on the calling thread."""

    def _sessions(self):
        _com()
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetAllSessions()

    def sessions(self, sample: bool = True) -> list[dict]:
        """Raw per-process sessions grouped by exe name: name, volume, muted, state (1 = active), peak."""
        from pycaw.pycaw import IAudioMeterInformation
        out: dict[str, dict] = {}
        ss = self._sessions()
        for _ in range(4 if sample else 1):
            for s in ss:
                try:
                    p = s.Process
                    if p is None:
                        continue                                  # the system-sounds session
                    nm = p.name()
                    v = s.SimpleAudioVolume
                    try:
                        peak = float(s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue())
                    except Exception:
                        peak = 0.0
                    e = out.setdefault(nm, {"name": nm, "volume": 0.0, "muted": True, "state": 0, "peak": 0.0})
                    e["volume"] = max(e["volume"], float(v.GetMasterVolume()))
                    e["muted"] = e["muted"] and bool(v.GetMute())
                    e["state"] = max(e["state"], int(s.State))
                    e["peak"] = max(e["peak"], peak)
                except Exception:
                    continue
            if sample:
                time.sleep(0.04)
        return list(out.values())

    def master(self) -> dict:
        _com()
        from pycaw.pycaw import AudioUtilities
        ev = AudioUtilities.GetSpeakers().EndpointVolume
        return {"volume": float(ev.GetMasterVolumeLevelScalar()), "muted": bool(ev.GetMute())}

    def set_master(self, v: float):
        _com()
        from pycaw.pycaw import AudioUtilities
        ev = AudioUtilities.GetSpeakers().EndpointVolume
        ev.SetMasterVolumeLevelScalar(max(0.0, min(1.0, v)), None)
        if v > 0 and ev.GetMute():
            ev.SetMute(0, None)

    def set_master_mute(self, m: bool):
        _com()
        from pycaw.pycaw import AudioUtilities
        AudioUtilities.GetSpeakers().EndpointVolume.SetMute(1 if m else 0, None)

    def set_app(self, name: str, v: float):
        for s in self._sessions():
            if s.Process is not None and s.Process.name() == name:
                s.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, v)), None)
                if v > 0 and s.SimpleAudioVolume.GetMute():
                    s.SimpleAudioVolume.SetMute(0, None)

    def set_app_mute(self, name: str, m: bool):
        for s in self._sessions():
            if s.Process is not None and s.Process.name() == name:
                s.SimpleAudioVolume.SetMute(1 if m else 0, None)


def backend():
    return BACKEND or PycawBackend()


# ------------------------------------------------------------------------------ model (pure)
def analyse(raw: list[dict], now: float, last: dict) -> list[dict]:
    """Active apps, most audible first. `last` ({name: time last heard}) is updated in place."""
    apps = []
    for s in raw:
        heard = s.get("peak", 0.0) > ACTIVE_PEAK
        if heard:
            last[s["name"]] = now
        t = last.get(s["name"])
        if s.get("state") == 1 and t is not None and now - t <= RECENT_S:
            apps.append({"name": s["name"], "volume": float(s["volume"]), "muted": bool(s["muted"]),
                         "peak": float(s.get("peak", 0.0)), "last": t})
    apps.sort(key=lambda a: (-a["last"], -a["peak"], a["name"]))
    return apps


def layout(apps: list[dict], master: dict, offset: int = 0) -> dict:
    """{key: spec} for the 15 keys. Specs: role step (master bar), identity, level, more."""
    keys: dict[int, dict] = {}
    if not apps:
        steps = round(master["volume"] * 15)
        for k in range(1, 16):
            keys[k] = {"role": "step", "target": "master", "step": k, "of": 15,
                       "lit": k <= steps and not master["muted"], "muted": master["muted"]}
        return {"mode": "master-bar", "keys": keys, "shown": [], "hidden": 0, "offset": 0}
    n = len(apps)
    if n > 3:
        o = offset % n
        shown = [apps[(o + i) % n] for i in range(3)]
    else:
        o, shown = 0, list(apps)
    for r in range(3):
        base = 1 + 5 * r
        if r < len(shown):
            a = shown[r]
            tgt, vol, muted, accent = a["name"], a["volume"], a["muted"], ACCENTS[r]
            ident = {"role": "identity", "target": tgt, "label": short(tgt), "volume": vol,
                     "muted": muted, "accent": accent}
        else:
            tgt, vol, muted, accent = "master", master["volume"], master["muted"], MASTER_COL
            ident = {"role": "identity", "target": "master", "label": "VOL", "volume": vol,
                     "muted": muted, "accent": accent}
        if n > 3 and r == 2:
            ident = {"role": "more", "target": None, "label": f"+{n - 3}", "app": short(tgt),
                     "accent": accent}
        keys[base] = ident
        for j in range(1, LEVELS + 1):
            fill = 0.0 if muted else min(1.0, max(0.0, vol * LEVELS - (j - 1)))
            keys[base + j] = {"role": "level", "target": tgt, "step": j, "of": LEVELS, "fill": fill,
                              "muted": muted, "master": tgt == "master", "accent": accent}
    return {"mode": "rows", "keys": keys, "shown": [a["name"] for a in shown], "hidden": max(0, n - 3),
            "offset": o}


def action_for(lay: dict, key: int, master: dict | None = None):
    """What a press does: {"op": "set"|"mute"|"page", "target", "value"} or None."""
    sp = lay["keys"].get(key)
    if not sp:
        return None
    if sp["role"] == "step":
        cur = sum(1 for s in lay["keys"].values() if s["lit"])
        step = sp["step"] - 1 if (sp["step"] == cur and not sp["muted"]) else sp["step"]
        return {"op": "set", "target": "master", "value": step / sp["of"]}
    if sp["role"] == "level":
        return {"op": "set", "target": sp["target"], "value": sp["step"] / sp["of"]}
    if sp["role"] == "identity":
        return {"op": "mute", "target": sp["target"], "value": not sp["muted"]}
    if sp["role"] == "more":
        return {"op": "page", "target": None, "value": 2}
    return None


def apply(op: dict, be) -> None:
    if op["op"] == "set":
        (be.set_master(op["value"]) if op["target"] == "master" else be.set_app(op["target"], op["value"]))
    elif op["op"] == "mute":
        (be.set_master_mute(op["value"]) if op["target"] == "master" else be.set_app_mute(op["target"], op["value"]))


# ------------------------------------------------------------------------------ drawing
_HEART = (".###.###.", "#########", "#########", "#########", ".#######.", "..#####..", "...###...", "....#....")
_CELL = {(x, y) for y, row in enumerate(_HEART) for x, c in enumerate(row) if c == "#"}
_HI = {(1, 1), (2, 1), (1, 2)}
PLATE = ("#1a1a1a", "#0a0a0a", "#4a4a4a")          # fill, top-left bevel, bottom-right bevel


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class Pen:
    """Draws on a 64 px grid image, using the theme's pixel font when it has one."""

    def __init__(self, ctx):
        from m18lib import render as R
        self.R = R
        mod = None
        try:
            mod, _ = R.load_theme(ctx.daemon.cfg["settings"], ctx.daemon.base)
        except Exception:
            pass
        self.mc = mod if (mod and hasattr(mod, "draw_text") and hasattr(mod, "text_width")) else None
        self.pal = ctx.theme or {}

    def plate(self, bg=None):
        im = Image.new("RGB", (64, 64), _rgb(PLATE[0]))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 63, 1), fill=_rgb(PLATE[1]))
        d.rectangle((0, 0, 1, 63), fill=_rgb(PLATE[1]))
        d.rectangle((0, 62, 63, 63), fill=_rgb(PLATE[2]))
        d.rectangle((62, 0, 63, 63), fill=_rgb(PLATE[2]))
        if bg:
            d.rectangle((2, 2, 61, 61), fill=_rgb(bg))
        return im

    def width(self, s, scale=1):
        if self.mc:
            return self.mc.text_width(s) * scale
        return int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(s, font=self.R.font("bold", 11 * scale)))

    def text(self, im, cx, y, s, fill, scale=1):
        """Text centred on cx, top at y."""
        if self.mc:
            self.mc.draw_text(im, cx - self.width(s, scale) // 2, y, s, fill, shadow=True, scale=scale)
        else:
            ImageDraw.Draw(im).text((cx, y), s, font=self.R.font("bold", 10 + 3 * scale), fill=fill, anchor="mt")

    def heart(self, im, fill: float, muted=False):
        full = _rgb("#e5251f") if not muted else _rgb("#6a6a7a")
        edge, empty = _rgb("#000000"), _rgb("#4a4a4a")
        sc, x0, y0 = 5, 9, 12                    # 5 px cells; sprite cell (0,0) at (9,12), outline cell at (4,7)
        d = ImageDraw.Draw(im)

        def cell(x, y, col):
            d.rectangle((x0 + x * sc, y0 + y * sc, x0 + x * sc + sc - 1, y0 + y * sc + sc - 1), fill=col)
        for x in range(-1, 10):
            for y in range(-1, 9):
                if (x, y) not in _CELL and any((x + dx, y + dy) in _CELL for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                    cell(x, y, edge)
        for x, y in _CELL:
            on = fill >= 0.99 or (fill >= 0.45 and x <= 4)
            cell(x, y, (_rgb("#ff8a70") if (x, y) in _HI else full) if on else empty)

    def bar(self, im, fill: float, color, y=26, h=12):
        d = ImageDraw.Draw(im)
        d.rectangle((4, y - 1, 59, y + h), fill=(0, 0, 0))
        d.rectangle((5, y, 58, y + h - 1), fill=_rgb("#3a3a3a"))
        w = int(round(54 * max(0.0, min(1.0, fill))))
        if w > 0:
            d.rectangle((5, y, 4 + w, y + h - 1), fill=_rgb(color))
            d.rectangle((5, y, 4 + w, y + 1), fill=tuple(min(255, c + 70) for c in _rgb(color)))


def render_faces(lay: dict, ctx) -> dict:
    pen = Pen(ctx)
    pal = pen.pal
    good = pal.get("good", "#3ddc84")
    faces = {}
    for k, sp in lay["keys"].items():
        if sp["role"] == "step":
            im = pen.plate()
            pen.bar(im, 1.0 if sp["lit"] else 0.0, MASTER_COL if pen.mc else good, y=24, h=16)
            pen.text(im, 32, 46, f"{round(sp['step'] / sp['of'] * 100)}", "#8b8b8b" if not sp["lit"] else "#ffffff", 1)
        elif sp["role"] == "level":
            im = pen.plate()
            if pen.mc and not sp["master"]:
                pen.heart(im, sp["fill"], sp["muted"])
            else:
                pen.bar(im, sp["fill"], MASTER_COL if pen.mc else (good if not sp["muted"] else "#6a6a7a"))
        elif sp["role"] == "more":
            im = pen.plate()
            ImageDraw.Draw(im).rectangle((2, 2, 61, 6), fill=_rgb(sp["accent"]))
            pen.text(im, 32, 14, sp["label"], "#ffaa00", 3)
            pen.text(im, 32, 44, sp["app"], sp["accent"], 1)
        else:
            im = pen.plate("#4a1010" if sp["muted"] else None)
            ImageDraw.Draw(im).rectangle((2, 2, 61, 6), fill=_rgb(sp["accent"]))
            lab = sp["label"]
            sc = 2 if pen.width(lab, 2) <= 56 else 1
            pen.text(im, 32, 14, lab, "#ffffff", sc)
            pen.text(im, 32, 40, "MUTE" if sp["muted"] else f"{round(sp['volume'] * 100)}%",
                     "#ff5555" if sp["muted"] else "#aaaaaa", 2 if sp["muted"] else 1)
        faces[k] = {"image": im.resize((192, 192), Image.NEAREST)}
    return faces


# ------------------------------------------------------------------------------ the action
def open(ctx, **_):
    """Open the mixer on the pad. Closes 2.5 s after the last press; b1 closes it at once."""
    be = backend()
    now = time.time()
    last = ctx.daemon.plugin_state.setdefault("mixer.last_audible", {})
    apps = analyse(be.sessions(sample=True), now, last)
    st = {"apps": apps, "master": be.master(), "offset": 0}

    def build():
        st["lay"] = layout(st["apps"], st["master"], st["offset"])
        return render_faces(st["lay"], ctx)

    def on_key(c, key, down, t):
        if not down:
            return
        op = action_for(st["lay"], key)
        if op and op["op"] == "page":
            st["offset"] += op["value"]
        elif op:
            apply(op, be)
            fresh = {s["name"]: s for s in be.sessions(sample=False)}      # new volumes, same app set
            for a in st["apps"]:
                if a["name"] in fresh:
                    a["volume"], a["muted"] = fresh[a["name"]]["volume"], fresh[a["name"]]["muted"]
            st["master"] = be.master()
        c.modal_update(st["tok"], build(), CLOSE_S)

    faces = build()
    st["tok"] = ctx.modal(faces, on_key=on_key, timeout=CLOSE_S)
