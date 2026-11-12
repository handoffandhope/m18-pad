"""Layout key: spread the agent windows (Claude, ChatGPT/Codex) over the side monitors, and snap them back.

    9: {live: {plugin: layout.face, interval: 2}, action: {plugin: layout.spread}, hold: {plugin: layout.open}}

Face   a small map of the monitors in their real arrangement; each monitor shows its agent windows as coloured
       strips (orange Claude, teal ChatGPT/Codex). Green outline = arrangement matches the target; amber = agent
       windows on the main monitor, on a monitor switched off, or unevenly stacked (the planner would move one).
Tap    spread now with the saved mode. The window that has focus on the main monitor is never moved by a tap.
Hold   takeover: each monitor gets its own key (same arrangement as the desk) showing what the spread WOULD do;
       Even / By app pick the mode, a monitor key switches that monitor on or off for spreading (for a laptop
       screen that is on the desktop while nobody watches it), Go applies (this one also moves a focused agent
       window off the main monitor), Undo puts back the placements saved before the last move. b1 closes.

Placement: least movement. Every side monitor gets an even share (counts differ by at most one; the bigger
shares go to the monitors that already hold the most), windows already on a monitor stay there while it is
within its share, the rest fill the gaps left to right. One window is maximised, 2 are halves, 3 are thirds
(columns on a landscape screen). CAP = 3 per monitor; windows beyond 3 x side monitors stay where they are.
By app: Claude takes the left monitors, ChatGPT the right ones, monitors split by window count.
Before every move the old placements go to state/layout-last.json (Undo reads it). Windows of elevated
processes are skipped (Windows blocks moving them from a normal process).

Moves use SetWindowPlacement (handles minimised and maximised windows; the normal rect is put on the target
monitor first so a maximise lands there) and then SetWindowPos for tiles, twice, because a per-monitor-DPI app
resizes itself when it crosses to a monitor with another scale. The calling thread runs per-monitor-DPI-aware
(v2) so every coordinate is in physical pixels. Against a --mock daemon (or with M18_LAYOUT_DRY=1) nothing
moves; the plan is logged. M18_LAYOUT_SNAPSHOT=<json> replaces the live window list (tests and previews).

CLI (read-only):  python layout.py plan [--mode even|app] [--snapshot f.json]   prints the plan for now
                  python layout.py snapshot out.json                             saves monitors + windows
Monitor names: plugins/layout.json may name them by hardware id or, for virtual screens, by parent device:
{"names_by_id": {"ABC1234": "Left"}, "names_by_parent": {"ROOT\\DISPLAY\\0001": "Tablet"}}. Others show as "Mon N".
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import json
import os
import re
import sys
import time
from pathlib import Path

# ------------------------------------------------------------------------------ what counts as an agent window
# (group, process name regex, title regex). First match wins; both case-insensitive; "" title = any title.
# Observed 2026-11-11: the Claude desktop app is ONE top-level window (claude.exe, Chrome_WidgetWin_1, title
# "Claude"; its sessions are tabs/splits inside it). ChatGPT.exe is OpenAI's desktop app (MSIX OpenAI.Codex,
# ChatGPT and Codex in one window). codex.exe runs had no windows. Browser and terminal rows match the title
# of the active tab only.
BROWSERS = r"chrome\.exe|msedge\.exe|firefox\.exe|brave\.exe"
TERMINALS = r"windowsterminal\.exe|openconsole\.exe|conhost\.exe|wezterm-gui\.exe|alacritty\.exe"
AGENTS = [
    ("claude", r"claude\.exe", ""),
    ("gpt", r"chatgpt\.exe|codex\.exe", ""),
    ("claude", BROWSERS, r"\bclaude\b|claude\.ai"),
    ("gpt", BROWSERS, r"\bchatgpt\b|chatgpt\.com|\bcodex\b"),
    ("claude", TERMINALS, r"claude"),
    ("gpt", TERMINALS, r"\bcodex\b"),
]
CAP = 3
COLORS = {"claude": "#e8875b", "gpt": "#3fd0a8", "other": "#9a9aaa"}
MODES = ("even", "app")
MODAL_S = 15
AMBER = "#ffaa00"            # one amber in every theme (hillside's own "warn" is yellow, too close to the main stand)
TOL = 10                     # px: a tile this close to its target counts as in place

# monitor names by device, from plugins/layout.json (see docstring); anything else is "Mon N"
NAMES_BY_ID, NAMES_BY_PARENT = {}, {}
try:
    import json as _json
    from pathlib import Path as _Path
    _names = _json.loads(_Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
    NAMES_BY_ID = {str(k): str(v) for k, v in (_names.get("names_by_id") or {}).items()}
    NAMES_BY_PARENT = {str(k).upper(): str(v) for k, v in (_names.get("names_by_parent") or {}).items()}
except (OSError, ValueError, AttributeError):
    pass


# ============================================================================== pure planner
def classify(w: dict, rules=None) -> str | None:
    for group, proc, title in (rules or AGENTS):
        if re.fullmatch(proc, w.get("proc", ""), re.I) and (not title or re.search(title, w.get("title", ""), re.I)):
            return group
    return None


def _ctr(r):
    return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)


def grid(monitors: list[dict]) -> dict:
    """{monitor id: (col, row)} that keeps the desk arrangement on a small grid: monitors whose x ranges mostly
    overlap share a column (stacked), rows come from the tallest stack; a lone monitor takes the row of the
    stack member it is level with."""
    cols: list[dict] = []
    for m in sorted(monitors, key=lambda m: (m["rect"][0], m["rect"][1])):
        l, t, r, b = m["rect"]
        for c in cols:
            ov = min(r, c["r"]) - max(l, c["l"])
            if ov >= 0.5 * min(r - l, c["r"] - c["l"]):
                c["m"].append(m)
                c["l"], c["r"] = min(l, c["l"]), max(r, c["r"])
                break
        else:
            cols.append({"l": l, "r": r, "m": [m]})
    cols.sort(key=lambda c: (c["l"] + c["r"]) / 2)
    tall = max(cols, key=lambda c: len(c["m"]))
    bands = [_ctr(m["rect"])[1] for m in sorted(tall["m"], key=lambda m: m["rect"][1])]
    out = {}
    for ci, c in enumerate(cols):
        ms = sorted(c["m"], key=lambda m: m["rect"][1])
        if len(ms) == len(bands):
            for ri, m in enumerate(ms):
                out[m["id"]] = (ci, ri)
        else:
            used = set()
            for m in ms:
                cy = _ctr(m["rect"])[1]
                ri = min((i for i in range(len(bands)) if i not in used), key=lambda i: abs(bands[i] - cy))
                used.add(ri)
                out[m["id"]] = (ci, ri)
    return out


def tiles(work, k: int) -> list[list[int]]:
    l, t, r, b = work
    w, h = r - l, b - t
    if k <= 1:
        return [[l, t, r, b]]
    if w >= h:
        xs = [l + w * i // k for i in range(k + 1)]
        return [[xs[i], t, xs[i + 1], b] for i in range(k)]
    ys = [t + h * i // k for i in range(k + 1)]
    return [[l, ys[i], r, ys[i + 1]] for i in range(k)]


def _distribute(wins: list[dict], mons: list[str], cap: int):
    """Even shares over `mons` with the least movement. Returns ({mon: [win]}, overflow)."""
    S = len(mons)
    if not S:
        return {}, list(wins)
    place = min(len(wins), cap * S)
    cur = {m: sum(1 for w in wins if w["monitor"] == m) for m in mons}
    base, extra = divmod(place, S)
    quota = {m: base for m in mons}
    for m in sorted(mons, key=lambda m: (-cur[m], mons.index(m)))[:extra]:
        quota[m] += 1
    assign, surplus = {}, []
    for m in mons:
        there = [w for w in wins if w["monitor"] == m]
        assign[m] = there[:quota[m]]
        surplus += there[quota[m]:]
    pool = surplus + [w for w in wins if w["monitor"] not in mons]
    for m in mons:
        while len(assign[m]) < quota[m] and pool:
            assign[m].append(pool.pop(0))
    return assign, pool


def _noop(w: dict, mon: str, rect, maximize: bool) -> bool:
    if w["monitor"] != mon or w.get("min"):
        return False
    if maximize:
        return bool(w.get("max"))
    f = w.get("frame") or w.get("rect")
    return not w.get("max") and all(abs(a - b) <= TOL for a, b in zip(f, rect))


def plan(snap: dict, mode: str = "even", skip=(), include_focus: bool = False, cap: int = CAP, rules=None) -> dict:
    """Pure: which agent window goes where. `snap` = {"monitors": [...], "windows": [...]} as snapshot() makes."""
    mons = snap["monitors"]
    byid = {m["id"]: m for m in mons}
    main = next((m["id"] for m in mons if m.get("primary")), mons[0]["id"] if mons else None)
    order = sorted((m for m in mons if m["id"] != main), key=lambda m: (_ctr(m["rect"])[0], m["rect"][1]))
    sides = [m["id"] for m in order if m["name"] not in skip and m["id"] not in skip]
    mrank = {m["id"]: i for i, m in enumerate(order)}
    mrank[main] = len(order)

    agents, pinned, elevated = [], [], []
    for w in snap["windows"]:
        g = classify(w, rules)
        if not g or w.get("monitor") not in byid:
            continue
        w = dict(w, group=g)
        if w.get("elevated"):
            elevated.append(w)
        elif w.get("fg") and w["monitor"] == main and not include_focus:
            pinned.append(w)
        else:
            agents.append(w)
    agents.sort(key=lambda w: (mrank.get(w["monitor"], 99), (w.get("frame") or w["rect"])[0], w["hwnd"]))

    if mode == "app":
        cl = [w for w in agents if w["group"] == "claude"]
        gp = [w for w in agents if w["group"] != "claude"]
        if cl and gp and len(sides) >= 2:
            best = None
            for k in range(1, len(sides)):
                a1, o1 = _distribute(cl, sides[:k], cap)
                a2, o2 = _distribute(gp, sides[k:], cap)
                a = {**a1, **a2}
                moves = sum(1 for m, ws in a.items() for w in ws if w["monitor"] != m)
                score = (len(o1) + len(o2), abs(k - len(sides) * len(cl) / len(agents)), moves, k)
                if best is None or score < best[0]:
                    best = (score, a, o1 + o2)
            _, assign, overflow = best
        else:
            assign, overflow = _distribute(agents, sides, cap)
    else:
        assign, overflow = _distribute(agents, sides, cap)

    moves = []
    for m, ws in assign.items():
        kept = sorted((w for w in ws if w["monitor"] == m), key=lambda w: ((w.get("frame") or w["rect"])[0], w["hwnd"]))
        ws = kept + [w for w in ws if w["monitor"] != m]
        assign[m] = ws
        for w, r in zip(ws, tiles(byid[m]["work"], len(ws))):
            mx = len(ws) == 1
            moves.append({"hwnd": w["hwnd"], "title": w.get("title", ""), "proc": w.get("proc", ""),
                          "group": w["group"], "from": w["monitor"], "to": m, "rect": r, "maximize": mx,
                          "noop": _noop(w, m, r, mx)})
    now = {m["id"]: [] for m in mons}
    for w in agents + pinned + elevated:
        now[w["monitor"]].append(w["group"])
    after = {m["id"]: [] for m in mons}
    for m, ws in assign.items():
        after[m] = [w["group"] for w in ws]
    for w in overflow + pinned + elevated:
        after[w["monitor"]].append(w["group"])
    relocations = [mv for mv in moves if mv["from"] != mv["to"]]
    on_off = [w for w in agents + pinned if w["monitor"] == main or w["monitor"] not in sides]
    total = len(agents) + len(pinned) + len(elevated)
    status = "none" if not total else ("warn" if (relocations or on_off) else "ok")
    hot = {mv["from"] for mv in relocations} | {mv["to"] for mv in relocations} | {w["monitor"] for w in on_off}
    return {"mode": mode, "main": main, "sides": sides, "skipped": [m["id"] for m in order if m["id"] not in sides],
            "moves": moves, "overflow": [w["hwnd"] for w in overflow], "pinned": [w["hwnd"] for w in pinned],
            "elevated": [w["hwnd"] for w in elevated], "now": now, "after": after, "status": status,
            "hot": sorted(hot), "grid": grid(mons)}


def describe(p: dict, snap: dict) -> str:
    names = {m["id"]: m["name"] + (" (main)" if m["id"] == p["main"] else "") for m in snap["monitors"]}
    out = [f"mode {p['mode']}  status {p['status']}  sides {[names[s] for s in p['sides']]}"]
    for mv in p["moves"]:
        how = "maximise" if mv["maximize"] else "tile " + "x".join(str(v) for v in mv["rect"])
        verb = "stays" if mv["noop"] else ("move" if mv["from"] != mv["to"] else "re-tile")
        out.append(f"  {verb:7} {mv['proc']:<14} {mv['title'][:40]!r:44} {names[mv['from']]} -> {names[mv['to']]}  {how}")
    for k in ("pinned", "overflow", "elevated"):
        if p[k]:
            out.append(f"  {k}: {[hex(h) for h in p[k]]}")
    return "\n".join(out)


# ============================================================================== Win32 (live snapshot + moves)
class _MI(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD),
                ("szDevice", W.WCHAR * 32)]


class _DD(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("DeviceName", W.WCHAR * 32), ("DeviceString", W.WCHAR * 128),
                ("StateFlags", W.DWORD), ("DeviceID", W.WCHAR * 128), ("DeviceKey", W.WCHAR * 128)]


class _WP(ctypes.Structure):
    _fields_ = [("length", W.UINT), ("flags", W.UINT), ("showCmd", W.UINT), ("ptMin", W.POINT),
                ("ptMax", W.POINT), ("rcNormal", W.RECT)]


def _api():
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    u.MonitorFromWindow.restype = W.HMONITOR
    u.GetForegroundWindow.restype = W.HWND
    u.GetWindow.restype = W.HWND
    u.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
    k.OpenProcess.restype = W.HANDLE
    return u, k


class _PMv2:
    """Run a block per-monitor-DPI-aware (v2) on this thread only; the daemon's other threads are untouched."""

    def __enter__(self):
        u, _ = _api()
        self.old = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        return self

    def __exit__(self, *a):
        if self.old:
            _api()[0].SetThreadDpiAwarenessContext(ctypes.c_void_p(self.old))


_PARENT: dict[str, str] = {}


def _parent_of(inst: str) -> str:
    if inst not in _PARENT:
        cm = ctypes.windll.cfgmgr32
        dn, par = W.DWORD(), W.DWORD()
        buf = ctypes.create_unicode_buffer(400)
        p = ""
        if cm.CM_Locate_DevNodeW(ctypes.byref(dn), inst, 0) == 0 and cm.CM_Get_Parent(ctypes.byref(par), dn, 0) == 0 \
                and cm.CM_Get_Device_IDW(par, buf, 400, 0) == 0:
            p = buf.value
        _PARENT[inst] = p
    return _PARENT[inst]


def _mon_name(iface: str, n: int) -> str:
    m = re.match(r"^\\\\\?\\(DISPLAY#[^#]+#[^#]+)#", iface or "")
    if m:
        inst = m.group(1).replace("#", "\\")
        hw = inst.split("\\")[1]
        if hw in NAMES_BY_ID:
            return NAMES_BY_ID[hw]
        par = _parent_of(inst).upper()
        if par in NAMES_BY_PARENT:
            return NAMES_BY_PARENT[par]
    return f"Mon {n}"


def _elevated(pid: int, k) -> bool:
    adv = ctypes.windll.advapi32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    tok = W.HANDLE()
    try:
        if not adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
            return False
        e, n = W.DWORD(), W.DWORD()
        ok = adv.GetTokenInformation(tok, 20, ctypes.byref(e), 4, ctypes.byref(n))
        k.CloseHandle(tok)
        return bool(ok and e.value)
    finally:
        k.CloseHandle(h)


def _self_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pname(pid: int, k) -> str:
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    buf, n = ctypes.create_unicode_buffer(1024), W.DWORD(1024)
    ok = k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    k.CloseHandle(h)
    return os.path.basename(buf.value) if ok else ""


def _frame(h, u) -> list[int]:
    r = W.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(W.HWND(h), 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
        return [r.left, r.top, r.right, r.bottom]
    u.GetWindowRect(W.HWND(h), ctypes.byref(r))
    return [r.left, r.top, r.right, r.bottom]


def snapshot() -> dict:
    """Monitors and movable top-level windows now (read-only)."""
    f = os.environ.get("M18_LAYOUT_SNAPSHOT")
    if f:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    u, k = _api()
    with _PMv2():
        mons, hmap = [], {}
        PROC = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)

        def mcb(h, hdc, r, lp):
            mi = _MI(); mi.cbSize = ctypes.sizeof(_MI)
            u.GetMonitorInfoW(h, ctypes.byref(mi))
            dd = _DD(); dd.cb = ctypes.sizeof(_DD)
            u.EnumDisplayDevicesW(mi.szDevice, 0, ctypes.byref(dd), 1)
            dx, dy = ctypes.c_uint(96), ctypes.c_uint(96)
            try:
                ctypes.windll.shcore.GetDpiForMonitor(h, 0, ctypes.byref(dx), ctypes.byref(dy))
            except Exception:
                pass
            R, Wk = mi.rcMonitor, mi.rcWork
            mid = dd.DeviceID or mi.szDevice
            hmap[int(h)] = mid
            mons.append({"id": mid, "device": mi.szDevice, "name": _mon_name(dd.DeviceID, len(mons) + 1),
                         "rect": [R.left, R.top, R.right, R.bottom], "work": [Wk.left, Wk.top, Wk.right, Wk.bottom],
                         "primary": bool(mi.dwFlags & 1), "dpi": dx.value})
            return True
        u.EnumDisplayMonitors(None, None, PROC(mcb), 0)

        fg = int(u.GetForegroundWindow() or 0)
        me = _self_elevated()
        wins = []
        EP = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

        def wcb(h, lp):
            if not u.IsWindowVisible(h) or u.GetWindow(h, 4):           # hidden or owned (dialogs, popups)
                return True
            style = u.GetWindowLongW(h, -16) & 0xFFFFFFFF
            ex = u.GetWindowLongW(h, -20) & 0xFFFFFFFF
            if ex & 0x80 or not style & 0x10000:                          # tool window, or no maximise box
                return True
            cl = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(cl), 4)
            if cl.value:                                                  # cloaked: another virtual desktop, UWP ghost
                return True
            n = u.GetWindowTextLengthW(h)
            t = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(h, t, n + 1)
            pid = W.DWORD(); u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            r = W.RECT(); u.GetWindowRect(h, ctypes.byref(r))
            hw = int(h)
            wins.append({"hwnd": hw, "title": t.value, "proc": _pname(pid.value, k), "pid": pid.value,
                         "monitor": hmap.get(int(u.MonitorFromWindow(h, 2) or 0)),
                         "rect": [r.left, r.top, r.right, r.bottom], "frame": _frame(hw, u),
                         "min": bool(u.IsIconic(h)), "max": bool(u.IsZoomed(h)), "fg": hw == fg,
                         "elevated": (not me) and _elevated(pid.value, k)})
            return True
        u.EnumWindows(EP(wcb), 0)
    return {"t": time.time(), "monitors": mons, "windows": wins, "foreground": fg}


def _placement(h, u) -> dict:
    wp = _WP(); wp.length = ctypes.sizeof(_WP)
    u.GetWindowPlacement(W.HWND(h), ctypes.byref(wp))
    r = W.RECT(); u.GetWindowRect(W.HWND(h), ctypes.byref(r))
    return {"flags": wp.flags, "show": wp.showCmd, "min": [wp.ptMin.x, wp.ptMin.y], "max": [wp.ptMax.x, wp.ptMax.y],
            "normal": [wp.rcNormal.left, wp.rcNormal.top, wp.rcNormal.right, wp.rcNormal.bottom],
            "rect": [r.left, r.top, r.right, r.bottom], "iconic": bool(u.IsIconic(W.HWND(h))),
            "zoomed": bool(u.IsZoomed(W.HWND(h)))}


def _set_placement(h, u, p: dict, show: int) -> bool:
    wp = _WP(); wp.length = ctypes.sizeof(_WP)
    wp.flags = p["flags"] & ~0x0002            # drop WPF_RESTORETOMAXIMIZED unless asked for again
    wp.showCmd = show
    wp.ptMin = W.POINT(*p["min"]); wp.ptMax = W.POINT(*p["max"])
    wp.rcNormal = W.RECT(*p["normal"])
    return bool(u.SetWindowPlacement(W.HWND(h), ctypes.byref(wp)))


def _margins(h, u):
    """Invisible resize borders: window rect minus DWM frame (left, top, right, bottom), restored windows only."""
    r = W.RECT(); u.GetWindowRect(W.HWND(h), ctypes.byref(r))
    f = _frame(h, u)
    return (f[0] - r.left, f[1] - r.top, r.right - f[2], r.bottom - f[3])


def _unaware(h, u) -> bool:
    try:
        u.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        return u.GetAwarenessFromDpiAwarenessContext(u.GetWindowDpiAwarenessContext(W.HWND(h))) == 0
    except Exception:
        return False


def _mon_of(h, u, snap) -> str | None:
    hm = int(u.MonitorFromWindow(W.HWND(h), 2) or 0)
    mi = _MI(); mi.cbSize = ctypes.sizeof(_MI)
    u.GetMonitorInfoW(hm, ctypes.byref(mi))
    for m in snap["monitors"]:
        if m["device"] == mi.szDevice:
            return m["id"]
    return None


def move_window(h: int, mon: dict, rect, maximize: bool) -> None:
    """Put window h on monitor `mon`: maximised there, or with its visible frame on `rect` (screen px)."""
    u, _ = _api()
    p = _placement(h, u)
    # the normal rect, in workspace coordinates (relative to the work area), on the target monitor
    ox, oy = mon["work"][0] - mon["rect"][0], mon["work"][1] - mon["rect"][1]
    w = p["normal"][2] - p["normal"][0]
    hh = p["normal"][3] - p["normal"][1]
    l, t = rect[0], rect[1]
    if maximize:
        w, hh = min(w, rect[2] - rect[0]), min(hh, rect[3] - rect[1])
        l, t = rect[0] + (rect[2] - rect[0] - w) // 2, rect[1] + (rect[3] - rect[1] - hh) // 2
    else:
        w, hh = rect[2] - rect[0], rect[3] - rect[1]
    p["normal"] = [l - ox, t - oy, l - ox + w, t - oy + hh]
    _set_placement(h, u, p, 4)                 # SW_SHOWNOACTIVATE: restored, on the target monitor, not focused
    if maximize:
        u.ShowWindow(W.HWND(h), 3)             # SW_MAXIMIZE (activates; the caller gives focus back)
        return
    # Settle the visible frame on `rect`: add the invisible borders, and repeat because a per-monitor-aware app
    # resizes itself on WM_DPICHANGED when it arrives on a monitor with another scale. SetWindowPlacement, not
    # SetWindowPos: on 2026-11-11 SetWindowPos from this (PMv2) thread grew a DPI-unaware tk window on the
    # 120-DPI monitor to ~1.8x the asked size, while SetWindowPlacement put it exactly where asked.
    # A DPI-unaware window whose outer rect (frame + invisible borders) pokes past the monitor edge gets rescaled
    # by Windows (seen: a tk window grew to 1.8x when its left border crossed into the next screen), so
    # its outer rect is clamped to the monitor: the visible frame then sits a border width (~9 px) inside.
    unaware = _unaware(h, u)
    for _ in range(3):
        f = _frame(h, u)
        if all(abs(a - b) <= 2 for a, b in zip(f, rect)):
            break
        ml, mt, mr, mb = _margins(h, u)
        x0, y0, x1, y1 = rect[0] - ml, rect[1] - mt, rect[2] + mr, rect[3] + mb
        if unaware:
            mr_ = mon["rect"]
            x0, y0, x1, y1 = max(x0, mr_[0]), max(y0, mr_[1]), min(x1, mr_[2]), min(y1, mr_[3])
        p = _placement(h, u)
        p["normal"] = [x0 - ox, y0 - oy, x1 - ox, y1 - oy]
        _set_placement(h, u, p, 4)
        if unaware:
            break


def _state_dir(base: Path | None) -> Path:
    d = Path(base or Path(os.environ.get("M18_HOME", Path.home() / "tools" / "m18"))) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def apply(p: dict, snap: dict, base: Path | None = None, log=print) -> dict:
    """Carry out a plan on the real windows. Saves the old placements first (state/layout-last.json)."""
    u, _ = _api()
    todo = [mv for mv in p["moves"] if not mv["noop"]]
    if not todo:
        return {"moved": 0, "failed": []}
    byid = {m["id"]: m for m in snap["monitors"]}
    fg = int(u.GetForegroundWindow() or 0)
    with _PMv2():
        saved = []
        for mv in todo:
            if u.IsWindow(W.HWND(mv["hwnd"])):
                pid = W.DWORD(); u.GetWindowThreadProcessId(W.HWND(mv["hwnd"]), ctypes.byref(pid))
                saved.append({"hwnd": mv["hwnd"], "pid": pid.value, "title": mv["title"],
                              "frame": _frame(mv["hwnd"], u), **_placement(mv["hwnd"], u)})
        f = _state_dir(base) / "layout-last.json"
        f.write_text(json.dumps({"t": time.time(), "windows": saved}, indent=1), encoding="utf-8")
        failed = []
        for mv in todo:
            try:
                move_window(mv["hwnd"], byid[mv["to"]], mv["rect"], mv["maximize"])
                if _mon_of(mv["hwnd"], u, snap) != mv["to"]:
                    failed.append(mv["hwnd"])
            except Exception as e:
                log(f"layout: move {mv['hwnd']:#x} failed: {e}")
                failed.append(mv["hwnd"])
        if fg and u.IsWindow(W.HWND(fg)):
            u.SetForegroundWindow(W.HWND(fg))
    log(f"layout: moved {len(todo) - len(failed)} of {len(todo)} ({p['mode']}), undo saved to {f}")
    return {"moved": len(todo) - len(failed), "failed": failed}


def undo(base: Path | None = None, log=print) -> int:
    """Put back the placements saved before the last move. Returns how many windows were restored."""
    f = _state_dir(base) / "layout-last.json"
    if not f.exists():
        return 0
    data = json.loads(f.read_text(encoding="utf-8"))
    u, _ = _api()
    fg = int(u.GetForegroundWindow() or 0)
    n = 0
    with _PMv2():
        for s in data.get("windows", []):
            h = s["hwnd"]
            pid = W.DWORD(); u.GetWindowThreadProcessId(W.HWND(h), ctypes.byref(pid))
            if not u.IsWindow(W.HWND(h)) or pid.value != s["pid"]:
                continue                                  # closed, or the handle now belongs to someone else
            show = 2 if s["iconic"] else (3 if s["zoomed"] else 4)    # SHOWMINIMIZED / MAXIMIZE / SHOWNOACTIVATE
            if s["zoomed"]:
                _set_placement(h, u, s, 4)
            _set_placement(h, u, s, show)
            if s["iconic"] and s["flags"] & 0x0002:
                wp = _WP(); wp.length = ctypes.sizeof(_WP)
                u.GetWindowPlacement(W.HWND(h), ctypes.byref(wp))
                wp.flags |= 0x0002
                u.SetWindowPlacement(W.HWND(h), ctypes.byref(wp))
            if not s["iconic"] and not s["zoomed"]:
                _set_placement(h, u, s, 4)              # again: a DPI change on the way back can resize it
            n += 1
        if fg and u.IsWindow(W.HWND(fg)):
            u.SetForegroundWindow(W.HWND(fg))
    f.replace(f.with_name("layout-last.undone.json"))     # Undo once; the file stays for inspection
    log(f"layout: undo restored {n} window(s)")
    return n


# ============================================================================== prefs and daemon glue
def _prefs(ctx) -> dict:
    st = ctx.daemon.plugin_state.setdefault("layout.prefs", {})
    if not st:
        f = _state_dir(ctx.config_dir) / "layout-prefs.json"
        try:
            st.update(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
        st.setdefault("mode", "even")
        st.setdefault("skip", [])
    return st


def _save_prefs(ctx, st):
    (_state_dir(ctx.config_dir) / "layout-prefs.json").write_text(json.dumps(st), encoding="utf-8")


def _dry(ctx) -> bool:
    return bool(getattr(ctx.daemon, "mock", False) or os.environ.get("M18_LAYOUT_DRY"))


def _run(ctx, include_focus: bool) -> dict:
    st = _prefs(ctx)
    snap = snapshot()
    p = plan(snap, st["mode"], st["skip"], include_focus)
    if _dry(ctx):
        ctx.log("layout (dry run, nothing moved):\n" + describe(p, snap))
        return {"moved": sum(1 for m in p["moves"] if not m["noop"]), "failed": [], "dry": True}
    return apply(p, snap, ctx.config_dir, ctx.log)


# ============================================================================== drawing
def _pen(ctx):
    from m18lib import tiles as T
    return T.Pen(ctx.daemon)


def _good_warn(pen):
    return pen.pal.get("good", "#55ff55"), AMBER


def _screen(pen, img, box, groups, edge, plan_rects=None, off=False):
    """One monitor: outline, dark screen, a coloured strip per window (side by side like the tiles)."""
    from m18lib import tiles as T
    x0, y0, x1, y1 = box
    pen.rect(img, (x0, y0, x1, y1), (0, 0, 0, 255))
    pen.rect(img, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), T.rgb(edge) + (255,))
    pen.rect(img, (x0 + 2, y0 + 2, x1 - 2, y1 - 2), (12, 12, 18, 255))
    if off:
        from PIL import ImageDraw
        k = pen.k
        d = ImageDraw.Draw(img)
        d.line(((x0 + 3) * k, (y0 + 3) * k, (x1 - 3) * k, (y1 - 3) * k), fill=T.rgb(T.GREY) + (255,), width=k)
        d.line(((x0 + 3) * k, (y1 - 3) * k, (x1 - 3) * k, (y0 + 3) * k), fill=T.rgb(T.GREY) + (255,), width=k)
        return
    shown = groups[:CAP]
    n = len(shown)
    if not n:
        return
    ix0, iy0, ix1, iy1 = x0 + 3, y0 + 3, x1 - 3, y1 - 3
    cols = (ix1 - ix0 + 1) >= (iy1 - iy0 + 1)
    span = (ix1 - ix0 + 1) if cols else (iy1 - iy0 + 1)
    for i, g in enumerate(shown):
        a = span * i // n
        b = span * (i + 1) // n - (1 if i < n - 1 else 0) - 1
        c = COLORS.get(g, COLORS["other"])
        if cols:
            pen.rect(img, (ix0 + a, iy0, ix0 + b, iy1), T.rgb(c) + (255,))
        else:
            pen.rect(img, (ix0, iy0 + a, ix1, iy0 + b), T.rgb(c) + (255,))
    if len(groups) > CAP:
        pen.text(img, (x0 + x1) / 2, (y0 + y1) / 2, f"+{len(groups) - CAP}", "#ffffff", scale=1)


def _stand(pen, img, cx, y, main):
    from m18lib import tiles as T
    c = T.rgb("#ffffff" if main else "#6a6a7a") + (255,)
    pen.rect(img, (cx - 1, y, cx + 1, y + 1), c)
    pen.rect(img, (cx - 4, y + 2, cx + 4, y + 2), c)


def draw_map(pen, snap, p) -> object:
    """The live key: every monitor at its place on the desk grid, its agent windows as strips."""
    img = pen.canvas()
    pen.plate(img)
    good, warn = _good_warn(pen)
    g = p["grid"]
    C = max(c for c, _ in g.values()) + 1
    Rw = max(r for _, r in g.values()) + 1
    gap = 2
    cw = (62 - (C - 1) * gap) // C
    ch = min(int(cw * 0.72), (56 - (Rw - 1) * (gap + 4)) // Rw - 4)
    top = (64 - (Rw * (ch + 4) + (Rw - 1) * gap)) // 2
    left = (64 - (C * cw + (C - 1) * gap)) // 2
    for m in snap["monitors"]:
        c, r = g[m["id"]]
        x0 = left + c * (cw + gap)
        y0 = top + r * (ch + 4 + gap)
        hot = m["id"] in p["hot"]
        edge = "#4a4a5a" if p["status"] == "none" else (warn if hot else good)
        _screen(pen, img, (x0, y0, x0 + cw - 1, y0 + ch - 1), p["now"][m["id"]], edge,
                off=m["id"] in p["skipped"])
        _stand(pen, img, x0 + cw // 2, y0 + ch, m["id"] == p["main"])
    return pen.face(img)


def draw_monitor_key(pen, m, p) -> dict:
    """Takeover: one monitor per key, showing what it will hold after Go."""
    img = pen.canvas()
    pen.plate(img)
    good, warn = _good_warn(pen)
    off = m["id"] in p["skipped"]
    changed = sorted(p["now"][m["id"]]) != sorted(p["after"][m["id"]]) or (m["id"] == p["main"] and p["after"][m["id"]])
    edge = "#6a6a7a" if off else (warn if changed else good)
    _screen(pen, img, (6, 6, 57, 39), p["after"][m["id"]], edge, off=off)
    _stand(pen, img, 32, 40, m["id"] == p["main"])
    pen.word(img, 32, 53, m["name"], "#77778a" if off else "#ffffff", maxw=56, top=2, lines=1, maxh=14)
    return pen.face(img)


def keymap(p) -> dict:
    """Desk grid -> pad keys: rows 1-2 hold the monitors (centred), row 3 the controls."""
    g = p["grid"]
    C = max(c for c, _ in g.values()) + 1
    Rw = max(r for _, r in g.values()) + 1
    if C <= 5 and Rw <= 2:
        c0 = (5 - C) // 2
        return {mid: 1 + r * 5 + c0 + c for mid, (c, r) in g.items()}
    ids = sorted(g, key=lambda m: (g[m][0], g[m][1]))            # too big: one row per order, wraps over rows 1-2
    return {mid: 1 + i for i, mid in enumerate(ids[:10])}


def _mode_key(pen, icon, label, selected, color) -> dict:
    """Mode key: the theme's own icon face; the chosen mode gets a bar across the top (a border would cover the
    label in the plain look)."""
    from PIL import ImageDraw
    from m18lib import tiles as T
    img = pen.icon_face(icon, label, key=11)
    if selected:
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, 191, 17), fill=(0, 0, 0, 255))
        d.rectangle((0, 0, 191, 13), fill=T.rgb(color) + (255,))
    return {"image": img}


CONTROLS = {11: "even", 12: "app", 14: "undo", 15: "go"}


def takeover_faces(ctx, snap, p, st) -> dict:
    pen = _pen(ctx)
    good, _ = _good_warn(pen)
    faces = {}
    km = keymap(p)
    for m in snap["monitors"]:
        if m["id"] in km:
            faces[km[m["id"]]] = draw_monitor_key(pen, m, p)
    faces[11] = _mode_key(pen, "mdi:view-column", "Even", st["mode"] == "even", good)
    faces[12] = _mode_key(pen, "mdi:arrow-split-vertical", "By app", st["mode"] == "app", good)
    has_undo = (_state_dir(ctx.config_dir) / "layout-last.json").exists()
    faces[14] = {"icon": "mdi:undo", "label": "Undo", **({} if has_undo else {"fg": "#5a5a66", "label_color": "#5a5a66"})}
    n = sum(1 for mv in p["moves"] if not mv["noop"])
    faces[15] = ({"icon": "mdi:check-bold", "label": "Go", "bg": "#1d6b2a"} if n else
                 {"icon": "mdi:check", "label": "Done", "fg": "#8a8a96"})
    return faces


# ============================================================================== plugin entry points
def face(ctx, **_):
    """Live face: the monitor map."""
    try:
        st = _prefs(ctx)
        snap = snapshot()
        p = plan(snap, st["mode"], st["skip"], include_focus=False)
        return draw_map(_pen(ctx), snap, p)
    except Exception as e:
        ctx.log(f"layout face: {e}")
        return {"icon": "mdi:monitor-multiple", "label": "layout?", "bg": "#333344"}


def spread(ctx, **_):
    """Tap: spread with the saved mode; the focused window on the main monitor stays."""
    r = _run(ctx, include_focus=False)
    ctx.flash({"glyph": str(r["moved"]) if r["moved"] else "OK", "label": "moved" if r["moved"] else ""}, 1.2)
    ctx.daemon.live_next.clear()


def open(ctx, **_):
    """Hold: the takeover (see the module docstring)."""
    st = _prefs(ctx)
    box = {"snap": snapshot()}

    def build():
        box["plan"] = plan(box["snap"], st["mode"], st["skip"], include_focus=True)
        return takeover_faces(ctx, box["snap"], box["plan"], st)

    def on_key(c, key, down, t):
        if not down:
            return
        km = {v: k for k, v in keymap(box["plan"]).items()}
        act = CONTROLS.get(key)
        if act in MODES:
            st["mode"] = act
            _save_prefs(ctx, st)
        elif key in km:
            m = next(m for m in box["snap"]["monitors"] if m["id"] == km[key])
            if m["id"] == box["plan"]["main"]:
                return
            st["skip"] = [s for s in st["skip"] if s != m["name"]] if m["name"] in st["skip"] else st["skip"] + [m["name"]]
            _save_prefs(ctx, st)
        elif act == "undo":
            n = 0 if _dry(ctx) else undo(ctx.config_dir, ctx.log)
            ctx.end_modal()
            ctx.flash({"glyph": str(n), "label": "undone"}, 1.2)
            ctx.daemon.live_next.clear()
            return
        elif act == "go":
            r = _run(ctx, include_focus=True)
            ctx.end_modal()
            ctx.flash({"glyph": str(r["moved"]) if r["moved"] else "OK", "label": "moved" if r["moved"] else ""}, 1.2)
            ctx.daemon.live_next.clear()
            return
        else:
            return
        box["snap"] = snapshot()
        c.modal_update(box["tok"], build(), MODAL_S)

    box["tok"] = ctx.modal(build(), on_key=on_key, timeout=MODAL_S)


# ============================================================================== CLI (read-only)
if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["snapshot"] and len(a) > 1:
        Path(a[1]).write_text(json.dumps(snapshot(), indent=1), encoding="utf-8")
        print("saved", a[1])
    elif a[:1] == ["plan"]:
        mode = a[a.index("--mode") + 1] if "--mode" in a else "even"
        if "--snapshot" in a:
            os.environ["M18_LAYOUT_SNAPSHOT"] = a[a.index("--snapshot") + 1]
        s = snapshot()
        print(describe(plan(s, mode), s))
    else:
        print(__doc__)
