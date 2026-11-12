"""M18 panel: a small local web app in front of the m18d daemon.

Shows the pad, switches button sets and themes, and runs a headless Claude Code agent for plain-language
requests. Python stdlib only, plus the daemon's own m18lib (and its PyYAML) for validation.
See references/panel.md in the m18-macropad skill.

Env: M18_HOME (default %USERPROFILE%/tools/m18), M18_PORT (daemon, default 8718),
     M18_PANEL_PORT (this app, default 8725), M18_PANEL_TOKEN_TARGET (default Claude/M18/OAuth),
     M18_PANEL_CLAUDE (claude CLI path), M18_PANEL_MODEL (default claude-sonnet-5-5).
"""
from __future__ import annotations

import ctypes
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

PANEL = Path(__file__).resolve().parent
SKILL = PANEL.parents[2]                                   # .../skills/m18-macropad
HOME = Path(os.environ.get("M18_HOME") or Path.home() / "tools" / "m18").resolve()
CFG = HOME / "macros.yaml"
SETS = HOME / "sets"
STATE = HOME / "state" / "panel"
DAEMON_PORT = int(os.environ.get("M18_PORT", 8718))
PORT = int(os.environ.get("M18_PANEL_PORT", 8725))
TOKEN_TARGET = os.environ.get("M18_PANEL_TOKEN_TARGET", "Claude/M18/OAuth")
MODEL = os.environ.get("M18_PANEL_MODEL", "claude-sonnet-5-5")
TOKEN = secrets.token_urlsafe(32)                          # per start; embedded in the served page
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ORIGINS = {f"http://{h}" for h in HOSTS}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

sys.path.insert(0, str(HOME))
import yaml  # noqa: E402  (from the daemon's venv)
from m18lib import config as C  # noqa: E402

THEMES = {"hillside": "#4caf2a", "vaporjazz": "#ff4fa0", "wabisabi": "#c4682f", "plain": "#7c3aed"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
STATIC = {"/app.js": "application/javascript; charset=utf-8", "/app.css": "text/css; charset=utf-8"}
write_lock = threading.Lock()


# ------------------------------------------------------------------ helpers
def python_exe() -> str:
    exe = Path(sys.executable)
    con = exe.with_name("python.exe")
    return str(con if exe.name.lower() == "pythonw.exe" and con.exists() else exe)


def log(*a):
    STATE.mkdir(parents=True, exist_ok=True)
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    try:
        with open(STATE / "panel.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def daemon(method, path, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:{DAEMON_PORT}{path}", method=method, headers={"X-M18": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return None
    except Exception:
        return None


def render(yaml_path: Path, out_dir: Path, pages="home", validate_only=False, timeout=40) -> dict:
    """Validate + render in a child process (plugins and themes are imported there, not here)."""
    cmd = [python_exe(), str(PANEL / "render_set.py"), "--home", str(HOME), "--yaml", str(yaml_path),
           "--out-dir", str(out_dir), "--pages", pages]
    if validate_only:
        cmd.append("--validate-only")
    env = {**os.environ, "M18_HOME": str(HOME), "M18_SESSIONS_CLAUDE": str(PANEL / "no-cli"), "M18_SESSIONS_DRY": "1"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env,
                           creationflags=NO_WINDOW)
        return json.loads(r.stdout or "{}") or {"ok": False, "errors": [r.stderr[-400:]]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": ["rendering took too long"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "errors": [f"{type(e).__name__}: {e}"]}


def validate_text(text: str) -> tuple[dict | None, list[str]]:
    """The daemon's own loader, with HOME as the base folder (what `m18ctl validate` runs)."""
    try:
        return C.validate(yaml.safe_load(text), HOME), []
    except yaml.YAMLError as e:
        return None, [f"YAML syntax: {e}"]
    except C.ConfigError as e:
        return None, list(e.errors)
    except Exception as e:  # noqa: BLE001
        return None, [f"{type(e).__name__}: {e}"]


def read_cfg_text() -> str:
    return CFG.read_text(encoding="utf-8")


def atomic_write(path: Path, text: str):
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    for i in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1 * (i + 1))
    tmp.unlink(missing_ok=True)
    raise PermissionError(f"could not replace {path.name}")


def wait_daemon_load(before_seq_log: list | None, seconds=4.0) -> str:
    """After a write: did the daemon take it? 'loaded', 'rejected' or 'offline'."""
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(0.4)
        st = daemon("GET", "/status", timeout=2)
        if st is None:
            return "offline"
        if st.get("config_errors"):
            return "rejected"
        if (st.get("log") or []) != (before_seq_log or []) and any("config loaded" in x for x in (st.get("log") or [])[-4:]):
            return "loaded"
    return "pending"


# ------------------------------------------------------------------ undo (one level, survives restarts)
def undo_rec() -> dict | None:
    try:
        return json.loads((STATE / "undo.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def write_config(new_text: str, label: str, expect: str | None = None) -> dict:
    cfg, errs = validate_text(new_text)
    if errs:
        return {"ok": False, "errors": errs}
    with write_lock:
        cur = read_cfg_text()
        if expect is not None and cur != expect:
            return {"ok": False, "errors": ["macros.yaml changed since then, so Undo would lose that edit. "
                                            "Apply the Previous set instead."]}
        if cur == new_text:
            return {"ok": True, "unchanged": True}
        SETS.mkdir(parents=True, exist_ok=True)
        STATE.mkdir(parents=True, exist_ok=True)
        atomic_write(SETS / "_previous.yaml", cur)
        (SETS / "_previous.png").unlink(missing_ok=True)
        st = daemon("GET", "/status", timeout=2)
        atomic_write(STATE / "undo.json", json.dumps({"label": label, "before": cur, "after": new_text, "at": time.time()}))
        atomic_write(CFG, new_text)
    log("wrote macros.yaml:", label)
    bump()
    return {"ok": True, "daemon": wait_daemon_load((st or {}).get("log"))}


def do_undo() -> dict:
    rec = undo_rec()
    if not rec:
        return {"ok": False, "errors": ["Nothing to undo."]}
    res = write_config(rec["before"], f"undo {rec['label']}", expect=rec["after"])
    if res.get("ok"):
        (STATE / "undo.json").unlink(missing_ok=True)
    return res


# ------------------------------------------------------------------ sets
def set_path(name: str) -> Path:
    return SETS / f"{name}.yaml"


def seed_sets():
    """First start: the current layout as 'everyday', plus the starter sets shipped beside this file."""
    SETS.mkdir(parents=True, exist_ok=True)
    mark = SETS / ".seeded"
    if mark.exists():
        return
    if CFG.exists() and not set_path("everyday").exists():
        set_path("everyday").write_text(read_cfg_text(), encoding="utf-8", newline="")
    for f in (PANEL / "starters").glob("*.yaml"):
        if not set_path(f.stem).exists():
            shutil.copyfile(f, set_path(f.stem))
    mark.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def list_sets() -> list[dict]:
    out = []
    cur = read_cfg_text() if CFG.exists() else ""
    for f in sorted(SETS.glob("*.yaml"), key=lambda p: (p.stem.startswith("_"), p.stem)):
        try:
            txt = f.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({"name": f.stem, "mtime": int(f.stat().st_mtime), "active": txt == cur})
    return out


def thumb(name: str) -> Path | None:
    y = set_path(name)
    if not y.exists():
        return None
    png = SETS / f"{name}.png"
    if png.exists() and png.stat().st_mtime >= y.stat().st_mtime:
        return png
    tmp = STATE / "thumb" / name
    res = render(y, tmp, "home")
    f = next(iter(res.get("files", {}).values()), None)
    if not f:
        return None
    shutil.copyfile(f, png)
    shutil.rmtree(tmp, ignore_errors=True)
    return png


# ------------------------------------------------------------------ themes (edit only settings.theme and settings.rgb)
def _split_comment(line: str) -> tuple[str, str]:
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip(), line[i:]
    return line.rstrip(), ""


def themed_text(text: str, theme: str, cfg: dict) -> str:
    lines = text.splitlines(keepends=True)
    try:
        s0 = next(i for i, l in enumerate(lines) if re.match(r"^settings\s*:", l))
    except StopIteration:
        raise ValueError("macros.yaml has no settings: block")
    s1 = next((i for i in range(s0 + 1, len(lines)) if re.match(r"^\S", lines[i]) and not lines[i].startswith("#")), len(lines))
    indent = next((re.match(r"^(\s+)\S", lines[i]).group(1) for i in range(s0 + 1, s1)
                   if re.match(r"^\s+[A-Za-z_]", lines[i])), "  ")
    nl = "\r\n" if lines[s0].endswith("\r\n") else "\n"
    rgb = cfg["settings"].get("rgb")
    bri = rgb.get("brightness") if isinstance(rgb, dict) else None
    rgb_val = f'{{color: "{THEMES[theme]}", brightness: {bri}}}' if bri is not None else f'"{THEMES[theme]}"'
    want = {"theme": None if theme == "plain" else f"{{name: {theme}}}", "rgb": rgb_val}
    for field, val in want.items():
        idx = next((i for i in range(s0 + 1, s1) if re.match(rf"^\s+{field}\s*:", lines[i])), None)
        if idx is not None:
            _, comment = _split_comment(lines[idx].rstrip("\r\n"))
            if val is None:
                del lines[idx]
                s1 -= 1
            else:
                lines[idx] = f"{indent}{field}: {val}" + (f"   {comment}" if comment else "") + nl
        elif val is not None:
            at = next((i + 1 for i in range(s0 + 1, s1) if re.match(r"^\s+brightness\s*:", lines[i])), s0 + 1)
            lines.insert(at, f"{indent}{field}: {val}{nl}")
            s1 += 1
    return "".join(lines)


def current_theme(cfg: dict | None) -> str:
    t = (cfg or {}).get("settings", {}).get("theme")
    name = t if isinstance(t, str) else (t or {}).get("name") if isinstance(t, dict) else None
    return name or "plain"


# ------------------------------------------------------------------ key descriptions in plain words
MEDIA = {"play_pause": "Play / pause", "next": "Next track", "prev": "Previous track", "stop": "Stop playback",
         "vol_up": "Volume up", "vol_down": "Volume down", "mute": "Mute"}
PLUGIN_ACT = {"discord_voice.toggle_mute": "Mutes or unmutes Discord", "discord_voice.toggle_deaf": "Deafens Discord",
              "mixer.open": "Opens the volume mixer on the pad", "sessions.press": "Opens the agent session shown",
              "ring.codex_meter": "Shows the Codex limit on the ring", "ring.hold_progress": "Hold to confirm"}
PLUGIN_LIVE = {"sysstats.tile": "Shows CPU, RAM and GPU", "sessions.slot": "Shows one running agent session",
               "ring.status": "Counts working, waiting and finished agents; lights the ring",
               "discord_voice.status": "Shows Discord mute state", "ring.codex_face": "Shows the Codex limit used",
               "anim.orbit": "Animation"}


def _keys(s: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in str(s).split("+"))


def _clip(s, n=60) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def say(a) -> str:
    if a is None:
        return ""
    if isinstance(a, list):
        return ", then ".join(x for x in (say(x) for x in a) if x)
    k = next((x for x in a if x in C.ACTION_TYPES), None)
    v = a.get(k)
    if k == "hotkey" or k == "key":
        s = f"Presses {_keys(v)}"
    elif k == "type":
        s = f"Types “{_clip(v, 40)}”"
    elif k == "paste":
        s = f"Pastes “{_clip(v, 40)}”"
    elif k == "run":
        first = v[0] if isinstance(v, list) else str(v).split(" --")[0]
        s = "Starts " + Path(str(first).strip('"')).name
    elif k == "shell":
        s = f"Runs “{_clip(v, 40)}”"
    elif k == "open":
        m = re.match(r"^https?://([^/]+)", str(v))
        s = f"Opens {m.group(1) if m else _clip(v, 50)}"
    elif k == "media":
        s = MEDIA.get(v, f"Media {v}")
    elif k == "page":
        s = {"back": "Goes back a page", "home": "Goes to the home page"}.get(v, f"Opens page {v}")
    elif k == "delay":
        s = f"Waits {v} ms"
    elif k == "countdown":
        s = f"Counts down {v} s"
    elif k == "plugin":
        s = PLUGIN_ACT.get(v, f"Runs {v}")
    elif k == "brightness":
        s = f"Brightness {v}" if str(v)[:1] in "+-" else f"Brightness to {v}"
    elif k == "rgb":
        s = "Ring colour " + (v.get("color") if isinstance(v, dict) else str(v))
    elif k == "notify":
        s = "Writes a note to the log"
    elif k == "dark":
        s = "Lights out; press again to wake"
    else:
        s = json.dumps(a)[:60]
    if a.get("repeat"):
        s += f" ×{a['repeat']}"
    return s


def describe_page(page: str) -> dict:
    cfg = C.load(CFG)
    if page not in cfg["pages"]:
        page = cfg["settings"]["home"]
    rk = C.resolved_keys(cfg, page)
    own = cfg["pages"][page]["keys"]
    keys = {}
    for k in C.ALL_KEYS:
        name = f"b{k - 15}" if k > 15 else str(k)
        kd = rk.get(k)
        if not kd:
            keys[name] = {"name": name, "empty": True}
            continue
        live = kd.get("live")
        title = kd.get("label") or kd.get("glyph") or (PLUGIN_LIVE.get(live["plugin"], live["plugin"]) if live else "")
        d = {"name": name, "title": str(title) or f"Key {name}", "tap": say(kd.get("action")),
             "hold": say(kd.get("hold")), "note": kd.get("comment", ""),
             "live": (PLUGIN_LIVE.get(live["plugin"], f"Live face from {live['plugin']}") +
                      f", every {live.get('interval', 2)} s") if live else "",
             "inherited": k not in own and bool(cfg["pages"][page].get("inherit")),
             "physical_only": bool(kd.get("physical_only"))}
        if kd.get("states"):
            d["tap"] = "Toggles: " + " / ".join(say(s.get("action")) or "–" for s in kd["states"])
        keys[name] = d
    return {"page": page, "keys": keys}


# ------------------------------------------------------------------ live state: version bumps on any pad event
_v = {"n": 1, "status": None}


def bump():
    _v["n"] += 1


def watch_daemon():
    seq = None
    while True:
        st = daemon("GET", "/status", timeout=3)
        sig = None if st is None else (st.get("page"), tuple(st.get("pages") or []), bool(st.get("config_errors")), st.get("connected"))
        if sig != (_v["status"] or {}).get("_sig"):
            bump()
        _v["status"] = None if st is None else {"_sig": sig, "page": st.get("page"), "pages": st.get("pages") or [],
                                                "connected": st.get("connected"), "dry_run": st.get("dry_run"),
                                                "errors": st.get("config_errors")}
        if st is None:
            seq = None
            time.sleep(3)
            continue
        if seq is None:
            seq = st.get("events_seq", 0)
        ev = daemon("GET", f"/events?since={seq}&timeout=8", timeout=12)
        if ev and "seq" in ev:
            if ev.get("events") or ev.get("reset"):
                bump()
            seq = ev["seq"]
        else:
            time.sleep(1)


_pad_cache = {}


def pad_png(page: str | None) -> bytes | None:
    st = _v["status"]
    if st and (not page or page in st["pages"]):
        # the daemon renders any page without touching the pad (live keys show their last face)
        r = daemon("GET", "/preview" + (f"?page={quote(page)}" if page else ""), timeout=15)
        if r and r.get("path") and Path(r["path"]).exists():
            return Path(r["path"]).read_bytes()
    # the daemon is down or does not know the page yet: render the file offline (cached per file version)
    cfg = C.load(CFG)
    page = page if page in cfg["pages"] else cfg["settings"]["home"]
    key = (CFG.stat().st_mtime_ns, page)
    if key in _pad_cache:
        return _pad_cache[key]
    out = STATE / "pages"
    res = render(CFG, out, page)
    f = res.get("files", {}).get(page)
    if not f:
        return None
    data = Path(f).read_bytes()
    _pad_cache.clear()
    _pad_cache[key] = data
    return data


# ------------------------------------------------------------------ request agent
SYSTEM = f"""You are the M18 panel's helper. The user types a request into a small local web page that drives his VSD M18 macro pad. He sees only your short replies and pictures of the pad, never a terminal, a diff or a permission prompt.
- Follow the m18-macropad skill: read {SKILL}\\SKILL.md first, then the references it points to as needed (config.md, plugins.md, the active theme's BRIEF.md under aesthetics/).
- The pad's folder is your working directory ({HOME}). Edit only macros.yaml, files in plugins/ and files in sets/ there. Never edit m18d.py, m18lib/, themes/, the skill folder or anything else.
- Edit macros.yaml surgically and keep the user's comments. Never silently overwrite an existing key: use a free slot unless he names one (if he names an occupied key, replacing it is what he asked).
- Check your work: run `m18ctl.cmd validate` until it says OK, then `m18ctl.cmd preview` and look at the PNG it prints. Both commands are in the working directory and already point at the right config and daemon (M18_CONFIG, M18_PORT are set).
- Never stop, start, restart or kill the daemon (no `m18ctl stop`/`start`, no taskkill of python). Config and plugin edits hot-reload by themselves.
- Never `m18ctl press` a key whose effect is disruptive; use `m18ctl.cmd dryrun N` instead. Opening a URL is harmless.
- Don't ask for approval. If the request is truly ambiguous, make the most sensible choice and say what you chose. Pickers don't work here.
- Finish with one or two plain sentences: which key or page changed and what it does, plus any one-time step the user must do himself. No file paths or build steps.
- Don't read or print credentials. Keep the work to a few minutes."""

ALLOWED = ["Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Bash", "PowerShell", "TodoWrite", "WebSearch", "WebFetch"]
req = {"id": None, "state": "idle", "events": [], "child": None, "result": None}
req_lock = threading.Lock()


def cred_token() -> str | None:
    """Generic credential from Windows Credential Manager. Only ever placed in the child's env."""
    if os.name != "nt":
        return None

    class CRED(ctypes.Structure):
        _fields_ = [("Flags", ctypes.c_ulong), ("Type", ctypes.c_ulong), ("TargetName", ctypes.c_wchar_p),
                    ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_ulonglong),
                    ("Size", ctypes.c_ulong), ("Blob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", ctypes.c_ulong), ("Count", ctypes.c_ulong), ("Attrs", ctypes.c_void_p),
                    ("Alias", ctypes.c_wchar_p), ("User", ctypes.c_wchar_p)]
    adv = ctypes.windll.advapi32
    pc = ctypes.POINTER(CRED)()
    adv.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.POINTER(CRED))]
    if not adv.CredReadW(TOKEN_TARGET, 1, 0, ctypes.byref(pc)):
        return None
    try:
        raw = bytes(bytearray(pc.contents.Blob[: pc.contents.Size]))
    finally:
        adv.CredFree(pc)
    try:
        txt = raw.decode("utf-16-le") if len(raw) % 2 == 0 and b"\x00" in raw else raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return txt.strip().strip("\x00") or None


def claude_exe() -> str | None:
    return (os.environ.get("M18_PANEL_CLAUDE") or shutil.which("claude")
            or next((str(p) for p in [Path.home() / ".local" / "bin" / "claude.exe"] if p.exists()), None))


def tool_label(b: dict) -> str | None:
    i = b.get("input") or {}
    short = lambda v: _clip(str(v or "").replace("\\", "/").replace(str(HOME).replace("\\", "/") + "/", "")
                            .replace(str(SKILL).replace("\\", "/") + "/", "skill/"), 80)
    n = b.get("name")
    if n in ("Edit", "MultiEdit"):
        return f"Edited {short(i.get('file_path'))}"
    if n == "Write":
        return f"Wrote {short(i.get('file_path'))}"
    if n == "Read":
        return f"Read {short(i.get('file_path'))}"
    if n == "Grep":
        return f"Searched for {short(i.get('pattern'))}"
    if n == "Glob":
        return f"Looked for {short(i.get('pattern'))}"
    if n in ("Bash", "PowerShell"):
        return short(i.get("description") or i.get("command"))
    if n == "WebSearch":
        return f"Searched the web: {short(i.get('query'))}"
    if n == "WebFetch":
        return f"Read {short(i.get('url'))}"
    if n == "TodoWrite":
        return None
    return n


def ev(kind: str, text: str):
    with req_lock:
        req["events"].append({"i": len(req["events"]), "t": kind, "text": text})


def plugin_stamp() -> dict:
    d = HOME / "plugins"
    return {p.name: p.stat().st_mtime_ns for p in d.glob("*.py")} if d.exists() else {}


def changed_pages(a: dict | None, b: dict) -> list[str]:
    out = []
    for p in b["pages"]:
        if not a or p not in a["pages"]:
            out.append(p)
            continue
        ka = json.dumps(C.resolved_keys(a, p), sort_keys=True, default=str)
        kb = json.dumps(C.resolved_keys(b, p), sort_keys=True, default=str)
        sa = {k: v for k, v in a["pages"][p].items() if k != "keys"}
        sb = {k: v for k, v in b["pages"][p].items() if k != "keys"}
        if ka != kb or json.dumps(sa, sort_keys=True, default=str) != json.dumps(sb, sort_keys=True, default=str):
            out.append(p)
    if not out and json.dumps(a and a["settings"], sort_keys=True, default=str) != json.dumps(b["settings"], sort_keys=True, default=str):
        out.append(b["settings"]["home"])
    return out


def run_request(rid: str, text: str):
    rdir = STATE / "requests" / rid
    rdir.mkdir(parents=True, exist_ok=True)
    before = read_cfg_text()
    (rdir / "before.yaml").write_text(before, encoding="utf-8", newline="")
    plugins_before = plugin_stamp()
    t0 = time.time()
    exe = claude_exe()
    if not exe:
        return finish(rid, "error", {"error": "The claude CLI wasn't found on this PC."})
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
                        "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_OAUTH_TOKEN")}
    tok = cred_token()
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    env.update({"M18_HOME": str(HOME), "M18_CONFIG": str(CFG), "M18_PORT": str(DAEMON_PORT)})
    argv = [exe, "-p", "--model", MODEL, "--effort", "high", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--strict-mcp-config", "--permission-mode", "dontAsk",
            "--permission-prompts", "none", "--no-session-persistence", "--add-dir", str(SKILL),
            "--allowedTools", *ALLOWED, "--append-system-prompt", SYSTEM]
    log(f"request {rid}: starting {MODEL} (token {'from Credential Manager' if tok else 'missing: CLI login'})")
    ev("status", "Reading your request")
    try:
        child = subprocess.Popen(argv, cwd=str(HOME), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    except OSError as e:
        return finish(rid, "error", {"error": f"Couldn't start Claude: {e}"})
    del tok, env
    with req_lock:
        req["child"] = child
    err_tail = []
    threading.Thread(target=lambda: err_tail.extend(child.stderr.read().decode("utf-8", "replace").splitlines()[-20:]),
                     daemon=True).start()
    child.stdin.write(text.encode("utf-8"))
    child.stdin.close()
    result = None
    for raw in child.stdout:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if e.get("type") == "assistant":
            for b in (e.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    ev("text", b["text"].strip())
                elif b.get("type") == "tool_use":
                    lab = tool_label(b)
                    if lab:
                        ev("tool", lab)
        elif e.get("type") == "system" and e.get("subtype") == "permission_denied":
            ev("tool", f"Blocked: {e.get('tool_name', 'a tool')}")
        elif e.get("type") == "result":
            result = e
    child.wait()
    with req_lock:
        stopped = req["state"] == "stopping"
        req["child"] = None
    info = {"seconds": round(time.time() - t0, 1)}
    if result:
        info.update({"cost_usd": result.get("total_cost_usd"), "turns": result.get("num_turns"),
                     "duration_ms": result.get("duration_ms"), "reply": (result.get("result") or "")[:1500]})
    after = read_cfg_text()
    (rdir / "after.yaml").write_text(after, encoding="utf-8", newline="")
    info["plugins_changed"] = sorted(n for n, m in plugin_stamp().items() if plugins_before.get(n) != m)
    if after != before:
        cfg_a, errs = validate_text(after)
        if errs:
            with write_lock:
                atomic_write(CFG, before)
            info["error"] = "The edit didn't validate, so macros.yaml was put back: " + "; ".join(errs[:3])
            ev("status", "Put macros.yaml back (the edit didn't validate)")
            return finish(rid, "error", info)
        cfg_b, _ = validate_text(before)
        pages = changed_pages(cfg_b, cfg_a)
        info["pages"] = pages
        info["keys"] = {}
        for p in pages:                                # which keys moved, for the outline on the After picture
            ka = C.resolved_keys(cfg_b, p) if cfg_b and p in cfg_b["pages"] else {}
            kb = C.resolved_keys(cfg_a, p)
            info["keys"][p] = [k for k in C.ALL_KEYS if json.dumps(ka.get(k), sort_keys=True, default=str)
                               != json.dumps(kb.get(k), sort_keys=True, default=str)]
        for side, y, cfg in (("before", rdir / "before.yaml", cfg_b), ("after", rdir / "after.yaml", cfg_a)):
            want = [p for p in pages if cfg and p in cfg["pages"]]
            if want:
                render(y, rdir / side, ",".join(want))
        SETS.mkdir(parents=True, exist_ok=True)
        atomic_write(SETS / "_previous.yaml", before)
        (SETS / "_previous.png").unlink(missing_ok=True)
        atomic_write(STATE / "undo.json", json.dumps({"label": f"request {rid}", "before": before, "after": after, "at": time.time()}))
        info["undo"] = True
        bump()
    if stopped:
        return finish(rid, "stopped", info)
    if result is None or result.get("is_error"):
        detail = (result or {}).get("result") or "\n".join(err_tail[-5:]) or "Claude stopped without a result."
        if re.search(r"failed to authenticate|not logged in|/login|invalid.*token", detail, re.I):
            detail = "The panel's Claude login has run out. Renew it with panel-login.ps1 (see panel.md), then ask again."
        info["error"] = _clip(detail, 600)
        return finish(rid, "error", info)
    finish(rid, "done", info)


def finish(rid, state, info):
    log(f"request {rid}: {state} {json.dumps({k: v for k, v in info.items() if k != 'reply'})}")
    with req_lock:
        if req["id"] == rid:
            req["state"] = state
            req["result"] = info
    bump()


# ------------------------------------------------------------------ http
class H(BaseHTTPRequestHandler):
    server_version = "m18panel"
    sys_version = ""

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode())

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in HOSTS

    def do_OPTIONS(self):                     # no CORS, ever
        self._json({"error": "forbidden"}, 403)

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "forbidden"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            return self._get(u.path, q)
        except Exception as e:  # noqa: BLE001
            log("GET", u.path, "failed:", repr(e))
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _get(self, path, q):
        if path in ("/", "/index.html"):
            html = (PANEL / "index.html").read_text(encoding="utf-8").replace("{{TOKEN}}", TOKEN)
            csp = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
                   "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            return self._send(200, html.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
        if path in STATIC:
            return self._send(200, (PANEL / path[1:]).read_bytes(), STATIC[path])
        if path == "/api/state":
            cfg = None
            try:
                cfg = C.load(CFG)
            except Exception:
                pass
            with req_lock:
                r = {"id": req["id"], "state": req["state"], "n": len(req["events"])}
            u = undo_rec()
            dmn = {k: v for k, v in _v["status"].items() if k != "_sig"} if _v["status"] else None
            return self._json({"v": _v["n"], "daemon": dmn, "theme": current_theme(cfg),
                               "pages": list(cfg["pages"]) if cfg else [], "home": cfg["settings"]["home"] if cfg else None,
                               "sets": list_sets(), "themes": THEMES, "request": r,
                               "undo": {"label": u["label"]} if u and CFG.exists() and read_cfg_text() == u["after"] else None})
        if path == "/api/pad.png":
            data = pad_png(q.get("page") or None)
            return self._send(200, data, "image/png") if data else self._json({"error": "no picture"}, 503)
        if path == "/api/keys":
            return self._json(describe_page(q.get("page") or ""))
        if path == "/api/set.png":
            name = q.get("name", "")
            p = thumb(name) if NAME_RE.match(name.lstrip("_")) else None
            return self._send(200, p.read_bytes(), "image/png") if p else self._json({"error": "no set"}, 404)
        if path == "/api/request":
            since = int(q.get("since", 0) or 0)
            with req_lock:
                return self._json({"id": req["id"], "state": req["state"], "events": req["events"][since:],
                                   "result": req["result"]})
        if path == "/api/request.png":
            rid, side, page = q.get("id", ""), q.get("side", ""), q.get("page", "")
            if not re.match(r"^\d{8}-\d{6}(-[0-9a-f]{4})?$", rid) or side not in ("before", "after") or not NAME_RE.match(page.lower()):
                return self._json({"error": "bad request"}, 400)
            p = STATE / "requests" / rid / side / f"{page}.png"
            return self._send(200, p.read_bytes(), "image/png") if p.exists() else self._json({"error": "none"}, 404)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        # Same-origin check plus the per-start token from the served page: another site in the browser
        # can neither read the token nor send the custom header without a CORS preflight (refused above).
        if not self._host_ok() or self.headers.get("Origin") not in ORIGINS or \
                not hmac.compare_digest(self.headers.get("X-Panel-Token", ""), TOKEN):
            log("refused POST", self.path, "origin=", self.headers.get("Origin"))
            return self._json({"error": "forbidden"}, 403)
        try:
            n = max(0, int(self.headers.get("Content-Length") or 0))
            body = json.loads(self.rfile.read(min(n, 65536)) or b"{}") if n else {}
            return self._post(urlparse(self.path).path, body if isinstance(body, dict) else {})
        except Exception as e:  # noqa: BLE001
            log("POST", self.path, "failed:", repr(e))
            return self._json({"ok": False, "errors": [f"{type(e).__name__}: {e}"]}, 500)

    def _post(self, path, b):
        with req_lock:
            agent_busy = req["state"] in ("running", "stopping")
        if agent_busy and path in ("/api/apply", "/api/theme", "/api/undo"):
            return self._json({"ok": False, "errors": ["Wait for the request to finish (or Stop it)."]}, 409)
        if path == "/api/apply":
            name = str(b.get("name", ""))
            if not NAME_RE.match(name.lstrip("_")) or not set_path(name).exists():
                return self._json({"ok": False, "errors": ["No such set."]}, 404)
            return self._json(write_config(set_path(name).read_text(encoding="utf-8"), f"set {name}"))
        if path == "/api/save":
            name = str(b.get("name", "")).strip().lower().replace(" ", "-")
            if not NAME_RE.match(name):
                return self._json({"ok": False, "errors": ["Use letters, digits, - or _ (up to 32)."]}, 400)
            SETS.mkdir(parents=True, exist_ok=True)
            atomic_write(set_path(name), read_cfg_text())
            (SETS / f"{name}.png").unlink(missing_ok=True)
            bump()
            return self._json({"ok": True, "name": name})
        if path == "/api/theme":
            name = str(b.get("name", ""))
            if name not in THEMES:
                return self._json({"ok": False, "errors": ["Unknown theme."]}, 400)
            if name != "plain" and not (HOME / "themes" / name / "theme.py").exists():
                return self._json({"ok": False, "errors": [f"Theme {name} isn't installed in themes/."]}, 400)
            text = read_cfg_text()
            cfg, errs = validate_text(text)
            if errs:
                return self._json({"ok": False, "errors": ["macros.yaml doesn't validate right now."] + errs[:3]})
            return self._json(write_config(themed_text(text, name, cfg), f"theme {name}"))
        if path == "/api/undo":
            return self._json(do_undo())
        if path == "/api/request":
            text = str(b.get("text", "")).strip()
            if not text or len(text) > 2000:
                return self._json({"ok": False, "errors": ["Type a request (up to 2000 characters)."]}, 400)
            with req_lock:
                if req["state"] in ("running", "stopping"):
                    return self._json({"ok": False, "errors": ["One request at a time."]}, 409)
                rid = time.strftime("%Y%m%d-%H%M%S")
                if (STATE / "requests" / rid).exists() or rid == req["id"]:
                    rid += f"-{secrets.token_hex(2)}"
                req.update({"id": rid, "state": "running", "events": [], "child": None, "result": None})
            threading.Thread(target=self._run_safe, args=(rid, text), daemon=True).start()
            bump()
            return self._json({"ok": True, "id": rid})
        if path == "/api/stop":
            with req_lock:
                child = req["child"]
                if req["state"] == "running":
                    req["state"] = "stopping"
            if child and child.poll() is None:
                subprocess.run(["taskkill.exe", "/pid", str(child.pid), "/t", "/f"], capture_output=True,
                               creationflags=NO_WINDOW)
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    @staticmethod
    def _run_safe(rid, text):
        try:
            run_request(rid, text)
        except Exception as e:  # noqa: BLE001
            finish(rid, "error", {"error": f"{type(e).__name__}: {e}"})


def main():
    seed_sets()
    threading.Thread(target=watch_daemon, daemon=True).start()
    ThreadingHTTPServer.allow_reuse_address = False   # on Windows SO_REUSEADDR would let a 2nd copy share the port
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    log(f"panel on http://127.0.0.1:{PORT} home={HOME} daemon={DAEMON_PORT} pid={os.getpid()}")
    (STATE / "panel.pid").write_text(str(os.getpid()), encoding="utf-8")
    print(f"M18 panel: http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
