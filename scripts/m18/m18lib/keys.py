"""Key-combo parsing and Windows keystroke injection (SendInput via ctypes, no deps).

On non-Windows hosts (or with M18_DRYRUN=1) injection is logged instead of sent,
which is what makes the whole daemon testable without the pad or Windows.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

VK = {
    # modifiers
    "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "alt": 0x12, "lalt": 0xA4, "ralt": 0xA5, "altgr": 0xA5,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "cmd": 0x5B, "super": 0x5B,
    # editing / nav
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22,
    "pgdn": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91, "printscreen": 0x2C,
    "prtsc": 0x2C, "pause": 0x13, "apps": 0x5D, "menu": 0x5D,
    # media / browser
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2,
    "media_play_pause": 0xB3, "browser_back": 0xA6, "browser_forward": 0xA7,
    "browser_refresh": 0xA8, "browser_home": 0xAC, "launch_mail": 0xB4,
    "launch_calc": 0xB7,
    # punctuation (US layout VK_OEM_*)
    ";": 0xBA, "semicolon": 0xBA, "=": 0xBB, "equals": 0xBB, "plus": 0xBB,
    ",": 0xBC, "comma": 0xBC, "-": 0xBD, "minus": 0xBD, ".": 0xBE, "period": 0xBE,
    "/": 0xBF, "slash": 0xBF, "`": 0xC0, "backtick": 0xC0, "grave": 0xC0,
    "[": 0xDB, "\\": 0xDC, "backslash": 0xDC, "]": 0xDD, "'": 0xDE, "quote": 0xDE,
    # numpad
    "num*": 0x6A, "num+": 0x6B, "num-": 0x6D, "num.": 0x6E, "num/": 0x6F,
}
for i in range(1, 25):
    VK[f"f{i}"] = 0x6F + i            # F1=0x70 .. F24=0x87
for i in range(10):
    VK[str(i)] = 0x30 + i
    VK[f"num{i}"] = 0x60 + i
for c in "abcdefghijklmnopqrstuvwxyz":
    VK[c] = ord(c.upper())

MODIFIERS = {0x11, 0xA2, 0xA3, 0x10, 0xA0, 0xA1, 0x12, 0xA4, 0xA5, 0x5B, 0x5C}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
            0x5B, 0x5C, 0x5D, 0xA3, 0xA5, 0x6F, 0x90, 0x2C} | set(range(0xA6, 0xB8))

MEDIA = {"play_pause": "media_play_pause", "next": "media_next",
         "prev": "media_prev", "stop": "media_stop", "vol_up": "volume_up",
         "vol_down": "volume_down", "mute": "volume_mute"}


def parse_combo(combo: str) -> list[int]:
    """'ctrl+shift+m' -> [VK_CTRL, VK_SHIFT, 'M']. Raises ValueError with a hint."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+")]
    if combo.strip().endswith("++"):          # 'ctrl++' means ctrl and plus
        parts = parts[:-2] + ["plus"]
    if not parts or any(p == "" for p in parts):
        raise ValueError(f"bad combo {combo!r}")
    out = []
    for p in parts:
        if p not in VK:
            raise ValueError(f"unknown key {p!r} in {combo!r}; see references/config.md#key-names")
        out.append(VK[p])
    return out


DRY = sys.platform != "win32" or os.environ.get("M18_DRYRUN") == "1"
LOG: list[str] = []          # dry-run transcript, read by tests / `m18ctl press`


def _log(msg: str):
    LOG.append(msg)
    del LOG[:-200]


if not DRY:
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    KEYUP, UNICODE, EXT = 0x0002, 0x0004, 0x0001

    def _send(*inputs):
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    def _vk(vk, up=False):
        flags = (KEYUP if up else 0) | (EXT if vk in EXTENDED else 0)
        return INPUT(type=1, u=_U(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags,
                                               time=0, dwExtraInfo=0)))

    def _uni(ch, up=False):
        return INPUT(type=1, u=_U(ki=KEYBDINPUT(wVk=0, wScan=ord(ch),
                                               dwFlags=UNICODE | (KEYUP if up else 0),
                                               time=0, dwExtraInfo=0)))


def send_combo(combo: str, hold_ms: int = 0):
    vks = parse_combo(combo)
    if DRY:
        _log(f"hotkey {combo}" + (f" hold={hold_ms}" if hold_ms else ""))
        return
    for vk in vks:
        _send(_vk(vk))
        time.sleep(0.008)
    if hold_ms:
        time.sleep(hold_ms / 1000)
    for vk in reversed(vks):
        _send(_vk(vk, up=True))
        time.sleep(0.004)


def type_text(text: str, cps: int = 400):
    if DRY:
        _log(f"type {text!r}")
        return
    delay = 1 / cps
    for ch in text:
        if ch == "\n":
            _send(_vk(0x0D), _vk(0x0D, True))
        else:
            # characters outside the BMP need a surrogate pair
            units = ch.encode("utf-16-le")
            for i in range(0, len(units), 2):
                c = chr(int.from_bytes(units[i:i + 2], "little"))
                _send(_uni(c), _uni(c, True))
        time.sleep(delay)


def foreground_exe() -> str | None:
    """Lowercased exe name of the focused window (Windows only)."""
    if DRY:
        return os.environ.get("M18_FAKE_FOREGROUND")
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        k32.CloseHandle(h)
    return None
