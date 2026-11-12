"""Discord mute/deafen: live status on the key + toggle on tap, via Discord's local RPC.

Unlike a keybind, this reads Discord's real state, so the key can't drift out of sync.

One-time setup (2 minutes, explain this to the user):
  1. https://discord.com/developers/applications -> New Application (any name, e.g. "M18").
  2. OAuth2 page: copy Client ID and Client Secret; under Redirects add  http://localhost
  3. Create  plugins/discord.json  next to this file:
        {"client_id": "...", "client_secret": "..."}
  4. First time the key updates, Discord shows an "Authorize" popup -> Authorize.
     The token is cached in plugins/.discord_token.json and refreshed automatically.
  RPC voice scopes are restricted by Discord, but the *owner* of the app can always authorize
  it for themselves, which is exactly this case.

macros.yaml:
  6: {live: {plugin: discord_voice.status, interval: 1}, action: {plugin: discord_voice.toggle_mute},
      hold: {plugin: discord_voice.toggle_deaf}}
"""
from __future__ import annotations

import json
import os
import struct
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = HERE / "discord.json"
TOKEN = HERE / ".discord_token.json"
SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write"]

_lock = threading.Lock()
_pipe = None
_authed = False
_last_err = 0.0


def _cfg():
    if not CFG.exists():
        raise RuntimeError("plugins/discord.json missing (see discord_voice.py header)")
    return json.loads(CFG.read_text())


def _send(op, payload):
    data = json.dumps(payload).encode()
    _pipe.write(struct.pack("<II", op, len(data)) + data)
    _pipe.flush()


def _recv():
    hdr = _pipe.read(8)
    if len(hdr) < 8:
        raise ConnectionError("discord pipe closed")
    op, ln = struct.unpack("<II", hdr)
    return op, json.loads(_pipe.read(ln))


def _cmd(cmd, args=None, evt=None, timeout=60):
    nonce = str(uuid.uuid4())
    _send(1, {"cmd": cmd, "args": args or {}, "nonce": nonce, **({"evt": evt} if evt else {})})
    t0 = time.time()
    while time.time() - t0 < timeout:
        op, msg = _recv()
        if msg.get("nonce") == nonce:
            if msg.get("evt") == "ERROR":
                raise RuntimeError(f"discord {cmd}: {msg['data'].get('message')}")
            return msg.get("data")
    raise TimeoutError(cmd)


def _token_request(fields):
    c = _cfg()
    body = urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                   **fields}).encode()
    req = urllib.request.Request("https://discord.com/api/oauth2/token", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "m18-macropad"})
    with urllib.request.urlopen(req, timeout=15) as r:
        tok = json.loads(r.read())
    tok["expires_at"] = time.time() + tok.get("expires_in", 0) - 60
    TOKEN.write_text(json.dumps(tok))
    return tok


def _connect():
    global _pipe, _authed
    c = _cfg()
    for i in range(10):
        try:
            _pipe = open(rf"\\?\pipe\discord-ipc-{i}", "r+b", buffering=0)
            break
        except OSError:
            continue
    else:
        raise ConnectionError("Discord isn't running")
    _send(0, {"v": 1, "client_id": str(c["client_id"])})
    _recv()                                                   # READY
    tok = json.loads(TOKEN.read_text()) if TOKEN.exists() else None
    if tok and tok.get("expires_at", 0) < time.time():
        try:
            tok = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        except Exception:
            tok = None
    if not tok:                                               # shows Discord's consent popup
        code = _cmd("AUTHORIZE", {"client_id": str(c["client_id"]), "scopes": SCOPES},
                    timeout=120)["code"]
        tok = _token_request({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": c.get("redirect_uri", "http://localhost")})
    _cmd("AUTHENTICATE", {"access_token": tok["access_token"]})
    _authed = True


def _call(cmd, args=None):
    global _pipe, _authed
    with _lock:
        for attempt in (1, 2):
            try:
                if not _authed:
                    _connect()
                return _cmd(cmd, args)
            except Exception:
                try:
                    _pipe and _pipe.close()
                except Exception:
                    pass
                _pipe, _authed = None, False
                if attempt == 2:
                    raise


OFFLINE = {"icon": "mdi:microphone-question", "label": "offline", "bg": "#2b2d31", "fg": "#80848e"}
NEEDS_SETUP = {"icon": "mdi:microphone-settings", "label": "Setup", "bg": "#5865f2", "fg": "#ffffff"}


def status(ctx, **_):
    global _last_err
    if not CFG.exists():                      # one-time discord.json setup not done yet
        return NEEDS_SETUP
    if time.time() - _last_err < 5:           # don't hammer a closed Discord
        return OFFLINE
    try:
        v = _call("GET_VOICE_SETTINGS")
    except Exception as e:
        _last_err = time.time()
        ctx.log("discord:", e)
        return OFFLINE
    if v.get("deaf"):
        return {"icon": "mdi:headphones-off", "label": "DEAF", "bg": "#8b1a1a", "fg": "#ffffff"}
    if v.get("mute"):
        return {"icon": "mdi:microphone-off", "label": "MUTED", "bg": "#a01818", "fg": "#ffffff"}
    return {"icon": "mdi:microphone", "label": "LIVE", "bg": "#1f7a3a", "fg": "#ffffff"}


def toggle_mute(ctx, **_):
    v = _call("GET_VOICE_SETTINGS")
    _call("SET_VOICE_SETTINGS", {"mute": not v.get("mute")})
    ctx.daemon.live_next.clear()              # refresh the face immediately


def toggle_deaf(ctx, **_):
    v = _call("GET_VOICE_SETTINGS")
    _call("SET_VOICE_SETTINGS", {"deaf": not v.get("deaf")})
    ctx.daemon.live_next.clear()
