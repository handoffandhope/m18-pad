"""Scenes: one picture across the 15 keys, driven by signals. A scene file only draws.

`$M18/scenes/<name>.py`:

    SIGNALS = ["cpu", "ram", "time_of_day"]     # see m18lib/signals.py, or `m18ctl signals`
    FPS = 8                                      # optional, 1-20 (default 8)
    RING = {"signal": "cpu", "color": "#ffb000"} # optional: the LED ring as a meter of one signal

    def setup(state):                            # optional, once: build anything slow or random
        state["stars"] = [...]

    def draw(im, d, s, t, state):                # every frame
        # im: PIL RGB image W x H (440 x 252), d: its ImageDraw
        # s: signals, e.g. s.cpu.value (0..1), s.cpu.text ("46%")
        # t: seconds since the scene started; state: your dict, kept between frames
        ...

    def tap(key, x, y, s, t, state):             # optional: key 1-15 pressed; (x, y) is its centre
        ...                                      # may return a daemon action, e.g. {"open": "https://..."}

    def hold(key, x, y, s, t, state): ...        # optional: long press (page needs scene: {name: X, hold: true})
    def ring(s, t, state, rm): ...               # optional, instead of RING: 24 colours, rm = m18lib.ringmap map

Before `setup`, the runtime puts two entries in `state`: `state["opts"]`, the page's own settings
(`scene: {name: tank, fixtures: {1: lamp}}` gives `{"fixtures": {1: "lamp"}}`), and `state["dir"]`,
a folder the scene may keep small files in (None when there is nowhere to write).

The runtime owns everything else: the frame loop, cutting the picture into keys, sending only the
keys that changed, previews, hot reload on save, an error tile, the ring, and stopping when the
page is not showing. `m18ctl scene shot NAME` renders a scene with no daemon at all.

Geometry: each key is 64 px and the bar between two keys hides 30 px, so key column c (0-4) shows
x = c*94 .. c*94+63 and row r (0-2) shows y = r*94 .. r*94+63. `bars(im)` blacks out the hidden
strips so a saved frame looks like the pad.
"""
from __future__ import annotations

import functools
import importlib.util
import threading
import time
import traceback
import types
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from m18lib import tiles as T

COLS, ROWS, PITCH = 5, 3, 64 + T.GAP
W, H = T.block_size(COLS, ROWS)
HERE = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ helpers a scene may import
def key_box(key: int) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of key 1-15 on the canvas."""
    c, r = (key - 1) % COLS, (key - 1) // COLS
    return c * PITCH, r * PITCH, c * PITCH + 63, r * PITCH + 63


def key_at(x: float, y: float) -> int | None:
    """The key that shows canvas point (x, y), or None when a bar hides it."""
    c, r = int(x // PITCH), int(y // PITCH)
    if 0 <= c < COLS and 0 <= r < ROWS and x - c * PITCH < 64 and y - r * PITCH < 64:
        return r * COLS + c + 1
    return None


def lerp(a, b, t):
    return a + (b - a) * max(0.0, min(1.0, t))


def mix(c1, c2, t):
    """Blend two colours ("#rrggbb" or (r, g, b)); t=0 gives c1, t=1 gives c2."""
    return T.mix(c1, c2, max(0.0, min(1.0, t)))


def ramp(stops, t):
    """Colour along a list of stops, e.g. ramp(["#001", "#f80", "#8cf"], s.time_of_day.value)."""
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    return T.mix(stops[i], stops[i + 1], t - i)


def text(d, xy, s, size=14, fill="#ffffff", anchor="mm", bold=True):
    """Draw text centred on xy (anchor "mm"; "lm" = left, "rm" = right). Sizes 10-22 read well on a key."""
    d.text(xy, str(s), fill=fill, anchor=anchor, font=_font(bool(bold), int(size)))


@functools.lru_cache(maxsize=64)
def _font(bold, size):
    for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def bars(im: Image.Image, color=(0, 0, 0)) -> Image.Image:
    """Copy of a frame with the strips behind the bars painted over: what the pad really shows."""
    out = im.copy()
    d = ImageDraw.Draw(out)
    for c in range(COLS - 1):
        d.rectangle((c * PITCH + 64, 0, (c + 1) * PITCH - 1, H), fill=color)
    for r in range(ROWS - 1):
        d.rectangle((0, r * PITCH + 64, W, (r + 1) * PITCH - 1), fill=color)
    return out


# ------------------------------------------------------------------ loading and rendering
_mods: dict[str, tuple] = {}
_mod_lock = threading.Lock()


def find(base: Path | None, name: str) -> Path | None:
    for root in ([Path(base)] if base else []) + [HERE]:
        p = root / "scenes" / f"{name}.py"
        if p.exists():
            return p
    return None


def names(base: Path | None) -> list[str]:
    out = set()
    for root in ([Path(base)] if base else []) + [HERE]:
        if (root / "scenes").is_dir():
            out |= {p.stem for p in (root / "scenes").glob("*.py") if not p.stem.startswith("_")}
    return sorted(out)


def load(base: Path | None, name: str):
    """(module, mtime). Reloads when the file changes. Raises FileNotFoundError / the file's own error."""
    p = find(base, name)
    if p is None:
        raise FileNotFoundError(f"no scene {name!r} (looked in scenes/{name}.py)")
    m = p.stat().st_mtime
    with _mod_lock:
        hit = _mods.get(str(p))
        if not hit or hit[0] != m:
            mod = types.ModuleType(f"m18scene_{name}")       # compiled from source: no stale .pyc after a quick re-save
            mod.__file__ = str(p)
            exec(compile(p.read_text(encoding="utf-8"), str(p), "exec"), mod.__dict__)
            if not callable(getattr(mod, "draw", None)):
                raise AttributeError(f"scene {name}: needs def draw(im, d, s, t, state)")
            _mods[str(p)] = hit = (m, mod)
    return hit[1], m


def error_frame(name: str, err: str) -> Image.Image:
    """A frame that says what broke, one short line per key row, so the pad never just goes black."""
    im = Image.new("RGB", (W, H), (60, 8, 8))
    d = ImageDraw.Draw(im)
    words = [f"scene {name}", "error:"] + err.strip().splitlines()[-1][:60].split()
    for i, w in enumerate(words[:15]):
        x0, y0, _, _ = key_box(i + 1)
        d.text((x0 + 3, y0 + 26), w[:10], fill=(255, 200, 200))
    return im


class Player:
    """One scene's state and clock. `frame()` returns the next picture; it never raises."""

    def __init__(self, base, name, hub, log=None, opts=None, store=None):
        self.base, self.name, self.hub = base, name, hub
        self.opts, self.store = dict(opts or {}), store
        self.log = log or (lambda *_: None)
        self.state, self.t0, self.mtime, self.mod, self.err, self.t = {}, time.time(), None, None, None, 0.0

    def _ensure(self):
        mod, m = load(self.base, self.name)
        if m != self.mtime:                       # first load or the file was saved: start clean
            state = {"opts": dict(self.opts), "dir": self.store}
            if callable(getattr(mod, "setup", None)):
                mod.setup(state)                  # a failing setup is retried next frame, never half-applied
            self.mod, self.mtime, self.state, self.t0 = mod, m, state, time.time()
        return mod

    def signals(self):
        return self.hub.snapshot(getattr(self.mod, "SIGNALS", None) or [])

    @property
    def fps(self) -> float:
        try:
            return max(1.0, min(20.0, float(getattr(self.mod, "FPS", 8))))
        except (TypeError, ValueError):
            return 8.0

    def frame(self, t: float | None = None) -> Image.Image:
        try:
            mod = self._ensure()
            im = Image.new("RGB", (W, H), (0, 0, 0))
            self.t = time.time() - self.t0 if t is None else t
            mod.draw(im, ImageDraw.Draw(im), self.signals(), self.t, self.state)
            if self.err:
                self.err = None
            return im
        except Exception as e:                    # noqa: BLE001  a scene bug must not stop the loop
            msg = f"{type(e).__name__}: {e}"
            if msg != self.err:
                self.err = msg
                tb = traceback.extract_tb(e.__traceback__)
                where = next((f"line {f.lineno}" for f in reversed(tb) if self.name in Path(f.filename).stem), "")
                self.log(f"scene {self.name}: {msg} {where}".rstrip())
            return error_frame(self.name, msg)

    def tap(self, key: int, gesture: str = "tap"):
        """Run the scene's tap (or hold); returns the daemon action it asked for, if any."""
        try:
            mod = self._ensure()
            fn = getattr(mod, gesture, None)
            if callable(fn):
                x0, y0, _, _ = key_box(key)
                r = fn(key, x0 + 32, y0 + 32, self.signals(), self.t, self.state)
                return r if isinstance(r, (dict, list)) else None
        except Exception as e:                    # noqa: BLE001
            self.log(f"scene {self.name} {gesture}: {type(e).__name__}: {e}")
        return None

    def ring_frame(self, rm):
        """A full 24-colour frame from the scene's own ring(), or None."""
        fn = getattr(self.mod, "ring", None)
        if not callable(fn):
            return None
        out = fn(self.signals(), self.t, self.state, rm)
        return list(out) if out else None

    def ring(self):
        """(fraction, color) for the LED ring, or None."""
        r = getattr(self.mod, "RING", None)
        if not isinstance(r, dict) or not r.get("signal"):
            return None
        sig = self.hub.snapshot([r["signal"]]).__dict__[r["signal"]]
        return float(sig), str(r.get("color", "#ffffff"))
