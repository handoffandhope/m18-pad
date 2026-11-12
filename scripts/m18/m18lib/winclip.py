"""Windows clipboard through ctypes (no pywin32 needed): read with the privacy formats honoured, write
text / image / file lists. Every open retries briefly because other apps hold the clipboard for ms.

Privacy: a clip is `hidden` (its content is never read) when the owner set
ExcludeClipboardContentFromMonitorProcessing, "Clipboard Viewer Ignore", or CanIncludeInClipboardHistory
with a DWORD 0. Password managers (KeePass, 1Password, Bitwarden) set these.
"""
from __future__ import annotations

import ctypes
import io
import sys
import time

CF_DIB, CF_UNICODETEXT, CF_HDROP = 8, 13, 15
GMEM_MOVEABLE = 0x0002

if sys.platform == "win32":
    from ctypes import wintypes as W
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sh32 = ctypes.WinDLL("shell32", use_last_error=True)
    u32.GetClipboardSequenceNumber.restype = W.DWORD
    u32.OpenClipboard.argtypes = [W.HWND]
    u32.OpenClipboard.restype = W.BOOL
    u32.CloseClipboard.restype = W.BOOL
    u32.EmptyClipboard.restype = W.BOOL
    u32.GetClipboardData.argtypes = [W.UINT]
    u32.GetClipboardData.restype = W.HANDLE
    u32.SetClipboardData.argtypes = [W.UINT, W.HANDLE]
    u32.SetClipboardData.restype = W.HANDLE
    u32.IsClipboardFormatAvailable.argtypes = [W.UINT]
    u32.IsClipboardFormatAvailable.restype = W.BOOL
    u32.RegisterClipboardFormatW.argtypes = [W.LPCWSTR]
    u32.RegisterClipboardFormatW.restype = W.UINT
    k32.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = W.HGLOBAL
    k32.GlobalLock.argtypes = [W.HGLOBAL]
    k32.GlobalLock.restype = W.LPVOID
    k32.GlobalUnlock.argtypes = [W.HGLOBAL]
    k32.GlobalUnlock.restype = W.BOOL
    k32.GlobalSize.argtypes = [W.HGLOBAL]
    k32.GlobalSize.restype = ctypes.c_size_t
    k32.GlobalFree.argtypes = [W.HGLOBAL]
    k32.GlobalFree.restype = W.HGLOBAL
    sh32.DragQueryFileW.argtypes = [W.HANDLE, W.UINT, W.LPWSTR, W.UINT]
    sh32.DragQueryFileW.restype = W.UINT


def available() -> bool:
    return sys.platform == "win32"


def seq() -> int:
    """Changes on every clipboard change; costs one call, no open."""
    return int(u32.GetClipboardSequenceNumber()) if available() else 0


def _fmt(name):
    return u32.RegisterClipboardFormatW(name)


class _Open:
    def __enter__(self):
        for _ in range(25):
            if u32.OpenClipboard(None):
                return self
            time.sleep(0.02)
        raise OSError("clipboard is busy")

    def __exit__(self, *a):
        u32.CloseClipboard()


def _bytes(h) -> bytes:
    p = k32.GlobalLock(h)
    if not p:
        return b""
    try:
        return ctypes.string_at(p, k32.GlobalSize(h))
    finally:
        k32.GlobalUnlock(h)


MAX_TEXT = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000


def read() -> dict:
    """{"hidden": True} or {"text"?: str, "image"?: PIL.Image, "files"?: [paths]} or {} when empty."""
    if not available():
        return {}
    ex, ign, can = (_fmt("ExcludeClipboardContentFromMonitorProcessing"), _fmt("Clipboard Viewer Ignore"),
                    _fmt("CanIncludeInClipboardHistory"))
    if u32.IsClipboardFormatAvailable(ex) or u32.IsClipboardFormatAvailable(ign):
        return {"hidden": True}
    png = _fmt("PNG")
    with _Open():
        if u32.IsClipboardFormatAvailable(can):
            h = u32.GetClipboardData(can)
            b = _bytes(h) if h else b""
            if len(b) >= 4 and int.from_bytes(b[:4], "little") == 0:
                return {"hidden": True}
        out: dict = {}
        if u32.IsClipboardFormatAvailable(CF_HDROP):
            h = u32.GetClipboardData(CF_HDROP)
            if h:
                n = sh32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
                files = []
                for i in range(min(n, 500)):
                    ln = sh32.DragQueryFileW(h, i, None, 0)
                    buf = ctypes.create_unicode_buffer(ln + 1)
                    sh32.DragQueryFileW(h, i, buf, ln + 1)
                    files.append(buf.value)
                out["files"] = files
                out["file_count"] = n
        if "files" not in out:
            from PIL import Image
            raw, kind = None, None
            if png and u32.IsClipboardFormatAvailable(png):
                h = u32.GetClipboardData(png)
                raw, kind = (_bytes(h) if h else None), "png"
            if raw is None and u32.IsClipboardFormatAvailable(CF_DIB):
                h = u32.GetClipboardData(CF_DIB)
                raw, kind = (_bytes(h) if h else None), "dib"
            if raw:
                try:
                    if kind == "dib":
                        hdr = int.from_bytes(raw[0:4], "little")
                        bits = int.from_bytes(raw[14:16], "little")
                        ncol = int.from_bytes(raw[32:36], "little") if hdr >= 36 else 0
                        comp = int.from_bytes(raw[16:20], "little")
                        masks = 12 if (comp == 3 and hdr == 40) else 0
                        pal = (ncol or (1 << bits if bits <= 8 else 0)) * 4
                        off = 14 + hdr + masks + pal
                        raw = b"BM" + (len(raw) + 14).to_bytes(4, "little") + b"\0\0\0\0" + off.to_bytes(4, "little") + raw
                    im = Image.open(io.BytesIO(raw))
                    if im.width * im.height <= MAX_PIXELS:
                        im.load()
                        out["image"] = im
                except Exception:                            # noqa: BLE001
                    pass
            if u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                h = u32.GetClipboardData(CF_UNICODETEXT)
                p = k32.GlobalLock(h) if h else None
                if p:
                    try:
                        n = k32.GlobalSize(h) // 2
                        out["text"] = ctypes.wstring_at(p, min(n, MAX_TEXT)).split("\0", 1)[0]
                    finally:
                        k32.GlobalUnlock(h)
        return out


def _put(fmt, data: bytes):
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    if not u32.SetClipboardData(fmt, h):
        k32.GlobalFree(h)
        raise OSError("SetClipboardData failed")


def set_text(s: str):
    with _Open():
        u32.EmptyClipboard()
        _put(CF_UNICODETEXT, (s + "\0").encode("utf-16-le"))


def set_image(im):
    b = io.BytesIO()
    im.convert("RGB").save(b, "BMP")
    pb = io.BytesIO()
    im.save(pb, "PNG")
    with _Open():
        u32.EmptyClipboard()
        _put(CF_DIB, b.getvalue()[14:])
        _put(_fmt("PNG"), pb.getvalue())


def set_files(paths: list[str]):
    """CF_HDROP: a DROPFILES header (pFiles=20, wide) and a double-NUL-terminated UTF-16 list."""
    body = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    hdr = (20).to_bytes(4, "little") + b"\0" * 8 + b"\0\0\0\0" + (1).to_bytes(4, "little")
    with _Open():
        u32.EmptyClipboard()
        _put(CF_HDROP, hdr + body)
