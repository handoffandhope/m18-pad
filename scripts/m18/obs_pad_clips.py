"""Create or refresh the OBS profile + scene collection the clip key owns: "Pad Clips".

    %USERPROFILE%\\tools\\m18\\.venv\\Scripts\\python.exe obs_pad_clips.py [--check] [--json]

Writes (only while OBS is not running; OBS rewrites its config on exit):
  %APPDATA%\\obs-studio\\basic\\profiles\\Pad_Clips\\{basic.ini, recordEncoder.json}
  %APPDATA%\\obs-studio\\basic\\scenes\\Pad_Clips.json
  profile: replay buffer on (30 s), record encoder NVENC H.264 (x264 veryfast without an NVIDIA GPU) in CQP/CRF
           20 (desktop text stays sharp at a low bitrate), mp4, keyframe every 1 s, 30 fps, canvas = the primary
           monitor's pixels, folder %USERPROFILE%\\Videos\\PadClips
  scenes:  "Monitor 1".."Monitor N" in Windows order, primary first; each is one Display Capture of that monitor
           scaled to fit the canvas; Desktop Audio on track 1, no microphone.
Idempotent: a collection that still lists the same monitors and a profile with the same canvas are left alone (OBS
rewrites them on exit; sources added by hand survive); a changed monitor set rebuilds the collection, keeping the
chosen monitor if it still exists. Other OBS profiles and collections are never touched; global.ini and
user.ini are left alone (OBS decides what is "last used").

`monitors()` is also used by plugins/clip.py for the pick takeover.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import uuid
from ctypes import wintypes as W
from pathlib import Path

NAME = "Pad Clips"
SAFE = "Pad_Clips"
CLIPS_DIR = Path.home() / "Videos" / "PadClips"
OBS_ROOT = Path(os.environ.get("APPDATA", "")) / "obs-studio"
NS = uuid.UUID("5f0d3c52-9a1e-4b7a-8d0e-0c1a50ad0c11")
RB_SECONDS = 30
NOWIN = 0x08000000


# ------------------------------------------------------------------ monitors
class _MI(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


class _DD(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("DeviceName", ctypes.c_wchar * 32), ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", W.DWORD), ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]


def monitors() -> list[dict]:
    """[{n, w, h, x, y, primary, device, id}] primary first, then Windows enumeration order.
    `id` is the display's device-interface path, which is what OBS stores as monitor_id."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor v2: real pixels
    except (OSError, AttributeError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (OSError, AttributeError):
            pass
    out = []

    def cb(h, hdc, rect, lp):
        mi = _MI()
        mi.cbSize = ctypes.sizeof(_MI)
        ctypes.windll.user32.GetMonitorInfoW(h, ctypes.byref(mi))
        dd = _DD()
        dd.cb = ctypes.sizeof(_DD)
        ident = ""
        if ctypes.windll.user32.EnumDisplayDevicesW(mi.szDevice, 0, ctypes.byref(dd), 1):   # 1 = interface name
            ident = dd.DeviceID
        r = mi.rcMonitor
        out.append({"w": r.right - r.left, "h": r.bottom - r.top, "x": r.left, "y": r.top,
                    "primary": bool(mi.dwFlags & 1), "device": mi.szDevice, "id": ident})
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)(cb)
    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc, 0)
    out.sort(key=lambda m: not m["primary"])               # stable: primary first, the rest in Windows order
    for i, m in enumerate(out, 1):
        m["n"] = i
    return out


# ------------------------------------------------------------------ OBS file shapes
def _nvenc() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=10, creationflags=NOWIN)
        return r.returncode == 0 and "GPU" in r.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _ini(sections: dict) -> str:
    lines = ["\ufeff[General]", f"Name={NAME}", ""]
    for sec, kv in sections.items():
        lines.append(f"[{sec}]")
        lines += [f"{k}={v}" for k, v in kv.items()]
        lines.append("")
    return "\n".join(lines)


def profile_files(mons: list[dict], nvenc: bool) -> dict[str, str]:
    p = mons[0]
    enc = "obs_nvenc_h264_tex" if nvenc else "obs_x264"
    rec = ({"rate_control": "CQP", "cqp": 20, "preset2": "p5", "multipass": "disabled", "tune": "hq",
            "profile": "high", "bf": 2, "lookahead": False, "psycho_aq": True, "keyint_sec": 1}
           if nvenc else
           {"rate_control": "CRF", "crf": 20, "preset": "veryfast", "profile": "high", "keyint_sec": 1})
    ini = _ini({
        "Video": {"BaseCX": p["w"], "BaseCY": p["h"], "OutputCX": p["w"], "OutputCY": p["h"], "FPSType": 0,
                  "FPSCommon": 30, "ScaleType": "bicubic", "ColorFormat": "NV12", "ColorSpace": "709",
                  "ColorRange": "Partial"},
        "Audio": {"SampleRate": 48000, "ChannelSetup": "Stereo"},
        "Output": {"Mode": "Advanced", "FilenameFormatting": "%CCYY-%MM-%DD %hh-%mm-%ss", "DelayEnable": "false"},
        "AdvOut": {"Encoder": enc, "AudioEncoder": "ffmpeg_aac", "TrackIndex": 1, "Rescale": "false",
                   "RecType": "Standard", "RecFilePath": str(CLIPS_DIR).replace("\\", "/"), "RecFormat2": "mp4",
                   "RecEncoder": enc, "RecAudioEncoder": "ffmpeg_aac", "RecTracks": 1, "RecRescale": "false",
                   "RecSplitFile": "false", "RecRB": "true", "RecRBTime": RB_SECONDS, "RecRBSize": 1024,
                   "Track1Bitrate": 160, "Track1Name": "Desktop audio"},
        "Stream1": {"IgnoreRecommended": "true", "EnableMultitrackVideo": "false"},
    })
    return {"basic.ini": ini, "recordEncoder.json": json.dumps(rec, indent=4)}


def _src_common(name, sid, vid, settings, mixers, uid):
    return {"prev_ver": 537001986, "name": name, "uuid": uid, "id": sid, "versioned_id": vid, "settings": settings,
            "mixers": mixers, "sync": 0, "flags": 0, "volume": 1.0, "balance": 0.5, "enabled": True, "muted": False,
            "push-to-mute": False, "push-to-mute-delay": 0, "push-to-talk": False, "push-to-talk-delay": 0,
            "hotkeys": {}, "deinterlace_mode": 0, "deinterlace_field_order": 0, "monitoring_type": 0,
            "private_settings": {}}


def collection(mons: list[dict]) -> dict:
    cw, ch = mons[0]["w"], mons[0]["h"]
    sources, order = [], []
    for m in mons:
        key = m["id"] or m["device"]
        dc_uid = str(uuid.uuid5(NS, "display:" + key))
        sc_uid = str(uuid.uuid5(NS, "scene:" + key))
        dc = _src_common(f"Display {m['n']}", "monitor_capture", "monitor_capture",
                         {"monitor_id": m["id"], "monitor": m["n"] - 1, "method": 0, "capture_cursor": True},
                         0, dc_uid)
        dc["hotkeys"] = {"libobs.mute": [], "libobs.unmute": [], "libobs.push-to-mute": [], "libobs.push-to-talk": []}
        s = min(cw / m["w"], ch / m["h"])
        item = {"name": dc["name"], "source_uuid": dc_uid, "visible": True, "locked": False, "rot": 0.0,
                "scale_ref": {"x": float(cw), "y": float(ch)}, "align": 5, "bounds_type": 2, "bounds_align": 0,
                "bounds_crop": False, "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0, "id": 1,
                "group_item_backup": False, "pos": {"x": 0.0, "y": 0.0}, "scale": {"x": s, "y": s},
                "bounds": {"x": float(cw), "y": float(ch)}, "scale_filter": "disable", "blend_method": "default",
                "blend_type": "normal", "show_transition": {"duration": 0}, "hide_transition": {"duration": 0},
                "private_settings": {}}
        sc = _src_common(f"Monitor {m['n']}", "scene", "scene",
                         {"id_counter": 1, "custom_size": False, "items": [item]}, 0, sc_uid)
        sc["hotkeys"] = {"OBSBasic.SelectScene": [], "libobs.show_scene_item.1": [], "libobs.hide_scene_item.1": []}
        sources += [dc, sc]
        order.append({"name": sc["name"]})
    desk = _src_common("Desktop Audio", "wasapi_output_capture", "wasapi_output_capture", {"device_id": "default"},
                       1, str(uuid.uuid5(NS, "desktop-audio")))
    desk["hotkeys"] = {"libobs.mute": [], "libobs.unmute": [], "libobs.push-to-mute": [], "libobs.push-to-talk": []}
    return {"name": NAME, "sources": sources, "groups": [], "scene_order": order,
            "current_scene": order[0]["name"], "current_program_scene": order[0]["name"], "canvases": [],
            "current_transition": "Cut", "transition_duration": 300, "transitions": [], "quick_transitions": [],
            "saved_projectors": [], "preview_locked": False, "scaling_enabled": False, "scaling_level": -8,
            "scaling_off_x": 0.0, "scaling_off_y": 0.0, "virtual-camera": {"type2": 3},
            "DesktopAudioDevice1": desk, "modules": {"scripts-tool": []},
            "resolution": {"x": cw, "y": ch}, "migration_resolution": {"x": cw, "y": ch}, "version": 2}


# ------------------------------------------------------------------ write
def obs_running() -> bool:
    out = subprocess.run(["tasklist", "/fi", "imagename eq obs64.exe", "/nh"], capture_output=True, text=True,
                         creationflags=NOWIN).stdout
    return "obs64.exe" in out.lower()


def _put(path: Path, text: str) -> bool:
    """Write only when the content changed. True if written."""
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return True


def _profile_stale(ini: Path, primary: dict) -> bool:
    """True when the profile is missing or no longer matches (OBS rewrites the file on exit, so compare values)."""
    try:
        txt = ini.read_text(encoding="utf-8-sig")
    except OSError:
        return True
    kv = dict(ln.split("=", 1) for ln in txt.splitlines() if "=" in ln)
    return (kv.get("BaseCX") != str(primary["w"]) or kv.get("BaseCY") != str(primary["h"])
            or kv.get("RecRB") != "true" or not (ini.parent / "recordEncoder.json").exists())


def _collection_state(path: Path):
    """{ids: monitor ids in display order, res: canvas, current: program scene} of an existing collection, or None.
    A collection that still shows the same monitors is left alone, so sources the user adds by hand survive."""
    try:
        d = json.loads(path.read_text(encoding="utf-8-sig"))
        ids = [x["settings"].get("monitor_id") or "" for x in d["sources"] if x.get("id") == "monitor_capture"]
        return {"ids": ids, "res": (d["resolution"]["x"], d["resolution"]["y"]), "current": d.get("current_program_scene")}
    except (OSError, ValueError, KeyError):
        return None


def ensure(force: bool = False) -> dict:
    """Create or refresh the profile and collection. Returns {changed: [...], monitors: [...], encoder}.
    Raises RuntimeError while OBS runs (unless force) since OBS would overwrite the files on exit."""
    if obs_running() and not force:
        raise RuntimeError("OBS is running; close it first")
    mons = monitors()
    if not mons:
        raise RuntimeError("no monitors found")
    nv = _nvenc()
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    changed = []
    prof = OBS_ROOT / "basic" / "profiles" / SAFE
    if _profile_stale(prof / "basic.ini", mons[0]):
        for fn, text in profile_files(mons, nv).items():
            if _put(prof / fn, text):
                changed.append(f"profiles/{SAFE}/{fn}")
    scn = OBS_ROOT / "basic" / "scenes" / f"{SAFE}.json"
    keep = _collection_state(scn)
    if keep is None or keep["ids"] != [m["id"] or m["device"] for m in mons] or keep["res"] != (mons[0]["w"], mons[0]["h"]):
        col = collection(mons)
        if keep and keep["current"] in [x["name"] for x in col["scene_order"]]:
            col["current_scene"] = col["current_program_scene"] = keep["current"]
        if _put(scn, json.dumps(col, indent=4)):
            changed.append(f"scenes/{SAFE}.json")
    return {"changed": changed, "monitors": [{k: m[k] for k in ("n", "w", "h", "primary")} for m in mons],
            "encoder": "nvenc" if nv else "x264"}


if __name__ == "__main__":
    if "--check" in sys.argv:
        print(json.dumps({"monitors": monitors(), "nvenc": _nvenc()}, indent=1))
        sys.exit(0)
    try:
        res = ensure()
    except RuntimeError as e:
        print(f"obs_pad_clips: {e}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(res) if "--json" in sys.argv else
          f"Pad Clips: {len(res['monitors'])} monitor(s), {res['encoder']}, "
          f"{'wrote ' + ', '.join(res['changed']) if res['changed'] else 'already up to date'}")
