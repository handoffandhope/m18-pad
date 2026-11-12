#!/usr/bin/env python3
"""Real-keystroke check of m18lib/sendtext.py against a throwaway stand-in composer (never a chat app).

Opens two small "M18 stand-in" windows (WinForms text boxes: Enter appends the first box's text to a log
and clears it), then drives sendtext.send() for real: focus verify, paste, Enter, clipboard restore, and
each abort guard. Keep hands off the keyboard and mouse while it runs (about 30 s): input is one of the
guards, so touching anything makes the next case fail. Closes its windows and temp folder at the end.

    python send_check.py
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes as W
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from m18lib import keys as K          # noqa: E402
from m18lib import sendtext as S      # noqa: E402
from m18lib import winclip as WC      # noqa: E402

RESULTS: list[tuple[bool, str]] = []
STANDIN = r'''param([string]$Log, [string]$Title)
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = $Title; $f.Width = 420; $f.Height = 160; $f.StartPosition = "CenterScreen"
$c = New-Object System.Windows.Forms.TextBox
$c.Name = "composer"; $c.Left = 10; $c.Top = 10; $c.Width = 380
$o = New-Object System.Windows.Forms.TextBox
$o.Name = "other"; $o.Left = 10; $o.Top = 50; $o.Width = 380
$c.Add_KeyDown({ param($s, $e)
    if ($e.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
        Add-Content -Path $Log -Value ("SENT:" + $c.Text) -Encoding utf8; $c.Text = ""; $e.SuppressKeyPress = $true } })
$f.Controls.Add($c); $f.Controls.Add($o)
$f.Add_Shown({ $f.Activate(); $c.Focus() })
[void]$f.ShowDialog()
'''
u = ctypes.WinDLL("user32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
u.GetForegroundWindow.restype = W.HWND
u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
u.GetWindowThreadProcessId.restype = W.DWORD
u.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
u.FindWindowW.restype = W.HWND
u.RegisterClipboardFormatW.argtypes = [W.LPCWSTR]


def check(ok, name, detail=""):
    RESULTS.append((bool(ok), name))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


class Ctx:
    def __init__(self, source="device"):
        self.source = source


def front(hwnd):
    """Bring a window forward without generating input (AttachThreadInput to the current foreground thread)."""
    fg = u.GetForegroundWindow()
    a = u.GetWindowThreadProcessId(fg, None) if fg else 0
    b = k.GetCurrentThreadId()
    if a and a != b:
        u.AttachThreadInput(a, b, True)
    u.ShowWindow(hwnd, 9)
    u.BringWindowToTop(hwnd)
    u.SetForegroundWindow(hwnd)
    if a and a != b:
        u.AttachThreadInput(a, b, False)
    time.sleep(0.25)
    return u.GetForegroundWindow() == hwnd


def edits(hwnd):
    uia, U = S._uia()
    root = uia.ElementFromHandle(hwnd)
    arr = root.FindAll(4, uia.CreatePropertyCondition(30003, S.EDIT))
    out = {}
    for i in range(arr.Length):
        e = arr.GetElement(i)
        out[e.CurrentAutomationId or str(i)] = e
    return out, U


def set_value(hwnd, name, text):
    es, U = edits(hwnd)
    es[name].GetCurrentPattern(10002).QueryInterface(U.IUIAutomationValuePattern).SetValue(text)


def get_value(hwnd, name):
    es, U = edits(hwnd)
    return es[name].GetCurrentPattern(10002).QueryInterface(U.IUIAutomationValuePattern).CurrentValue


def focus(hwnd, name):
    es, _ = edits(hwnd)
    es[name].SetFocus()


TESTFMT = "M18SendCheckFormat"


def clip_set_original():
    with WC._Open():
        u.EmptyClipboard()
        WC._put(13, ("M18-ORIGINAL-CLIP\0").encode("utf-16-le"))
        WC._put(u.RegisterClipboardFormatW(TESTFMT), b"\x01\x02\x03payload")


def clip_is_original() -> bool:
    saved = S.clip_save() or []
    d = dict(saved)
    t = d.get(13, b"").decode("utf-16-le", "replace").split("\0")[0]
    return t == "M18-ORIGINAL-CLIP" and d.get(u.RegisterClipboardFormatW(TESTFMT), b"")[:10] == b"\x01\x02\x03payload"


def wait_idle(s=1.7):
    end = time.time() + 20
    while S.idle_ms() < s * 1000 and time.time() < end:
        time.sleep(0.1)


def main() -> int:
    if sys.platform != "win32":
        print("Windows only")
        return 1
    K.DRY = False
    tmp = Path(tempfile.mkdtemp(prefix="m18send_"))
    log = tmp / "sent.log"
    log.write_text("", encoding="utf-8")
    (tmp / "standin.ps1").write_text(STANDIN, encoding="utf-8")
    procs = []
    app = {"name": "Stand-in", "exe": "powershell.exe", "path_has": ""}
    try:
        for title in ("M18 stand-in", "M18 stand-in 2"):
            procs.append(subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                           str(tmp / "standin.ps1"), "-Log", str(log), "-Title", title]))
        h1 = h2 = None
        for _ in range(100):
            h1, h2 = u.FindWindowW(None, "M18 stand-in"), u.FindWindowW(None, "M18 stand-in 2")
            if h1 and h2:
                break
            time.sleep(0.1)
        check(bool(h1 and h2), "stand-in windows open")
        if not (h1 and h2):
            return 1
        time.sleep(0.8)
        ver = (lambda h, f: None if h == h1 else "wrong window")

        def sent_lines():
            return [x for x in log.read_text(encoding="utf-8-sig").splitlines() if x]

        def case(name, *, prep=None, src="device", expect_sent, why_has="", text="hello from the pad", **kw):
            front(h1)
            focus(h1, "composer")
            set_value(h1, "composer", "")
            clip_set_original()
            if prep:
                prep()
            else:
                wait_idle()
            n0 = len(sent_lines())
            t0 = time.time()
            r = S.send(Ctx(src), text, app=app, keys=K, verify=kw.pop("verify", ver), timeout=kw.pop("timeout", 3.0), **kw)
            dt = time.time() - t0
            lines = sent_lines()[n0:]
            okc = clip_is_original()
            good = (r["sent"] == expect_sent and why_has in r["why"] and okc
                    and (lines == [f"SENT:{text}"] if expect_sent else lines == []))
            check(good, f"{name}  ({r['why']}; {dt:.2f} s)", f"{r} lines={lines} clipboard_restored={okc}")
            return r

        case("happy path: verify, paste, Enter, clipboard restored (text + a custom format)", expect_sent=True)
        case("refused: source is not the device", src="api", expect_sent=False, why_has="not a hardware press")

        def recent():
            wait_idle()
            K.send_combo("f24")                       # harmless key; counts as input for GetLastInputInfo
        case("abort: input in the last 1.5 s", prep=recent, expect_sent=False, why_has="ms ago")
        case("abort: input during the send (before paste)", expect_sent=False, why_has="input during",
             probe=lambda w: K.send_combo("f24") if w == "before paste" else None)
        case("abort: focus moved to another box (before paste)", expect_sent=False, why_has="focus moved",
             probe=lambda w: focus(h1, "other") if w == "before paste" else None)
        case("abort: foreground changed (before enter)", expect_sent=False, why_has="foreground window changed",
             probe=lambda w: front(h2) if w == "before enter" else None)
        left = get_value(h1, "composer")
        check(left == "hello from the pad", "abort after the paste leaves the text unsent in the composer", repr(left))
        front(h1); clip_set_original(); wait_idle()
        r = S.send(Ctx(), "x", app={"name": "Notepad", "exe": "notepad.exe", "path_has": ""}, keys=K, timeout=1.5)
        check(not r["sent"] and "not notepad.exe" in r["why"] and clip_is_original(), f"abort: wrong app in front ({r['why']})", str(r))

        def draft():
            set_value(h1, "composer", "my draft")
            wait_idle()
        case("abort: a draft in the composer is never touched", prep=draft, expect_sent=False, why_has="already holds text")
        check(get_value(h1, "composer") == "my draft", "the draft is still there", get_value(h1, "composer"))
        case("abort: target check fails", expect_sent=False, why_has="wrong window", verify=lambda h, f: "wrong window")

        def prefill():
            set_value(h1, "composer", "prefilled line")
            wait_idle()
        case("prefilled mode (Codex route): text already in the composer, only Enter", prep=prefill,
             expect_sent=True, text="prefilled line", mode="prefilled")
    finally:
        for p in procs:
            try:
                p.kill()
            except Exception:                                # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for ok, n in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
