"""Find out, with a person at the pad, what this M18's firmware can show and how fast.

    python panel_probe.py            (stops the daemon, runs about 3 minutes, starts the daemon again)

Two things the PC cannot see for itself:
  1. Whether the firmware honours the SDK's panel-frame call (`set_background_frame_stream`: a JPEG of any size at
     any x, y of the 480x272 panel). The SDK lists it for N4 Pro, XL and M3 only.
  2. Whether fast writes queue up. Writes return at once and nothing comes back, so the probe floods the pad for a
     few seconds, then turns the middle key white and times the press. Lag = that time minus the calm baseline.

Questions print here; answer on the pad: bottom right key = YES, the key left of it = NO. Every wait gives up
after 90 s, so an unattended run ends by itself. Result: state/panel-probe.json beside macros.yaml.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
YES, NO, MID = 15, 14, 8
W, H = 480, 272
WAIT_S = 90


def api_up():
    try:
        r = urllib.request.Request("http://127.0.0.1:8718/status", headers={"X-M18": "1"})
        urllib.request.urlopen(r, timeout=1.5).read()
        return True
    except Exception:                                              # noqa: BLE001
        return False


def ctl(*args):
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    return subprocess.Popen([sys.executable, str(HERE / "m18ctl.py"), *args], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)


def font(size):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def jpeg(im, q=90):
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=q)
    return b.getvalue()


def tile(text, bg, fg="#000000"):
    im = Image.new("RGB", (64, 64), bg)
    d = ImageDraw.Draw(im)
    f = font(20 if len(text) <= 3 else 14)
    w = d.textlength(text, font=f)
    d.text(((64 - w) / 2, 20), text, fill=fg, font=f)
    return im


def slab(w, h, text, bg, fg="#000000"):
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    f = font(28)
    for y in range(8, h - 30, 90):
        for x in range(8, w - 40, 96):
            d.text((x, y), text, fill=fg, font=f)
    return im


def busy(w, h, seed):
    """A tank-like picture: gradient water, a few blobs. About as hard to compress as a real frame."""
    r = random.Random(seed)
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line((0, y, w, y), fill=(10, 40 + y * 60 // max(h, 1), 90 + y * 80 // max(h, 1)))
    for _ in range(max(3, w * h // 900)):
        x, y, s = r.randrange(w), r.randrange(h), r.randrange(3, 12)
        d.ellipse((x, y, x + s, y + s // 2 + 2), fill=(r.randrange(256), r.randrange(256), r.randrange(256)))
    return im


class Pad:
    def __init__(self, sdk):
        from m18lib.device import M18Device
        self.d = M18Device(sdk)
        self.presses: list = []
        self.ev = threading.Event()
        self.d.start(self._press)
        t0 = time.time()
        while not self.d.connected and time.time() - t0 < 15:
            time.sleep(0.2)
        if not self.d.connected:
            raise SystemExit("no M18 found (is VSD Craft or another program holding it?)")
        self.tr = self.d.dev.transport
        self.calls: dict = {}

    def _press(self, key, down, t=None, state=None):
        key = key + 10 if key <= 5 else key - 10 if 11 <= key <= 15 else key   # the SDK reports rows bottom-up
        if down:
            self.presses.append((key, t or time.time()))
            self.ev.set()

    def wait(self, keys, timeout=None, since=None):
        """(key, time) of the first press of one of `keys` after `since` (default now), or None."""
        since = time.time() if since is None else since
        end = time.time() + (WAIT_S if timeout is None else timeout)
        while time.time() < end:
            for k, t in self.presses:
                if t >= since and k in keys:
                    return k, t
            self.ev.clear()
            self.ev.wait(0.05)
        return None

    def key(self, k, im):
        self.raw_key(k, jpeg(im))

    def raw_key(self, k, data):
        self.tr.set_key_image_stream(data, self.d.dev.get_image_key(self.d._BK(k)))

    def show(self):
        self.d.dev.refresh()

    def timed(self, name, fn, *a):
        t = time.perf_counter()
        rc = fn(*a)
        self.calls.setdefault(name, []).append(round((time.perf_counter() - t) * 1000, 2))
        return rc

    def frame(self, im, x=0, y=0, layer=0, q=90):
        return self.raw_frame(jpeg(im, q), im.width, im.height, x, y, layer)

    def raw_frame(self, data, w, h, x=0, y=0, layer=0):
        return self.timed("frame", self.tr.set_background_frame_stream, data, w, h, x, y, layer)

    def answer_keys(self):
        self.key(YES, tile("YES", "#1f9d3a", "#ffffff"))
        self.key(NO, tile("NO", "#b3261e", "#ffffff"))
        self.show()

    def blank(self, keep_answers=True):
        for k in range(1, 16):
            if keep_answers and k in (YES, NO):
                continue
            self.key(k, Image.new("RGB", (64, 64), "#000000"))
        if keep_answers:
            self.answer_keys()
        self.show()

    def ask(self, q):
        print(f"\n  {q}\n  -> pad: bottom right key = YES, the key left of it = NO", flush=True)
        r = self.wait((YES, NO))
        if r is None:
            print(f"     (no answer in {WAIT_S:g} s)", flush=True)
            return None
        print("     " + ("yes" if r[0] == YES else "no"), flush=True)
        time.sleep(0.4)
        return r[0] == YES


def react(pad, flood=None, secs=3.0, signal="key"):
    """Seconds from sending the white signal to the press of the middle key. `flood(i)` is called at 60 Hz for
    `secs` first; None = calm. The signal goes down the same queue as the flood, so a backlog delays it."""
    pad.key(MID, tile("WAIT", "#202020", "#909090"))
    pad.show()
    n = 0
    if flood is None:
        time.sleep(1.5 + random.random() * 2)
    else:
        t0 = nxt = time.perf_counter()
        end = t0 + secs + random.random()
        while time.perf_counter() < end:
            n += flood(n)
            nxt += 1 / 60
            dt = nxt - time.perf_counter()
            if dt > 0:
                time.sleep(dt)
        n = round(n / (time.perf_counter() - t0))                 # writes per second actually sent
    early = [p for p in pad.presses if p[0] == MID and p[1] > time.time() - 0.3]
    if signal == "frame":
        pad.frame(Image.new("RGB", (W, H), "#ffffff"))
    else:
        pad.key(MID, Image.new("RGB", (64, 64), "#ffffff"))
        pad.show()
    t = time.time()
    r = None if early else pad.wait((MID,), 8, since=t)
    return (round(r[1] - t, 3) if r else None), n


def lag_block(pad, name, flood, out, signal="key", secs=3.0, trials=3):
    got, sent = [], 0
    for _ in range(trials):
        v, n = react(pad, flood, secs, signal)
        sent = max(sent, n)
        if v is not None:
            got.append(v)
        if signal == "frame":
            pad.tr.clear_background_frame_stream(0x03)
            pad.blank()
    med = round(statistics.median(got), 3) if got else None
    out[name] = {"press_s": got, "median_s": med, "writes_per_s": sent}
    print(f"     {name}: {got}  median {med}", flush=True)
    return med


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdk", default=os.environ.get("M18_SDK", str(HERE / "sdk" / "Python-SDK" / "src")))
    ap.add_argument("--state", default=str(HERE / "state"))
    ap.add_argument("--wait", type=float, default=WAIT_S, help="seconds to wait for each answer")
    a = ap.parse_args()
    globals()["WAIT_S"] = a.wait
    out = {"t": time.time(), "answers": {}, "lag": {}}
    was_up = api_up()
    if was_up:
        print("stopping the pad daemon...", flush=True)
        ctl("stop").wait(20)
        t0 = time.time()
        while api_up() and time.time() - t0 < 15:
            time.sleep(0.5)
        time.sleep(7)                                              # the device needs a moment after release
    pad = None
    try:
        pad = Pad(a.sdk)
        A = out["answers"]
        pad.blank()
        print("\nPANEL PROBE. Watch the pad; answer with its keys.", flush=True)
        if not pad.ask("Ready? The pad should be black with a green YES and a red NO key. Press YES to start."):
            print("not started.", flush=True)
            return
        # ---- 1. does the panel-frame call draw anything?
        pad.frame(slab(W, H, "PINK", "#ff2fd0"))
        A["frame_full"] = pad.ask("Did the whole pad just turn PINK (every key)?")
        if A["frame_full"] is False:
            for layer in (1, 2, 3):
                pad.frame(slab(W, H, "PINK", "#ff2fd0"), layer=layer)
                time.sleep(0.5)
            A["frame_full_other_layers"] = pad.ask("And now? Is the pad PINK?")
        works = bool(A.get("frame_full") or A.get("frame_full_other_layers"))
        if works:
            pad.frame(slab(192, H, "GRN", "#22e04a"), 0, 0)
            A["frame_rect"] = pad.ask("Are only the LEFT TWO columns GREEN now, the rest still pink?")
            pad.key(MID, tile("KEY", "#ffe000"))
            pad.show()
            A["key_over_frame"] = pad.ask("Did the MIDDLE key turn YELLOW?")
            pad.frame(slab(96, 96, "", "#2040ff"), 192, 88)
            A["frame_over_key"] = pad.ask("Did the middle key (or the area around it) turn BLUE?")
            pad.answer_keys()
            A["answers_over_frame"] = pad.ask("Can you still see the green YES and red NO keys? (Press where they "
                                              "were either way.)")
            pad.timed("clear", pad.tr.clear_background_frame_stream, 0x03)
            pad.show()
            A["clear_removes"] = pad.ask("Did the pink and green go away?")
        out["frame_works"] = works
        # ---- 2. do writes queue up?
        pad.blank()
        print("\nNow a reaction game, about 2 minutes. The middle key says WAIT, the pad may flicker, then the "
              "middle key\n(or the whole pad) turns WHITE: press the MIDDLE key as fast as you can. Don't press "
              "early.", flush=True)
        if pad.ask("Understood? Press YES to begin."):
            L = out["lag"]
            lag_block(pad, "calm", None, L, trials=5)
            tiles = [jpeg(busy(64, 64, i)) for i in range(40)]
            out["tile_bytes"] = round(statistics.mean(len(t) for t in tiles))
            others = [k for k in range(1, 16) if k != MID]

            def keys_flood(per_frame):
                def f(i):
                    for j in range(per_frame):
                        pad.raw_key(others[(i * per_frame + j) % len(others)], tiles[(i + j) % len(tiles)])
                    pad.show()
                    return per_frame
                return f
            for per in (3, 8, 14):
                lag_block(pad, f"keys_{per * 60}_per_s", keys_flood(per), L)
                pad.blank()
            if works:
                full = [jpeg(busy(W, H, i), 85) for i in range(12)]
                out["full_frame_bytes"] = round(statistics.mean(len(t) for t in full))
                part = [jpeg(busy(96, 96, i)) for i in range(20)]

                def full_flood(i):
                    pad.raw_frame(full[i % len(full)], W, H)
                    return 1

                def part_flood(i):
                    for j in range(3):
                        pad.raw_frame(part[(i + j) % len(part)], 96, 96, 96 * ((i + j) % 5), 88 * ((i // 5 + j) % 3))
                    return 3
                lag_block(pad, "full_frames_60_per_s", full_flood, L, signal="frame")
                lag_block(pad, "rect_frames_180_per_s", part_flood, L, signal="frame")
                pad.blank()
                A["full_frames_smooth"] = None
                for i in range(240):                                   # 4 s: a white bar crossing twice
                    im = Image.new("RGB", (W, H), "#001830")
                    x = (i * 4) % W
                    ImageDraw.Draw(im).rectangle((x, 0, x + 23, H), fill="#ffffff")
                    t = time.perf_counter()
                    pad.frame(im, q=80)
                    time.sleep(max(0, 1 / 60 - (time.perf_counter() - t)))
                A["full_frames_smooth"] = pad.ask("A white bar just swept across the pad twice. Was it SMOOTH "
                                                  "(no stutter, about 2 s per crossing)?")
                pad.tr.clear_background_frame_stream(0x03)
        out["call_ms"] = {k: {"n": len(v), "median": statistics.median(v), "max": max(v)} for k, v in pad.calls.items()}
        print("\nDone. Thank you.", flush=True)
    finally:
        try:
            p = Path(a.state)
            p.mkdir(parents=True, exist_ok=True)
            (p / "panel-probe.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
            print(f"result: {p / 'panel-probe.json'}", flush=True)
        except OSError as e:
            print("could not save the result:", e, flush=True)
        if pad is not None:
            try:
                pad.tr.clear_background_frame_stream(0x03)
                pad.d.close()
            except Exception:                                      # noqa: BLE001
                pass
        if was_up:
            time.sleep(7)
            print("starting the pad daemon again...", flush=True)
            ctl("start")
            time.sleep(4)


if __name__ == "__main__":
    main()
