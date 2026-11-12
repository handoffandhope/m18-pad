#!/usr/bin/env python3
"""m18d -- the M18 macro pad daemon.

Owns the pad, renders macros.yaml onto it, runs actions on key presses,
hot-reloads the config (bad edits are rejected and the last good config keeps
running), and exposes a localhost control API that m18ctl talks to.

    python m18d.py                 # real device (needs VSD Craft closed)
    python m18d.py --mock          # no hardware: simulate with `m18ctl press`
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import math
import os
import shlex
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from m18lib import cards as CS          # noqa: E402
from m18lib import config as C          # noqa: E402
from m18lib import keys as K            # noqa: E402
from m18lib import render as R          # noqa: E402
from m18lib import ringmap as RM        # noqa: E402
from m18lib.ringwalk import RingWalk    # noqa: E402
from m18lib.device import M18Device, MockDevice  # noqa: E402

DEFAULT_PORT = 8718
MODAL_MAX = 120.0            # seconds; a crashed client can never strand the pad longer
EVENT_RING = 500
RAW_LOG_MAX = 2 * 1024 * 1024
LED_MIN_DT = 0.05            # per-LED frames are capped at 20 fps
STALE_DOWN = 30.0            # a "down" older than this with no "up" is treated as lost
HEX_RE = C.HEX


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


class Ctx:
    """What a plugin function receives. Kept small and stable on purpose."""
    preview = False                    # True only in PreviewCtx (a one-off render for a preview)

    def __init__(self, daemon: "Daemon", key: int | None, page: str, source: str = "daemon"):
        self.daemon, self.key, self.page = daemon, key, page
        self.source = source           # "device" | "api" (who pressed) | "daemon" (live tick)
        self.config_dir = daemon.cfg_path.parent

    def log(self, *a):
        self.daemon.log("[plugin]", *a)

    def run(self, action):
        self.daemon.run_action(action, self.key)

    def goto(self, page):
        self.daemon.goto(page)

    def flash(self, face: dict, seconds: float = 0.6):
        self.daemon.flash(self.key, face, seconds)

    def modal(self, faces: dict, on_key=None, timeout: float = MODAL_MAX, yields: bool = False):
        """Take over the pad: show `faces` ({key: face}), blank the rest, and send every press
        to on_key(ctx, key, down, t) instead of running actions. Ends on timeout (max 120 s),
        ctx.end_modal(), or a physical press on b1. Returns a token for ctx.modal_update.
        yields=True marks an ambient takeover (a screensaver): any other owner's modal replaces
        it instead of getting 409, and its next modal_update returns False."""
        return self.daemon.modal_start(faces, on_key=on_key, timeout=timeout,
                                       owner=f"plugin:{self.key}", yields=yields)

    def modal_update(self, token, faces=None, timeout=None) -> bool:
        """Redraw the modal that ctx.modal returned `token` for (new faces and/or restart its
        timeout). False when that modal has already ended."""
        return self.daemon.modal_update(token, faces, timeout)

    def end_modal(self):
        self.daemon.modal_end("plugin")

    def rgb(self, color, brightness=None, ttl=None):
        """Set the ring now; with ttl (seconds) the page's own ring colour returns afterwards."""
        self.daemon.rgb_set(color, brightness, ttl)

    def leds(self, colors, ttl=None, brightness=None, owner=None, priority=1) -> bool:
        """Per-LED ring frame: 24 "#rrggbb" or None (off). The latest frame wins until it is
        released or its ttl ends, except that a lower `priority` (0 = ambient) never replaces a
        higher one. Returns False when it was ignored for that reason. Unchanged frames are not
        resent; frames are capped at 20 fps. Raises ApiError (409) in dark mode."""
        return self.daemon.leds_set(colors, ttl, brightness, owner or f"plugin:{self.key}", priority)

    def leds_release(self, owner=None) -> bool:
        """Give the ring back to the page colour (only if `owner` still holds it, when given)."""
        return self.daemon.leds_release(owner or f"plugin:{self.key}")

    @property
    def pressed_at(self):
        """When this call's key went down (epoch seconds), or None."""
        return self.daemon.down_at.get(self.key) if self.key else None

    def wait_release(self, timeout: float = 10.0) -> bool:
        """Block until this call's key is released: True, or False after `timeout` seconds.
        For a plugin action on a key without `hold:` (the action runs at key down)."""
        return self.daemon.wait_release(self.key, timeout)

    @property
    def theme(self) -> dict:          # active theme's PALETTE ({} without a theme)
        mod, _ = R.load_theme(self.daemon.cfg["settings"], self.daemon.base)
        return dict(getattr(mod, "PALETTE", {}) or {}) if mod else {}

    def theme_fn(self, name: str):
        """A drawing helper the active theme offers to plugins (e.g. "meters"), or None."""
        mod, opts = R.load_theme(self.daemon.cfg["settings"], self.daemon.base)
        fn = getattr(mod, name, None) if mod else None
        return (lambda *a, **k: fn(*a, opts=opts, **k)) if callable(fn) else None

    @property
    def state(self) -> dict:          # per-plugin scratch dict that survives reloads
        return self.daemon.plugin_state.setdefault(self.key or 0, {})

    @property
    def cards(self) -> "CardsApi":
        """The card store: ctx.cards.post(card, handler="mod.fn"), .remove(id), .list(feed=None),
        .get(id), .on_action(feed, fn(ctx, card, action_id)), .act(id, action_id). See references/cards.md."""
        return CardsApi(self.daemon, self.source)


class _PreviewDaemon:
    """What a preview render's ctx.daemon is: reads pass through to the daemon, but plugin_state is the
    daemon's separate preview scratch space, so a plugin's caches and threads never mix with the live ones."""

    def __init__(self, d):
        object.__setattr__(self, "_d", d)

    @property
    def plugin_state(self):
        return self._d.preview_state

    def __getattr__(self, name):
        return getattr(self._d, name)


class PreviewCtx(Ctx):
    """Ctx for a one-off live call made only to draw a preview (`ctx.preview` is True, source "preview").
    Nothing it does reaches the pad, the ring, pages, modals, cards or actions."""
    preview = True

    def __init__(self, daemon: "Daemon", key: int | None, page: str):
        super().__init__(_PreviewDaemon(daemon), key, page, "preview")

    def run(self, action): pass
    def goto(self, page): pass
    def flash(self, face, seconds=0.6): pass
    def modal(self, faces, on_key=None, timeout=MODAL_MAX, yields=False): return None
    def modal_update(self, token, faces=None, timeout=None): return False
    def end_modal(self): pass
    def rgb(self, color, brightness=None, ttl=None): pass
    def leds(self, colors, ttl=None, brightness=None, owner=None, priority=1): return False
    def leds_release(self, owner=None): return False
    def wait_release(self, timeout=10.0): return True

    @property
    def cards(self):
        raise ApiError("cards are not available in a preview render", 409)


class CardsApi:
    def __init__(self, daemon: "Daemon", source: str):
        self._d, self._src = daemon, source

    def post(self, card: dict, handler: str | None = None) -> dict:
        """Upsert a card (same fields as POST /cards). `handler` "module.fn" is stored with the card
        and called as fn(ctx, card, action_id) when one of its actions is pressed, even after a restart."""
        try:
            c = CS.validate(card, HEX_RE, lambda n: R.mdi_codepoint(n) is not None)
        except CS.CardError as e:
            raise ApiError(str(e), e.code)
        return self._d.cards.post(c, handler)

    def remove(self, cid: str) -> bool:
        return self._d.cards.remove(cid)

    def list(self, feed: str | None = None) -> list[dict]:
        return self._d.cards.ordered(feed)

    def get(self, cid: str):
        return self._d.cards.get(cid)

    def on_action(self, feed: str, fn) -> None:
        """In-process callback for this feed's actions (until the daemon restarts; takes
        precedence over a stored handler)."""
        self._d.cards.callbacks[feed] = fn

    def act(self, cid: str, action: str):
        """Resolve a card as if its action key was pressed (the takeover calls this)."""
        return self._d.cards.act(cid, action, self._src)


class Daemon:
    def __init__(self, cfg_path: Path, device, port: int, mock: bool = False,
                 skip_plugin_actions: bool = False):
        self.mock = mock                              # enables the test-only source=device hook
        self.skip_plugin_actions = skip_plugin_actions
        self.cfg_path = cfg_path.resolve()
        self.base = self.cfg_path.parent
        self.dev = device
        self.port = port
        self.cfg = None
        self.cfg_mtime = 0.0
        self.last_error: list[str] | None = None
        self.page = None
        self.page_stack: list[str] = []
        self.follow_return: str | None = None
        self.state_idx: dict[tuple[str, int], int] = {}
        self.live_faces: dict[tuple[str, int], dict] = {}
        self.live_next: dict[tuple[str, int], float] = {}
        self.overrides: dict[int, dict] = {}          # flash / countdown faces (temporary)
        self.ov_tok: dict[int, int] = {}              # generation per override: a stale clear can't remove a newer one
        self._tok = 0
        self.pushed: dict[int, dict] = {}             # {key: {"tok", "face", "until"}} from POST /face
        self.modal: dict | None = None                # {"owner","faces","on_key","until","tok","external"}
        self.swallowed: set[int] = set()              # keys whose down a modal ate: their up is eaten too
        self.modal_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="modal")
        self.events: deque = deque(maxlen=EVENT_RING)
        self.ev_seq = 0
        self.ev_cv = threading.Condition()
        self.rgb_tok = 0
        self.rgb_until: tuple[int, float] | None = None
        self.rgb_prev = None
        self.leds_owner: dict | None = None           # {"owner","prio","until","tok"}: who holds the ring
        self.leds_want: tuple | None = None           # frame the owner asked for
        self.leds_bri_want: int | None = None
        self.leds_sent: tuple | None = None           # last frame / brightness written to the pad
        self.leds_bri_sent: int | None = None
        self.leds_t = 0.0
        self.leds_dirty = False
        self.led_frames_sent = 0
        self.led_bri_sent_n = 0
        self.led_rc = None
        self.walk: RingWalk | None = None
        self.rel_cv = threading.Condition()
        self.rejected = 0
        self.sent_hash: dict[int, str] = {}
        self.faces_sent = 0                           # key images written to the pad (status; tests)
        self.down_at: dict[int, float] = {}
        self.press: dict[int, dict] = {}              # gesture keys that are down: what that press may still become
        self.taps: dict[int, dict] = {}               # double/triple keys waiting double_ms for another tap
        self.preview_live: dict[tuple[str, int], tuple[float, dict]] = {}   # one-off live faces for previews
        self.preview_state: dict = {}                 # plugin_state seen by PreviewCtx (kept apart from the live one)
        self.plugin_state: dict = {}
        self._plugins: dict[str, tuple[float, object]] = {}
        self._plugin_lock = threading.RLock()
        self._plugin_seen: dict[str, float] = {}      # when each plugin file was last looked at (plugin_fn)
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="act")
        self.lock = threading.RLock()
        self.logbuf: list[str] = []
        self.logfile = self.base / "m18d.log"
        self.stop = threading.Event()
        self.anim_next: dict[int, float] = {}
        self.dark_key: int | None = None             # set while `dark` mode is on
        self.live_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="live")
        self.live_busy: set = set()
        self.cards = CS.CardStore(self.base / "state", publish=self.publish_event,
                                  on_change=self._cards_changed, run_handler=self._card_handler,
                                  log=self.log)

    # ---------------------------------------------------------------- logging
    def log(self, *a):
        line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
        print(line, flush=True)
        self.logbuf.append(line)
        del self.logbuf[:-300]
        try:
            with open(self.logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ----------------------------------------------------------------- config
    def load_config(self, initial=False) -> bool:
        try:
            cfg = C.load(self.cfg_path)
        except C.ConfigError as e:
            self.last_error = e.errors
            self.log(f"config REJECTED ({len(e.errors)} error(s)); keeping previous config")
            for er in e.errors:
                self.log("   ", er)
            if initial:
                raise
            return False
        with self.lock:
            self.cfg = cfg
            self.last_error = None
            self._plugins.clear()                       # re-import plugins on reload
            if self.page not in cfg["pages"]:
                self.page = cfg["settings"]["home"]
                self.page_stack.clear()
            s = cfg["settings"]
            self.dev.set_brightness(int(cfg["pages"][self.page].get("brightness",
                                                                    s.get("brightness", 70))))
            self.apply_rgb()
            self.live_next.clear()
            self.log(f"config loaded: {len(cfg['pages'])} page(s), page={self.page}")
        self.redraw(force=True)
        return True

    def watch_config(self):
        while not self.stop.is_set():
            try:
                m = self.cfg_path.stat().st_mtime
                if m != self.cfg_mtime:
                    self.cfg_mtime = m
                    time.sleep(0.15)                    # let the editor finish writing
                    self.load_config()
                elif self.cfg:
                    ts = R.theme_stamp(self.cfg["settings"], self.base)
                    if ts != getattr(self, "_theme_seen", None):   # theme.py edited: redraw
                        self._theme_seen = ts
                        self.redraw()
            except FileNotFoundError:
                pass
            except Exception as e:
                self.log("watcher error:", e)
            time.sleep(0.5)

    # ------------------------------------------------------------------ faces
    def key_def(self, k: int, page: str | None = None) -> dict | None:
        return C.resolved_keys(self.cfg, page or self.page).get(k)

    def face_for(self, k: int, page: str | None = None, live: dict | None = None) -> dict | None:
        """Key k's face. For the current page: what the pad shows. For another `page`: that page's own
        face (base, state, live result, pushed face), read without touching any live state; `live`
        supplies live results for it (else the cached ones)."""
        if page is not None and page != self.page:
            kd = self.key_def(k, page)
            pf = self.pushed.get(k)                    # pushed faces survive a page change, so they show
            if pf and not (kd and kd.get("physical_only")):
                return self.animate(dict(pf["face"]), k, track=False)
            if not kd:
                return None
            face = {f: kd[f] for f in C.FACE_FIELDS if f in kd}
            if "states" in kd:
                i = self.state_idx.get((page, k), 0) % len(kd["states"])
                face.update({f: v for f, v in kd["states"][i].items() if f in C.FACE_FIELDS})
            lf = (live or {}).get(k) or self.live_faces.get((page, k))
            if lf:
                face.update(lf)
            return self.animate(face, k, track=False)
        if self.dark_key is not None:
            if k != self.dark_key:
                return {"bg": "#000000", "_plain": True}
            kd = self.key_def(k) or {}
            icon = kd.get("icon") if str(kd.get("icon", "")).startswith("mdi:") else "mdi:power"
            return {"icon": icon, "bg": "#000000", "fg": "#2c2c38", "label_pos": "none", "_plain": True}
        if self.modal is not None:
            return self.modal["faces"].get(k) or {"bg": "#000000", "_plain": True}
        if k in self.overrides:
            return self.overrides[k]
        kd = self.key_def(k)
        pf = self.pushed.get(k)
        if pf and not (kd and kd.get("physical_only")):   # a consent key's face can't be spoofed
            return self.animate(dict(pf["face"]), k)
        if not kd:
            return None
        face = {f: kd[f] for f in C.FACE_FIELDS if f in kd}
        if "states" in kd:
            i = self.state_idx.get((self.page, k), 0) % len(kd["states"])
            face.update({f: v for f, v in kd["states"][i].items() if f in C.FACE_FIELDS})
        lf = self.live_faces.get((self.page, k)) or (live or {}).get(k)
        if lf:
            face.update(lf)
        return self.animate(face, k)

    def animate(self, face: dict, k: int, track: bool = True) -> dict:
        """Pick the current frame for animated faces (GIF icon or plugin `frames`) and
        record when key k next changes frame, so watch_anim wakes only then (not for track=False,
        a face that is not on the pad)."""
        now = time.time()
        nxt = self.anim_next if track else {}
        frames = face.get("frames")
        if frames:
            fps = min(float(face.get("fps", 12) or 12), 30.0)
            t0 = float(face.get("t0", 0))
            n = int((now - t0) * fps)
            if face.get("loop", True):
                i = n % len(frames)
                nxt[k] = t0 + (n + 1) / fps
            else:
                i = min(n, len(frames) - 1)
                if n < len(frames) - 1:
                    nxt[k] = t0 + (n + 1) / fps
            face = {f: v for f, v in face.items() if f != "frames"}
            face["image"] = frames[i]
            face["_fid"] = (id(frames), i)      # cheap hash: frame identity, not pixels
            return face
        tl = R.gif_timeline(face, self.base)
        if tl:
            t = now % tl[-1]
            i = next((n for n, end in enumerate(tl) if t < end), 0)
            nxt[k] = now - t + tl[i]
            face = dict(face, _frame=i)
        return face

    def watch_anim(self):
        """Sleep until the next animated key changes frame, then redraw just the due keys.
        Costs nothing while no visible key animates."""
        while not self.stop.is_set():
            nxt = dict(self.anim_next)
            if not nxt or not self.cfg:
                time.sleep(0.05)
                continue
            wait = min(nxt.values()) - time.time()
            time.sleep(min(max(wait, 1 / 30), 0.25))
            now = time.time() + 0.002
            due = [k for k, t in nxt.items() if t <= now]
            if due:
                self.redraw(keys=due)

    def redraw(self, force=False, keys=None):
        with self.lock:
            if not self.cfg:
                return
            s = self.cfg["settings"]
            if keys is None:
                self.anim_next.clear()          # face_for re-registers what still animates
                keys = C.LCD_KEYS
            for k in keys:
                self.anim_next.pop(k, None)
                face = self.face_for(k) or ({} if R.theme_path(s, self.base)[0] else {"bg": "#000000"})
                h = R.face_hash(dict(face, _key=k, _theme=R.theme_stamp(s, self.base)))
                if force or self.sent_hash.get(k) != h:
                    try:
                        if self.dev.cached(h):
                            self.dev.set_face(k, None, h)
                        else:
                            self.dev.set_face(k, R.render_face(face, self.base, s, key=k), h)
                    except Exception as e:
                        self.log(f"render key {k} failed: {e}")
                        continue
                    self.sent_hash[k] = h
                    self.faces_sent += 1
            self.dev.flush()

    def blit(self, page: str, images: dict, owner: str = "blit") -> int:
        """Fast path for a plugin that animates whole keys itself (the tank): write ready 64x64 images straight to
        the pad, skipping face_for / face_hash / render_face. `images` is {key: (PIL image, digest)}. A key is
        written only while it really shows that page's own face (not dark, no modal, no override, no pushed face).
        The caller also keeps `live_faces` current, so any normal redraw shows the same picture. Returns the
        number of keys written."""
        n = 0
        with self.lock:
            if not self.cfg or self.page != page or self.dark_key is not None or self.modal is not None:
                return 0
            for k, (im, dg) in images.items():
                if k in self.overrides or k in self.pushed:
                    continue
                h = (owner, page, k, dg)
                if self.sent_hash.get(k) == h:
                    continue
                try:
                    self.dev.set_face(k, None if self.dev.cached(h) else im, h)
                except Exception as e:
                    self.log(f"blit key {k} failed: {e}")
                    continue
                self.sent_hash[k] = h
                self.faces_sent += 1
                n += 1
            if n:
                self.dev.flush()
        return n

    # ------------------------------------------------- temporary overrides (flash/countdown)
    def set_override(self, k: int, face: dict) -> int:
        """Show `face` on key k above everything but dark/modal; returns the generation token."""
        with self.lock:
            self._tok += 1
            self.overrides[k] = face
            self.ov_tok[k] = self._tok
            return self._tok

    def clear_override(self, k: int, tok: int, redraw=True):
        """Remove key k's override only if it is still the one `tok` made (race fix)."""
        with self.lock:
            if self.ov_tok.get(k) != tok:
                return
            self.overrides.pop(k, None)
            self.ov_tok.pop(k, None)
        if redraw:
            self.redraw()

    def flash(self, k, face, seconds=0.6):
        if not k:
            return
        tok = self.set_override(k, face)
        self.redraw()

        def clear():
            time.sleep(seconds)
            self.clear_override(k, tok)
        threading.Thread(target=clear, daemon=True).start()

    def set_dark(self, on: bool, k=None):
        if on:
            self.modal_end("dark")                  # dark wins; a modal can't start while dark is on
        self.dark_key = (k or 8) if on else None
        self.log(f"dark {'on' if on else 'off'}")
        if on:
            self._leds_drop()
            self.dev.set_rgb("off", None)
        else:
            self.apply_rgb()
        self.redraw()

    def apply_rgb(self):
        self.rgb_tok += 1                       # any pending ttl restore is now obsolete
        self.rgb_until = None
        if self.dark_key is not None:
            return
        if self.leds_owner is not None:         # a per-LED owner keeps the ring; the page colour
            return                              # (read fresh from the config) returns on release
        self._leds_forget()
        s = self.cfg["settings"]
        rgb = self.cfg["pages"][self.page].get("rgb", s.get("rgb"))
        if isinstance(rgb, dict):
            self.dev.set_rgb(rgb.get("color"), rgb.get("brightness"))
        elif rgb:
            self.dev.set_rgb(rgb, None)

    # ------------------------------------------------------------------ pages
    def goto(self, name: str, push=True):
        with self.lock:
            if name == "back":
                name = self.page_stack.pop() if self.page_stack else self.cfg["settings"]["home"]
                push = False
            elif name == "home":
                name = self.cfg["settings"]["home"]
            if name not in self.cfg["pages"] or name == self.page:
                return
            if push:
                self.page_stack.append(self.page)
                del self.page_stack[:-20]
            self.page = name
            self.overrides.clear()                  # temporary faces only; pushed faces and modals stay
            self.ov_tok.clear()
            self.live_next.clear()
            p = self.cfg["pages"][name]
            if "brightness" in p:
                self.dev.set_brightness(int(p["brightness"]))
            self.apply_rgb()
            self.log(f"page -> {name}")
        self.redraw()

    def watch_focus(self):
        """Auto-switch to a page whose `follow` lists the focused exe."""
        last = None
        while not self.stop.is_set():
            time.sleep(0.5)
            if not self.cfg or not self.cfg["settings"].get("follow_focus", True) or self.modal:
                continue                       # the page under a modal stays untouched
            exe = K.foreground_exe()
            if exe == last:
                continue
            last = exe
            target = None
            for name, p in self.cfg["pages"].items():
                if exe and exe in [x.lower() for x in p.get("follow", [])]:
                    target = name
                    break
            if target and target != self.page:
                if self.follow_return is None:
                    self.follow_return = self.page
                self.goto(target, push=False)
            elif not target and self.follow_return:
                ret, self.follow_return = self.follow_return, None
                if self.cfg["pages"][self.page].get("follow"):
                    self.goto(ret, push=False)

    # ------------------------------------------------------------------- live
    def watch_live(self):
        while not self.stop.is_set():
            time.sleep(0.02)
            if not self.cfg:
                continue
            now = time.time()
            page = self.page
            for k, kd in C.resolved_keys(self.cfg, page).items():
                lv = kd.get("live")
                if not lv:
                    continue
                key = (page, k)
                if key in self.live_busy or now < self.live_next.get(key, 0):
                    continue           # each key runs in its own worker; a slow one skips ticks
                self.live_next[key] = now + max(float(lv.get("interval", 2)), 0.03)
                self.live_busy.add(key)
                self.live_pool.submit(self._run_live, key, k, page, lv)

    def _run_live(self, key, k, page, lv):
        try:
            fn = self.plugin_fn(lv["plugin"])
            face = fn(Ctx(self, k, page), **(lv.get("args") or {})) or {}
            changed = face != self.live_faces.get(key)
            if changed:
                self.live_faces[key] = face
        except Exception as e:
            self.live_faces[key] = {"glyph": "!", "label": "plugin err", "bg": "#5a1010"}
            self.log(f"live {lv['plugin']} on key {k}: {e}")
            self.live_next[key] = time.time() + 10
            changed = True
        finally:
            self.live_busy.discard(key)
        if changed and page == self.page:
            self.redraw()

    def plugin_fn(self, ref: str):
        mod, fn = ref.rsplit(".", 1)
        cached = self._plugins.get(mod)
        now = time.monotonic()
        if cached and now - self._plugin_seen.get(mod, 0) < 0.5:     # look at the file twice a second at most
            return getattr(cached[1], fn)
        with self._plugin_lock:                # one load per change: eight live workers ask at the same moment
            path = self.base / "plugins" / f"{mod}.py"
            if not path.exists():
                path = HERE / "plugins" / f"{mod}.py"
            m = path.stat().st_mtime
            cached = self._plugins.get(mod)
            if not cached or cached[0] != m:
                spec = importlib.util.spec_from_file_location(f"m18plugin_{mod}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._plugins[mod] = cached = (m, module)
            self._plugin_seen[mod] = now
        return getattr(cached[1], fn)

    # ---------------------------------------------------------------- presses
    def _raw_log(self, t, hw, state, logical):
        """settings.log_raw: one line per device event; 2 MB cap, one rotation."""
        path = self.base / "raw-events.log"
        try:
            if path.exists() and path.stat().st_size > RAW_LOG_MAX:
                os.replace(path, self.base / "raw-events.log.1")
            with open(path, "a", encoding="ascii") as f:
                f.write(f"{int(t * 1000)} {hw} {state} {logical}\n")
        except OSError:
            pass

    def record_event(self, t, k, down, held_ms, source, handled_by, note=None):
        ev = {"seq": 0, "type": "press", "t": t, "key": k, "down": down, "page": self.page,
              "source": source, "handled_by": handled_by}
        if held_ms is not None:
            ev["held_ms"] = round(held_ms, 1)
        if note:
            ev["note"] = note
        with self.ev_cv:
            self.ev_seq += 1
            ev["seq"] = self.ev_seq
            self.events.append(ev)
            self.ev_cv.notify_all()

    def publish_event(self, ev: dict):
        """Any non-press event (card_action, ...) on the same /events ring and sequence."""
        ev = dict(ev, seq=0, t=ev.get("t", time.time()), page=self.page)
        with self.ev_cv:
            self.ev_seq += 1
            ev["seq"] = self.ev_seq
            self.events.append(ev)
            self.ev_cv.notify_all()

    # ------------------------------------------------------------------ cards
    def _cards_changed(self):
        """Refresh the card key(s) now instead of at their next interval."""
        if not self.cfg:
            return
        for k, kd in C.resolved_keys(self.cfg, self.page).items():
            lv = kd.get("live")
            if lv and str(lv.get("plugin", "")).startswith("cards."):
                self.live_next.pop((self.page, k), None)

    def _card_handler(self, fn, card, action, source):
        def run():
            try:
                f = self.plugin_fn(fn) if isinstance(fn, str) else fn
                f(Ctx(self, None, self.page, source), card, action)
            except Exception as e:
                self.log(f"card {card.get('id')} handler failed: {e}")
        self.pool.submit(run)

    def events_since(self, since: int, timeout: float) -> dict:
        deadline = time.time() + timeout
        with self.ev_cv:
            while True:
                if since > self.ev_seq:                       # daemon restarted: tell the client
                    return {"seq": self.ev_seq, "events": [], "reset": True}
                evs = [e for e in self.events if e["seq"] > since]
                left = deadline - time.time()
                if evs or left <= 0:
                    return {"seq": self.ev_seq, "events": evs}
                self.ev_cv.wait(min(left, 1.0))

    def logical(self, k: int, source: str) -> int:
        if source != "device" or not self.cfg:
            return k
        return int(self.cfg["settings"].get("key_map", {}).get(k, k))

    def on_press(self, k: int, down: bool, t: float | None = None, state: int | None = None,
                 source: str = "device") -> str:
        """Entry for every press. source is "device" (hardware callback, k is the hardware code and
        goes through key_map) or "api" (/press, k is already the logical key). Returns what
        happened: "action" | "modal" | "ignored" | "refused"."""
        t = t if t is not None else time.time()       # stamp before anything can take time
        if not self.cfg:
            return "ignored"
        st = self.cfg["settings"]
        hw = k
        k = self.logical(k, source)
        if source == "device" and st.get("log_raw"):
            self._raw_log(t, hw, state if state is not None else int(down), k)
        held_ms = None
        # down/up bookkeeping comes first so a mode change between them can't leave a stale down
        if down:
            prev = self.down_at.get(k)
            if prev is not None and t - prev < STALE_DOWN:
                self.record_event(t, k, True, None, source, "ignored", "duplicate down")
                return "ignored"
            self.down_at[k] = t
        else:
            t0 = self.down_at.pop(k, None)
            if t0 is not None:
                held_ms = (t - t0) * 1000
            with self.rel_cv:
                self.rel_cv.notify_all()                 # ctx.wait_release wakes here
        # a modal eats every press, and the release of any press it ate
        with self.lock:
            md = self.modal
            ate = k in self.swallowed
            if md is not None and down:
                self.swallowed.add(k)
            if not down:
                self.swallowed.discard(k)
        if md is not None or ate:
            if md is not None and down and source == "device" and k == 16 and md.get("home_exit", True):
                self.modal_end("home")                  # physical b1 ends a modal (ringwalk alone opts out)
            elif md is not None and md["on_key"]:
                self.modal_pool.submit(self._modal_cb, md["on_key"], k, down, t, source)
            self.record_event(t, k, down, held_ms, source, "modal")
            return "modal"
        if self.dark_key is not None and k != self.dark_key:
            self.record_event(t, k, down, held_ms, source, "ignored", "dark mode")
            return "ignored"             # everything but the dark key is off
        kd = self.key_def(k)
        if kd and kd.get("physical_only") and source != "device":
            if down:
                self.log(f"key {k} on {self.page}: refused: physical_only (source={source})")
            self.record_event(t, k, down, held_ms, source, "ignored", "refused: physical_only")
            return "refused"
        if down:
            res, note = self._gesture_down(k, kd, t, source) if kd else ("ignored", None)
        else:
            res, note = self._gesture_up(k, kd, held_ms)
        self.record_event(t, k, down, held_ms, source, res, note)
        return res

    # Gestures. A key with only `action` fires on key down, exactly as before. `hold` fires the moment
    # hold_ms passes with the key still down (a hold is a long FIRST press and never also counts as a tap).
    # `double` / `triple` make the key wait double_ms after each release for another tap; the last
    # gesture the key defines (double, or triple) fires on the down that completes it, without waiting.
    def _gesture_down(self, k, kd, t, source):
        multi = "double" in kd or "triple" in kd
        if not multi and "hold" not in kd:
            return self.fire(k, kd, "action", source), None
        st = self.cfg["settings"]
        with self.lock:
            pend = self.taps.pop(k, None)
            if pend and (pend["kd"] is not kd or pend["page"] != self.page):
                pend = None                             # the page or config changed between taps: start over
            n0 = pend["n"] if pend else 0
            # a sequence counts as a hardware press only if every tap in it was one
            src = source if not pend or pend["source"] == "device" else pend["source"]
            pr = {"t0": t, "kd": kd, "page": self.page, "source": src, "n0": n0, "held": False, "done": False}
            self.press[k] = pr
        if multi and n0 + 1 >= (3 if "triple" in kd else 2):
            pr["done"] = True
            return self._fire_taps(k, kd, n0 + 1, src), f"taps={n0 + 1}"
        if "hold" in kd and n0 == 0:
            def due(pr=pr):
                with self.lock:
                    if self.press.get(k) is not pr or self.page != pr["page"] or self.modal is not None:
                        return                          # released first (a tap), or the pad changed meanwhile
                    pr["held"] = True
                self.fire(k, kd, "hold", pr["source"])

            tm = threading.Timer(int(st.get("hold_ms", 450)) / 1000.0, due)
            tm.daemon = True
            tm.start()
        return "action", (f"taps={n0 + 1} so far" if n0 else None)

    def _gesture_up(self, k, kd_now, held_ms):
        with self.lock:
            pr = self.press.pop(k, None)
            held = pr is not None and pr["held"]
        if pr is None:                                  # a plain key (fired on down), or a down a mode ate
            return ("action" if kd_now else "ignored"), None
        kd = pr["kd"]
        if held:
            return "action", "hold"                     # its hold already ran while the key was down
        if pr["done"]:
            return "action", None
        if kd_now is not kd or pr["page"] != self.page:
            return "ignored", "pad changed during the press"
        thr = int(self.cfg["settings"].get("hold_ms", 450))
        if "hold" in kd and pr["n0"] == 0 and held_ms is not None and held_ms >= thr:
            return self.fire(k, kd, "hold", pr["source"]), "hold"   # released just as the hold timer was due
        n = pr["n0"] + 1
        if "double" not in kd and "triple" not in kd:
            return self.fire(k, kd, "action", pr["source"]), None
        with self.lock:
            self._tok += 1
            tok = self._tok
            self.taps[k] = {"n": n, "kd": kd, "page": pr["page"], "source": pr["source"], "tok": tok}
        tm = threading.Timer(int(self.cfg["settings"].get("double_ms", 250)) / 1000.0, self._taps_due, (k, tok))
        tm.daemon = True
        tm.start()
        return "action", f"taps={n} waiting"

    def _taps_due(self, k, tok):
        """double_ms passed after a release with no further tap: fire what the taps so far add up to."""
        with self.lock:
            pend = self.taps.get(k)
            if not pend or pend["tok"] != tok:
                return                                  # another tap came (or a newer sequence started)
            del self.taps[k]
            live = (pend["page"] == self.page and self.modal is None and self.key_def(k) is pend["kd"]
                    and self.dark_key in (None, k))
        if live:
            self._fire_taps(k, pend["kd"], pend["n"], pend["source"])
        else:
            self.log(f"key {k}: {pend['n']} tap(s) dropped (page, modal or config changed)")

    def _fire_taps(self, k, kd, n, source) -> str:
        if n >= 3 and "triple" in kd:
            return self.fire(k, kd, "triple", source)
        if n >= 2 and "double" in kd:
            return self.fire(k, kd, "double", source)
        res = "action"
        for _ in range(n):                              # e.g. two taps on a triple-only key: the single action twice
            res = self.fire(k, kd, "action", source)
        return res

    def wait_release(self, k, timeout: float) -> bool:
        """True once key k is up (or was never down), False if it is still down after `timeout`."""
        end = time.time() + max(0.0, float(timeout))
        with self.rel_cv:
            while k in self.down_at:
                left = end - time.time()
                if left <= 0:
                    return False
                self.rel_cv.wait(min(left, 0.5))
        return True

    def _modal_cb(self, cb, k, down, t, source):
        try:
            cb(Ctx(self, k, self.page, source), k, down, t)
        except Exception as e:
            self.log(f"modal on_key failed: {e}")

    def fire(self, k, kd, kind: str = "action", source: str = "device") -> str:
        """Run one of a key's gestures: kind is action | hold | double | triple."""
        # the single gate: a physical_only key's gestures run only for real hardware presses
        if kd.get("physical_only") and source != "device":
            self.log(f"key {k}: refused: physical_only (source={source})")
            return "refused"
        page = self.page
        if kind != "action":
            action = kd[kind]
        elif "states" in kd:
            i = self.state_idx.get((page, k), 0) % len(kd["states"])
            action = kd["states"][i].get("action", kd.get("action"))
            self.state_idx[(page, k)] = i + 1
            self.pool.submit(self.redraw)              # never draw on the SDK reader thread
        else:
            action = kd.get("action")
        self.log(f"key {k} on {page}{f' ({kind})' if kind != 'action' else ''}: {kd.get('label', '')!s}")
        if action is not None:
            self.pool.submit(self._safe_run, action, k, source)
        return "action"

    def _safe_run(self, action, k, source="daemon"):
        try:
            self.run_action(action, k, source)
        except Exception as e:
            self.log(f"action on key {k} failed: {e}")
            traceback.print_exc()
            self.flash(k, {"glyph": "✕", "label": "error", "bg": "#7a1010"}, 1.2)

    def run_action(self, a, k=None, source="daemon"):
        if isinstance(a, list):
            for step in a:
                self.run_action(step, k, source)
            return
        for _ in range(int(a.get("repeat", 1))):
            self._step(a, k, source)

    def _step(self, a: dict, k, source="daemon"):
        kind = next((x for x in a if x in C.ACTION_TYPES), None)
        if K.DRY and kind in ("page", "brightness", "rgb", "plugin", "notify", "delay"):
            K._log(f"{kind} {a[kind]}")
        if "hotkey" in a:
            K.send_combo(a["hotkey"], int(a.get("hold_ms", 0)))
        elif "key" in a:
            K.send_combo(a["key"], int(a.get("hold_ms", 0)))
        elif "type" in a:
            K.type_text(str(a["type"]))
        elif "paste" in a:
            self._paste(str(a["paste"]))
        elif "media" in a:
            K.send_combo(K.MEDIA[a["media"]])
        elif "delay" in a:
            time.sleep(int(a["delay"]) / 1000)
        elif "countdown" in a:
            n = int(a["countdown"])
            base = {f: v for f, v in (self.face_for(k) or {}).items() if f != "image"}
            tok = None
            for i in range(n, 0, -1):
                if k:
                    face = {**base, "glyph": str(i), "emoji": None, "icon": None,
                            "label": base.get("label", ""), "border": "#ffd400"}
                    tok = self.set_override(k, {f: v for f, v in face.items() if v is not None})
                    self.redraw()
                K._log(f"countdown {i}") if K.DRY else None
                time.sleep(1)
            if k and tok is not None:
                self.clear_override(k, tok)
        elif "page" in a:
            self.goto(a["page"])
        elif "open" in a:
            self._open(str(a["open"]))
        elif "run" in a:
            self._run(a["run"], a.get("args"))
        elif "shell" in a:
            self._shell(str(a["shell"]))
        elif "plugin" in a:
            if self.skip_plugin_actions:
                self.log(f"plugin {a['plugin']} (skipped: dry run)")
                K._log(f"plugin {a['plugin']} (skipped)")
            else:
                self.plugin_fn(a["plugin"])(Ctx(self, k, self.page, source), **(a.get("args") or {}))
        elif "brightness" in a:
            v = a["brightness"]
            cur = int(self.dev.current_brightness or 70)
            new = cur + int(v) if isinstance(v, str) and v[:1] in "+-" else int(v)
            self.dev.set_brightness(max(0, min(100, new)))
        elif "rgb" in a:
            v = a["rgb"]
            self.rgb_tok += 1
            self.rgb_until = None
            self._leds_drop()
            if isinstance(v, dict):
                self.dev.set_rgb(v.get("color"), v.get("brightness"))
            else:
                self.dev.set_rgb(v, None)
        elif "dark" in a:
            v = a["dark"]
            self.set_dark(self.dark_key is None if v == "toggle" else v == "on", k)
        elif "notify" in a:
            self.log("[notify]", a["notify"])

    def _open(self, target: str):
        target = os.path.expandvars(os.path.expanduser(target))
        if K.DRY:
            K._log(f"open {target}")
            return
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
        else:
            os.startfile(target)  # type: ignore[attr-defined]  # files, folders, URIs, apps

    def _run(self, cmd, args=None):
        argv = cmd if isinstance(cmd, list) else shlex.split(cmd, posix=False)
        argv = [os.path.expandvars(os.path.expanduser(x)).strip('"') for x in argv]
        if K.DRY:
            K._log(f"run {argv}")
            return
        flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen(argv, creationflags=flags, close_fds=True, cwd=str(Path.home()))

    def _shell(self, cmd):
        if K.DRY:
            K._log(f"shell {cmd}")
            return
        subprocess.Popen(["cmd", "/c", cmd], creationflags=0x08000000,  # CREATE_NO_WINDOW
                         cwd=str(Path.home()))

    def _paste(self, text):
        if K.DRY:
            K._log(f"paste {text!r}")
            return
        p = subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                           input=text, text=True, creationflags=0x08000000)
        if p.returncode == 0:
            K.send_combo("ctrl+v")

    # ------------------------------------------------- push / modal / ring (the "two-way" layer)
    def _clean_face(self, face, where: str, allow_image=False) -> dict:
        if not isinstance(face, dict):
            raise ApiError(f"{where}: face must be an object")
        extra = {"image", "frames", "fps", "loop", "t0"} if allow_image else set()
        bad = [f for f in face if f not in C.FACE_FIELDS and f not in extra]
        if bad:
            raise ApiError(f"{where}: unknown face field(s) {bad}; allowed {sorted(C.FACE_FIELDS)}")
        errs: list[str] = []
        for f in ("label", "icon", "emoji", "glyph", "label_pos"):
            if f in face and (not isinstance(face[f], str) or len(face[f]) > 200):
                errs.append(f"{where}.{f}: string up to 200 chars")
        if "font_size" in face and not (isinstance(face["font_size"], (int, float))
                                        and not isinstance(face["font_size"], bool)
                                        and 6 <= face["font_size"] <= 40):
            errs.append(f"{where}.font_size: number 6-40")
        if not errs:
            C._check_face({k: v for k, v in face.items() if k in C.FACE_FIELDS}, where, errs)
        if errs:
            raise ApiError("; ".join(errs))
        return dict(face)

    @staticmethod
    def _decode_image(b64, size: tuple[int, int]):
        from PIL import Image
        if not isinstance(b64, str):
            raise ApiError("image_b64: base64 string of a PNG or JPEG")
        try:
            im = Image.open(io.BytesIO(base64.b64decode(b64)))
            if im.format not in ("PNG", "JPEG") or im.width > 4096 or im.height > 4096:
                raise ValueError("need a PNG or JPEG up to 4096x4096")
            im.load()
            return im.convert("RGB").resize(size, Image.LANCZOS)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f"image_b64: {e}")

    @staticmethod
    def _tiles(im) -> dict[int, object]:
        """A 320x192 image -> the 15 LCD tiles, row-major."""
        return {1 + r * 5 + c: im.crop((c * 64, r * 64, c * 64 + 64, r * 64 + 64))
                for r in range(3) for c in range(5)}

    @staticmethod
    def _key_list(spec, allow_all=True) -> list[int]:
        if spec == "all" and allow_all:
            return list(C.LCD_KEYS)
        items = spec if isinstance(spec, list) else [spec]
        out = []
        for x in items:
            if isinstance(x, bool):
                raise ApiError("key: 1-18, 'all', or a list of those")
            n = C.norm_key(x)
            if n is None:
                raise ApiError(f"key {x!r}: need 1-18 (16-18 = b1-b3), 'all', or a list")
            out.append(n)
        if not out:
            raise ApiError("key: empty list")
        return out

    @staticmethod
    def _ttl(v, what="ttl", cap=86400.0):
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ApiError(f"{what}: positive number of seconds or null")
        return min(float(v), cap)

    def face_push(self, body: dict) -> dict:
        keys = self._key_list(body.get("key"))
        face_in, b64 = body.get("face"), body.get("image_b64")
        if face_in is None and b64 is None:
            raise ApiError("need face and/or image_b64")
        face = self._clean_face(face_in or {}, "face")
        ttl = self._ttl(body.get("ttl"))
        tiles, one = {}, None
        if b64 is not None:
            if body.get("key") == "all":
                tiles = self._tiles(self._decode_image(b64, (320, 192)))
            else:
                one = self._decode_image(b64, (128, 128))
        lcd = [k for k in keys if k in C.LCD_KEYS]
        now = time.time()
        with self.lock:
            skipped = [k for k in lcd if (self.key_def(k) or {}).get("physical_only")]
            for k in lcd:
                self._tok += 1
                f = dict(face)
                if k in tiles:
                    f["image"] = tiles[k]
                elif one is not None:
                    f["image"] = one
                self.pushed[k] = {"tok": self._tok, "face": f, "until": now + ttl if ttl else None}
        self.redraw()
        return {"ok": True, "keys": lcd, "ttl": ttl,
                "no_screen": [k for k in keys if k not in C.LCD_KEYS],
                "not_shown_physical_only": skipped}

    def face_clear(self, spec) -> list[int]:
        keys = self._key_list(spec)
        with self.lock:
            gone = [k for k in keys if self.pushed.pop(k, None) is not None]
        if gone:
            self.redraw()
        return gone

    def _modal_faces(self, faces, image=None, external=False) -> dict:
        if not isinstance(faces, dict):
            raise ApiError("faces: object {key: face}")
        out: dict[int, dict] = {}
        base = self._tiles(image) if image is not None else {}
        for k, v in base.items():
            out[k] = {"image": v}
        for rk, fv in faces.items():
            k = C.norm_key(rk)
            if k is None or k not in C.LCD_KEYS:
                raise ApiError(f"faces.{rk}: LCD keys 1-15 only")
            out[k] = self._clean_face(fv, f"faces.{rk}", allow_image=not external)
        return out

    def modal_update(self, tok, faces=None, timeout=None) -> bool:
        """Change the modal identified by `tok` (from modal_start) in place: new faces and/or a
        fresh timeout counted from now. False if that modal is gone."""
        out = self._modal_faces(faces) if faces is not None else None
        tm = self._ttl(timeout, "timeout", MODAL_MAX) if timeout is not None else None
        with self.lock:
            md = self.modal
            if md is None or md["tok"] != tok:
                return False
            if out is not None:
                md["faces"] = out
            if tm:
                md["until"] = time.time() + tm
        if out is not None:
            self.redraw()
        return True

    def modal_start(self, faces: dict, on_key=None, timeout=None, owner="api",
                    image=None, external=False, home_exit=True, yields=False):
        if timeout is None:
            timeout = MODAL_MAX
        timeout = self._ttl(timeout, "timeout", MODAL_MAX)
        out = self._modal_faces(faces, image, external)
        replaced = None
        with self.lock:
            if self.dark_key is not None:
                raise ApiError("dark mode is on; wake the pad first", 409)
            if self.modal and self.modal["owner"] != owner:
                if not self.modal.get("yields"):
                    raise ApiError(f"another modal is up (owner {self.modal['owner']!r})", 409)
                replaced = self.modal["owner"]      # an ambient modal gives way; its token goes stale
            self._tok += 1
            self.modal = {"owner": owner, "faces": out, "on_key": on_key, "tok": self._tok,
                          "until": time.time() + timeout, "external": external,
                          "home_exit": home_exit, "yields": bool(yields)}
            tok = self._tok
        if replaced:
            self.log(f"modal ended (replaced by {owner}) owner={replaced}")
        self.log(f"modal start owner={owner} keys={sorted(out)} timeout={timeout:g}s")
        self.redraw()
        return tok

    def modal_end(self, reason="api", tok=None) -> bool:
        with self.lock:
            if tok is not None and (self.modal is None or self.modal["tok"] != tok):
                return False
            md, self.modal = self.modal, None
        if md is None:
            return False
        self.log(f"modal ended ({reason}) owner={md['owner']}")
        self.redraw()                       # the page underneath was never touched: just repaint it
        return True

    def rgb_set(self, color, brightness=None, ttl=None):
        if self.dark_key is not None:
            raise ApiError("dark mode is on; ring stays off", 409)
        if color != "off" and not (isinstance(color, str) and HEX_RE.match(color)):
            raise ApiError("color: '#rrggbb' or 'off'")
        if brightness is not None and not (isinstance(brightness, int) and not isinstance(brightness, bool)
                                           and 0 <= brightness <= 100):
            raise ApiError("brightness: integer 0-100 or null")
        ttl = self._ttl(ttl, "ttl", 3600.0)
        if self.rgb_until is None and self.leds_owner is None:
            self.rgb_prev = getattr(self.dev, "ring", None)
        self._leds_drop()                       # a whole-ring colour is a frame too: latest wins
        self.dev.set_rgb(color, brightness)
        self.rgb_tok += 1
        self.rgb_until = (self.rgb_tok, time.time() + ttl) if ttl else None

    def _restore_ring(self):
        if self.leds_owner is not None:
            return
        self.apply_rgb()
        s = self.cfg["settings"]
        if not (self.cfg["pages"][self.page].get("rgb", s.get("rgb"))) and self.rgb_prev \
                and self.dark_key is None:
            self.dev.set_rgb(*self.rgb_prev)

    # ------------------------------------------------------------- per-LED ring (one owner at a time)
    @staticmethod
    def _norm_leds(colors) -> tuple:
        if not isinstance(colors, (list, tuple)) or len(colors) != RM.N_LEDS:
            raise ApiError(f"colors: list of {RM.N_LEDS} '#rrggbb' strings (null or 'off' = off)")
        out = []
        for i, c in enumerate(colors):
            if c is None or c == "off":
                out.append((0, 0, 0))
            elif isinstance(c, str) and HEX_RE.match(c):
                out.append(RM.parse_hex(c))
            else:
                raise ApiError(f"colors[{i}]: '#rrggbb', null or 'off'")
        return tuple(out)

    def _leds_forget(self):
        """A whole-ring write (or a release) makes what we last sent unknown: send in full next time."""
        self.leds_sent = None
        self.leds_bri_sent = None

    def _leds_drop(self):
        self.leds_owner = None
        self.leds_want = None
        self.leds_dirty = False
        self._leds_forget()

    def _led_bri(self) -> int:
        if self.leds_bri_want is not None:
            return self.leds_bri_want
        pg = self.cfg["pages"][self.page].get("rgb", self.cfg["settings"].get("rgb")) if self.cfg else None
        if isinstance(pg, dict) and pg.get("brightness") is not None:
            return int(pg["brightness"])
        ring = getattr(self.dev, "ring", None)
        return int(ring[1]) if ring and ring[1] is not None else 60

    def leds_set(self, colors, ttl=None, brightness=None, owner="api", priority=1) -> bool:
        """Show a 24-LED frame. One owner holds the ring at a time: the latest frame wins (a new
        owner replaces the old one) until it is released or its ttl ends, except that a frame with a
        lower `priority` than the live owner's is ignored (returns False). Dark mode refuses (409).
        The page's `rgb:` is left alone while an owner holds the ring and returns on release."""
        frame = self._norm_leds(colors)
        ttl = self._ttl(ttl, "ttl", 3600.0)
        if brightness is not None and not (isinstance(brightness, int) and not isinstance(brightness, bool)
                                           and 0 <= brightness <= 100):
            raise ApiError("brightness: integer 0-100 or null")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 2:
            raise ApiError("priority: 0 (ambient), 1 (default) or 2 (foreground)")
        if not isinstance(owner, str) or not owner:
            raise ApiError("owner: non-empty string")
        if not hasattr(self.dev, "set_leds"):
            raise ApiError("this device has no per-LED call")
        with self.lock:
            if self.dark_key is not None:
                raise ApiError("dark mode is on; ring stays off", 409)
            now = time.time()
            cur = self.leds_owner
            if cur and cur["until"] and now >= cur["until"]:
                cur = None
            if cur and cur["owner"] != owner and priority < cur["prio"]:
                return False
            if cur is None and self.rgb_until is None:
                self.rgb_prev = getattr(self.dev, "ring", None)
            self._tok += 1
            self.leds_owner = {"owner": owner, "prio": priority, "tok": self._tok,
                               "until": now + ttl if ttl else None}
            self.rgb_tok += 1
            self.rgb_until = None                 # a pending /rgb ttl restore no longer applies
            self.leds_want, self.leds_bri_want = frame, brightness
            self._leds_push()
        return True

    def leds_release(self, owner=None, reason="released") -> bool:
        """Give the ring back to the page colour. With `owner`, only if that owner still holds it."""
        with self.lock:
            cur = self.leds_owner
            if cur is None or (owner is not None and cur["owner"] != owner):
                return False
            self._leds_drop()
            if self.dark_key is None and self.cfg:
                self._restore_ring()
        self.log(f"ring: {cur['owner']} {reason}")
        return True

    def _leds_push(self):
        """Write the wanted frame if it differs from what the pad has and 50 ms have passed
        (else mark it dirty for watch_ring). Colour first, then brightness, each only if changed."""
        with self.lock:
            if self.leds_want is None:
                self.leds_dirty = False
                return
            frame, bri = self.leds_want, self._led_bri()
            if frame == self.leds_sent and bri == self.leds_bri_sent:
                self.leds_dirty = False
                return
            if time.time() - self.leds_t < LED_MIN_DT:
                self.leds_dirty = True
                return
            try:
                if frame != self.leds_sent:
                    self.led_rc = self.dev.set_leds(list(frame))
                    self.led_frames_sent += 1
                    self.leds_sent = frame
                if bri != self.leds_bri_sent:
                    self.dev.set_led_brightness(bri)
                    self.led_bri_sent_n += 1
                    self.leds_bri_sent = bri
            except Exception as e:
                self.log("per-LED write failed:", e)
            self.leds_t = time.time()
            self.leds_dirty = False

    def watch_ring(self):
        while not self.stop.is_set():
            time.sleep(0.02)
            try:
                if self.leds_dirty:
                    self._leds_push()
            except Exception as e:
                self.log("ring error:", e)

    def ringtest(self, seconds=6.0) -> dict:
        """One 24-LED frame (0-11 red, 12-23 blue), then the page colour comes back."""
        self.leds_set(["#ff0000"] * 12 + ["#0000ff"] * 12, ttl=seconds, owner="ringtest")
        self.log(f"ringtest: frame sent (sdk returned {self.led_rc!r}); restoring in {seconds:g}s")
        return {"ok": True, "sdk_return": self.led_rc, "seconds": seconds,
                "expect": "LEDs 0-11 red, 12-23 blue"}

    def leds_info(self) -> dict:
        cur = self.leds_owner
        hx = lambda fr: None if fr is None else [RM.to_hex(c) if c != (0, 0, 0) else None for c in fr]  # noqa: E731
        return {"owner": cur["owner"] if cur else None, "priority": cur["prio"] if cur else None,
                "ttl_left": round(cur["until"] - time.time(), 2) if cur and cur["until"] else None,
                "want": hx(self.leds_want), "sent": hx(self.leds_sent),
                "frames_sent": self.led_frames_sent, "brightness_sent": self.led_bri_sent_n}

    def walk_start(self, idle_timeout=None) -> dict:
        it = 60.0 if idle_timeout is None else self._ttl(idle_timeout, "idle_timeout", 60.0)
        if it < 1:
            raise ApiError("idle_timeout: 1-60 seconds")
        with self.lock:
            if self.walk and self.walk.state == "running":
                raise ApiError("a ringwalk is already running", 409)
            if self.dark_key is not None:
                raise ApiError("dark mode is on; wake the pad first", 409)
            self.walk = RingWalk(self, it)
            try:
                self.walk.start()
            except ApiError:
                self.walk = None
                raise
        return self.walk.info()

    def walk_abort(self):
        if self.walk:
            self.walk.abort("aborted by api")

    def watch_timers(self):
        """Expire pushed faces, end timed-out modals, restore the ring after a ttl."""
        while not self.stop.is_set():
            time.sleep(0.1)
            try:
                now = time.time()
                changed = False
                with self.lock:
                    for k, pf in list(self.pushed.items()):
                        if pf["until"] and now >= pf["until"]:
                            del self.pushed[k]
                            changed = True
                    md = self.modal
                if md is not None and now >= md["until"]:
                    self.modal_end("timeout")
                self.cards.expire(now)
                lo = self.leds_owner
                if lo and lo["until"] and now >= lo["until"] and self.cfg:
                    self.leds_release(lo["owner"], "ttl ended")
                ru = self.rgb_until
                if ru and now >= ru[1] and ru[0] == self.rgb_tok and self.cfg:
                    self.rgb_until = None
                    self._restore_ring()
                if changed:
                    self.redraw()
            except Exception as e:
                self.log("timer error:", e)

    # ---------------------------------------------------------------- preview
    def preview(self, page: str | None = None, out: Path | None = None) -> Path:
        """PNG of any page, side-effect free: self.page, the live caches and the device are never touched
        (the old version swapped self.page, which could flicker the pad or undo a page change). A live key
        with no cached face for that page gets a one-off plugin call; its result is kept apart in
        preview_live for one interval (at least 1 s) and never drawn on the pad."""
        cfg = self.cfg
        page = page or self.page
        if page not in cfg["pages"]:
            raise ApiError(f"no page {page!r} (pages: {list(cfg['pages'])})")
        live: dict[int, dict] = {}
        now = time.time()
        for k, kd in C.resolved_keys(cfg, page).items():
            lv = kd.get("live")
            if not lv or (page, k) in self.live_faces:
                continue
            hit = self.preview_live.get((page, k))
            if hit and now - hit[0] < max(float(lv.get("interval", 2)), 1.0):
                live[k] = hit[1]
                continue
            try:
                live[k] = self.plugin_fn(lv["plugin"])(PreviewCtx(self, k, page), **(lv.get("args") or {})) or {}
            except Exception as e:
                live[k] = {"glyph": "!", "label": "plugin err", "bg": "#5a1010"}
                print(f"live plugin error on key {k}: {e}", file=sys.stderr)
            self.preview_live[(page, k)] = (now, live[k])
        faces = {k: self.face_for(k, page, live) for k in C.LCD_KEYS}
        faces = {k: v for k, v in faces.items() if v}
        buttons = {k: str((self.key_def(k, page) or {}).get("label", "")) for k in C.BUTTON_KEYS}
        rgb = cfg["pages"][page].get("rgb", cfg["settings"].get("rgb"))
        if isinstance(rgb, dict):
            rgb = rgb.get("color")
        out = out or (self.base / "preview" / f"{page}.png")
        return R.render_preview(faces, page, self.base, cfg["settings"], rgb, out, buttons=buttons)

    # -------------------------------------------------------------------- api
    def serve(self):
        d = self
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

        def secs(q, name, default):
            v = q.get(name)
            if v is None:
                return default
            try:
                f = float(v)
            except ValueError:
                raise ApiError(f"{name}: number of seconds")
            if not math.isfinite(f) or f < 0:
                raise ApiError(f"{name}: number of seconds >= 0")
            return min(f, 10.0)

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj, indent=2, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b)

            def _guard(self) -> bool:
                """Browsers can reach 127.0.0.1, so every method needs: no Origin header (pages
                always send one on cross-site writes), a Host that is us (blocks DNS rebinding),
                and the X-M18: 1 header that a page cannot add without a preflight we refuse."""
                h = self.headers
                why = None
                if h.get("Origin") is not None:
                    why = "Origin header present"
                elif (h.get("Host") or "").lower() not in hosts:
                    why = "Host must be 127.0.0.1:<port> or localhost:<port>"
                elif h.get("X-M18") != "1":
                    why = "missing X-M18: 1 header"
                if why:
                    d.rejected += 1
                    self._json({"error": f"forbidden: {why}"}, 403)
                    return False
                return True

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 12 * 1024 * 1024:
                    raise ApiError("body too large", 413)
                if not n:
                    return {}
                try:
                    b = json.loads(self.rfile.read(n))
                except ValueError as e:
                    raise ApiError(f"body is not valid JSON: {e}")
                if not isinstance(b, dict):
                    raise ApiError("body must be a JSON object")
                return b

            def _dispatch(self, method):
                if not self._guard():
                    return
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    r = self._route(method, u.path, q)
                except ApiError as e:
                    return self._json({"error": str(e)}, e.code)
                except Exception as e:
                    d.log(f"api {method} {u.path} failed: {e}")
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
                if r is not None:
                    self._json(r[0], r[1]) if isinstance(r, tuple) else self._json(r)

            do_GET = lambda self: self._dispatch("GET")          # noqa: E731
            do_POST = lambda self: self._dispatch("POST")        # noqa: E731
            do_DELETE = lambda self: self._dispatch("DELETE")    # noqa: E731
            do_PUT = lambda self: self._dispatch("PUT")          # noqa: E731
            do_PATCH = lambda self: self._dispatch("PATCH")      # noqa: E731
            do_OPTIONS = lambda self: self._dispatch("OPTIONS")  # noqa: E731
            do_HEAD = lambda self: self._dispatch("HEAD")        # noqa: E731

            def _route(self, method, path, q):
                if method == "GET":
                    if path == "/status":
                        return d.status()
                    if path == "/preview":
                        try:
                            return {"path": str(d.preview(q.get("page")))}
                        except Exception as e:
                            raise ApiError(str(e))
                    if path == "/leds":
                        return d.leds_info()
                    if path == "/cards":
                        return d.cards.public(q.get("feed"))
                    if path == "/ringwalk":
                        return d.walk.info() if d.walk else {"state": "idle"}
                    if path == "/events":
                        try:
                            since = int(q.get("since", 0))
                            tmo = float(q.get("timeout", 0))
                            if tmo != tmo or tmo in (float("inf"), float("-inf")):
                                raise ValueError("timeout must be finite")
                            tmo = min(max(tmo, 0.0), 25.0)
                        except ValueError:
                            raise ApiError("since: integer, timeout: seconds (max 25)")
                        return d.events_since(since, tmo)
                elif method == "POST":
                    if path == "/press":
                        return self._press(q)
                    if path == "/page":
                        d.goto(q.get("name", "home"))
                        return {"page": d.page}
                    if path == "/reload":
                        ok = d.load_config()
                        return {"ok": ok, "errors": d.last_error}
                    if path == "/identify":
                        d.identify(float(q.get("seconds", 8)))
                        return {"ok": True}
                    if path == "/quit":
                        self._json({"ok": True})
                        d.stop.set()
                        return None
                    if path == "/face":
                        return d.face_push(self._body())
                    if path == "/cards":
                        try:
                            c = CS.validate(self._body(), HEX_RE, lambda n: R.mdi_codepoint(n) is not None)
                        except CS.CardError as e:
                            raise ApiError(str(e), e.code)
                        return d.cards.post(c)
                    if path == "/modal":
                        b = self._body()
                        img = d._decode_image(b["image_b64"], (320, 192)) if b.get("image_b64") else None
                        owner = b.get("owner", "api")
                        if not isinstance(owner, str) or not owner:
                            raise ApiError("owner: non-empty string")
                        d.modal_start(b.get("faces") or {}, timeout=b.get("timeout"), owner=owner,
                                      image=img, external=True)
                        return {"ok": True, "timeout": min(b.get("timeout") or MODAL_MAX, MODAL_MAX)}
                    if path == "/rgb":
                        b = self._body()
                        d.rgb_set(b.get("color"), b.get("brightness"), b.get("ttl"))
                        return {"ok": True}
                    if path == "/ringtest":
                        return d.ringtest()
                    if path == "/leds":
                        b = self._body()
                        owner = b.get("owner", "api")
                        shown = d.leds_set(b.get("colors"), b.get("ttl"), b.get("brightness"), owner,
                                           b.get("priority", 1))
                        if not shown:
                            raise ApiError(f"ring is held by {d.leds_owner['owner']!r} at a higher priority", 409)
                        return {"ok": True, "owner": owner}
                    if path == "/ringwalk":
                        b = self._body()
                        return d.walk_start(b.get("idle_timeout"))
                elif method == "DELETE":
                    if path == "/leds":
                        return {"ok": True, "released": d.leds_release(q.get("owner"), "released by api")}
                    if path == "/ringwalk":
                        d.walk_abort()
                        return {"ok": True, "walk": d.walk.info() if d.walk else None}
                    if path == "/face":
                        if "key" not in q:
                            raise ApiError("key=<1-18|all|1,2,3> required")
                        spec = q["key"] if q["key"] == "all" else [x for x in q["key"].split(",") if x]
                        return {"ok": True, "removed": d.face_clear(spec)}
                    if path == "/modal":
                        return {"ok": True, "ended": d.modal_end("api")}
                    if path == "/cards":
                        if not q.get("id"):
                            raise ApiError("id=<card id> required")
                        return {"ok": True, "removed": d.cards.remove(q["id"])}
                raise ApiError("unknown", 404)

            def _press(self, q):
                k = C.norm_key(q.get("key", ""))
                if k is None:
                    raise ApiError("key 1-18 or b1-b3")
                hold, wait = secs(q, "hold", 0.0), secs(q, "wait", 0.3)
                src = q.get("source", "api")
                if src not in ("api", "device") or (src == "device" and not d.mock):
                    raise ApiError("source: only 'api' (source=device exists in --mock only)")
                if q.get("page"):
                    d.goto(q["page"])
                phase = q.get("phase", "both")          # test hook: only one half of a press
                if phase not in ("both", "down", "up") or (phase != "both" and not d.mock):
                    raise ApiError("phase: only 'both' (down/up exist in --mock only)")
                try:
                    taps = int(q.get("taps", 1))
                except ValueError:
                    taps = 0
                if not 1 <= taps <= 3 or (taps > 1 and phase != "both"):
                    raise ApiError("taps: 1-3 (with phase=both)")
                gap = secs(q, "gap", 0.08)                    # up-to-next-down between taps
                mark = len(K.LOG)
                rs = []
                for i in range(taps):
                    if i:
                        time.sleep(gap)
                    rs.append(d.on_press(k, True, source=src) if phase != "up" else None)
                    time.sleep(hold)
                    rs.append(d.on_press(k, False, source=src) if phase != "down" else None)
                time.sleep(wait)                              # let short actions finish
                if "refused" in rs:
                    return {"error": "refused: physical_only", "pressed": k, "source": src}, 403
                by = "modal" if "modal" in rs else "action" if "action" in rs else "ignored"
                return {"pressed": k, "page": d.page, "source": src, "handled_by": by,
                        "dry_run": K.DRY, "sent": K.LOG[mark:]}

        srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.log(f"control API on http://127.0.0.1:{self.port}")

    def identify(self, seconds):
        """Show each key's logical number so physical layout can be confirmed."""
        toks = {k: self.set_override(k, {"glyph": str(k), "bg": "#203a5a", "label": "key"})
                for k in C.LCD_KEYS}
        self.redraw()

        def clear():
            time.sleep(seconds)
            for k, t in toks.items():
                self.clear_override(k, t, redraw=False)
            self.redraw()
        threading.Thread(target=clear, daemon=True).start()

    def status(self):
        return {
            "device": self.dev.name, "connected": self.dev.connected, "dry_run": K.DRY,
            "config": str(self.cfg_path), "page": self.page,
            "pages": list(self.cfg["pages"]) if self.cfg else [],
            "config_errors": self.last_error, "log": self.logbuf[-25:],
            "modal": ({"owner": self.modal["owner"],
                       "seconds_left": round(self.modal["until"] - time.time(), 1)}
                      if self.modal else None),
            "pushed_keys": sorted(self.pushed), "ring": getattr(self.dev, "ring", None),
            "leds": self.leds_info() if self.leds_owner else None,
            "ring_map": RM.load(self.base).source,
            "events_seq": self.ev_seq, "rejected_requests": self.rejected,
            "cards": len(self.cards.ordered()), "faces_sent": self.faces_sent,
        }

    def run(self):
        self.load_config(initial=True)
        self.cfg_mtime = self.cfg_path.stat().st_mtime
        self.dev.start(self.on_press)
        self.redraw(force=True)
        self.serve()
        for fn in (self.watch_config, self.watch_focus, self.watch_live, self.watch_anim,
                   self.watch_timers, self.watch_ring):
            threading.Thread(target=fn, daemon=True).start()
        try:
            while not self.stop.is_set():
                time.sleep(0.3)
        except KeyboardInterrupt:
            pass
        finally:
            self.dev.close()
            self.log("bye")


def warn_if_vsd_craft():
    if sys.platform != "win32":
        return
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             creationflags=0x08000000).stdout.lower()
        if "vsd" in out or "streamdock" in out:
            print("[m18] WARNING: VSD Craft / StreamDock looks like it's running. "
                  "Quit it (tray icon > Exit) or the pad will be claimed by it.", flush=True)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get("M18_CONFIG", str(HERE / "macros.yaml")))
    ap.add_argument("--mock", action="store_true", help="no hardware; drive with m18ctl press")
    ap.add_argument("--live-actions", action="store_true",
                    help="with --mock, really send keystrokes/launch apps instead of logging them")
    ap.add_argument("--skip-plugin-actions", action="store_true",
                    help="with --mock, log plugin actions instead of running them (m18ctl dryrun)")
    ap.add_argument("--sdk", default=os.environ.get("M18_SDK", str(HERE / "sdk" / "Python-SDK" / "src")))
    ap.add_argument("--port", type=int, default=int(os.environ.get("M18_PORT", DEFAULT_PORT)))
    a = ap.parse_args()
    cfg = Path(a.config).resolve()
    if a.mock:
        dev = MockDevice()
        if not a.live_actions:
            K.DRY = True
    else:
        warn_if_vsd_craft()
        dev = M18Device(a.sdk)
    Daemon(cfg, dev, a.port, mock=a.mock, skip_plugin_actions=a.skip_plugin_actions).run()


if __name__ == "__main__":
    main()
