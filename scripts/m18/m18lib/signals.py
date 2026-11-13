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
them as `s.inbox.items` and `s.inbox.data`. The built-ins carry what is inside their number there:
`cpu` has `cores` (a load per core), `kernel` (the share of the work done in the kernel), `top` and
`top_share` (the busiest program and its share of all cores); `ram` has `cache` (memory holding
files that can be given back at once) and `swap`; `net` has `down` and `up`; `disk` has `read` and
`write`; `gpu` has `vram` and `temp` (deg C). All are 0..1 unless said, and the rates come with a
`*_text` ("2.1M/s"). Any of them may be missing, so read them with `.get`. `read` and `sim` may take one argument, the Hub, for
`hub.base` (the install folder) and `hub.fn("plugin.function")`. Signals shipped with the skill
live next to this package in `signals/`; a file of the same name in `$M18/signals/` replaces one.

Every signal also has a simulated form (slow waves), used when `sim=True`, when its source is
missing, and in the browser. A scene therefore always gets a number to draw.
"""
from __future__ import annotations

import importlib.util
import inspect
import math
import struct
import sys
import threading
import time
import zlib
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent


class Sig:
    __slots__ = ("name", "value", "text", "label", "items", "data", "live")

    def __init__(self, name, value=None, text="", label=None, items=None, live=True, data=None):
        self.name, self.value, self.text = name, value, text
        self.label, self.items, self.live, self.data = label or name.upper(), list(items or []), live, dict(data) if isinstance(data, dict) else {}

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
def _log(bps, low):
    """A rate on a log scale: `low` bytes a second reads 0 and a thousand times that reads 1."""
    return _clamp((math.log10(max(bps, 1.0)) - math.log10(low)) / 3)


def _proc_times():
    """{(pid, when it started): (program, CPU seconds it has used so far)} for every process. Windows gives
    the whole table in one call; asking psutil for each process takes about a millisecond apiece there."""
    out = {}
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cpu_times", "create_time"]):
            ct = p.info.get("cpu_times")
            if ct:
                out[(p.info.get("pid"), p.info.get("create_time"))] = (p.info.get("name") or "?", ct.user + ct.system)
        return out
    import ctypes
    size = ctypes.c_ulong(1 << 20)
    while True:
        buf = ctypes.create_string_buffer(size.value)
        status = ctypes.windll.ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(size)) & 0xFFFFFFFF
        if status == 0:
            break
        if status != 0xC0000004:                                 # anything but "buffer too small"
            raise OSError(f"NtQuerySystemInformation {status:#x}")
        size.value += 1 << 16
    off = 0
    while True:                                                  # SYSTEM_PROCESS_INFORMATION, 64-bit layout
        step, = struct.unpack_from("<L", buf, off)
        born, user, kernel = struct.unpack_from("<qqq", buf, off + 32)
        length, = struct.unpack_from("<H", buf, off + 56)
        ptr, pid = struct.unpack_from("<Q", buf, off + 64)[0], struct.unpack_from("<Q", buf, off + 80)[0]
        if pid:                                                  # pid 0 is the idle time
            name = ctypes.wstring_at(ptr, length // 2) if ptr and length else "System"
            out[(pid, born)] = (name, (user + kernel) / 1e7)
        if not step:
            return out
        off += step


def _top(memo, cores):
    """The program that used the most CPU since the last look, and its share of all cores."""
    now, times = time.time(), _proc_times()
    last = memo.get("procs")
    memo["procs"] = (now, times)
    if not last or now - last[0] < 0.5:
        return memo.get("top") or ("", 0.0)
    used = {}
    for key, (name, secs) in times.items():                      # one that started since the last look counts from nothing
        gain = secs - last[1].get(key, ("", 0.0))[1]
        if gain > 0:
            used[name] = used.get(name, 0.0) + gain              # every process of one program added up
    if not used:
        return "", 0.0
    name = max(used, key=used.get)
    share = _clamp(used[name] / ((now - last[0]) * max(1, cores)))
    return (name[:-4] if name.lower().endswith(".exe") else name), share


def _cpu(memo):
    import psutil
    cores = [c / 100 for c in psutil.cpu_percent(interval=None, percpu=True)]
    v = 100 * sum(cores) / max(1, len(cores))
    ct = psutil.cpu_times_percent(interval=None)
    busy = 100 - ct.idle - getattr(ct, "iowait", 0.0)
    kernel = _clamp((busy - ct.user - getattr(ct, "nice", 0.0)) / busy) if busy > 1 else 0.0
    if time.time() - memo.get("top_at", 0) >= 3:                 # who is doing the work: every three seconds
        memo["top_at"] = time.time()
        try:
            memo["top"] = _top(memo, len(cores))
        except Exception:                                        # noqa: BLE001  the number matters more than the name
            memo["top"] = ("", 0.0)
    name, share = memo.get("top") or ("", 0.0)
    return {"value": v / 100, "text": f"{v:.0f}%", "data": {"cores": cores, "kernel": kernel, "top": name, "top_share": share}}


def _cache(vm):
    """The part of memory that holds files and can be handed back at once, as a share of all of it."""
    if hasattr(vm, "cached"):                                    # Linux
        return _clamp((vm.cached + getattr(vm, "buffers", 0)) / vm.total)
    if sys.platform == "win32":                                  # the standby list, with the system's own working set
        import ctypes
        info = (ctypes.c_size_t * 13)()                          # PERFORMANCE_INFORMATION: cb, 10 sizes, 3 counts
        ctypes.cast(info, ctypes.POINTER(ctypes.c_ulong))[0] = ctypes.sizeof(info)
        if ctypes.windll.psapi.GetPerformanceInfo(info, ctypes.sizeof(info)):
            return _clamp(min(info[6] * info[10], vm.available) / vm.total)
        return None
    return _clamp(vm.inactive / vm.total) if hasattr(vm, "inactive") else None


def _ram(memo):
    import psutil
    vm = psutil.virtual_memory()
    data = {"used_text": _rate(vm.total - vm.available)[:-2], "total_text": _rate(vm.total)[:-2]}
    try:
        data.update(cache=_cache(vm), swap=psutil.swap_memory().percent / 100)
    except Exception:                                            # noqa: BLE001  extras never cost the reading
        pass
    return {"value": vm.percent / 100, "text": f"{vm.percent:.0f}%", "data": {k: v for k, v in data.items() if v is not None}}


def _net(memo):
    import psutil
    c, now = psutil.net_io_counters(), time.time()
    last = memo.get("last")
    memo["last"] = (c.bytes_sent, c.bytes_recv, now)
    if not last or now <= last[2]:
        return {"value": 0.0, "text": "0B/s"}
    up, down = (max(0.0, (a - b) / (now - last[2])) for a, b in ((c.bytes_sent, last[0]), (c.bytes_recv, last[1])))
    # log scale: 10 KB/s reads 0, 10 MB/s reads 1
    return {"value": _log(up + down, 1e4), "text": _rate(up + down),
            "data": {"up": _log(up, 1e4), "down": _log(down, 1e4), "up_text": _rate(up), "down_text": _rate(down)}}


def _disk(memo):
    import psutil
    now = time.time()
    if now - memo.get("at", 0) > 30:                             # how full it is changes slowly
        memo["at"], memo["full"] = now, psutil.disk_usage(str(Path.home().anchor or "/")).percent
    v, data = memo["full"], {}
    try:
        c = psutil.disk_io_counters()
        last = memo.get("last")
        memo["last"] = (c.read_bytes, c.write_bytes, now)
        if last and now > last[2]:                               # log scale: 100 KB/s reads 0, 100 MB/s reads 1
            rd, wr = (max(0.0, (a - b) / (now - last[2])) for a, b in ((c.read_bytes, last[0]), (c.write_bytes, last[1])))
            data = {"read": _log(rd, 1e5), "write": _log(wr, 1e5), "read_text": _rate(rd), "write_text": _rate(wr)}
    except Exception:                                            # noqa: BLE001  no counters on this system
        pass
    return {"value": v / 100, "text": f"{v:.0f}%", "data": data}


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
    v, data = pynvml.nvmlDeviceGetUtilizationRates(memo["h"]).gpu, {}
    try:
        mem = pynvml.nvmlDeviceGetMemoryInfo(memo["h"])
        data = {"vram": mem.used / mem.total, "vram_text": _rate(mem.used)[:-2],
                "temp": pynvml.nvmlDeviceGetTemperature(memo["h"], pynvml.NVML_TEMPERATURE_GPU)}
    except Exception:                                            # noqa: BLE001  extras never cost the reading
        pass
    return {"value": v / 100, "text": f"{v}%", "data": data}


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
    "disk": (_disk, 1, "DISK", "how full the system drive is: nearly static, good for terrain or sediment"),
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


def _inside(name: str, v: float, t: float) -> dict | None:
    """What a real reading carries in `data`, made up to fit a pinned or simulated value, so a preview
    shows the same picture the PC would."""
    def wave(period, phase=0.0):
        return 0.5 + 0.5 * math.sin(t / period + phase)

    def rate(x, low):
        return _rate(low * 10 ** (3 * x))
    if name == "cpu":
        cores = [_clamp(v * (0.35 + 1.3 * ((i * 0.618) % 1.0)) + 0.05 * math.sin(t * 0.7 + i * 2.1)) for i in range(8)]
        return {"cores": cores, "kernel": 0.2 + 0.1 * wave(13), "top_share": v * 0.55,
                "top": ("compiler", "browser", "renderer", "indexer")[int(t / 40) % 4]}
    if name == "ram":
        return {"cache": (1 - v) * 0.6, "swap": max(0.0, v - 0.6) * 0.5, "used_text": f"{v * 32:.1f}G", "total_text": "32.0G"}
    if name == "net":
        up = _clamp(v * (0.35 + 0.3 * wave(5)))
        return {"down": v, "up": up, "down_text": rate(v, 1e4), "up_text": rate(up, 1e4)}
    if name == "disk":
        rd, wr = _clamp(1.6 * wave(11) - 0.9), _clamp(1.6 * wave(17, 2.0) - 0.8)
        return {"read": rd, "write": wr, "read_text": rate(rd, 1e5), "write_text": rate(wr, 1e5)}
    if name == "gpu":
        return {"vram": 0.25 + 0.6 * v, "vram_text": f"{2 + 4.8 * v:.1f}G", "temp": round(42 + 40 * v)}
    return None


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
        if raw.get("data") is None and v is not None and (not live or name in self._fixed):
            raw = {**raw, "data": _inside(name, v, now)}         # a pinned or simulated number gets insides to match
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
