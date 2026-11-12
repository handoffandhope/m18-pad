"""Signals: named live values in one shape, so a scene never talks to the system itself.

A reading is a Sig: `value` (0..1, or None when unknown), `text` (ready to show, "46%"), `label`
("CPU") and `items` (a list, for signals that are collections). Built-ins are below. A PC-specific
signal is one file, `$M18/signals/<name>.py`:

    LABEL = "Inbox"          # optional
    PERIOD = 60              # seconds between reads, optional (default 5)
    def read():              # a float 0..1, or a dict with any of value / text / items / data
        return {"value": 0.3, "text": "3 unread"}
    def sim(t):              # optional: what it returns in simulated mode (default: a slow wave)
        return {"value": 0.3, "text": "3 unread"}

`items` is a list (one entry per mail, per agent, per build) and `data` a dict of extras; scenes read
them as `s.inbox.items` and `s.inbox.data`. `read` and `sim` may take one argument, the Hub, for
`hub.base` (the install folder) and `hub.fn("plugin.function")`. Signals shipped with the skill
live next to this package in `signals/`; a file of the same name in `$M18/signals/` replaces one.

Every signal also has a simulated form (slow waves), used when `sim=True`, when its source is
missing, and in the browser. A scene therefore always gets a number to draw.
"""
from __future__ import annotations

import importlib.util
import inspect
import math
import threading
import time
import zlib
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent


class Sig:
    __slots__ = ("name", "value", "text", "label", "items", "data", "live")

    def __init__(self, name, value=None, text="", label=None, items=None, live=True, data=None):
        self.name, self.value, self.text = name, value, text
        self.label, self.items, self.live, self.data = label or name.upper(), list(items or []), live, dict(data or {})

    def __float__(self):
        return float(self.value or 0.0)

    def __repr__(self):
        return f"Sig({self.name}={self.value!r} {self.text!r}{'' if self.live else ' sim'})"


class Snapshot:
    """`s.cpu.value`, `s.ram.text`; an unknown name gives an empty Sig instead of an error."""

    def __init__(self, sigs: dict):
        self.__dict__.update(sigs)

    def __getattr__(self, name):
        return Sig(name, 0.0, "--", live=False)

    def __iter__(self):
        return iter(self.__dict__.values())


def _clamp(v):
    return max(0.0, min(1.0, float(v)))


def _rate(n):
    for unit, size in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if n >= size:
            return f"{n / size:.1f}{unit}/s"
    return f"{int(n)}B/s"


# ------------------------------------------------------------------ built-in readers: fn(memo) -> dict
def _cpu(memo):
    import psutil
    v = psutil.cpu_percent(interval=None)
    return {"value": v / 100, "text": f"{v:.0f}%"}


def _ram(memo):
    import psutil
    v = psutil.virtual_memory().percent
    return {"value": v / 100, "text": f"{v:.0f}%"}


def _net(memo):
    import psutil
    c, now = psutil.net_io_counters(), time.time()
    total = c.bytes_sent + c.bytes_recv
    last = memo.get("last")
    memo["last"] = (total, now)
    if not last or now <= last[1]:
        return {"value": 0.0, "text": "0B/s"}
    bps = max(0.0, (total - last[0]) / (now - last[1]))
    # log scale: 10 KB/s reads 0, 10 MB/s reads 1
    return {"value": _clamp((math.log10(max(bps, 1.0)) - 4) / 3), "text": _rate(bps)}


def _disk(memo):
    import psutil
    v = psutil.disk_usage(str(Path.home().anchor or "/")).percent
    return {"value": v / 100, "text": f"{v:.0f}%"}


def _battery(memo):
    import psutil
    b = psutil.sensors_battery()
    if b is None:
        return {"value": None, "text": "--"}
    return {"value": b.percent / 100, "text": f"{b.percent:.0f}%{'+' if b.power_plugged else ''}"}


def _gpu(memo):
    import pynvml
    if not memo.get("h"):
        pynvml.nvmlInit()
        memo["h"] = pynvml.nvmlDeviceGetHandleByIndex(0)
    v = pynvml.nvmlDeviceGetUtilizationRates(memo["h"]).gpu
    return {"value": v / 100, "text": f"{v}%"}


def _time_of_day(memo):
    lt = time.localtime()
    return {"value": (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec) / 86400, "text": time.strftime("%H:%M", lt)}


def _weekday(memo):
    lt = time.localtime()
    return {"value": lt.tm_wday / 6, "text": time.strftime("%a", lt)}


# name: (reader, period seconds, label, what a designer can use it for)
BUILTIN = {
    "cpu": (_cpu, 1, "CPU", "effort, heat, crowding: how hard the PC is working right now"),
    "ram": (_ram, 2, "RAM", "pressure, fullness: slow to change, good for weather or water level"),
    "gpu": (_gpu, 2, "GPU", "a second kind of effort (NVIDIA only; simulated elsewhere)"),
    "net": (_net, 1, "NET", "flow, traffic, chatter: jumpy, log scale from 10 KB/s to 10 MB/s"),
    "disk": (_disk, 60, "DISK", "how full the system drive is: nearly static, good for terrain or sediment"),
    "battery": (_battery, 30, "BATT", "energy left (None on a desktop)"),
    "time_of_day": (_time_of_day, 5, "TIME", "0 at midnight, 0.5 at noon: sun, moon, sky colour, opening hours"),
    "weekday": (_weekday, 600, "DAY", "0 Monday to 1 Sunday: weekly rhythm, weekend mood"),
}


def _sim(name: str, t: float) -> dict:
    """A slow, repeatable wave per name, so mock runs and previews move."""
    if name in ("time_of_day", "weekday"):
        return BUILTIN[name][0]({})
    seed = zlib.crc32(name.encode()) % 1000
    v = 0.5 + 0.35 * math.sin(t / (7 + seed % 9) + seed) + 0.15 * math.sin(t / 1.7 + seed * 3)
    v = _clamp(v)
    return {"value": v, "text": f"{v * 100:.0f}%"}


class Hub:
    """Reads the signals a scene asked for on one background thread and hands out snapshots."""

    def __init__(self, base: Path | None = None, sim: bool = False, log=None, plugin_fn=None):
        self.base, self.sim, self.log = (Path(base) if base else None), sim, (log or (lambda *_: None))
        self._plugin_fn, self._plugins = plugin_fn, {}
        self._lock = threading.Lock()
        self._want: set[str] = set()
        self._vals: dict[str, Sig] = {}
        self._due: dict[str, float] = {}
        self._memo: dict[str, dict] = {}
        self._user: dict[str, tuple] = {}
        self._fixed: dict = {}
        self._thread = None
        self.clock = time.time                   # a recorder may replace it to play simulated signals at its own pace

    # -- test hook: `hub.fix(cpu=0.9)` pins a value (m18ctl scene shot --set cpu=0.9). A dict pins a whole
    #    reading, for list signals: hub.fix(agents={"value": 0.5, "text": "2 agents", "items": [...]})
    def fix(self, **values):
        self._fixed.update({k: dict(v) if isinstance(v, dict) else _clamp(v) for k, v in values.items()})

    def unfix(self, *names):
        for n in names or list(self._fixed):
            self._fixed.pop(n, None)

    def _roots(self):
        return ([self.base] if self.base else []) + [HERE]

    def names(self) -> list[str]:
        found = set()
        for root in self._roots():
            if (root / "signals").is_dir():
                found |= {p.stem for p in (root / "signals").glob("*.py") if not p.stem.startswith("_")}
        return list(BUILTIN) + sorted(found - set(BUILTIN))

    def fn(self, ref: str):
        """A plugin function by name ("sessions.scan"): the daemon's own copy when there is one."""
        if self._plugin_fn:
            return self._plugin_fn(ref)
        mod, name = ref.rsplit(".", 1)
        if mod not in self._plugins:
            p = next((r / "plugins" / f"{mod}.py" for r in self._roots() if (r / "plugins" / f"{mod}.py").exists()), None)
            if p is None:
                raise FileNotFoundError(f"no plugin {mod}")
            spec = importlib.util.spec_from_file_location(f"m18plugin_{mod}", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            self._plugins[mod] = m
        return getattr(self._plugins[mod], name)

    def _call(self, f):
        return f(self) if len(inspect.signature(f).parameters) >= 1 else f()

    def describe(self, name: str) -> str:
        if name in BUILTIN:
            return BUILTIN[name][3]
        mod = self._user_mod(name)
        return (getattr(mod, "ABOUT", None) or (mod.__doc__ or "").strip().split("\n")[0] or "user signal") if mod else "unknown"

    def _user_mod(self, name):
        p = next((r / "signals" / f"{name}.py" for r in self._roots() if (r / "signals" / f"{name}.py").exists()), None)
        if p is None:
            return None
        m = p.stat().st_mtime
        hit = self._user.get(name)
        if not hit or hit[0] != m:
            spec = importlib.util.spec_from_file_location(f"m18signal_{name}", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._user[name] = hit = (m, mod)
        return hit[1]

    def _read(self, name: str, now: float) -> tuple[Sig, float]:
        label, period, live = name.upper(), 5.0, True
        try:
            if name in self._fixed:
                v = self._fixed[name]
                raw, period = (v if isinstance(v, dict) else {"value": v, "text": f"{v * 100:.0f}%"}), 0.2
                label = BUILTIN[name][2] if name in BUILTIN else label
            elif self.sim:
                mod = None if name in BUILTIN else self._user_mod(name)
                if mod is None or not callable(getattr(mod, "sim", None)):
                    raise LookupError("sim")
                f = mod.sim
                raw = f(self, now) if len(inspect.signature(f).parameters) >= 2 else f(now)
                period, label, live = 0.25, getattr(mod, "LABEL", label), False
            elif name in BUILTIN:
                fn, period, label, _ = BUILTIN[name]
                raw = fn(self._memo.setdefault(name, {}))
            else:
                mod = self._user_mod(name)
                if mod is None:
                    raise LookupError(name)
                raw, period, label = self._call(mod.read), float(getattr(mod, "PERIOD", 5)), getattr(mod, "LABEL", label)
            if not isinstance(raw, dict):
                raw = {"value": raw}
            if raw.get("value") is not None:
                raw = {**raw, "value": _clamp(raw["value"])}     # a bad value fails here, inside the guard
            if raw.get("items") is not None and not isinstance(raw["items"], (list, tuple)):
                raise TypeError("items must be a list")
            if raw.get("data") is not None and not isinstance(raw["data"], dict):
                raise TypeError("data must be a dict")
        except Exception as e:                                   # noqa: BLE001  missing source: simulate, say so once
            prev = self._vals.get(name)
            if prev is not None and prev.live and not self.sim and name not in self._fixed:
                return prev, 2.0                                 # one bad read: the last real reading stands
            if not self.sim and not isinstance(e, LookupError) and not self._memo.setdefault(name, {}).get("warned"):
                self._memo[name]["warned"] = True
                self.log(f"signal {name}: {type(e).__name__}: {e}; using simulated values")
            label = BUILTIN[name][2] if name in BUILTIN else label
            raw, period, live = _sim(name, now), 0.25, False
        v = raw.get("value")
        v = None if v is None else _clamp(v)
        text = raw.get("text") or ("--" if v is None else f"{v * 100:.0f}%")
        return Sig(name, v, str(text), label, raw.get("items"), live, raw.get("data")), period

    def _loop(self):
        while True:
            now = time.time()
            for name in list(self._want):
                if now >= self._due.get(name, 0):
                    try:
                        sig, period = self._read(name, now)
                    except Exception as e:                       # noqa: BLE001  one signal never stops the rest
                        self.log(f"signal {name}: {type(e).__name__}: {e}")
                        self._due[name] = now + 10
                        continue
                    with self._lock:
                        self._vals[name] = sig
                    self._due[name] = now + period
            time.sleep(0.1)

    def snapshot(self, names) -> Snapshot:
        """Latest reading of each name. A built-in is read at once on first use; a user signal starts as
        0 ("--") and is read on the background thread, so a slow one never holds a frame."""
        now, out = self.clock(), {}
        for name in names:
            if name not in self._vals and not (self.sim or name in self._fixed or name in BUILTIN):
                with self._lock:                                  # a user signal may be slow: first read on the thread
                    self._vals.setdefault(name, Sig(name, 0.0, "--", live=False))
            elif name not in self._vals or name in self._fixed or self.sim:   # sim and pinned values need no thread
                sig, period = self._read(name, now)
                with self._lock:
                    self._vals[name] = sig
                self._due[name] = now + period
            self._want.add(name)
            out[name] = self._vals[name]
        if self._thread is None and not self._fixed and not self.sim:
            with self._lock:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._loop, daemon=True, name="m18.signals")
                    self._thread.start()
        return Snapshot(out)
