"""Claude limit meter: Claude's 5-hour and weekly usage on one key, a countdown after a limit is hit, green after it resets.

    12: {live: {plugin: claude_limit.face, interval: 15},
         action: {plugin: claude_limit.ring},
         hold: {plugin: claude_limit.resume}}

Local sources only (no web call, no token, no cookie; see references/claude-limit.md):
- Limit hits: Claude Code transcripts (~/.claude/projects/*/<session>.jsonl). When a limit stops a turn, Claude Code
  writes a synthetic assistant record with apiError "usage_limit_reached" and quotaLimits {status: "rejected",
  rateLimitType: five_hour | seven_day, resetsAt: epoch}. Exact, but only written when a limit is hit.
- Use: the Claude desktop app's plan-usage-history.json (samples {t, org, u: {fh: 5-hour %, sd: 7-day %}}). Exact
  when written, but written irregularly (minutes to days apart), so the fills are "as of" that sample: bars from a
  sample older than STALE_S are drawn grey, and a window that reset after the sample shows no bar.

Faces: normal = two bars (5H, WK); hit = countdown to resetsAt (h:mm, or days); reset = green until held (resume)
or RESET_KEEP after the reset. Press = the fill on the LED ring. Hold after a reset = "It has reset now. Please
continue." on the clipboard and the stopped session opened; with `args: {send: true}` it is sent for real
(m18lib/sendtext.py, guarded; see references/claude-limit.md).
"""
from __future__ import annotations

import glob
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

WINDOWS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}
SHORT = {"five_hour": "5H", "seven_day": "WK"}
SCAN_DAYS = 8                 # transcripts modified in the last 8 days are read for limit hits
TAIL = 1024 * 1024            # first read of a transcript: its last 1 MB; later reads: only the bytes appended since
REFRESH = 30.0                # seconds between rescans
STALE_S = 60 * 60             # a usage sample older than this is drawn grey (still shown)
RESET_KEEP = 4 * 3600         # the green "reset" face stays this long after the reset unless held
RESUME_TEXT = "It has reset now. Please continue."
_MARK = b'"usage_limit_reached"'


# ------------------------------------------------------------------ paths
def _home() -> Path:
    return Path.home()


def state_dir(ctx=None) -> Path:
    m = os.environ.get("M18_HOME")
    if m:
        return Path(m) / "state"
    return Path(getattr(ctx, "config_dir", None) or _home() / "tools" / "m18") / "state"


def projects_root() -> Path:
    return Path(os.environ.get("M18_LIMIT_PROJECTS") or _home() / ".claude" / "projects")


def history_files() -> list:
    env = os.environ.get("M18_LIMIT_HISTORY")
    if env is not None:
        return [Path(x) for x in env.split(os.pathsep) if x]
    local = Path(os.environ.get("LOCALAPPDATA") or _home() / "AppData" / "Local")
    out = glob.glob(str(local / "Claude-Data" / "*" / "plan-usage-history.json"))
    out += glob.glob(str(_home() / ".claude-instances" / "*" / "plan-usage-history.json"))
    return [Path(x) for x in out]


def app_records_glob() -> str:
    env = os.environ.get("M18_LIMIT_APP")
    local = Path(os.environ.get("LOCALAPPDATA") or _home() / "AppData" / "Local")
    base = Path(env) if env else local / "Claude-Data"
    return str(base / "*" / "claude-code-sessions" / "*" / "*" / "local_*.json")


# ------------------------------------------------------------------ readers (pure)
def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def parse_limit_line(line: bytes):
    """One transcript line -> {t, type, resets_at, sid} for a rejected usage limit, else None."""
    if _MARK not in line:
        return None
    try:
        o = json.loads(line)
    except ValueError:
        return None
    if o.get("apiError") != "usage_limit_reached":
        return None
    q = o.get("quotaLimits") or ((o.get("apiErrorParams") or {}).get("rate_limit_info")) or {}
    typ, rs = q.get("rateLimitType"), q.get("resetsAt")
    if q.get("status") != "rejected" or typ not in WINDOWS or not isinstance(rs, (int, float)):
        return None
    return {"t": _ts(o.get("timestamp")) or 0.0, "type": typ, "resets_at": float(rs), "sid": o.get("sessionId") or ""}


_EV: dict = {"files": {}, "t": 0.0, "events": [], "root": None}
_EVLOCK = threading.Lock()


def limit_events(root: Path | None = None, now: float | None = None, force=False) -> list:
    """Limit-hit events from transcripts modified in the last SCAN_DAYS, read incrementally, cached REFRESH s."""
    root = Path(root or projects_root())
    now = now or time.time()
    with _EVLOCK:
        if not force and _EV["root"] == str(root) and time.time() - _EV["t"] < REFRESH:
            return _EV["events"]
        if _EV["root"] != str(root):
            _EV.update(files={}, root=str(root))
        files = _EV["files"]
        seen = set()
        for f in glob.glob(str(root / "*" / "*.jsonl")):
            try:
                st = os.stat(f)
            except OSError:
                continue
            if now - st.st_mtime > SCAN_DAYS * 86400:
                continue
            seen.add(f)
            c = files.get(f)
            if c and c["size"] == st.st_size and c.get("mtime") == st.st_mtime:
                continue
            start = c["size"] if c and st.st_size > c["size"] else max(0, st.st_size - TAIL)
            evs = list(c["events"]) if c and st.st_size > c["size"] else []
            try:
                with open(f, "rb") as h:
                    h.seek(start)
                    data = h.read()
            except OSError:
                continue
            lines = data.split(b"\n")
            if start > 0 and not (c and st.st_size > c["size"]):
                lines = lines[1:]                     # the first line of a cold tail read is cut
            for ln in lines:
                e = parse_limit_line(ln)
                if e:
                    evs.append(e)
            files[f] = {"size": st.st_size, "mtime": st.st_mtime, "events": evs[-50:]}
        for f in [f for f in files if f not in seen]:
            del files[f]
        out = sorted((e for c in files.values() for e in c["events"]), key=lambda e: e["t"])
        _EV.update(t=time.time(), events=out)
        return out


def latest_sample(files=None):
    """Newest plan-usage-history sample over every data dir: {t (epoch s), fh, sd, org}, or None."""
    best = None
    for p in files if files is not None else history_files():
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for s in (d.get("samples") or []) if isinstance(d, dict) else []:
            try:
                t = float(s["t"]) / 1000.0
                u = s.get("u") or {}
                cand = {"t": t, "fh": u.get("fh"), "sd": u.get("sd"), "org": s.get("org")}
            except (KeyError, TypeError, ValueError):
                continue
            if best is None or t > best["t"]:
                best = cand
    return best


def meter(events, sample, now, acked=()) -> dict:
    """The key's state from limit events and the newest usage sample.
    -> {state: hit|reset|normal, type, resets_at, sids, fh, sd, as_of, stale}"""
    acked = set(round(a) for a in acked)
    hits = [e for e in events if e["resets_at"] > now]
    if hits:
        top = max(hits, key=lambda e: (e["resets_at"], e["t"]))
        same = [e for e in hits if abs(e["resets_at"] - top["resets_at"]) < 120]
        return dict(state="hit", type=top["type"], resets_at=top["resets_at"],
                    sids=_sids(same), fh=100 if top["type"] == "five_hour" else None,
                    sd=100 if top["type"] == "seven_day" else None, as_of=top["t"], stale=False)
    done = [e for e in events if now - RESET_KEEP < e["resets_at"] <= now and round(e["resets_at"]) not in acked]
    if done:
        top = max(done, key=lambda e: (e["resets_at"], e["t"]))
        same = [e for e in done if abs(e["resets_at"] - top["resets_at"]) < 120]
        return dict(state="reset", type=top["type"], resets_at=top["resets_at"], sids=_sids(same),
                    fh=None, sd=None, as_of=top["resets_at"], stale=False)
    fh = sd = None
    as_of = None
    if sample:
        as_of = sample["t"]
        age = now - sample["t"]
        reset_after = {typ: any(e["type"] == typ and sample["t"] < e["resets_at"] <= now for e in events)
                       for typ in WINDOWS}
        if age < WINDOWS["five_hour"] and not reset_after["five_hour"] and sample.get("fh") is not None:
            fh = float(sample["fh"])
        if age < WINDOWS["seven_day"] and not reset_after["seven_day"] and sample.get("sd") is not None:
            sd = float(sample["sd"])
    return dict(state="normal", type=None, resets_at=None, sids=[], fh=fh, sd=sd, as_of=as_of,
                stale=as_of is None or now - as_of > STALE_S)


def _sids(evs) -> list:
    out = []
    for e in sorted(evs, key=lambda e: -e["t"]):
        if e["sid"] and e["sid"] not in out:
            out.append(e["sid"])
    return out


def countdown(secs) -> str:
    """h:mm under a day (0:07, 4:52), else whole days (3D)."""
    secs = max(0, int(secs))
    if secs >= 86400:
        return f"{secs // 86400}D"
    return f"{secs // 3600}:{secs // 60 % 60:02d}"


# ------------------------------------------------------------------ state file (acks + last reading for other tools)
def _load(sd: Path) -> dict:
    try:
        return json.loads((sd / "claude-limit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(sd: Path, d: dict):
    try:
        sd.mkdir(parents=True, exist_ok=True)
        tmp = sd / f"claude-limit.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        os.replace(tmp, sd / "claude-limit.json")
    except OSError:
        pass


def current(ctx=None, now=None) -> dict:
    now = now or time.time()
    sd = state_dir(ctx)
    st = _load(sd)
    m = meter(limit_events(now=now), latest_sample(), now, st.get("acked") or [])
    row = {k: m[k] for k in ("state", "type", "resets_at", "fh", "sd", "as_of", "stale")}
    if st.get("last") != row:
        st["last"] = row
        st["updated"] = round(now)
        _save(sd, st)
    return m


# ------------------------------------------------------------------ drawing (64 px, whole pixels)
_F = {
    "A": ".###.|#...#|#...#|#####|#...#|#...#|#...#", "C": ".###.|#...#|#....|#....|#....|#...#|.###.",
    "D": "####.|#...#|#...#|#...#|#...#|#...#|####.", "E": "#####|#....|#....|####.|#....|#....|#####",
    "H": "#...#|#...#|#...#|#####|#...#|#...#|#...#", "I": "###|.#.|.#.|.#.|.#.|.#.|###",
    "K": "#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#", "L": "#....|#....|#....|#....|#....|#....|#####",
    "M": "#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#", "N": "#...#|##..#|#.#.#|#..##|#...#|#...#|#...#",
    "O": ".###.|#...#|#...#|#...#|#...#|#...#|.###.", "R": "####.|#...#|#...#|####.|#.#..|#..#.|#...#",
    "S": ".####|#....|#....|.###.|....#|....#|####.", "T": "#####|..#..|..#..|..#..|..#..|..#..|..#..",
    "U": "#...#|#...#|#...#|#...#|#...#|#...#|.###.", "W": "#...#|#...#|#...#|#.#.#|#.#.#|##.##|#...#",
    "0": ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.", "1": "..#..|.##..|..#..|..#..|..#..|..#..|#####",
    "2": ".###.|#...#|....#|..##.|.#...|#....|#####", "3": ".###.|#...#|....#|..##.|....#|#...#|.###.",
    "4": "...##|..#.#|.#..#|#...#|#####|....#|....#", "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.", "7": "#####|....#|...#.|..#..|..#..|..#..|..#..",
    "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.", "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    ":": ".|.|#|.|#|.|.", "-": "....|....|....|####|....|....|....", "?": ".###.|#...#|....#|...#.|..#..|.....|..#..",
}
_G = {c: v.split("|") for c, v in _F.items()}


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def text_w(s, sc=1):
    return max(0, sum(((len(_G[c][0]) if c in _G else 3) + 1) * sc for c in s.upper()) - sc)


def draw_text(d, x, y, s, col, sc=1, shadow=True):
    col = _rgb(col) if isinstance(col, str) else col
    sh = tuple(c // 4 for c in col)
    for off, c in (((sc, sh),) if shadow else ()) + ((0, col),):
        cx = x
        for ch in s.upper():
            g = _G.get(ch)
            if not g:
                cx += 4 * sc
                continue
            for ry, row in enumerate(g):
                for rx, b in enumerate(row):
                    if b == "#":
                        px, py = cx + rx * sc + off, y + ry * sc + off
                        d.rectangle((px, py, px + sc - 1, py + sc - 1), fill=c)
            cx += (len(g[0]) + 1) * sc


GOOD, WARN, GREY, TRACK = "#55dd55", "#ffaa22", "#8a8a96", "#1a1a20"
SLOT = {"normal": "#2b2b36", "hit": "#2b2b36", "reset": "#2f8a3a"}


def _slot(bg):
    img = Image.new("RGB", (64, 64), bg)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 63, 1), fill="#373737")
    d.rectangle((0, 0, 1, 63), fill="#373737")
    d.rectangle((0, 62, 63, 63), fill="#ffffff")
    d.rectangle((62, 0, 63, 63), fill="#ffffff")
    return img, d


def _plate(d, label, col):
    """Tooltip plate along the bottom (same as the session keys)."""
    w = text_w(label) + 6
    x0 = (64 - w) // 2
    d.rectangle((x0, 52, x0 + w - 1, 62), fill="#100010", outline="#3a00a8")
    for cx, cy in ((x0, 52), (x0 + w - 1, 52), (x0, 62), (x0 + w - 1, 62)):
        d.point((cx, cy), fill="#100010")
    draw_text(d, x0 + 3, 54, label, col)


def _bar(d, y, name, pct, stale):
    """Label + 10 pips of 4 px; None = an empty track with a '?'."""
    draw_text(d, 4, y + 1, name, "#ffffff")
    x0 = 18
    for i in range(10):
        x = x0 + i * 4
        d.rectangle((x, y, x + 2, y + 8), fill=TRACK)
    if pct is None:
        draw_text(d, x0 + 16, y + 1, "?", GREY, shadow=False)
        return
    n = max(1 if pct > 0 else 0, min(10, round(pct / 10)))
    col = GREY if stale else (GOOD if pct < 70 else WARN)
    for i in range(n):
        x = x0 + i * 4
        d.rectangle((x, y, x + 2, y + 8), fill=col)


def render(m, now, themed=False):
    st = m["state"]
    if st == "hit":
        img, d = _slot(SLOT["hit"])
        tag = SHORT[m["type"]]
        draw_text(d, (64 - text_w(tag)) // 2, 6, tag, WARN)
        cd = countdown(m["resets_at"] - now)
        sc = 2 if text_w(cd, 2) <= 58 else 1
        draw_text(d, (64 - text_w(cd, sc)) // 2, 20, cd, "#ffffff", sc)
        _plate(d, "LIMIT", WARN)
    elif st == "reset":
        img, d = _slot(SLOT["reset"])
        tag = SHORT[m["type"]]
        draw_text(d, (64 - text_w(tag)) // 2, 6, tag, "#ffffff")
        _check(d, 20, 18)
        _plate(d, "RESET", GOOD)
    else:
        img, d = _slot(SLOT["normal"])
        _bar(d, 8, "5H", m["fh"], m["stale"])
        _bar(d, 26, "WK", m["sd"], m["stale"])
        _plate(d, "CLAUDE", GREY if m["stale"] else "#ffffff")
    if themed:
        return img.resize((192, 192), Image.NEAREST).convert("RGBA")
    return img


def _check(d, x, y):
    """A chunky 24 x 18 check mark with a dark edge."""
    pts = [(0, 9), (3, 6), (8, 11), (19, 0), (22, 3), (8, 17)]
    pts = [(x + px, y + py) for px, py in pts]
    d.polygon([(px + 1, py + 1) for px, py in pts], fill="#14451a")
    d.polygon(pts, fill="#ffffff")


# ------------------------------------------------------------------ live + actions
def _themed(ctx) -> bool:
    try:
        return bool(ctx.theme)
    except Exception:                                        # noqa: BLE001
        return False


def face(ctx, **_):
    now = time.time()
    m = current(ctx, now)
    key = (m["state"], m["type"], m["fh"], m["sd"], m["stale"],
           countdown(m["resets_at"] - now) if m["state"] == "hit" else None, _themed(ctx))
    cache = ctx.state
    if cache.get("k") != key:
        cache["k"], cache["img"] = key, render(m, now, _themed(ctx))
    return {"image": cache["img"]}


def ring_fraction(m, now) -> tuple:
    """(fraction 0-1, colour role) for the ring: time left in a hit window, else the 5-hour use (weekly if unknown)."""
    if m["state"] == "hit":
        return max(0.0, min(1.0, (m["resets_at"] - now) / WINDOWS[m["type"]])), "warn"
    if m["state"] == "reset":
        return 1.0, "good"
    v = m["fh"] if m["fh"] is not None else m["sd"]
    if v is None:
        return None, None
    return v / 100.0, ("good" if v < 70 else "warn")


def ring(ctx, seconds=6.0, **_):
    """Action: the meter on the LED ring for `seconds` (5-hour use, or the time left after a hit)."""
    now = time.time()
    m = current(ctx, now)
    fr, role = ring_fraction(m, now)
    if fr is None:
        ctx.flash({"label": "no data", "bg": "#2a2a33"}, 1.5)
        return False
    from m18lib import ringmap as RM
    th = ctx.theme or {}
    col = th.get(role) or (GOOD if role == "good" else WARN)
    ctx.log(f"claude_limit: ring {m['state']} {fr:.2f}")
    return ctx.leds(RM.meter_frame(RM.load(ctx.config_dir), fr, col, None, "#0c0c0c"),
                    ttl=min(float(seconds), 60.0), owner=f"claude_limit:{ctx.key}", priority=1)


def _link_for(sid) -> str | None:
    """claude:// link of the desktop session whose CLI id is `sid` (from the app's session records), or None."""
    return _record_for(sid)[0]


def _record_for(sid) -> tuple:
    """(claude:// link, app title) of the desktop session whose CLI id is `sid`, or (None, None)."""
    best = None
    for f in glob.glob(app_records_glob()):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if sid in (d.get("cliSessionId"), d.get("lastAssistantCliSessionId")) and d.get("sessionId"):
            if best is None or (d.get("lastActivityAt") or 0) > best[0]:
                best = ((d.get("lastActivityAt") or 0), d["sessionId"], d.get("title"))
    return (f"claude://claude.ai/epitaxy/{best[1]}", best[2]) if best else (None, None)


def _clipboard(text) -> bool:
    if os.environ.get("M18_LIMIT_DRY"):
        return True
    try:
        from m18lib import winclip
        winclip.set_text(text)
        return True
    except Exception:                                        # noqa: BLE001
        return False


def _open_link(link, sd, text, now):
    if os.environ.get("M18_LIMIT_DRY"):
        try:
            with open(sd / "opened.log", "a", encoding="utf-8") as f:
                f.write(f"{now:.0f} {link} {text}\n")
        except OSError:
            pass
        return f"dry:{link}"
    os.startfile(link)                                        # noqa: S606 - the app's own protocol handler
    return link


def resume(ctx, text=RESUME_TEXT, send=False, **_):
    """Hold action. After a reset: the session the limit stopped (the most recent one; the others are logged)
    gets `text`, and the green face clears. Otherwise the same as a press (ring).

    Default: the text on the clipboard and the session opened in the desktop app; the user pastes and sends.
    `send: true`: really send it through m18lib/sendtext.py (hardware press only, hands off for 1.5 s, the
    shown session checked by title, clipboard saved and restored); any guard failing falls back to the
    default and flashes PASTE on the key."""
    now = time.time()
    m = current(ctx, now)
    if m["state"] != "reset":
        return ring(ctx)
    link, title = _record_for(m["sids"][0]) if m["sids"] else (None, None)
    sd = state_dir(ctx)
    st = _load(sd)
    st["acked"] = (list(st.get("acked") or []) + [round(m["resets_at"])])[-20:]
    _save(sd, st)
    opened = None
    if send and link:
        from m18lib import sendtext
        holder = {}

        def opener(url):
            holder["o"] = _open_link(url, sd, text, now)
        r = sendtext.to_session(ctx, str(text), "claude", link, title, opener,
                                dry=bool(os.environ.get("M18_LIMIT_DRY")))
        opened = holder.get("o")
        ctx.log(f"claude_limit: send sent={r['sent']} ({r['why']}) steps={r['steps']}")
        ctx.state.pop("k", None)
        if r["sent"]:
            sendtext.flash_sent(ctx)
            return "sent"
        sendtext.flash_fallback(ctx, r["why"])
    ok = _clipboard(str(text))
    if link and not opened:
        try:
            opened = _open_link(link, sd, text, now)
        except OSError as e:
            ctx.log(f"claude_limit: open {link}: {e}")
    ctx.log(f"claude_limit: resume clipboard={'ok' if ok else 'failed'} opened={opened} "
            f"stopped sessions={[s[:8] for s in m['sids']]}")
    ctx.state.pop("k", None)
    return opened or ("clipboard" if ok else "failed")
