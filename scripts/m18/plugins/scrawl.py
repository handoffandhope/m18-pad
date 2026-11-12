"""Scrawl (screen + voice + ink capture) from a pad key.

    5: {action: {plugin: scrawl.record}}

`scrawl.record` presses Scrawl's own record shortcut (Alt+Shift+S: the same shortcut starts and stops a capture).
Scrawl lives in the notification area and only listens while it runs, so when it is not running the key starts
it first, waits for it to come up, then presses the shortcut. Args: `exe` (path to scrawl.exe), `hotkey`.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from m18lib import keys as K

EXE = os.environ.get("M18_SCRAWL_EXE", str(Path.home() / "Downloads/Scrawl-latest-windows-x64/Scrawl/scrawl.exe"))
HOTKEY = "alt+shift+s"
BOOT_S = 2.5                 # from process start to the shortcut being registered (by eye; not documented)


def _running(name: str):
    """True / False, or None when the process list cannot be read."""
    try:
        import psutil
        return any((p.info.get("name") or "").lower() == name for p in psutil.process_iter(["name"]))
    except Exception:                                              # noqa: BLE001
        return None


def record(ctx, exe: str = EXE, hotkey: str = HOTKEY, **_):
    p = Path(exe)
    up = _running(p.name.lower())
    if up is None:                                                 # never press the shortcut into another app blind
        ctx.log("scrawl: cannot read the process list; nothing pressed")
        return
    if not up:
        if not p.exists():
            ctx.log(f"scrawl: {p} not found")
            return
        subprocess.Popen([str(p)], cwd=str(p.parent), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        t0 = time.time()
        while time.time() - t0 < 8 and not _running(p.name.lower()):
            time.sleep(0.2)
        time.sleep(BOOT_S)
        if not _running(p.name.lower()):
            ctx.log("scrawl: the app did not start; nothing pressed")
            return
        ctx.log("scrawl: started the app")
    K.send_combo(hotkey, 0)
