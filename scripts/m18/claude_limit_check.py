#!/usr/bin/env python3
"""Checks for plugins/claude_limit.py: meter states from fixture data, then the key on a mock daemon.
Prints PASS/FAIL; exit 1 on any FAIL. Previews (plain + hillside) go to %TEMP%\\m18keys\\preview.

    python claude_limit_check.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent.parent
TMP = Path(tempfile.gettempdir()) / "m18keys" / "limit"
PREVIEW = Path(os.environ.get("M18_PREVIEW_DIR") or Path(tempfile.gettempdir()) / "m18keys" / "preview")
PORT = 8736
FAILS = []
SID = "cccccccc-0000-0000-0000-000000000001"


def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def limit_line(t, typ, resets, sid=SID):
    """Shape copied from a real transcript record (2026-11-07), text and ids made up."""
    q = {"status": "rejected", "resetsAt": int(resets), "unifiedRateLimitFallbackAvailable": False,
         "rateLimitType": typ, "overageStatus": "rejected", "isUsingOverage": False}
    return {"type": "assistant", "isSidechain": False, "timestamp": iso(t), "sessionId": sid,
            "message": {"model": "<synthetic>", "role": "assistant", "stop_reason": "stop_sequence",
                        "content": [{"type": "text", "text": "You've hit your 5-hour limit"}]},
            "apiError": "usage_limit_reached", "apiErrorParams": {"rate_limit_info": q}, "quotaLimits": q,
            "error": "rate_limit", "isApiErrorMessage": True, "apiErrorStatus": 429}


def fixture(root: Path, lines, sample=None):
    if root.exists():
        shutil.rmtree(root)
    tr = root / "projects" / "proj" / f"{SID}.jsonl"
    tr.parent.mkdir(parents=True)
    filler = [{"type": "user", "timestamp": iso(time.time() - 600), "message": {"role": "user", "content": "x" * 200}}] * 5
    tr.write_text("\n".join(json.dumps(o) for o in filler + lines) + "\n", encoding="utf-8")
    hist = root / "app" / "prof" / "plan-usage-history.json"
    hist.parent.mkdir(parents=True)
    if sample:
        t, fh, sd = sample
        hist.write_text(json.dumps({"version": 2, "samples": [
            {"t": int((t - 86400) * 1000), "org": "o", "u": {"fh": 1, "sd": 2}},
            {"t": int(t * 1000), "org": "o", "u": {"fh": fh, "sd": sd}}]}), encoding="utf-8")
    rec = root / "app" / "prof" / "claude-code-sessions" / "acct" / "org" / "local_aaaa.json"
    rec.parent.mkdir(parents=True)
    rec.write_text(json.dumps({"sessionId": "local_aaaa", "cliSessionId": SID, "lastActivityAt": 1}), encoding="utf-8")
    os.environ.update(M18_LIMIT_PROJECTS=str(root / "projects"), M18_LIMIT_HISTORY=str(hist),
                      M18_LIMIT_APP=str(root / "app"), M18_HOME=str(root / "home"), M18_LIMIT_DRY="1")
    return tr


def load():
    spec = importlib.util.spec_from_file_location("claude_limit_t", HERE / "plugins" / "claude_limit.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def state_of(m):
    m._EV.update(t=0.0)
    return m.meter(m.limit_events(force=True), m.latest_sample(), time.time(),
                   (m._load(m.state_dir()) or {}).get("acked") or [])


class Ctx:
    def __init__(self):
        self.state, self.logs, self.key, self.theme = {}, [], 12, {}
        self.config_dir = TMP

    def log(self, *a):
        self.logs.append(" ".join(map(str, a)))


def check_states(m):
    now = time.time()
    root = TMP / "fx"
    fixture(root, [], (now - 600, 40, 75))
    s = state_of(m)
    chk("normal: fresh sample (10 min) -> two fills 40 / 75, not stale",
        (s["state"], s["fh"], s["sd"], s["stale"]) == ("normal", 40, 75, False), s)
    fixture(root, [], (now - 2 * 3600, 40, 75))
    s = state_of(m)
    chk("normal: 2 h old sample -> same fills, drawn stale (grey)", (s["fh"], s["sd"], s["stale"]) == (40, 75, True), s)
    fixture(root, [], (now - 6 * 3600, 40, 75))
    s = state_of(m)
    chk("normal: 6 h old sample -> no 5-hour fill (window passed), weekly kept", (s["fh"], s["sd"]) == (None, 75), s)
    fixture(root, [], None)
    s = state_of(m)
    chk("normal: no sample at all -> both fills unknown", (s["state"], s["fh"], s["sd"]) == ("normal", None, None), s)
    fixture(root, [limit_line(now - 300, "five_hour", now + 2 * 3600 + 14 * 60 + 30)], (now - 600, 40, 75))
    s = state_of(m)
    chk("hit: 5-hour limit with reset in 2h14m -> countdown 2:14",
        s["state"] == "hit" and s["type"] == "five_hour" and m.countdown(s["resets_at"] - time.time()) == "2:14", s)
    fixture(root, [limit_line(now - 300, "five_hour", now + 3600),
                   limit_line(now - 200, "seven_day", now + 3 * 86400 + 600)], (now - 600, 40, 75))
    s = state_of(m)
    chk("hit: weekly and 5-hour both hit -> weekly (later reset) shown, 3D",
        s["type"] == "seven_day" and m.countdown(s["resets_at"] - time.time()) == "3D", s)
    tr = fixture(root, [limit_line(now - 3 * 3600, "five_hour", now - 600)], (now - 4 * 3600, 100, 60))
    s = state_of(m)
    chk("reset: a hit whose reset passed 10 min ago -> green reset, stopped session known",
        s["state"] == "reset" and s["sids"] == [SID], s)
    # incremental read: a new hit appended later is picked up
    with open(tr, "a", encoding="utf-8") as f:
        f.write(json.dumps(limit_line(time.time(), "five_hour", time.time() + 1800)) + "\n")
    s = state_of(m)
    chk("incremental: a hit appended to a transcript already read is picked up", s["state"] == "hit", s)
    fixture(root, [limit_line(now - 9 * 3600, "five_hour", now - 5 * 3600)], (now - 7 * 3600, 100, 60))
    s = state_of(m)
    chk("reset older than 4 h -> normal; the 5-hour fill from before that reset is not shown",
        (s["state"], s["fh"], s["sd"]) == ("normal", None, 60), s)
    # hold after a reset: clipboard text + the session opened + green cleared
    fixture(root, [limit_line(now - 3 * 3600, "five_hour", now - 600)], (now - 4 * 3600, 100, 60))
    ctx = Ctx()
    m._EV.update(t=0.0)
    r = m.resume(ctx)
    log = (Path(os.environ["M18_HOME"]) / "state" / "opened.log").read_text(encoding="utf-8")
    chk("resume: opens the stopped session's app link with the 'reset now' text (dry)",
        r == "dry:claude://claude.ai/epitaxy/local_aaaa" and "Please continue" in log, (r, log))
    chk("resume: the green face clears after the hold", state_of(m)["state"] == "normal")
    for i, (src, why) in enumerate(((None, "not a hardware press"), ("device", "dry run"))):   # never types in a check
        fixture(root, [limit_line(now - 3 * 3600, "five_hour", now - 900 - 60 * i)], (now - 4 * 3600, 100, 60))
        m._EV.update(t=0.0)
        c = Ctx()
        c.source = src
        r = m.resume(c, send=True)
        chk(f"resume send=true, source={src}: nothing typed ({why}); falls back to clipboard + open",
            r == "dry:claude://claude.ai/epitaxy/local_aaaa" and any(why in x for x in c.logs), (r, c.logs[-2:]))
    st = json.loads((Path(os.environ["M18_HOME"]) / "state" / "claude-limit.json").read_text())
    chk("state/claude-limit.json carries the last reading for other tools", st["last"]["state"] in ("reset", "normal"), st)


def previews(m):
    sys.path.insert(0, str(HERE))
    from m18lib import render as R
    from PIL import Image
    now = time.time()
    cases = [
        dict(state="normal", type=None, resets_at=None, sids=[], fh=40.0, sd=75.0, as_of=now, stale=False),
        dict(state="normal", type=None, resets_at=None, sids=[], fh=4.0, sd=0.0, as_of=now - 9000, stale=True),
        dict(state="normal", type=None, resets_at=None, sids=[], fh=None, sd=88.0, as_of=now, stale=False),
        dict(state="hit", type="five_hour", resets_at=now + 2 * 3600 + 14 * 60 + 30, sids=[], fh=100, sd=None, as_of=now, stale=False),
        dict(state="hit", type="seven_day", resets_at=now + 3 * 86400 + 60, sids=[], fh=None, sd=100, as_of=now, stale=False),
        dict(state="reset", type="five_hour", resets_at=now - 60, sids=[SID], fh=None, sd=None, as_of=now, stale=False),
    ]
    themes = SKILL / "aesthetics"
    base = TMP / "themebase"
    (base / "themes" / "hillside").mkdir(parents=True, exist_ok=True)
    shutil.copy(themes / "hillside" / "theme.py", base / "themes" / "hillside" / "theme.py")
    st = {"theme": {"name": "hillside"}}
    sheet = Image.new("RGB", (68 * len(cases), 64 * 2 + 8), "#000")
    for i, c in enumerate(cases):
        sheet.paste(R.render_face({"image": m.render(c, now, True)}, base, st, size=64, key=12), (i * 68, 0))
        sheet.paste(m.render(c, now, False), (i * 68, 72))
    PREVIEW.mkdir(parents=True, exist_ok=True)
    sheet.save(PREVIEW / "claude-limit.png")
    sheet.resize((sheet.width * 3, sheet.height * 3), Image.NEAREST).save(PREVIEW / "claude-limit-3x.png")
    print(f"     saved {PREVIEW / 'claude-limit.png'} (top: hillside, bottom: plain)")


def api(method, path, port=PORT):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers={"X-M18": "1"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        return json.load(e)


def start_mock(cfg: Path, env):
    p = subprocess.Popen([sys.executable, str(HERE / "m18d.py"), "--mock", "--port", str(PORT), "--config", str(cfg)],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=str(cfg.parent))
    for _ in range(100):
        try:
            api("GET", "/status")
            return p
        except Exception:                                    # noqa: BLE001
            time.sleep(0.15)
    raise RuntimeError("mock daemon did not start")


def check_mock(m):
    now = time.time()
    root = TMP / "mock"
    fixture(root, [limit_line(now - 3 * 3600, "five_hour", now - 600)], (now - 4 * 3600, 100, 60))
    cfg = root / "macros.yaml"
    cfg.write_text("settings: {home: main, hold_ms: 450}\npages:\n  main:\n    keys:\n"
                   "      12: {live: {plugin: claude_limit.face, interval: 2}, action: {plugin: claude_limit.ring},"
                   " hold: {plugin: claude_limit.resume}}\n", encoding="utf-8")
    p = start_mock(cfg, dict(os.environ))
    try:
        time.sleep(1.5)
        pv = api("GET", "/preview")
        st = api("GET", "/status")
        chk("mock: preview renders the key with no plugin error", Path(pv.get("path", "")).exists()
            and not any("err" in str(x).lower() for x in st["log"]), (pv, st["log"][-3:]))
        r = api("POST", "/press?key=12")
        leds = api("GET", "/leds")
        chk("mock: press = the meter on the ring (owner claude_limit:12)",
            r.get("handled_by") == "action" and "claude_limit:12" in json.dumps(leds.get("owner")), (r, leds.get("owner")))
        r = api("POST", "/press?key=12&hold=0.7&wait=0.8")
        log = (root / "home" / "state" / "opened.log")
        chk("mock: hold after a reset opens the stopped session with the text (dry) and acks it",
            log.exists() and "local_aaaa" in log.read_text() and
            json.loads((root / "home" / "state" / "claude-limit.json").read_text())["acked"], r)
    finally:
        try:
            api("POST", "/quit")
        except Exception:                                    # noqa: BLE001
            pass
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


def main():
    m = load()
    for fn in (check_states, previews, check_mock):
        print(f"-- {fn.__name__}")
        try:
            fn(m)
        except Exception as e:                                # noqa: BLE001
            import traceback
            traceback.print_exc()
            chk(f"{fn.__name__} ran without error", False, repr(e))
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
