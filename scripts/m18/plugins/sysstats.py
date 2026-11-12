"""Live system-stats tile: CPU / RAM / GPU bars on one key.

Use from macros.yaml:
    live: {plugin: sysstats.tile, interval: 1.5}
    action: {hotkey: ctrl+shift+esc}          # tap opens Task Manager

GPU: NVIDIA via NVML (pip install nvidia-ml-py) if present, else `nvidia-smi`.
If neither works the GPU row shows '--' rather than erroring.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

try:
    import psutil
except ImportError:          # tile still renders, telling you what's missing
    psutil = None

_nvml = None


def _gpu() -> tuple[float | None, float | None]:
    """(util %, vram %) or (None, None)."""
    global _nvml
    try:
        if _nvml is None:
            import pynvml
            pynvml.nvmlInit()
            _nvml = pynvml.nvmlDeviceGetHandleByIndex(0), pynvml
        h, nv = _nvml
        u = nv.nvmlDeviceGetUtilizationRates(h).gpu
        m = nv.nvmlDeviceGetMemoryInfo(h)
        return float(u), 100.0 * m.used / m.total
    except Exception:
        _nvml = False if _nvml is None else _nvml
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None, None
    try:
        out = subprocess.run([exe, "--query-gpu=utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=2, creationflags=0x08000000 if sys.platform == "win32" else 0)
        u, used, tot = (float(x) for x in out.stdout.strip().splitlines()[0].split(","))
        return u, 100 * used / tot
    except Exception:
        return None, None


def _color(p, pal=None):
    pal = pal or {}
    if p is None:
        return "#555566"
    if p < 60:
        return pal.get("good", "#3ddc84")
    return pal.get("warn", "#ffb020") if p < 85 else pal.get("bad", "#ff4d4d")


def _themed(rows, pal):
    """Theme look: transparent tile on a dark plate, chunky outlined text, a thin bar per row
    in the theme's accent colors (switching to warn/bad when high)."""
    from m18lib.render import font
    S = 192
    ink = pal.get("ink", "#101016")
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, S - 7, S - 7), radius=24, fill=ink + "c8" if len(ink) == 7 else ink)
    accents = [pal.get("teal", "#2ee6d6"), pal.get("pink", "#ff6ec7"), pal.get("yellow", "#ffe66d"),
               pal.get("peach", "#ffb38a")]
    fpath = pal.get("font")
    n = len(rows)
    rh = (S - 24) // n
    for i, (name, v) in enumerate(rows):
        y = 12 + i * rh
        col = accents[i % len(accents)]
        if v is not None and v >= 85:
            col = pal.get("bad", col)
        f_lab = font("bold", int(rh * 0.42), fpath)
        f_val = font("bold", int(rh * 0.56), fpath)
        txt = "--" if v is None else f"{v:.0f}%"
        d.text((20, y + rh * 0.42), name, font=f_lab, fill=col, anchor="lm",
               stroke_width=4, stroke_fill=ink)
        d.text((S - 18, y + rh * 0.42), txt, font=f_val, fill=pal.get("fg", "#ffffff"), anchor="rm",
               stroke_width=5, stroke_fill=ink)
        by = y + rh * 0.82
        d.rounded_rectangle((20, by - 4, S - 20, by + 4), radius=4, fill=pal.get("track", "#33224a"))
        if v is not None:
            w = int((S - 40) * max(0.0, min(100.0, v)) / 100)
            if w > 8:
                d.rounded_rectangle((20, by - 4, 20 + w, by + 4), radius=4, fill=col)
    return img


def _font(size):
    from m18lib.render import font
    return font("bold", size)


def tile(ctx, show_vram: bool = False, **_):
    if psutil is None:
        return {"glyph": "pip", "label": "psutil missing", "bg": "#402020"}
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    gu, gm = _gpu()
    rows = [("CPU", cpu), ("RAM", ram), ("GPU", gu)]
    if show_vram:
        rows.append(("VRM", gm))

    pal = getattr(ctx, "theme", None) or {}
    meters = ctx.theme_fn("meters") if hasattr(ctx, "theme_fn") else None
    if meters:                      # the theme draws meters in its own style (RGBA over its backdrop)
        return {"image": meters(rows, key=ctx.key)}
    if pal:
        return {"image": _themed(rows, pal)}
    S = 192
    ink = pal.get("ink", "#101016")
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0)) if pal else Image.new("RGB", (S, S), "#101016")
    d = ImageDraw.Draw(img)
    n = len(rows)
    pad, gap = 10, 8
    rh = (S - 2 * pad - gap * (n - 1)) // n
    lab_f, val_f = _font(int(rh * 0.42)), _font(int(rh * 0.5))
    for i, (name, v) in enumerate(rows):
        y = pad + i * (rh + gap)
        d.rounded_rectangle((pad, y, S - pad, y + rh), radius=10, fill=pal.get("track", "#1d1d28"))
        if v is not None:
            w = int((S - 2 * pad) * max(0.0, min(100.0, v)) / 100)
            if w > 12:
                d.rounded_rectangle((pad, y, pad + w, y + rh), radius=10, fill=_color(v, pal))
        d.text((pad + 8, y + rh // 2), name, font=lab_f, fill=pal.get("fg", "#ffffff"), anchor="lm",
               stroke_width=4 if pal else 3, stroke_fill=ink)
        txt = "--" if v is None else f"{v:.0f}%"
        d.text((S - pad - 8, y + rh // 2), txt, font=val_f, fill=pal.get("fg", "#ffffff"), anchor="rm",
               stroke_width=4 if pal else 3, stroke_fill=ink)
    return {"image": img}
