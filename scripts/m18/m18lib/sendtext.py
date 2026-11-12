"""Send a line of text into a chat app's composer for a physical pad press, with guards that abort
instead of typing blind. Used by sessions.nudge and claude_limit.resume (`args: {send: true}`).

    r = sendtext.send(ctx, "still going?", app=sendtext.CLAUDE, open_fn=lambda: os.startfile(link))
    r -> {"sent": bool, "why": str, "steps": [...]}

The sequence, each step checked before the next:
 1. refuse unless ctx.source == "device" (no API caller, and no tap sequence that included one, can type);
 2. refuse if the keyboard or mouse was used in the last `idle_s` (GetLastInputInfo; Windows does not
    split keyboard from mouse);
 3. open_fn() (the app's own link), then wait up to `timeout` s for the foreground window to belong to
    the app's exe in its install folder, and for its focused element to be an editable composer;
 4. the composer must be empty (a draft of the user's is never touched);
 5. save the whole clipboard (every format it holds), put the text there marked "not for clipboard
    history", Ctrl+V, wait until the composer reads exactly the text, restore the clipboard;
 6. Enter, then check the composer emptied (the message left).
Between steps: the foreground window and the focused element must stay the same, and no input but ours
may arrive (the GetLastInputInfo tick must equal the one after our own last key). Any failure: abort; if
the text was already pasted it is left in the composer unsent, and the clipboard is restored.

Modes: "paste" (above; the Claude app, plus `verify` = the window's document name must name the session and
`focus_fn` = UIA SetFocus on the composer when the link left focus elsewhere) and "prefilled" (Codex: the link
already put the text in the composer, so steps 4-5 become "the composer holds exactly the text" and only Enter
is sent). `to_session()` picks the mode per agent; a CLI session is refused (a terminal cannot be verified).
`probe(where)` is a test hook called at each checkpoint; `dry` stops before anything is typed.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

CLAUDE = {"name": "Claude", "exe": "claude.exe", "path_has": "\\windowsapps\\claude_"}
CODEX = {"name": "Codex", "exe": "codex.exe", "path_has": "\\windowsapps\\openai.codex_"}
NOTEPAD = {"name": "Notepad", "exe": "notepad.exe", "path_has": ""}      # the stand-in used by the checks

_LOCK = threading.Lock()          # one send at a time, daemon-wide

if sys.platform == "win32":
    from ctypes import wintypes as W
    _u = ctypes.WinDLL("user32", use_last_error=True)
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _u.GetForegroundWindow.restype = W.HWND
    _u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    _u.EnumClipboardFormats.argtypes = [W.UINT]
    _u.EnumClipboardFormats.restype = W.UINT
    _u.GetClipboardData.argtypes = [W.UINT]
    _u.GetClipboardData.restype = W.HANDLE
    _u.EmptyClipboard.restype = W.BOOL
    _k.GetTickCount.restype = W.DWORD
    _k.OpenProcess.restype = W.HANDLE

    class _LII(ctypes.Structure):
        _fields_ = [("cbSize", W.UINT), ("dwTime", W.DWORD)]


# ------------------------------------------------------------------ input and windows
def last_input_tick() -> int:
    li = _LII(cbSize=ctypes.sizeof(_LII))
    _u.GetLastInputInfo(ctypes.byref(li))
    return int(li.dwTime)


def idle_ms() -> int:
    return (int(_k.GetTickCount()) - last_input_tick()) & 0xFFFFFFFF


def _exe_of(pid: int) -> str:
    h = _k.OpenProcess(0x1000, False, pid)                  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = W.DWORD(1024)
        return buf.value if _k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        _k.CloseHandle(h)


def foreground() -> tuple[int, int, str]:
    """(hwnd, pid, full exe path) of the foreground window, or (0, 0, "")."""
    hwnd = _u.GetForegroundWindow() or 0
    if not hwnd:
        return 0, 0, ""
    pid = W.DWORD()
    _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(hwnd), int(pid.value), _exe_of(pid.value)


def is_app(path: str, app: dict) -> bool:
    p = (path or "").lower()
    return os.path.basename(p) == app["exe"] and app["path_has"] in p


# ------------------------------------------------------------------ UI Automation (focused element)
_tls = threading.local()


def _uia():
    if getattr(_tls, "uia", None) is None:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        _tls.U = U
        _tls.uia = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
    return _tls.uia, _tls.U


EDIT, DOCUMENT, GROUP = 50004, 50030, 50026        # UIA control type ids


def focused() -> dict | None:
    """The focused UI element: pid, control type, class, name, runtime id, editable, and its text (or None)."""
    try:
        uia, U = _uia()
        el = uia.GetFocusedElement()
        if not el:
            return None
        info = {"pid": int(el.CurrentProcessId), "type": int(el.CurrentControlType),
                "cls": str(el.CurrentClassName or ""), "name": str(el.CurrentName or ""),
                "rid": tuple(el.GetRuntimeId() or ()), "text": None, "editable": False}
        try:
            vp = el.GetCurrentPattern(10002)                 # ValuePattern
            if vp:
                vp = vp.QueryInterface(U.IUIAutomationValuePattern)
                info["text"] = str(vp.CurrentValue or "")
                info["editable"] = not bool(vp.CurrentIsReadOnly)
        except Exception:                                    # noqa: BLE001
            pass
        if info["text"] is None:
            try:
                tp = el.GetCurrentPattern(10014)             # TextPattern
                if tp:
                    tp = tp.QueryInterface(U.IUIAutomationTextPattern)
                    info["text"] = str(tp.DocumentRange.GetText(-1) or "")
            except Exception:                                # noqa: BLE001
                pass
        return info
    except Exception:                                        # noqa: BLE001
        return None


def _norm(s) -> str:
    return " ".join(str(s or "").replace("​", "").split())


# ------------------------------------------------------------------ clipboard save / restore
_GDI = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}        # handles that are not global memory: skipped
CLIP_MAX = 64 * 1024 * 1024


def clip_save() -> list[tuple[int, bytes]] | None:
    """Every global-memory format on the clipboard, as raw bytes (never parsed or logged). None = could not."""
    from . import winclip as WC
    out, total = [], 0
    try:
        with WC._Open():
            fmt = 0
            while True:
                fmt = _u.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                if fmt in _GDI or 0x300 <= fmt <= 0x3FF:
                    continue
                h = _u.GetClipboardData(fmt)
                if not h:
                    continue
                b = WC._bytes(h)
                total += len(b)
                if total > CLIP_MAX:
                    return None
                out.append((fmt, b))
    except OSError:
        return None
    return out


def clip_restore(saved: list[tuple[int, bytes]]):
    from . import winclip as WC
    with WC._Open():
        _u.EmptyClipboard()
        for fmt, b in saved:
            try:
                WC._put(fmt, b)
            except OSError:
                pass


def clip_put_private(text: str):
    """The text, marked so Windows clipboard history, cloud clipboard and monitors skip it."""
    from . import winclip as WC
    with WC._Open():
        _u.EmptyClipboard()
        WC._put(13, (text + "\0").encode("utf-16-le"))
        WC._put(WC._fmt("ExcludeClipboardContentFromMonitorProcessing"), b"\0")
        WC._put(WC._fmt("CanIncludeInClipboardHistory"), (0).to_bytes(4, "little"))
        WC._put(WC._fmt("CanUploadToCloudClipboard"), (0).to_bytes(4, "little"))


# ------------------------------------------------------------------ app specifics
def doc_name(el_rid=None) -> str:
    """Name of the web document that holds the focused element (Chromium apps title it after the view:
    the Claude app's is "<session title> - Claude Code"), or ""."""
    try:
        uia, U = _uia()
        w = uia.ControlViewWalker
        e = uia.GetFocusedElement()
        for _ in range(60):
            if e is None:
                break
            if int(e.CurrentControlType) == DOCUMENT and (e.CurrentName or ""):
                return str(e.CurrentName)
            e = w.GetParentElement(e)
    except Exception:                                        # noqa: BLE001
        pass
    return ""


def claude_verify(title):
    """Target check for the Claude app: the shown view must be the session titled `title`."""
    want = _norm(title)

    def verify(hwnd, f):
        got = _norm(doc_name())
        if not want:
            return "no session title to check the shown session against"
        if not (got == want or got.startswith(want + " - ")):
            return f"the app shows {got[:60]!r}, not the session {want[:60]!r}"
        return None
    return verify


def claude_focus(hwnd) -> bool:
    """Put focus in the Claude app's composer (UIA Edit "Prompt", a tiptap/ProseMirror editor). Observed
    2026-11-11 (app 5.1231.2.0): opening a recent session through claude:// already focuses it, so this is
    the fallback; a session with no composer (old ones) has none to focus."""
    try:
        uia, U = _uia()
        root = uia.ElementFromHandle(hwnd)
        arr = root.FindAll(4, uia.CreatePropertyCondition(30003, EDIT))
        for i in range(arr.Length):
            e = arr.GetElement(i)
            if (e.CurrentName or "") == "Prompt" and "ProseMirror" in (e.CurrentClassName or ""):
                e.SetFocus()
                return True
    except Exception:                                        # noqa: BLE001
        pass
    return False


# ------------------------------------------------------------------ the send
class Abort(Exception):
    pass


def send(ctx, text: str, app: dict, open_fn=None, mode: str = "paste", verify=None, focus_fn=None,
         timeout: float = 8.0, idle_s: float = 1.5, settle_s: float = 0.35, keys=None, probe=None,
         dry: bool = False) -> dict:
    """See the module docstring. Never raises; returns {"sent", "why", "steps"}.

    mode "paste": the composer must be empty; the text goes in by clipboard + Ctrl+V (Claude).
    mode "prefilled": open_fn already put the text in the composer (codex://threads/<id>?prompt=); it must
      read exactly the text, then only Enter is pressed (Codex).
    verify(hwnd, focused) -> None or the reason the shown view is the wrong target (claude_verify).
    focus_fn(hwnd): one try at focusing the composer when opening left focus elsewhere (claude_focus).
    keys: m18lib.keys; probe(step): test hook called at each checkpoint (the stand-in check uses it)."""
    steps: list[str] = []
    if keys is None:
        from . import keys
    src = getattr(ctx, "source", None)
    if src != "device":
        return {"sent": False, "why": f"not a hardware press (source={src})", "steps": steps}
    if dry or sys.platform != "win32" or keys.DRY:
        return {"sent": False, "why": "dry run (no real keystrokes)", "steps": steps}
    if not _LOCK.acquire(blocking=False):
        return {"sent": False, "why": "another send is running", "steps": steps}
    saved = None
    want = _norm(text)
    try:
        idle = idle_ms()
        if idle < idle_s * 1000:
            raise Abort(f"keyboard or mouse used {idle} ms ago (needs {int(idle_s * 1000)} ms idle)")
        mark = [last_input_tick()]

        def no_input(where):
            if last_input_tick() != mark[0]:
                raise Abort(f"keyboard or mouse input during the send ({where})")

        def hook(where):
            if probe:
                probe(where)

        if open_fn:
            open_fn()
            steps.append("opened")
        end = time.time() + timeout
        hwnd = el = None
        tried_focus = False
        why = "not in front"
        while time.time() < end:                              # the app in front, its composer focused
            no_input("waiting for the app")
            h, pid, path = foreground()
            f = focused() if h and is_app(path, app) else None
            ok = f and f["pid"] == pid and f["editable"] and f["text"] is not None
            if ok and mode == "prefilled" and _norm(f["text"]) != want:
                ok, why = False, "composer does not hold the prefilled text yet"
            if ok:
                if hwnd == h and el and el["rid"] == f["rid"]:
                    break                                     # same window and element after settle_s: settled
                hwnd, el = h, f
                time.sleep(settle_s)
                continue
            if h and is_app(path, app):
                why = why if f and f["editable"] else "composer not focused"
                if focus_fn and not tried_focus:
                    tried_focus = True
                    if focus_fn(h):
                        steps.append("focused the composer")
            else:
                why = f"front window is {os.path.basename(path) or 'none'}, not {app['exe']}"
            hwnd = el = None
            time.sleep(0.05)
        else:
            raise Abort(f"{app['name']}: {why} within {timeout:.0f} s")
        steps.append(f"composer {el['cls'][:24] or el['type']}")
        if verify:
            bad = verify(hwnd, el)
            if bad:
                raise Abort(bad)
            steps.append("target verified")

        def same(where):
            hook(where)
            no_input(where)
            if foreground()[0] != hwnd:
                raise Abort(f"foreground window changed ({where})")
            f = focused()
            if not f or f["rid"] != el["rid"]:
                raise Abort(f"focus moved off the composer ({where})")
            return f

        if mode == "paste":
            if _norm(same("before paste")["text"]):
                raise Abort("the composer already holds text (a draft is never touched)")
            saved = clip_save()
            if saved is None:
                raise Abort("could not save the clipboard")
            clip_put_private(text)
            steps.append("clipboard saved")
            same("before paste")
            keys.send_combo("ctrl+v")
            mark[0] = last_input_tick()
            steps.append("pasted")
            got, end = None, time.time() + 2.0
            while time.time() < end:
                f = same("after paste")
                got = _norm(f["text"])
                if got == want:
                    break
                time.sleep(0.05)
            clip_restore(saved)                               # only now: the app has read the clipboard
            saved = None
            steps.append("clipboard restored")
            if got != want:
                raise Abort(f"composer reads {len(got or '')} chars after the paste, not the message; not sent")
        same("before enter")
        keys.send_combo("enter")
        mark[0] = last_input_tick()
        steps.append("enter")
        f, end = None, time.time() + 2.0
        while time.time() < end:
            f = focused()
            if f and f["pid"] == foreground()[1] and not _norm(f["text"]):
                steps.append("composer empty")
                return {"sent": True, "why": "sent", "steps": steps}
            time.sleep(0.05)
        if f and f["rid"] == el["rid"] and _norm(f["text"]) == want:
            return {"sent": False, "why": "Enter did not send (the message is still in the composer)", "steps": steps}
        # Not confirmed, so not reported as sent: the caller shows the fallback and the user looks.
        return {"sent": False, "unconfirmed": True,
                "why": "Enter pressed; the composer did not read empty within 2 s (unconfirmed)", "steps": steps}
    except Abort as e:
        return {"sent": False, "why": str(e), "steps": steps}
    except Exception as e:                                   # noqa: BLE001
        return {"sent": False, "why": f"{type(e).__name__}: {e}", "steps": steps}
    finally:
        if saved is not None:
            try:
                clip_restore(saved)
                steps.append("clipboard restored")
            except Exception:                                # noqa: BLE001
                pass
        _LOCK.release()


CODEX_SEND = False

# ------------------------------------------------------------------ one call for the session plugins
def to_session(ctx, text: str, kind: str | None, link: str | None, title: str | None, opener,
               dry: bool = False) -> dict:
    """Send `text` into an agent session: Claude app (claude:// link; paste into the composer after checking
    the shown session's title) or Codex app (codex://threads/<id>?prompt=<text> prefills; Enter after the
    composer reads exactly the text). opener(url) opens a link. CLI sessions are not typed into."""
    from urllib.parse import quote
    if kind == "claude" and link and link.startswith("claude://"):
        return send(ctx, text, CLAUDE, open_fn=lambda: opener(link), mode="paste",
                    verify=claude_verify(title), focus_fn=claude_focus, timeout=8.0, dry=dry)
    if kind == "codex" and link and link.startswith("codex://"):
        url = link + ("&" if "?" in link else "?") + "prompt=" + quote(text, safe="")
        if not CODEX_SEND:
            # The Codex path has never run against the real app, and nothing here checks WHICH thread is
            # showing: if the link failed to switch threads, Enter could send into another one. Until a
            # thread check exists and one supervised test has passed, only prefill and let the user press Enter.
            try:
                opener(url)
            except Exception:                                # noqa: BLE001
                pass
            return {"sent": False, "why": "Codex: prefilled only (send not verified yet)", "steps": ["opened"]}
        return send(ctx, text, CODEX, open_fn=lambda: opener(url), mode="prefilled", timeout=12.0, dry=dry)
    return {"sent": False, "why": "no app link (a CLI session); a terminal is not typed into", "steps": []}


def flash_sent(ctx):
    try:
        ctx.flash({"icon": "mdi:send", "label": "SENT", "bg": "#1d6b2a"}, 1.5)
    except Exception:                                        # noqa: BLE001
        pass


# ------------------------------------------------------------------ the fallback face
def flash_fallback(ctx, why: str):
    """Clipboard-only fallback: one short word per key (the pad's keys are separate screens)."""
    word = "PASTE"
    if "hardware" in why:
        word = "NO SEND"
    try:
        ctx.flash({"icon": "mdi:content-paste", "label": word, "bg": "#7a5a10", "border": "#ffd400"}, 2.5)
    except Exception:                                        # noqa: BLE001
        pass
