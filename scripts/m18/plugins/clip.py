"""Clip the last 30 seconds of the screen with OBS's replay buffer, and post the clip as a card.

    4: {live: {plugin: clip.status, interval: 3}, action: {plugin: clip.save}, hold: {plugin: clip.pick}}

The pad owns its own OBS profile and scene collection, both called "Pad Clips" (built by ../obs_pad_clips.py:
replay buffer 30 s, one scene per monitor "Monitor 1".., each a full-screen Display Capture). Nothing to set up in OBS.

Tap (clip.save):
  OBS not running      refresh the Pad Clips files, then start OBS in them, minimised to tray with the buffer on
                       (`obs64.exe --profile "Pad Clips" --collection "Pad Clips" --startreplaybuffer
                       --minimize-to-tray --disable-updater`, cwd = its bin\\64bit folder). This only ARMS: a clip
                       exists once the buffer has run, so the key counts FILL up to 30 s.
  OBS in another profile  nothing happens but the face (OTHER). The plugin never touches a running OBS that is not
                       in Pad Clips.
  running, buffer off  StartReplayBuffer
  buffer running       SaveReplayBuffer, wait for the file, then post a card (feed "clip"): a frame from the
                       clip's last seconds (ffmpeg on PATH, else no image), duration and size; actions
                       Keep / Delete (to the Recycle Bin) / Copy path
Hold (clip.pick): takeover with one key per monitor (number + resolution, the monitor being recorded is green);
  a press switches OBS's program scene (SetCurrentProgramScene), so the next clips show that monitor.
Live face: CLIP + a dot + one word: ARMED (buffer full, red), FILL 12s (buffer still filling), BOOT (OBS starting),
IDLE (OBS up, buffer off), OFF (OBS not running), OTHER (OBS runs a different profile), NO RB (replay buffer
unavailable), WS? (websocket off or refused).

OBS is driven over its built-in websocket (obs-websocket v5, port and password from OBS's own
%APPDATA%\\obs-studio\\plugin_config\\obs-websocket\\config.json, read on every call, never logged or copied).
The websocket client is the small stdlib one below: no extra package.
OBS keeps "Pad Clips" as its last-used profile and collection (user.ini), so a normal start of OBS opens it too.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path

OBS_DIR = Path(r"C:\Program Files\obs-studio\bin\64bit")
OBS_EXE = "obs64.exe"
PROFILE = COLLECTION = "Pad Clips"       # what ../obs_pad_clips.py builds
RB_SECONDS = 30
NOWIN = 0x08000000


# ------------------------------------------------------------------ obs-websocket v5 (stdlib client)
def ws_settings() -> dict:
    """{enabled, port, auth, password}; the password stays in this process's memory only."""
    p = Path(os.environ.get("APPDATA", "")) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enabled": False, "port": 4455, "auth": False, "password": "", "missing": True}
    return {"enabled": bool(d.get("server_enabled")), "port": int(d.get("server_port") or 4455),
            "auth": bool(d.get("auth_required")), "password": str(d.get("server_password") or "")}


class ObsError(Exception):
    pass


class _WS:
    GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, port, timeout=2.0):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        key = base64.b64encode(os.urandom(16))
        self.s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       b"Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n"
                       b"Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n" % (port, key))
        buf = b""
        while b"\r\n\r\n" not in buf:
            ch = self.s.recv(4096)
            if not ch:
                raise ObsError("websocket handshake: connection closed")
            buf += ch
            if len(buf) > 65536:
                raise ObsError("websocket handshake: no end of headers")
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        lines = head.split(b"\r\n")
        if b" 101" not in lines[0]:
            raise ObsError(f"websocket handshake refused: {lines[0][:60]!r}")
        want = base64.b64encode(hashlib.sha1(key + self.GUID).digest())
        acc = [ln.split(b":", 1)[1].strip() for ln in lines[1:] if ln.lower().startswith(b"sec-websocket-accept:")]
        if not acc or acc[0] != want:
            raise ObsError("websocket handshake: bad accept key")

    def _exact(self, n):
        while len(self.buf) < n:
            ch = self.s.recv(65536)
            if not ch:
                raise ObsError("websocket closed")
            self.buf += ch
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, op, payload: bytes):
        mask = os.urandom(4)
        n = len(payload)
        hdr = bytes([0x80 | op])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, obj):
        self._frame(1, json.dumps(obj).encode())

    def recv(self):
        data = b""
        while True:
            b0, b1 = self._exact(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            mask = self._exact(4) if b1 & 0x80 else None
            pl = self._exact(n)
            if mask:
                pl = bytes(b ^ mask[i % 4] for i, b in enumerate(pl))
            if op == 8:
                code = struct.unpack(">H", pl[:2])[0] if len(pl) >= 2 else 0
                raise ObsError(f"websocket closed by OBS (code {code}{': auth failed' if code == 4009 else ''})")
            if op == 9:
                self._frame(10, pl)
                continue
            if op in (0, 1, 2):
                data += pl
                if b0 & 0x80:
                    return json.loads(data)

    def close(self):
        try:
            self._frame(8, struct.pack(">H", 1000))
        except OSError:
            pass
        self.s.close()


def obs(*requests, timeout=2.0) -> list[dict]:
    """Run (requestType, data) requests in one session. Each result: {ok, code, comment, data}."""
    cfg = ws_settings()
    if not cfg["enabled"]:
        raise ObsError("OBS websocket server is disabled (Tools > WebSocket Server Settings)")
    ws = _WS(cfg["port"], timeout)
    try:
        hello = ws.recv()
        if hello.get("op") != 0:
            raise ObsError("OBS did not say hello")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        au = hello["d"].get("authentication")
        if au:
            if not cfg["password"]:
                raise ObsError("OBS wants a password but config.json has none")
            secret = base64.b64encode(hashlib.sha256((cfg["password"] + au["salt"]).encode()).digest())
            ident["authentication"] = base64.b64encode(
                hashlib.sha256(secret + au["challenge"].encode()).digest()).decode()
        ws.send({"op": 1, "d": ident})
        if ws.recv().get("op") != 2:
            raise ObsError("OBS did not identify the client")
        out = []
        for i, (rt, data) in enumerate(requests):
            ws.send({"op": 6, "d": {"requestType": rt, "requestId": str(i), "requestData": data or {}}})
            while True:
                m = ws.recv()
                if m.get("op") == 7 and m["d"].get("requestId") == str(i):
                    break
            st = m["d"]["requestStatus"]
            out.append({"ok": bool(st.get("result")), "code": st.get("code"), "comment": st.get("comment"),
                        "data": m["d"].get("responseData") or {}})
        return out
    finally:
        ws.close()


def obs_running() -> bool:
    try:
        import psutil
        return any((p.info.get("name") or "").lower() == OBS_EXE for p in psutil.process_iter(["name"]))
    except ImportError:
        out = subprocess.run(["tasklist", "/fi", f"imagename eq {OBS_EXE}", "/nh"], capture_output=True,
                             text=True, creationflags=NOWIN).stdout
        return OBS_EXE in out.lower()


def probe() -> str:
    """armed | idle | off | norb | ws | other (OBS runs, but not in Pad Clips)"""
    if not obs_running():
        return "off"
    try:
        rb, prof, col = obs(("GetReplayBufferStatus", None), ("GetProfileList", None), ("GetSceneCollectionList", None))
    except (OSError, ObsError):
        return "ws"
    if (prof["ok"] and prof["data"].get("currentProfileName") != PROFILE) or \
            (col["ok"] and col["data"].get("currentSceneCollectionName") != COLLECTION):
        return "other"
    if rb["ok"]:
        return "armed" if rb["data"].get("outputActive") else "idle"
    return "norb" if rb["code"] == 604 else "ws"       # 604 = output not running/available (RB disabled)


# ------------------------------------------------------------------ media helpers
def media_info(path: Path) -> dict:
    """{duration (s or None), size (bytes), image (PNG bytes or None)} using ffmpeg/ffprobe when on PATH."""
    info = {"size": path.stat().st_size, "duration": None, "image": None}
    probe_exe, ff = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if probe_exe:
        try:
            out = subprocess.run([probe_exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(path)], capture_output=True, text=True, timeout=10, creationflags=NOWIN)
            info["duration"] = float(out.stdout.strip())
        except (ValueError, subprocess.SubprocessError, OSError):
            pass
    if ff:
        back = min(1.5, (info["duration"] or 3) / 2)
        try:
            out = subprocess.run([ff, "-v", "error", "-sseof", f"-{back:.2f}", "-i", str(path), "-frames:v", "1",
                                  "-vf", "scale=480:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                                 capture_output=True, timeout=15, creationflags=NOWIN)
            if out.stdout[:8] == b"\x89PNG\r\n\x1a\n":
                info["image"] = out.stdout
        except (subprocess.SubprocessError, OSError):
            pass
    return info


def _fmt_size(n):
    for unit, d in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= d:
            v = n / d
            return f"{v:.0f} {unit}" if v >= 10 else f"{v:.1f} {unit}"
    return f"{n} B"


def _fmt_dur(s):
    if s is None:
        return "?s"
    s = int(round(s))
    return f"{s}s" if s < 60 else f"{s // 60}:{s % 60:02d}"


def _paths_file(ctx) -> Path:
    return ctx.config_dir / "state" / "clip-files.json"


def _paths(ctx) -> dict:
    try:
        return json.loads(_paths_file(ctx).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_paths(ctx, m):
    p = _paths_file(ctx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m), encoding="utf-8")


def post_clip_card(ctx, path) -> dict:
    """Post the card for a saved clip file. Returns the card."""
    path = Path(path)
    info = media_info(path)
    cid = "clip:" + hashlib.sha1(str(path).lower().encode()).hexdigest()[:16]
    card = {"id": cid, "feed": "clip", "title": "Clip",
            "lines": [_fmt_dur(info["duration"]), _fmt_size(info["size"]), time.strftime("%H:%M")],
            "icon": "mdi:filmstrip",
            "actions": [{"id": "keep", "label": "Keep"}, {"id": "delete", "label": "Delete"},
                        {"id": "copy", "label": "Copy path"}],
            "priority": 1}
    if info["image"]:
        card["image_png_b64"] = base64.b64encode(info["image"]).decode()
    m = _paths(ctx)
    m[cid] = str(path)
    _save_paths(ctx, m)
    ctx.cards.post(card, handler="clip.on_card")
    return card


def _recycle(path: str) -> bool:
    """Send a file to the Recycle Bin (undoable), no dialogs."""
    import ctypes
    from ctypes import wintypes as W

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", W.HWND), ("wFunc", W.UINT), ("pFrom", W.LPCWSTR), ("pTo", W.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", W.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", W.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, path + "\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def on_card(ctx, card, action):
    """Handler for this feed's card actions (stored with the card, so it survives restarts)."""
    from m18lib import keys as K
    m = _paths(ctx)
    path = m.pop(card["id"], None)
    _save_paths(ctx, m)
    if not path:
        ctx.log(f"clip: no file recorded for {card['id']}")
        return
    if action == "delete":
        if K.DRY:
            K._log(f"recycle {path}")
        else:
            ok = _recycle(path)
            ctx.log(f"clip: {'recycled' if ok else 'could not recycle'} {path}")
            if not ok and os.path.exists(path):
                post_clip_card(ctx, path)                   # the file is still there: bring its card back
    elif action == "copy":
        if K.DRY:
            K._log(f"clipboard {path}")
        else:
            from m18lib import winclip
            winclip.set_text(path)
            ctx.log(f"clip: path copied {path}")
    else:
        ctx.log(f"clip: kept {path}")


# ------------------------------------------------------------------ face + action
LOOK = {"armed": ("#8b1a1a", "#ff5555", "ARMED"), "idle": ("#3a3a46", "#aaaaaa", "IDLE"),
        "off": ("#26262e", "#5a5a66", "OFF"), "norb": ("#6b5208", "#ffff55", "NO RB"),
        "ws": ("#6b5208", "#ffff55", "WS?"), "boot": ("#6b2a08", "#ffaa00", "BOOT"),
        "fill": ("#6b3a1a", "#ff8855", "FILL"), "other": ("#3a2a6b", "#bb99ff", "OTHER"),
        "saved": ("#1d5a24", "#55ff55", "SAVED"), "save": ("#8b1a1a", "#ffffff", "SAVING")}


def render(pen, state: str, word: str | None = None) -> dict:
    from PIL import ImageDraw
    from m18lib import tiles as T
    bg, dot, w = LOOK[state]
    img = pen.canvas()
    pen.slot(img, bg, (1, 1, 62, 62))
    pen.word(img, 32, 12, "CLIP", "#dddddd", maxw=54, top=1)
    k = pen.k
    d = ImageDraw.Draw(img)
    cx, cy, r = 32 * k, 29 * k, 8 * k
    if state in ("off", "idle", "other"):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=T.rgb(dot) + (255,), width=2 * k)
    else:
        d.ellipse((cx - r - k, cy - r - k, cx + r + k, cy + r + k), fill=(0, 0, 0, 255))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=T.rgb(dot) + (255,))
        d.ellipse((cx - r // 2 - k, cy - r // 2 - k, cx - r // 4, cy - r // 4), fill=T.mix(dot, "#ffffff", 0.6) + (255,))
    pen.word(img, 32, 49, word or w, T.WHITE if state != "off" else T.GREY, top=2, lines=1)
    return pen.face(img)


def _st(ctx) -> dict:
    return ctx.daemon.plugin_state.setdefault("clip", {})


def _arm_mark(ctx):
    """We just started OBS / the buffer: the buffer is empty until it has run RB_SECONDS."""
    _st(ctx)["arming"] = time.time()
    _st(ctx).pop("armed_at", None)


def _view(ctx, state):
    """(state, word) as shown: an armed buffer that is still filling reads FILL 12s, an OBS we are still
    starting reads BOOT."""
    st = _st(ctx)
    now = time.time()
    arming = st.get("arming")
    if state == "armed":
        if arming:
            st["armed_at"], st["arming"] = now, None
        at = st.get("armed_at")
        if at and now - at < RB_SECONDS:
            return "fill", f"FILL {int(now - at)}s"
        return "armed", None
    if state in ("off", "ws") and arming and now - arming < 90:
        return "boot", None
    if state != "armed":
        st.pop("armed_at", None)
    return state, None


def status(ctx, **_):
    """Live face: armed vs off (one websocket session per tick, only while OBS runs)."""
    from m18lib import tiles as T
    st = _st(ctx)
    tr = st.get("transient")
    if tr and time.time() < tr[1]:
        state, word = tr[0], None
    else:
        state, word = _view(ctx, probe())
        st["last"] = state
    return render(T.Pen(ctx.daemon), state, word)


def _flash(ctx, state, seconds):
    from m18lib import tiles as T
    _st(ctx)["transient"] = (state, time.time() + seconds)
    ctx.flash(render(T.Pen(ctx.daemon), state), seconds)


def _pad_files(ctx):
    """Create/refresh the Pad Clips profile + collection (only possible while OBS is not running)."""
    try:
        try:
            import obs_pad_clips as M
        except ImportError:
            import importlib.util
            spec = importlib.util.spec_from_file_location("obs_pad_clips", Path(__file__).resolve().parent.parent
                                                          / "obs_pad_clips.py")
            M = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(M)
        res = M.ensure()
        ctx.log(f"clip: Pad Clips files {'updated' if res['changed'] else 'current'} ({len(res['monitors'])} monitors)")
    except Exception as e:                      # a refresh failure must not stop the clip key from arming
        ctx.log(f"clip: could not refresh Pad Clips files: {e}")


def _start_obs(ctx):
    from m18lib import keys as K
    exe = OBS_DIR / OBS_EXE
    args = ["--profile", PROFILE, "--collection", COLLECTION, "--startreplaybuffer", "--minimize-to-tray",
            "--disable-updater"]
    if K.DRY:
        K._log(f"run {exe} {' '.join(args)} (cwd {OBS_DIR})")
    else:
        _pad_files(ctx)
        sent = Path(os.environ.get("APPDATA", "")) / "obs-studio" / ".sentinel"
        shutil.rmtree(sent, ignore_errors=True)         # a killed OBS leaves this and asks about safe mode
        subprocess.Popen([str(exe), *args], cwd=str(OBS_DIR), creationflags=0x00000008 | 0x00000200,
                         close_fds=True)
    _arm_mark(ctx)
    ctx.log("clip: OBS started in Pad Clips with the replay buffer; clips exist after it has run 30 s")
    _flash(ctx, "armed", 5)


def save(ctx, **_):
    """The clip key's press (see the module doc)."""
    if not obs_running():
        _start_obs(ctx)
        return
    try:
        pr = probe()
        if pr == "other":
            ctx.log("clip: OBS is running another profile/collection; left alone")
            _flash(ctx, "other", 3)
            return
        st = obs(("GetReplayBufferStatus", None))[0]
        if not st["ok"]:
            ctx.log(f"clip: replay buffer unavailable ({st['code']} {st['comment']})")
            _flash(ctx, "norb", 3)
            return
        if not st["data"].get("outputActive"):
            r = obs(("StartReplayBuffer", None))[0]
            ctx.log(f"clip: StartReplayBuffer -> {r['ok']} {r['comment'] or ''}")
            if r["ok"]:
                _arm_mark(ctx)
            _flash(ctx, "armed" if r["ok"] else "norb", 4)
            return
        _flash(ctx, "save", 6)
        before = obs(("GetLastReplayBufferReplay", None))[0]["data"].get("savedReplayPath")
        r = obs(("SaveReplayBuffer", None))[0]
        if not r["ok"]:
            raise ObsError(f"SaveReplayBuffer: {r['code']} {r['comment']}")
        path, end = None, time.time() + 8
        while time.time() < end:
            time.sleep(0.4)
            p = obs(("GetLastReplayBufferReplay", None))[0]["data"].get("savedReplayPath")
            if p and p != before and os.path.exists(p):
                path = p
                break
        if not path:
            raise ObsError("no new replay file within 8 s")
        size, end = -1, time.time() + 5                 # wait until the muxer stops writing
        while time.time() < end and os.path.getsize(path) != size:
            size = os.path.getsize(path)
            time.sleep(0.3)
        post_clip_card(ctx, path)
        ctx.log(f"clip: saved {path}")
        _flash(ctx, "saved", 2)
    except (OSError, ObsError) as e:
        ctx.log(f"clip: {e}")
        _flash(ctx, "ws", 3)


# ------------------------------------------------------------------ off (key 15 of the hold takeover)
def _close_obs_window() -> int:
    """Ask OBS to exit the way its own Exit does: WM_CLOSE to its main window, which is hidden while in the tray
    (so CloseMainWindow/taskkill without /f can't reach it). Returns how many windows were asked."""
    import ctypes
    from ctypes import wintypes
    import psutil
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == OBS_EXE}
    u = ctypes.windll.user32
    hit = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lp):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            buf = ctypes.create_unicode_buffer(200)
            u.GetWindowTextW(hwnd, buf, 200)
            if buf.value.startswith("OBS "):               # "OBS 32.2.2 - Profile: Pad Clips - Scenes: Pad Clips"
                hit.append(hwnd)
        return True

    u.EnumWindows(each, 0)
    for h in hit:
        u.PostMessageW(h, 0x0010, 0, 0)                     # WM_CLOSE
    return len(hit)


def off(ctx, **_):
    """Stop buffering and close OBS. Only an OBS that is in Pad Clips; another profile is left alone."""
    from m18lib import keys as K
    if K.DRY:
        K._log("obs StopReplayBuffer, then close OBS")
        _flash(ctx, "off", 3)
        return
    if not obs_running():
        _flash(ctx, "off", 3)
        return
    try:
        if probe() == "other":
            _flash(ctx, "other", 3)
            return
        obs(("StopReplayBuffer", None))
    except (OSError, ObsError) as e:
        ctx.log(f"clip.off: {e}")
    time.sleep(0.8)                                         # let the output stop before the window closes
    n = _close_obs_window()
    ctx.log(f"clip: off (replay buffer stopped, asked {n} OBS window(s) to close)")
    _flash(ctx, "off", 3)


# ------------------------------------------------------------------ monitor pick (hold)
PICK_S = 30
OFF_KEY = 15


def _monitor_list():
    """[{n, w, h}] in the order obs_pad_clips builds the scenes (primary first), or [] if unknown."""
    try:
        try:
            import obs_pad_clips as M
        except ImportError:
            import importlib.util
            spec = importlib.util.spec_from_file_location("obs_pad_clips", Path(__file__).resolve().parent.parent
                                                          / "obs_pad_clips.py")
            M = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(M)
        return [{"n": m["n"], "w": m["w"], "h": m["h"]} for m in M.monitors()]
    except Exception:
        return []


def _scene_n(name: str):
    if name.startswith("Monitor "):
        try:
            return int(name[8:])
        except ValueError:
            return None
    return None


def pick_state() -> dict:
    """{ok, current (monitor number or None), scenes: sorted [n], err}. Read-only."""
    try:
        sc, = obs(("GetSceneList", None))
    except (OSError, ObsError) as e:
        return {"ok": False, "err": str(e)}
    if not sc["ok"]:
        return {"ok": False, "err": sc["comment"] or "scene list"}
    d = sc["data"]
    ns = sorted(n for n in (_scene_n(x.get("sceneName", "")) for x in d.get("scenes", [])) if n)
    return {"ok": True, "current": _scene_n(d.get("currentProgramSceneName") or ""), "scenes": ns}


def pick_faces(pen, state: dict, mons: list) -> dict:
    """One key per monitor on keys 1-10: the number large, its resolution small; the recorded one is green."""
    from m18lib import tiles as T
    faces = {}
    size = {m["n"]: f"{m['w']}x{m['h']}" for m in mons}
    for i, n in enumerate(state["scenes"][:10]):
        cur = n == state.get("current")
        img = pen.canvas()
        pen.slot(img, "#1d6b2a" if cur else "#2a2a3a", (1, 1, 62, 62))
        pen.word(img, 32, 25, str(n), T.WHITE, maxw=54, top=3, lines=1)
        if n in size:
            pen.word(img, 32, 51, size[n], "#dddddd" if cur else "#9a9aaa", maxw=58, top=1, lines=1)
        faces[1 + i] = pen.face(img)
    img = pen.canvas()                                      # key 15: stop buffering and close OBS
    pen.slot(img, "#7a1c1c", (1, 1, 62, 62))
    pen.word(img, 32, 25, "OFF", T.WHITE, maxw=54, top=3, lines=1)
    pen.word(img, 32, 51, "clips", "#e0b0b0", maxw=58, top=1, lines=1)
    faces[OFF_KEY] = pen.face(img)
    return faces


def pick(ctx, **_):
    """Hold: choose which monitor the clips record (see the module doc)."""
    from m18lib import keys as K
    from m18lib import tiles as T
    if not K.DRY and not obs_running():
        _start_obs(ctx)                                  # off: arm it first, pick afterwards
        return
    pr = "armed" if K.DRY and not obs_running() else probe()
    if pr == "other":
        _flash(ctx, "other", 3)
        return
    state = pick_state() if not (K.DRY and not obs_running()) else \
        {"ok": True, "current": 1, "scenes": [m["n"] for m in _monitor_list()] or [1]}
    if not state["ok"]:
        ctx.log(f"clip.pick: {state['err']}")
        _flash(ctx, "ws", 3)
        return
    mons = _monitor_list()
    pen = T.Pen(ctx.daemon)
    box = {"state": state}

    def on_key(c, key, down, t):
        if not down:
            return
        if key == OFF_KEY:
            c.end_modal()
            off(ctx)
            return
        idx = key - 1
        ns = box["state"]["scenes"]
        if not 0 <= idx < min(len(ns), 10):
            return
        n = ns[idx]
        try:
            if K.DRY:
                K._log(f"obs SetCurrentProgramScene Monitor {n}")
            else:
                r = obs(("SetCurrentProgramScene", {"sceneName": f"Monitor {n}"}))[0]
                if not r["ok"]:
                    raise ObsError(f"{r['code']} {r['comment']}")
            ctx.log(f"clip: recording Monitor {n}")
        except (OSError, ObsError) as e:
            ctx.log(f"clip.pick: {e}")
            c.end_modal()
            _flash(ctx, "ws", 3)
            return
        box["state"]["current"] = n
        c.modal_update(box["tok"], pick_faces(pen, box["state"], mons), 1.2)    # show the choice, then close

    box["tok"] = ctx.modal(pick_faces(pen, state, mons), on_key=on_key, timeout=PICK_S)
