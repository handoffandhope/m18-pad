"""Film a scene on a drawn pad: the 15 screens, the three buttons and the LED ring, as a looping GIF.

    python gallery/film.py                 # every clip below, into gallery/
    python gallery/film.py tank night      # only these

No daemon and no pad: it runs the real scene code with a scripted clock and scripted signals, the same
way `m18ctl scene shot` does. A clip is a function `story(t, film)` called before every frame; it pins
signals (`film.pin(cpu=0.9)`) and presses keys (`film.tap(8)`). Add yours to CLIPS and run it.
"""
from __future__ import annotations

import math
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "m18"))
from m18lib import ringmap as RM  # noqa: E402
from m18lib import scene as S  # noqa: E402
from m18lib import signals as SG  # noqa: E402

K = 2                                                  # pad pixels per screen pixel
M = 22 * K                                             # dark margin round the body, for the ring's glow
PADX, PADY = M + 40 * K, M + 32 * K
CAP = 20 * K                                           # strip under the pad for the clip's caption
BW, PH = S.W * K + 2 * PADX, S.H * K + 2 * PADY + 62 * K
BH = PH + CAP
FONT = ImageFont.load_default(size=13 * K)
T0 = 1_800_000_000.0                                   # the scripted clock starts here


def _body():
    """The pad without its lights: (RGB image, mask of the body)."""
    mask = Image.new("L", (BW, BH), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, BW - 1, BH - 1), 14 * K, fill=255)
    im = Image.new("RGB", (BW, BH), "#050507")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((M, M, BW - M, PH - M), 26 * K, fill="#0c0c10", outline="#1d1e26", width=2)
    d.rounded_rectangle((M + 7 * K, M + 7 * K, BW - M - 7 * K, PH - M - 7 * K), 21 * K, fill="#121318")
    bw = 115 * K
    for b in range(3):                                 # the three plain buttons
        x = PADX + b * (bw + (S.W * K - 3 * bw) // 2)
        d.rounded_rectangle((x, PADY + S.H * K + 22 * K, x + bw, PADY + S.H * K + 53 * K), 15 * K, fill="#1b1c24", outline="#2a2c38", width=2)
    for k in range(1, 16):                             # screen wells
        x0, y0, _, _ = S.key_box(k)
        d.rounded_rectangle((PADX + x0 * K - 2, PADY + y0 * K - 2, PADX + (x0 + 64) * K + 1, PADY + (y0 + 64) * K + 1), 6 * K + 2, fill="#000000")
    return im, mask


class Film:
    def __init__(self, scene: str, opts=None, source: Path | None = None):
        self.base = Path(tempfile.mkdtemp(prefix="m18film-"))
        (self.base / "scenes").mkdir()
        if source:                                     # a scene file from outside scripts/m18/scenes
            shutil.copy(source, self.base / "scenes" / f"{scene}.py")
        self.hub = SG.Hub(self.base, sim=True)
        self.t = 0.0
        self.hub.clock = lambda: T0 + self.t
        self.player = S.Player(self.base, scene, self.hub, log=lambda m: print("  !", m), opts=opts)
        self.rm = RM.default_map()
        self.taps: list[tuple[int, float]] = []
        self.body, self.mask = _body()
        self.keymask = Image.new("L", (64 * K, 64 * K), 0)
        ImageDraw.Draw(self.keymask).rounded_rectangle((0, 0, 64 * K - 1, 64 * K - 1), 6 * K, fill=255)

    def pin(self, **values):
        self.hub.fix(**values)

    def tap(self, key: int, gesture: str = "tap"):
        self.player.tap(key, gesture)
        self.taps.append((key, self.t))

    def _leds(self):
        try:
            own = self.player.ring_frame(self.rm)
            if own:
                return own
            r = self.player.ring()
            return RM.meter_frame(self.rm, r[0], r[1]) if r else None
        except Exception as e:                         # noqa: BLE001
            print("  ! ring:", e)
            return None

    def frame(self, caption: str | None = None) -> Image.Image:
        pic = self.player.frame(self.t)
        im = self.body.copy()
        if caption:
            ImageDraw.Draw(im).text((BW // 2, PH - M // 2 + CAP // 2), caption, font=FONT, fill="#8f93a8", anchor="mm")
        big = pic.resize((S.W * K, S.H * K), Image.NEAREST)
        for k in range(1, 16):
            x0, y0, _, _ = S.key_box(k)
            im.paste(big.crop((x0 * K, y0 * K, (x0 + 64) * K, (y0 + 64) * K)), (PADX + x0 * K, PADY + y0 * K), self.keymask)
        glow = Image.new("RGB", (BW, BH), 0)
        g = ImageDraw.Draw(glow)
        leds = self._leds() or []
        for (x, y), c in zip(self.rm.xy, leds):
            if not isinstance(c, str):                 # an LED the scene leaves alone
                continue
            rgb = RM.parse_hex(c)
            if max(rgb) > 6:
                cx, cy = M + 4 * K + x * (BW - 2 * M - 8 * K), M + 4 * K + y * (PH - 2 * M - 8 * K)
                g.ellipse((cx - 4 * K, cy - 4 * K, cx + 4 * K, cy + 4 * K), fill=rgb)
        for key, t0 in self.taps:                      # a touch: a ring that opens and fades on the key
            a = (self.t - t0) / 0.6
            if 0 <= a < 1:
                x0, y0, _, _ = S.key_box(key)
                cx, cy, r = PADX + (x0 + 32) * K, PADY + (y0 + 32) * K, (10 + 26 * a) * K
                v = int(255 * (1 - a))
                g.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(v, v, v), width=3 * K)
        soft = glow.filter(ImageFilter.GaussianBlur(11 * K)).point(lambda v: min(255, int(v * 1.6)))
        im = ImageChops.add(ImageChops.add(im, soft), glow.filter(ImageFilter.GaussianBlur(K)))
        return im

    def close(self):
        shutil.rmtree(self.base, ignore_errors=True)


def record(out: Path, film: Film, story, seconds: float, fps: int = 8, warm: float = 0.0, width: int | None = None):
    """Run `story` for `warm` seconds unrecorded (things settle), then record `seconds` at `fps`.
    What `story` returns is the caption under the pad for that frame."""
    try:
        _record(out, film, story, seconds, fps, warm, width)
    finally:
        film.close()
        out.with_suffix(".tmp.gif").unlink(missing_ok=True)


def _record(out, film, story, seconds, fps, warm, width):
    t0 = time.time()
    n_warm = int(warm * fps)
    frames = []
    for i in range(n_warm + int(seconds * fps)):
        film.t = (i - n_warm) / fps
        caption = story(film.t, film)
        if i < n_warm:
            film.player.frame(film.t)
        else:
            frames.append(film.frame(caption))
        if film.player.err:
            raise SystemExit(f"{out.name}: scene error: {film.player.err}")
    mask = film.mask
    if width and width != BW:
        h = round(BH * width / BW)
        frames = [f.resize((width, h), Image.LANCZOS) for f in frames]
        mask = mask.resize((width, h), Image.LANCZOS)
    # one palette for the whole clip (no flicker); index 255 is the transparent corner outside the body
    step = max(1, len(frames) // 12)
    sample = Image.new("RGB", (frames[0].width, frames[0].height * len(frames[::step])))
    for j, f in enumerate(frames[::step]):
        sample.paste(f, (0, j * f.height))
    pal = sample.quantize(160, method=Image.Quantize.MEDIANCUT)
    hole = mask.point(lambda v: 255 if v < 128 else 0)
    out_frames = []
    for f in frames:
        q = f.quantize(palette=pal, dither=Image.Dither.NONE)
        q.paste(255, (0, 0), hole)
        out_frames.append(q)
    tmp = out.with_suffix(".tmp.gif")                  # a failed save never leaves a cut-off clip in place
    out_frames[0].save(tmp, save_all=True, append_images=out_frames[1:], duration=round(1000 / fps), loop=0,
                       transparency=255, disposal=1, optimize=False)
    tmp.replace(out)
    print(f"{out.name}: {len(frames)} frames, {out.stat().st_size / 1e6:.2f} MB, {time.time() - t0:.0f} s")


# ------------------------------------------------------------------------------------------- the clips
FIX = {"fixtures": {1: "lamp", 5: "buoy", 11: "gauge", 15: "chest"}}
CAST = [("opal", "claude", "opus"), ("sorrel", "claude", "sonnet"), ("haze", "claude", "haiku"),
        ("luna", "codex", "luna"), ("sol", "codex", "sol"), ("wren", "claude", "sonnet"), ("pip", "claude", "haiku")]


def roster(states: dict | None = None, tools=("edit", "read", "run", "web", "edit", "run", "read")):
    """An `agents` reading: everyone working unless `states` says otherwise ({"haze": ("waiting", "question")})."""
    rows = []
    for i, (name, agent, species) in enumerate(CAST):
        state, kind = (states or {}).get(name, ("working", None))
        rows.append(dict(id=f"sim-{name}", title=name, label=name, state=state, kind=kind, silent=False, agent=agent,
                         species=species, tool=tools[i] if state == "working" else None, subagents=2 if name == "opal" else 0,
                         commits=["a", "b"] if name in ("opal", "luna") else [], grid=None, path=None))
    need = sum(1 for r in rows if r["state"] == "waiting")
    return {"value": need / len(rows), "text": f"{len(rows)} agents", "items": rows}


def usage(v, frozen=None):
    return {"value": v, "text": f"{v * 100:.0f}%", "data": {"frozen": frozen, "resets_at": T0 + 3 * 3600 if frozen else None}}


def _key_of(film, name):
    tank = film.player.state["tank"]
    return next((k for k in (7, 8, 9, 12, 13, 14, 2, 3, 4, 6, 10) if tank.hit(k) == f"sim-{name}"), None)


def tank(t, film):
    """One asks a question and comes to the glass; a tap shows who it is; then it gets its answer."""
    st = film.player.state
    asked = 2.5 <= t < 11
    film.pin(agents=roster({"haze": ("waiting", "question")} if asked else {"sol": ("finished", None)} if t >= 11 else None),
             claude_usage=usage(0.38), time_of_day=0.58, cpu=0.35 + 0.2 * math.sin(t / 2), ram=0.55, gpu=0.2)
    if t >= 7.5 and not st.get("tapped") and "tank" in st:
        k = _key_of(film, "haze")
        if k:
            st["tapped"] = True
            film.tap(k)
    return ("7 agents working" if t < 2.5 else "haze has a question" if t < 7.5 else "tap: who is asking?" if t < 11
            else "answered: back to work")


def limit(t, film):
    """The water is the 5-hour usage window: it drains, then the limit hits and the tank freezes."""
    v = min(1.0, 0.45 + 0.55 * max(0.0, t) / 5.0)
    film.pin(agents=roster(), claude_usage=usage(v, 0.9 if t >= 5.5 else None), time_of_day=0.58, cpu=0.4, ram=0.5, gpu=0.2)
    return f"5-hour window: {v * 100:.0f}% used" if t < 5.5 else "limit hit: frozen until the reset in 3 h"


def night(t, film):
    """Nine in the evening: the lights go out and the lamp key is what is left."""
    tod = 0.845 + 0.06 * max(0.0, min(1.0, (t - 1.5) / 3.0))
    film.pin(agents=roster({"sorrel": ("finished", None)}), claude_usage=usage(0.3), time_of_day=tod, cpu=0.25, ram=0.5, gpu=0.1)
    return f"{int(tod * 24):02d}:{int(tod * 1440) % 60:02d}"


def city(t, film):
    """A day in ten seconds: the moon crosses, load lights the windows, the network is the traffic, memory is the rain."""
    a = max(0.0, t) / 10.0
    cpu, net, ram = 0.15 + 0.8 * (0.5 - 0.5 * math.cos(a * 2 * math.pi)), 0.3 + 0.6 * abs(math.sin(a * 5)), 0.4 + 0.58 * a
    film.pin(time_of_day=(0.32 + 0.66 * a) % 1.0, cpu=cpu, net=net, ram=ram, disk=0.5)
    return f"CPU {cpu * 100:.0f}%   network {net * 100:.0f}%   memory {ram * 100:.0f}%"


CLIPS = {
    "tank": lambda: record(ROOT / "gallery" / "tank.gif", Film("fishbowl", FIX), tank, 13, warm=25, width=720),
    "limit": lambda: record(ROOT / "gallery" / "limit.gif", Film("fishbowl", FIX), limit, 9, warm=25, width=720),
    "night": lambda: record(ROOT / "gallery" / "night.gif", Film("fishbowl", FIX), night, 7, warm=25, width=720),
    "city": lambda: record(ROOT / "gallery" / "city.gif", Film("city_haiku", source=ROOT / "gallery" / "city" / "city.py"), city, 10, fps=10, warm=3, width=720),
}

if __name__ == "__main__":
    for name in sys.argv[1:] or list(CLIPS):
        CLIPS[name]()
