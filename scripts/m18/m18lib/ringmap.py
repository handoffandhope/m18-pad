"""Ring position map: where each of the 24 LEDs sits on the M18, and frame helpers that use it.

Coordinates are a unit rectangle over the whole case: x 0 (left) .. 1 (right), y 0 (top) .. 1 (bottom).
The ring runs round its edge. Parameter s runs 0..4 round the perimeter starting at the left-middle
(level with the middle key row) going DOWN the left side, across the bottom, UP the right side and
back along the top. That is the direction of LED indices 0..21 on the user's unit (verified by eye
2026-11-11: LEDs 0-11 lit the bottom half, 12-21 the top half; the side boundaries sit level with the
middle key row, above the case's true middle because the case extends below the keys).
LEDs 22-23 are two short strips on the front; they are not part of the perimeter order.

Until `state/ring-map.json` exists (written by `m18ctl ringwalk`), default_map() is used: it spreads
LEDs 0-11 evenly over the lower part of the perimeter and 12-21 over the upper part. The strip
positions in the default are a guess.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

KEY_X = (0.14, 0.32, 0.50, 0.68, 0.86)       # LCD key centres (guess at the real spacing)
KEY_Y = (0.14, 0.32, 0.50)
BTN_XY = {16: (0.20, 0.84), 17: (0.50, 0.84), 18: (0.80, 0.84)}
YM = KEY_Y[1]                                 # side boundary: level with the middle key row
PERIM = 4.0
S_TL = PERIM - YM                             # perimeter parameter of the top-left corner
RING = tuple(range(22))                       # LEDs that run round the perimeter
STRIPS = (22, 23)                             # front strips
N_LEDS = 24
MAP_NAME = "ring-map.json"


def key_xy(k: int) -> tuple[float, float]:
    if k in BTN_XY:
        return BTN_XY[k]
    r, c = divmod(k - 1, 5)
    return KEY_X[c], KEY_Y[r]


def point(s: float) -> tuple[float, float]:
    """Perimeter parameter -> (x, y)."""
    s %= PERIM
    if s < 1 - YM:
        return 0.0, YM + s
    s -= 1 - YM
    if s < 1:
        return s, 1.0
    s -= 1
    if s < 1:
        return 1.0, 1.0 - s
    s -= 1
    if s < 1:
        return 1.0 - s, 0.0
    s -= 1
    return 0.0, s


def param(x: float, y: float) -> float:
    """(x, y) -> perimeter parameter of the nearest point on the rectangle's edge."""
    d = {"l": x, "r": 1 - x, "t": y, "b": 1 - y}
    e = min(d, key=d.get)
    if e == "l":
        yy = min(max(y, 0.0), 1.0)
        return (yy - YM) if yy >= YM else (PERIM - (YM - yy))
    if e == "b":
        return (1 - YM) + min(max(x, 0.0), 1.0)
    if e == "r":
        return (1 - YM) + 1 + (1 - min(max(y, 0.0), 1.0))
    return (1 - YM) + 2 + (1 - min(max(x, 0.0), 1.0))


class RingMap:
    def __init__(self, xy, s, key, source, created=None):
        self.xy = [tuple(p) for p in xy]
        self.s = list(s)                     # perimeter parameter, None for strips
        self.key = list(key)                 # key nearest each LED (walk), None for the default
        self.source = source
        self.created = created
        ring = [i for i in RING if self.s[i] is not None]
        cyc = sorted(ring, key=lambda i: self.s[i])                               # round the perimeter
        if cyc and cyc[0] != cyc[-1] and 0 in cyc:                                # start at LED 0 and run the way
            cyc = cyc[cyc.index(0):] + cyc[:cyc.index(0)]                          # the indices run (wrap-safe: LED 21
            if len(cyc) > 2 and cyc[1] != 1 and cyc[-1] == 1:                      # can sit just before LED 0)
                cyc = [cyc[0]] + cyc[:0:-1]
        self.order = cyc
        self.clockwise = sorted(ring, key=lambda i: (S_TL - self.s[i]) % PERIM)   # from top-left

    def as_json(self) -> dict:
        return {"version": 1, "source": self.source, "created": self.created,
                "leds": [{"i": i, "key": self.key[i], "x": round(self.xy[i][0], 4),
                          "y": round(self.xy[i][1], 4),
                          "s": None if self.s[i] is None else round(self.s[i], 4),
                          "strip": i in STRIPS} for i in range(N_LEDS)],
                "order": self.order, "clockwise": self.clockwise, "strips": list(STRIPS)}

    @staticmethod
    def from_json(j: dict) -> "RingMap":
        leds = sorted(j["leds"], key=lambda e: e["i"])
        if len(leds) != N_LEDS:
            raise ValueError("ring map needs 24 LEDs")
        return RingMap([(e["x"], e["y"]) for e in leds], [e.get("s") for e in leds],
                       [e.get("key") for e in leds], j.get("source", "file"), j.get("created"))


def default_map() -> RingMap:
    lower = 2 * (1 - YM) + 1                 # perimeter length of the lower half (0-11)
    s = []
    for i in range(12):
        s.append((i + 0.5) * lower / 12)
    for i in range(10):
        s.append(lower + (i + 0.5) * (PERIM - lower) / 10)
    xy = [point(v) for v in s]
    s += [None, None]
    xy += [(0.30, 1.0), (0.70, 1.0)]         # guess: the two front strips under the buttons
    return RingMap(xy, s, [None] * N_LEDS, "default")


def nearest_key(x: float, y: float, keys=range(1, 19)) -> int:
    return min(keys, key=lambda k: (key_xy(k)[0] - x) ** 2 + (key_xy(k)[1] - y) ** 2)


def build_from_walk(answers: list[int]) -> RingMap:
    """answers[i] = the key (1-18) the owner pressed as nearest to LED i. Each LED is put on the
    case edge next to that key (the edge chosen by the default map's position, which settles corner
    keys); LEDs that share a key and an edge are spread evenly across the key's width."""
    if len(answers) != N_LEDS:
        raise ValueError("need 24 answers")
    d = default_map()
    pts: list[tuple[float, float]] = []
    edges: list[str] = []
    for i, k in enumerate(answers):
        cx, cy = key_xy(k)
        cands = {"t": (cx, 0.0), "b": (cx, 1.0), "l": (0.0, cy), "r": (1.0, cy)}
        e = min(cands, key=lambda c: (cands[c][0] - d.xy[i][0]) ** 2 + (cands[c][1] - d.xy[i][1]) ** 2)
        pts.append(cands[e])
        edges.append(e)
    i = 0
    while i < N_LEDS:
        j = i
        while j + 1 < N_LEDS and answers[j + 1] == answers[i] and edges[j + 1] == edges[i] \
                and (j + 1 in STRIPS) == (i in STRIPS):
            j += 1
        n = j - i + 1
        if n > 1:
            hw = 0.15 if answers[i] in BTN_XY else 0.09
            ax = 0 if edges[i] in "tb" else 1                       # axis the run is spread along
            sign = 1 if d.xy[j][ax] >= d.xy[i][ax] else -1          # direction the indices travel
            c = pts[i][ax]
            for m in range(n):
                off = ((m + 0.5) / n * 2 - 1) * hw * sign
                p = list(pts[i + m])
                p[ax] = min(max(c + off, 0.0), 1.0)
                pts[i + m] = (p[0], p[1])
        i = j + 1
    s = [param(*pts[i]) if i not in STRIPS else None for i in range(N_LEDS)]
    return RingMap(pts, s, list(answers), "walk", time.strftime("%Y-%m-%dT%H:%M:%S"))


def map_path(base: Path) -> Path:
    return Path(base) / "state" / MAP_NAME


def save(base: Path, rm: RingMap) -> Path:
    p = map_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rm.as_json(), indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return p


_cache: dict = {}


def load(base: Path | None) -> RingMap:
    """The saved map when state/ring-map.json exists and is valid, else the default."""
    if base is not None:
        p = map_path(base)
        try:
            m = p.stat().st_mtime
            c = _cache.get(str(p))
            if not c or c[0] != m:
                _cache[str(p)] = c = (m, RingMap.from_json(json.loads(p.read_text(encoding="utf-8"))))
            return c[1]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return default_map()


# ------------------------------------------------------------------------------ colours / frames
def parse_hex(c) -> tuple[int, int, int]:
    c = str(c).lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def scaled(color, f: float) -> str:
    r, g, b = parse_hex(color)
    return to_hex((r * f, g * f, b * f))


def blank() -> list:
    return [None] * N_LEDS


def meter_frame(rm: RingMap, fraction: float, color: str, stop_at: float | None = None,
                track: str | None = None, marker: str = "#ffffff") -> list:
    """Fill the ring clockwise from the top-left corner to `fraction` (0-1). The last LED is dimmed
    by the remainder, so a meter moves in steps finer than one LED. `stop_at` lights one marker LED
    (e.g. 0.9). `track` colours the unfilled part (default dark)."""
    n = len(rm.clockwise)
    fr = min(max(float(fraction), 0.0), 1.0)
    lit = fr * n
    frame = blank()
    for pos, led in enumerate(rm.clockwise):
        if pos + 1 <= lit + 1e-9:
            frame[led] = color
        elif pos < lit:
            frame[led] = scaled(color, max(lit - pos, 0.0))
        elif track:
            frame[led] = track
    if fr > 0 and lit < 1:
        frame[rm.clockwise[0]] = scaled(color, max(lit, 0.25))
    if stop_at is not None:
        pos = min(n - 1, int(min(max(float(stop_at), 0.0), 1.0) * n))
        frame[rm.clockwise[pos]] = marker
    return frame


def arc_sizes(counts: list[int], total: int = 22) -> list[int]:
    """Split `total` LEDs by share (largest remainder). Every nonzero count gets at least one LED,
    so a small arc never vanishes next to a large one."""
    s = sum(counts)
    if s <= 0:
        return [0] * len(counts)
    raw = [c * total / s for c in counts]
    sizes = [int(math.floor(v)) for v in raw]
    for i, c in enumerate(counts):
        if c > 0 and sizes[i] == 0:
            sizes[i] = 1
    while sum(sizes) < total:
        i = max(range(len(counts)), key=lambda i: (raw[i] - sizes[i]) if counts[i] > 0 else -9)
        sizes[i] += 1
    while sum(sizes) > total:
        i = max((i for i in range(len(counts)) if sizes[i] > 1), key=lambda i: sizes[i] - raw[i])
        sizes[i] -= 1
    return sizes
