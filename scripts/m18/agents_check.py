#!/usr/bin/env python3
"""Checks for the agents layer (pad.py, plugins/agents.py, m18lib/agentpad.py) on a mock daemon.
Prints PASS/FAIL lines, exit 1 on any FAIL. Writes the demo pictures to %TEMP%\\m18agents\\preview.

    python agents_check.py            (use the m18 venv python: it needs PIL)

One check per primitive (status, progress + ring, face, button + wait, inbox, flash, ask, guest slot,
deck, clear) and per fair-use rule (button / card / ring quotas, flash rate, the user's keys untouched,
nothing run on a press, software presses ignored, b1 home, ring yields to the user, expired and dead agents
removed). The live daemon (8718) and the live install are never touched: a temp M18_HOME, port 8742,
fixture sessions through M18_SESSIONS_ROOT.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from m18lib import agentpad as A  # noqa: E402

PORT = 8742
TMP = Path(tempfile.gettempdir()) / "m18agents" / "check"
PREVIEW = Path(os.environ.get("M18_PREVIEW_DIR") or Path(tempfile.gettempdir()) / "m18agents" / "preview")
FAILS: list = []
SA = "0a6e1c2d-1111-4aaa-8bbb-00000000000a"      # session "Test runner" (working)
SB = "0b6e1c2d-2222-4aaa-8bbb-00000000000b"      # session "Kitchen bot" (finished)

CFG = """
settings: {home: main, theme: {name: hillside}, rgb: {color: "#4caf2a", brightness: 60}}
pages:
  main:
    keys:
      1: {icon: mdi:timer-3, label: Snip 3s, action: {notify: own-snip}}
      3: {live: {plugin: cards.key, interval: 1}, action: {plugin: cards.open}}
      6: {live: {plugin: agents.slot, interval: 1, args: {zone: [6, 7, 9], slot: 0}}, action: {plugin: agents.press, args: {zone: [6, 7, 9], slot: 0}}, hold: {plugin: agents.hold, args: {zone: [6, 7, 9], slot: 0}}}
      7: {live: {plugin: agents.slot, interval: 1, args: {zone: [6, 7, 9], slot: 1}}, action: {plugin: agents.press, args: {zone: [6, 7, 9], slot: 1}}, hold: {plugin: agents.hold, args: {zone: [6, 7, 9], slot: 1}}}
      9: {live: {plugin: agents.slot, interval: 1, args: {zone: [6, 7, 9], slot: 2}}, action: {plugin: agents.press, args: {zone: [6, 7, 9], slot: 2}}, hold: {plugin: agents.hold, args: {zone: [6, 7, 9], slot: 2}}}
      10: {icon: mdi:robot, label: Agents, action: {notify: own-ten}, hold: {plugin: agents.deck}}
      b1: {label: Home, action: {page: home}}
"""


def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)
    if not cond:
        FAILS.append(name)


def call(method, path, body=None, timeout=30):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-M18": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


# ------------------------------------------------------------------ fixture sessions (shapes from sessions_check.py)
def session(root: Path, sid, title, shape):
    now = time.time()
    t = now - 3
    u = lambda tt, c: dict(type="user", isSidechain=False, entrypoint="claude-desktop", timestamp=iso(tt),  # noqa: E731
                           message={"role": "user", "content": c})
    a = lambda tt, b, s: dict(type="assistant", isSidechain=False, entrypoint="claude-desktop", timestamp=iso(tt),  # noqa: E731
                              message={"role": "assistant", "content": b, "stop_reason": s})
    L = [u(t - 30, "Run the whole test suite and report")]
    if shape == "working":
        L += [a(t - 7, [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "pytest"}}], "tool_use"),
              u(t - 1, [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}])]
    else:
        L += [a(t - 3, [{"type": "text", "text": "Done. The oven timer is set and I am waiting on it."}], "end_turn"),
              dict(type="system", subtype="stop_hook_summary", timestamp=iso(t - 1))]
    L.append(dict(type="custom-title", customTitle=title, sessionId=sid))
    p = root / "claude" / "projects" / "proj" / f"{sid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(o) for o in L) + "\n", encoding="utf-8")
    os.utime(p, (t, t))
    host = f"local_{sid[-12:]}-aaaa-bbbb-cccc-000000000000"
    ap = root / "app" / "prof" / "claude-code-sessions" / "acct" / "org" / f"{host}.json"
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(dict(sessionId=host, cliSessionId=sid, title=title, isArchived=False,
                                  lastActivityAt=int(t * 1000), cwd="C:\\x")), encoding="utf-8")
    st = "busy" if shape == "working" else "idle"
    rp = root / "claude" / "sessions" / f"{sid[-12:]}.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(dict(pid=os.getpid(), sessionId=sid, kind="interactive", entrypoint="claude-desktop",
                                  hostSessionId=host, name=title, nameSource="user", status=st,
                                  statusUpdatedAt=int(now * 1000), updatedAt=int(now * 1000))), encoding="utf-8")


# ------------------------------------------------------------------ helpers
ENV: dict = {}


def pad(*args, as_=SA, timeout=40, bg=False):
    cmd = [sys.executable, str(HERE / "pad.py"), "--port", str(PORT)] + (["--as", as_] if as_ else []) + list(args)
    if bg:
        return subprocess.Popen(cmd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p = subprocess.run(cmd, env=ENV, capture_output=True, text=True, timeout=timeout)
    try:
        j = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        j = {"raw": p.stdout, "err": p.stderr[-400:]}
    return p.returncode, j, p.stderr


def runs_nothing(r) -> bool:
    """A mock press reports what it sent; the only allowed entry is the plugin call itself."""
    return all(str(x).startswith("plugin agents.") for x in r.get("sent", []))


def finish(proc, timeout=20):
    try:
        o, e = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        o, e = proc.communicate()
        return None, {}, e
    try:
        j = json.loads(o.strip().splitlines()[-1])
    except (ValueError, IndexError):
        j = {"raw": o}
    return proc.returncode, j, e


def ledger():
    return A.read_json(A.ledger_path(ENV_HOME)) or {}


def until(fn, timeout=6.0, step=0.15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(step)
    return None


def preview_img():
    from PIL import Image
    _, r = call("GET", "/preview?page=main")
    return Image.open(r["path"]).convert("RGB")


def tile(img, k, size=128):
    r, c = divmod(k - 1, 5)
    x, y = 40 + c * 146, 56 + r * 146
    return img.crop((x, y, x + size, y + size))


def same(a, b) -> bool:
    from PIL import ImageChops
    return ImageChops.difference(a, b).getbbox() is None


def fresh_faces():
    """Make the live keys redraw now (their next tick), then wait a beat."""
    time.sleep(1.4)


def with_ring(img, title):
    """The preview plus the LED ring as the daemon last sent it (dots at the ring map's positions)."""
    from PIL import ImageDraw
    from m18lib import ringmap as RM
    _, li = call("GET", "/leds")
    sent = li.get("sent") or [None] * 24
    rm = RM.load(ENV_HOME)
    im = img.copy()
    d = ImageDraw.Draw(im)
    w, h = im.size
    x0, y0, x1, y1 = 6, 6, w - 7, h - 7
    for i, (x, y) in enumerate(rm.xy):
        c = sent[i] if i < len(sent) else None
        cx, cy = x0 + x * (x1 - x0), y0 + y * (y1 - y0)
        d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=c or "#151515", outline="#000000")
    d.text((w - 330, 14), title, fill="#d0d0e0")
    return im


def save(img, name, keys=None):
    PREVIEW.mkdir(parents=True, exist_ok=True)
    img.save(PREVIEW / f"{name}.png")
    if keys:
        from PIL import Image
        strip = Image.new("RGB", (len(keys) * 200 + 10, 210), "#0b0b10")
        for i, k in enumerate(keys):
            strip.paste(tile(img, k).resize((192, 192), Image.NEAREST), (10 + i * 200, 9))
        strip.save(PREVIEW / f"{name}_keys.png")


FACE = {  # a fox, drawn by the agent itself: base palette letters plus two of its own colours (1, 2)
    "label": "FOX", "color": "#ee8c2c", "fps": 2, "palette": {"1": "#ff9d3b", "2": "#fff1d6"},
    "frames": [
        ["................", "..K..........K..", ".K1K........K1K.", ".K11K......K11K.", ".K111KKKKKK111K.",
         ".K111111111111K.", "K11111111111111K", "K11K2111111K211K", "K11KK11111KK111K", "K1111111111111K.",
         ".K22211KK11222K.", ".K22222KK22222K.", "..K222222222K...", "...KK222222KK...", ".....KKKKKK.....",
         "................"],
        ["................", "..K..........K..", ".K1K........K1K.", ".K11K......K11K.", ".K111KKKKKK111K.",
         ".K111111111111K.", "K11111111111111K", "K11KK111111KK11K", "K11111111111111K", "K1111111111111K.",
         ".K22211KK11222K.", ".K22222KK22222K.", "..K222222222K...", "...KK222222KK...", ".....KKKKKK.....",
         "................"],
    ]}


# ------------------------------------------------------------------ main
def main() -> int:
    global ENV, ENV_HOME
    shutil.rmtree(TMP, ignore_errors=True)
    home, sroot = TMP / "home", TMP / "sessions"
    (home / "plugins").mkdir(parents=True)
    (home / "macros.yaml").write_text(CFG, encoding="utf-8")
    for cand in (HERE / "themes" / "hillside", HERE.parent.parent / "aesthetics" / "hillside"):
        if (cand / "theme.py").exists():
            shutil.copytree(cand, home / "themes" / "hillside", ignore=shutil.ignore_patterns("__pycache__"))
            break
    session(sroot, SA, "Test runner", "working")
    session(sroot, SB, "Kitchen bot", "finished")
    ENV_HOME = home
    # a ledger as if agent "ringhog" already used 600 s of ring time this hour (ring quota check)
    now = time.time()
    A.write_json(A.ledger_path(home), {"ring": {"ringhog": [[now - 700, now - 100]]}})
    ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "M18_AGENT_ID")}
    ENV.update(M18_HOME=str(home), M18_SESSIONS_ROOT=str(sroot), M18_SESSIONS_DRY="1", M18_SESSIONS_DECISIONS="off",
               M18_SESSIONS_CLAUDE=str(TMP / "no-claude.exe"), M18_PORT=str(PORT))
    log = open(TMP / "daemon.log", "w")
    proc = subprocess.Popen([sys.executable, str(HERE / "m18d.py"), "--mock", "--port", str(PORT),
                             "--config", str(home / "macros.yaml")], env=ENV, stdout=log, stderr=subprocess.STDOUT)
    waiters = []
    try:
        if not until(lambda: call("GET", "/status", timeout=2)[0] == 200, 25):
            chk("mock daemon starts", False)
            return 1
        chk("mock daemon starts on 8742 with a temp home", call("GET", "/status")[1]["config_errors"] is None)
        until(lambda: ledger().get("t"), 8)
        img0 = preview_img()
        own1 = tile(img0, 1)

        # ------------------------------------------------ primitives
        rc, j, _ = pad("whoami")
        chk("whoami: session A sits on key 6", rc == 0 and j.get("plugin") is True, json.dumps(j)[:300])

        rc, j, _ = pad("status", "TESTS", "--tone", "ok")
        chk("status: accepted", rc == 0 and j.get("status") == "TESTS", json.dumps(j))
        fresh_faces()
        img1 = preview_img()
        chk("status: key 6 redrawn with the agent's plate", not same(tile(img0, 6), tile(img1, 6)))
        rc, j, _ = pad("status", "this is a sentence")
        chk("status: more than 8 chars refused (one idea per key)", rc == 2 and "at most 8" in j.get("error", ""), json.dumps(j))
        j = until(lambda: (lambda r: r[1] if "key 6" in str(r[1].get("shown")) else None)(pad("whoami")), 6) or {}
        chk("whoami: reports key 6", "key 6" in str(j.get("shown")), json.dumps(j)[:300])

        rc, j, _ = pad("progress", "62/100", "--ring", "--status", "TEST")
        ok = until(lambda: call("GET", "/leds")[1].get("owner") == "agents")
        _, li = call("GET", "/leds")
        lit = sum(1 for c in (li.get("sent") or [])[:22] if c and c != "#0c0c0c")
        chk("progress --ring: arc on the ring, owner 'agents'", rc == 0 and ok and 12 <= lit <= 15, f"rc={rc} lit={lit} {li.get('owner')}")
        fresh_faces()
        img2 = preview_img()
        save(with_ring(img2, "demo 1: tests 62% on key 6 + ring"), "demo1_progress", [6])

        # ring yields to the user's own ring use (a claude_limit meter press)
        call("POST", "/leds", {"colors": ["#ffffff"] * 24, "ttl": 2.5, "owner": "claude_limit:4", "priority": 1})
        time.sleep(1.0)
        chk("ring: yields to the user's own ring owner", call("GET", "/leds")[1].get("owner") == "claude_limit:4")
        chk("ring: takes it back after that owner's ttl", until(lambda: call("GET", "/leds")[1].get("owner") == "agents", 6))

        fp = TMP / "fox.json"
        fp.write_text(json.dumps(FACE), encoding="utf-8")
        rc, j, _ = pad("face", "--file", str(fp), as_=SB)
        chk("face: own sprite with 2 frames and 2 own colours accepted", rc == 0 and j.get("face", {}).get("frames") == 2, json.dumps(j))
        bad = dict(FACE, frames=[FACE["frames"][0][:15]])
        fp2 = TMP / "bad.json"
        fp2.write_text(json.dumps(bad), encoding="utf-8")
        rc, j, _ = pad("face", "--file", str(fp2), as_=SB)
        chk("face: a 15-row grid refused", rc == 2 and "16 rows" in j.get("error", ""), json.dumps(j))
        fresh_faces()
        img3 = preview_img()
        chk("face: key 7 shows the agent's face", not same(tile(img2, 7), tile(img3, 7)))
        save(img3, "demo3_face", [7])

        # button + wait (physical confirm)
        rc, j, _ = pad("button", "add", "oven", "--label", "OVEN", as_=SB)
        chk("button add: parked", rc == 0 and j.get("label") == "OVEN", json.dumps(j))
        w = pad("wait", "oven", "--timeout", "30", as_=SB, bg=True)
        waiters.append(w)
        until(lambda: (A.read_json(A.doc_path(home, SB)) or {}).get("waiters"), 5)
        fresh_faces()
        img4 = preview_img()
        save(with_ring(img4, "demo 2: OVEN parked on key 7"), "demo2_button", [7])
        log_before = len(call("GET", "/status")[1]["log"])
        s, r = call("POST", "/press?key=7&source=device&wait=0.5")
        chk("press: nothing is run (no keystrokes, launches or page changes)", s == 200 and runs_nothing(r), json.dumps(r)[:200])
        rc, j, _ = finish(w)
        ev = j.get("event") or {}
        chk("wait: returns the hardware press of OVEN", rc == 0 and ev.get("button") == "oven" and ev.get("source") == "device", json.dumps(j))
        doc = A.read_json(A.doc_path(home, SB)) or {}
        chk("button: a one-shot button is gone after the press", not doc.get("buttons"), json.dumps(doc.get("buttons")))
        chk("wait: its waiter entry is removed", not (doc.get("waiters") or {}))

        # a software press does not count as the user
        pad("button", "add", "go", "--label", "GO", as_=SB)
        w = pad("wait", "go", "--timeout", "4", as_=SB, bg=True)
        waiters.append(w)
        time.sleep(1.2)
        call("POST", "/press?key=7&wait=0.3")                 # source=api
        rc, j, err = finish(w)
        chk("wait: a software (api) press is ignored", rc == 3 and "ignored" in err, f"rc={rc} {err[-200:]}")
        pad("button", "rm", "go", as_=SB)

        # inbox + sticky stop button
        pad("button", "add", "stop", "--label", "STOP", "--sticky", as_=SA)
        fresh_faces()
        call("POST", "/press?key=6&source=device&wait=0.4")
        rc, j, _ = pad("inbox", "--check", "stop", as_=SA)
        chk("inbox: the STOP press is there between steps (exit 10)", rc == 10 and j.get("pressed") is True, json.dumps(j))
        doc = A.read_json(A.doc_path(home, SA)) or {}
        chk("button --sticky: still parked after the press", any(b["id"] == "stop" for b in doc.get("buttons") or []))
        rc, j, _ = pad("inbox", as_=SA)
        chk("inbox: consumed (empty the second time)", rc == 0 and j.get("events") == [], json.dumps(j))

        # quota: buttons
        pad("button", "add", "skip", "--label", "SKIP", as_=SA)
        pad("button", "add", "more", "--label", "MORE", as_=SA)
        rc, j, _ = pad("button", "add", "fourth", as_=SA)
        chk("quota: a 4th button is refused", rc == 4 and "quota" in j.get("error", ""), json.dumps(j))
        raw = A.read_json(A.doc_path(home, SA))
        raw["buttons"] += [{"id": f"x{i}", "label": "X", "until": time.time() + 99, "created": time.time() + i} for i in range(5)]
        with A.locked(home, SA):
            A.write_json(A.doc_path(home, SA), raw)
        eff = A.effective(A.read_json(A.doc_path(home, SA)), time.time())
        chk("quota: a hand-written file with 8 buttons shows only the oldest 3",
            [b["id"] for b in eff["buttons"]] == ["stop", "skip", "more"], str([b["id"] for b in eff["buttons"]]))
        # 3 buttons -> tap opens the agent's own takeover
        fresh_faces()
        call("POST", "/press?key=6&source=device&wait=0.6")
        st = call("GET", "/status")[1]
        chk("tap with 3 buttons: the agent's takeover opens", (st.get("modal") or {}).get("owner") is not None, str(st.get("modal")))
        save(preview_img(), "demo5_takeover", [1, 11, 12, 13, 15])
        call("POST", "/press?key=16&source=device&wait=0.4")
        chk("b1: the physical Home button closes the agent takeover", call("GET", "/status")[1].get("modal") is None)
        for b in ("stop", "skip", "more", "x0", "x1", "x2", "x3", "x4"):
            pad("button", "rm", b, as_=SA)

        # flash + rate limit
        rc, j, _ = pad("flash", as_=SA)
        chk("flash: shown (key border pulses, ring pulses)", rc == 0 and j.get("flash") == "shown", json.dumps(j))
        own = until(lambda: call("GET", "/leds")[1].get("owner") == "agents", 2)
        chk("flash: the ring pulse is drawn", bool(own))
        rc, j, _ = pad("flash", as_=SA)
        chk("flash: a second one within 120 s is refused", rc == 4 and "rate" in str(j.get("refused")), json.dumps(j))
        rc, j, _ = pad("flash", as_=SB)
        chk("flash: another agent within 15 s is refused (pad-wide gap)", rc == 4 and "pad-wide" in str(j.get("refused")), json.dumps(j))
        fresh_faces()
        save(preview_img(), "demo4_attention", [6])
        w = pad("wait", "--seen", "--timeout", "10", as_=SA, bg=True)
        waiters.append(w)
        time.sleep(1.0)
        call("POST", "/press?key=6&source=device&wait=0.4")
        rc, j, _ = finish(w)
        chk("flash: the user's tap reaches the agent as 'seen'", rc == 0 and (j.get("event") or {}).get("type") == "agent_seen", json.dumps(j))

        # ask: a card with actions, answered on the pad
        a1 = pad("ask", "Keep?", "--line", "4K 48MB", "--action", "keep:KEEP", "--action", "redo:REDO",
                 "--wait", "20", as_=SB, bg=True)
        waiters.append(a1)
        until(lambda: len(call("GET", f"/cards?feed={A.feed_of(SB)}")[1]) == 1, 6)
        rc, j, _ = pad("ask", "Second?", "--action", "ok:OK", as_=SB)
        rc3, j3, _ = pad("ask", "Third?", "--action", "ok:OK", as_=SB)
        chk("quota: a 3rd open card is refused", rc == 0 and rc3 == 4 and "quota" in j3.get("error", ""), f"{rc} {rc3} {j3}")
        call("POST", "/cards", {"id": "ag:handmade", "feed": A.feed_of(SB), "title": "sneaky", "actions": [{"id": "x", "label": "X"}]})
        chk("quota: a hand-posted 3rd card is removed by the plugin",
            bool(until(lambda: len(call("GET", f"/cards?feed={A.feed_of(SB)}")[1]) == 2, 6)))
        call("POST", "/press?key=3&source=device&wait=0.6")
        save(preview_img(), "demo6_card", [11, 12])
        call("POST", "/press?key=11&source=device&wait=0.5")
        rc, j, _ = finish(a1)
        chk("ask --wait: returns the user's answer KEEP", rc == 0 and j.get("action") == "keep", json.dumps(j)[:300])
        call("POST", "/press?key=16&source=device&wait=0.3")

        # The user's own keys
        raw = {"id": "forger", "updated": time.time(), "created": time.time(), "key": 1, "keys": {"1": {"label": "PWNED"}},
               "status": {"text": "HI", "until": time.time() + 99},
               "buttons": [{"id": "evil", "label": "EVIL", "until": time.time() + 99, "action": {"run": "calc.exe"},
                            "hotkey": "win+r"}]}
        A.write_json(A.doc_path(home, "forger"), raw)
        fresh_faces()
        img7 = preview_img()
        chk("The user's key 1 unchanged after agents posted (and one tried 'key: 1')", same(own1, tile(img7, 1)))
        chk("guest: an agent with no session fills the empty slot (key 9)", not same(tile(img0, 9), tile(img7, 9)))
        s, r = call("POST", "/press?key=9&source=device&wait=0.5")
        evs = A.inbox_list(home, "forger")
        chk("a forged button with action/hotkey only sends an event, runs nothing",
            s == 200 and runs_nothing(r) and evs and evs[0][1].get("button") == "evil", json.dumps(r)[:200])
        st = call("GET", "/status")[1]
        chk("nothing launched or typed (no run/hotkey in the daemon log)", not any("calc" in x or "win+r" in x for x in st["log"]))

        # deck: every agent, from a hold on key 10
        call("POST", "/press?key=10&hold=0.7&source=device&wait=0.6")
        st = call("GET", "/status")[1]
        chk("deck: hold opens the agents deck", st.get("modal") is not None, str(st.get("modal")))
        save(preview_img(), "demo7_deck", [1, 2, 3, 15])
        call("POST", "/press?key=16&source=device&wait=0.3")
        chk("b1: closes the deck", call("GET", "/status")[1].get("modal") is None)

        # ring quota (pre-seeded ledger: ringhog used 600 s this hour)
        pad("progress", "0.5", "--ring", as_=SA)
        pad("progress", "--clear", as_=SA)
        rc, j, _ = pad("progress", "0.9", "--ring", as_="ringhog")
        time.sleep(1.5)
        lg = ledger()
        chk("quota: ring time over 600 s/hour refused (bar on key only)",
            "refused" in str(j.get("ring_note")) and any(x["agent"] == "ringhog" and x["what"] == "ring" for x in lg.get("refused", [])),
            json.dumps(j))

        # expiry: ttl and a dead agent
        pad("status", "BRIEF", "--ttl", "1", as_="shortlived")
        time.sleep(1.3)
        chk("ttl: an expired status no longer shows", A.effective(A.read_json(A.doc_path(home, "shortlived")) or {}, time.time()) is None)
        dead = {"id": "ghost", "updated": time.time() - 20 * 60, "created": time.time() - 30 * 60,
                "status": {"text": "OLD", "until": time.time() + 3600}}
        A.write_json(A.doc_path(home, "ghost"), dead)
        A.inbox_put(home, "ghost", {"type": "agent_button", "button": "x", "source": "device"})
        call("POST", "/cards", {"id": "ag:ghost:1", "feed": A.feed_of("ghost"), "title": "left over", "actions": []})
        gone = until(lambda: not A.doc_path(home, "ghost").exists() and not A.inbox_dir(home, "ghost").exists()
                     and not call("GET", f"/cards?feed={A.feed_of('ghost')}")[1], 8)
        chk("dead agent (no session, no waiter, silent 20 min): file, inbox and card removed", bool(gone))
        chk("expired-only file removed too", bool(until(lambda: not A.doc_path(home, "shortlived").exists(), 70)))

        # clear
        rc, j, _ = pad("clear", as_=SB)
        chk("clear: everything of mine removed", rc == 0 and not A.doc_path(home, SB).exists()
            and not call("GET", f"/cards?feed={A.feed_of(SB)}")[1], json.dumps(j))
        rc, j, _ = pad("whoami", as_=None)
        chk("no id in the environment: a clear error", rc == 2 and "--as" in j.get("error", ""), json.dumps(j))
    finally:
        for w in waiters:
            if w.poll() is None:
                w.kill()
        try:
            call("POST", "/quit", timeout=3)
        except OSError:
            pass
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if not FAILS:
            shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAIL'}; previews in {PREVIEW}")
    return 1 if FAILS else 0


ENV_HOME = Path(".")
if __name__ == "__main__":
    sys.exit(main())
