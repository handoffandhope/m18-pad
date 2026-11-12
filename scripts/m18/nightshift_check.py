#!/usr/bin/env python3
"""Checks for plugins/nightshift.py: the tally from fixture summaries, then the key on a mock daemon
(away on/off, wake lock taken, daemon restart keeps away + tally, hold = monitors off in dry mode, zero sessions).
Monitors are never really turned off here (M18_NIGHTSHIFT_DRY=1). Previews go to %TEMP%\\m18keys\\preview.

    python nightshift_check.py
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent.parent
TMP = Path(tempfile.gettempdir()) / "m18keys" / "night"
PREVIEW = Path(os.environ.get("M18_PREVIEW_DIR") or Path(tempfile.gettempdir()) / "m18keys" / "preview")
PORT = 8736
FAILS = []


def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def load():
    spec = importlib.util.spec_from_file_location("nightshift_t", HERE / "plugins" / "nightshift.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def rows(**states):
    """rows(a="working", b="silent") -> summary rows; 'silent' means working + silent."""
    return [{"id": k, "state": "working" if v == "silent" else v, "silent": v == "silent"} for k, v in states.items()]


def check_tally(m):
    t = m.start_track(rows(a="working", b="finished", c="waiting"), 100.0)
    chk("tally: sessions already finished when you leave are not counted", m.counts(t, rows(a="working", b="finished", c="waiting"))["done"] == 0)
    m.step(t, rows(a="finished", b="finished", c="waiting", d="working"))
    m.step(t, rows(b="finished", c="working", d="silent"))      # a left the pad after finishing
    c = m.counts(t, rows(b="finished", c="working", d="silent"))
    chk("tally: a finished session stays counted after it leaves the pad; waiting and silent are 'now'",
        c == {"done": 1, "waiting": 0, "silent": 1}, c)
    m.step(t, rows(c="finished", d="waiting", e="finished"))   # e appeared already finished: it finished while away
    c = m.counts(t, rows(c="finished", d="waiting", e="finished"))
    chk("tally: a new session first seen finished counts; a session finishing twice counts once",
        c == {"done": 3, "waiting": 1, "silent": 0}, c)
    m.step(t, rows(c="working"))
    m.step(t, rows(c="finished"))
    chk("tally: the same session finishing again is not double counted", m.counts(t, [])["done"] == 3)


def previews(m):
    sys.path.insert(0, str(HERE))
    from m18lib import render as R
    from PIL import Image
    base = TMP / "themebase"
    (base / "themes" / "hillside").mkdir(parents=True, exist_ok=True)
    shutil.copy(SKILL / "aesthetics" / "hillside" / "theme.py", base / "themes" / "hillside" / "theme.py")
    st = {"theme": {"name": "hillside"}}
    faces = [lambda th: {"icon": "mdi:weather-night", "label": "Away"}]
    # every frame of: away with nothing yet, away 12 done / 2 waiting / 1 silent, back 4 done / 1 waiting, back 105 done
    for fn, c in ((m.render_away, (0, 0, 0)), (m.render_away, (12, 2, 1)), (m.render_back, (4, 1, 0)),
                  (m.render_back, (105, 0, 0))):
        for i in range(len(m.tally_frames(dict(zip(("done", "waiting", "silent"), c))))):
            faces.append(lambda th, fn=fn, c=c, i=i: {"image": fn(dict(zip(("done", "waiting", "silent"), c)), th)[i]})
    chk("faces: one count per frame (12/2/1 -> 3 frames; nothing yet -> 1 frame)",
        len(m.render_away({"done": 12, "waiting": 2, "silent": 1})) == 3 and len(m.render_away({})) == 1)
    sheet = Image.new("RGB", (68 * len(faces), 64 * 2 + 8), "#000")
    for i, f in enumerate(faces):
        sheet.paste(R.render_face(f(True), base, st, size=64, key=13), (i * 68, 0))
        sheet.paste(R.render_face(f(False), None, {}, size=64, key=13), (i * 68, 72))
    PREVIEW.mkdir(parents=True, exist_ok=True)
    sheet.save(PREVIEW / "nightshift.png")
    sheet.resize((sheet.width * 3, sheet.height * 3), Image.NEAREST).save(PREVIEW / "nightshift-3x.png")
    print(f"     saved {PREVIEW / 'nightshift.png'} (top: hillside, bottom: plain)")


def api(method, path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method, headers={"X-M18": "1"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        return json.load(e)


def start_mock(cfg: Path):
    env = dict(os.environ, M18_HOME=str(cfg.parent / "home"), M18_NIGHTSHIFT_DRY="1")
    p = subprocess.Popen([sys.executable, str(HERE / "m18d.py"), "--mock", "--port", str(PORT), "--config", str(cfg)],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(cfg.parent))
    for _ in range(100):
        try:
            api("GET", "/status")
            return p
        except Exception:                                    # noqa: BLE001
            time.sleep(0.15)
    raise RuntimeError("mock daemon did not start")


def stop_mock(p):
    try:
        api("POST", "/quit")
    except Exception:                                        # noqa: BLE001
        pass
    try:
        p.wait(8)
    except subprocess.TimeoutExpired:
        p.kill()


def wait_for(fn, secs=12.0):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.25)
    return fn()


def check_mock(m):
    root = TMP / "mock"
    if root.exists():
        shutil.rmtree(root)
    sd = root / "home" / "state"
    sd.mkdir(parents=True)
    cfg = root / "macros.yaml"
    cfg.write_text("settings: {home: main, hold_ms: 450}\npages:\n  main:\n    keys:\n"
                   "      13: {live: {plugin: nightshift.face, interval: 1}, action: {plugin: nightshift.tap},"
                   " hold: {plugin: nightshift.hold}}\n", encoding="utf-8")

    def summary(**states):
        (sd / "sessions-summary.json").write_text(json.dumps({"sessions": rows(**states)}), encoding="utf-8")

    def ns():
        return m._read(sd / "nightshift.json") or {}

    def no_err():
        return not [x for x in api("GET", "/status")["log"] if "err" in str(x).lower() or "Traceback" in str(x)]

    summary(a="working", b="working", c="finished")
    p = start_mock(cfg)
    try:
        time.sleep(1.5)
        chk("mock: idle key renders with no plugin error", no_err() and not (sd / "away.json").exists())
        api("POST", "/press?key=13")
        aw = m._read(sd / "away.json") or {}
        chk("tap: away.json written with since + by=pad", aw.get("by") == "pad" and aw.get("since"), aw)
        held = wait_for(lambda: (ns().get("wake") or {}).get("held"))
        chk("tap: the wake lock is taken (SetThreadExecutionState returned non-zero, recorded in nightshift.json)", held is True, ns())
        summary(a="finished", b="silent", c="finished", d="waiting")
        chk("away: a session turning finished is counted by the plugin's thread",
            wait_for(lambda: ns().get("done") == ["a"]), ns())
        before = (ns().get("wake") or {}).get("at")
        stop_mock(p)
        time.sleep(1.0)
        restart = time.time()
        p = start_mock(cfg)
        chk("restart: away.json survives a daemon restart", (sd / "away.json").exists())
        chk("restart: the next live tick re-arms the wake lock",
            wait_for(lambda: ((ns().get("wake") or {}).get("at") or 0) >= int(restart) and ns()["wake"]["held"]),
            (before, ns().get("wake")))
        summary(a="finished", b="finished", c="finished", d="waiting")
        chk("restart: the tally carries on (a counted before, b after)", wait_for(lambda: ns().get("done") == ["a", "b"]), ns())
        r = api("POST", "/press?key=13")
        f = ns()
        chk("tap again: away.json removed, final tally written (2 done, 1 waiting, 0 silent)",
            not (sd / "away.json").exists() and f.get("final") == {"done": 2, "waiting": 1, "silent": 0} and f.get("back_at"),
            (r, f))
        st = api("GET", "/status")
        chk("back: the log line has the tally", any("2 done, 1 waiting" in str(x) for x in st["log"]), st["log"][-4:])
        time.sleep(1.5)
        chk("back: key still renders without error (tally face)", no_err())
        # hold = away + monitors off (dry); zero sessions
        (sd / "sessions-summary.json").unlink()
        api("POST", "/press?key=13&hold=0.7&wait=0.8")
        log = sd / "nightshift-monitor.log"
        chk("hold: away + monitors-off call (dry log only)", (sd / "away.json").exists() and log.exists()
            and "dry" in log.read_text(), [x for x in api("GET", "/status")["log"][-4:]])
        time.sleep(1.5)
        chk("zero sessions: away face renders, tally 0", no_err() and ns().get("done") == [])
        n_lines = len(log.read_text().splitlines())
        api("POST", "/press?key=13&hold=0.7&wait=0.8")
        chk("hold while away = back, and does not turn monitors off",
            not (sd / "away.json").exists() and len(log.read_text().splitlines()) == n_lines)
    finally:
        stop_mock(p)


def main():
    m = load()
    for fn in (check_tally, previews, check_mock):
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
