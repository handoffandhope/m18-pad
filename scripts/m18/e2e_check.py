#!/usr/bin/env python3
"""End-to-end check for the daemon's API and pad layer. Starts its own mock daemon on a free
port with a private temp config (never touches the real pad or macros.yaml), asserts, prints one
PASS/FAIL line per assertion, exits non-zero on any FAIL.

    python e2e_check.py
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import base64
import io
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
RESULTS: list[tuple[bool, str]] = []
PORT = 0


def check(ok: bool, name: str, detail: str = ""):
    RESULTS.append((bool(ok), name))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def call(method, path, body=None, headers=None, host=None, timeout=40):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host or f"127.0.0.1:{PORT}")
    h = dict({"X-M18": "1"} if headers is None else headers)
    data = json.dumps(body).encode() if body is not None else b""
    if body is not None:
        h["Content-Type"] = "application/json"
    if method in ("POST", "DELETE", "PUT", "PATCH") or data:
        h["Content-Length"] = str(len(data))
    for k, v in h.items():
        c.putheader(k, v)
    c.endheaders(data or None)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:
        return r.status, {"raw": raw[:200].decode("latin1")}


def png_b64(color, size=(64, 64)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()


CFG = """\
settings:
  theme: {name: hillside}
  home: main
  hold_ms: 450
  log_raw: true
  rgb: "#112233"
pages:
  main:
    keys:
      1: {icon: mdi:timer-3, label: Snip 3s, action: [{countdown: 2}, {hotkey: win+shift+s}]}
      2: {label: Normal, action: {hotkey: ctrl+shift+f12}}
      3: {label: Human, physical_only: true, action: {hotkey: ctrl+alt+f11}}
      4: {label: Held, action: {key: f13}, hold: {key: f14}}
      5: {label: Src, action: {plugin: e2eplug.src}}
      6: {label: Modal, action: {plugin: e2eplug.modal}}
      7: {label: Ring, action: {plugin: e2eplug.ring}}
      8: {icon: mdi:power, label: Lights, action: {dark: toggle}}
      9: {label: Second, action: {page: second}}
      10: {label: Race, action: {plugin: e2eplug.flashrace}}
      11: {label: Live, live: {plugin: e2eplug.live, interval: 5}}
      12: {label: RingLive, live: {plugin: ring.status, interval: 1}}
      13: {live: {plugin: cards.key, interval: 1}, action: {plugin: cards.open}}
      14: {label: PostCard, action: {plugin: e2eplug.postcard}}
      15: {label: Multi, action: {key: f15}, double: {key: f16}, triple: {key: f17}, hold: {key: f18}}
      b1: {label: Home, action: {page: home}}
  second:
    keys:
      2: {label: Two, action: {hotkey: ctrl+shift+f10}, bg: "#ff8800"}
      3: {live: {plugin: e2eplug.live2, interval: 5}}
      4: {label: Dbl, action: {key: f19}, double: {key: f20}}
      b1: {label: Home, action: {page: home}}
"""

PLUGIN = '''\
import os
from PIL import Image


def src(ctx):
    ctx.log("E2E_SRC", ctx.source)


def modal(ctx):
    def on_key(c, key, down, t):
        c.log("E2E_MODALKEY", key, down, c.source)
    ctx.modal({1: {"label": "Plug", "bg": "#00ff00"}}, on_key=on_key, timeout=30)


def ring(ctx):
    ctx.rgb("#ff0000", 60, ttl=1.0)


def flashrace(ctx):
    ctx.flash({"image": Image.new("RGB", (64, 64), (255, 0, 0))}, 0.4)
    ctx.flash({"image": Image.new("RGB", (64, 64), (0, 255, 0))}, 3.0)


def postcard(ctx):
    ctx.cards.post({"id": "plug", "feed": "e2eplug", "title": "From plugin", "priority": 2,
                    "actions": [{"id": "ok", "label": "OK"}]}, handler="e2eplug.onact")


def onact(ctx, card, action):
    ctx.log("E2E_CARDACT", card["id"], action)


def live2(ctx):
    ctx.log("E2E_LIVE2", ctx.page)
    return {"glyph": "P", "bg": "#0000ff"}


def live(ctx):
    m = os.environ.get("E2E_LIVE_MARK")
    if m:
        open(m, "a").write("live ran\\n")
    return {"glyph": "L"}
'''


def tile(k, page=None):
    st, j = call("GET", "/preview" + (f"?page={page}" if page else ""))
    im = Image.open(j["path"]).convert("RGB")
    r, c = divmod(k - 1, 5)
    x, y = 40 + c * 146, 56 + r * 146
    return im.crop((x, y, x + 128, y + 128))


def center(k):
    return tile(k).getpixel((64, 64))


def status():
    return call("GET", "/status")[1]


def logtext(tmp):
    try:
        return (tmp / "m18d.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def main() -> int:
    global PORT
    tmp = Path(tempfile.mkdtemp(prefix="m18e2e_"))
    proc = None
    try:
        (tmp / "plugins").mkdir()
        (tmp / "plugins" / "e2eplug.py").write_text(PLUGIN, encoding="utf-8")
        (tmp / "macros.yaml").write_text(CFG, encoding="utf-8")
        for cand in (HERE / "themes" / "hillside", HERE.parent.parent / "aesthetics" / "hillside"):
            if (cand / "theme.py").exists():
                shutil.copytree(cand, tmp / "themes" / "hillside", ignore=shutil.ignore_patterns("__pycache__"))
                break
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            PORT = sk.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if k != "E2E_LIVE_MARK"}
        proc = subprocess.Popen([sys.executable, str(HERE / "m18d.py"), "--mock", "--port", str(PORT),
                                 "--config", str(tmp / "macros.yaml")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                if call("GET", "/status")[0] == 200:
                    break
            except OSError:
                time.sleep(0.25)
        else:
            check(False, "mock daemon starts")
            return 1
        check(status()["config_errors"] is None, "mock daemon starts with the private config")

        # ---------------------------------------------------------------- A: guard
        seq0 = status()["events_seq"]
        s1, _ = call("POST", "/press?key=2", headers={"X-M18": "1", "Origin": "http://evil.example"})
        check(s1 == 403, "guard: Origin header present -> 403", str(s1))
        s2, _ = call("POST", "/press?key=2", headers={"Origin": "http://evil.example"})
        check(s2 == 403, "guard: Origin and no X-M18 -> 403", str(s2))
        s3, _ = call("POST", "/press?key=2", host=f"evil.example:{PORT}")
        check(s3 == 403, "guard: wrong Host -> 403", str(s3))
        s4, _ = call("GET", "/status", headers={})
        check(s4 == 403, "guard: missing X-M18 -> 403", str(s4))
        s5, _ = call("DELETE", "/modal", headers={"Origin": "http://evil.example"})
        check(s5 == 403, "guard: DELETE is guarded too", str(s5))
        check(status()["events_seq"] == seq0, "guard: rejected requests ran nothing (no press events)")
        s6, _ = call("GET", "/status", host=f"localhost:{PORT}")
        check(s6 == 200, "guard: Host localhost:<port> with X-M18 accepted", str(s6))
        envc = dict(os.environ, M18_PORT=str(PORT), M18_CONFIG=str(tmp / "macros.yaml"))
        p = subprocess.run([sys.executable, str(HERE / "m18ctl.py"), "status"], env=envc,
                           capture_output=True, text=True, timeout=60)
        check(p.returncode == 0 and '"connected": true' in p.stdout, "guard: m18ctl status works (sends X-M18)", p.stderr[-200:])
        for bad in ("hold=abc", "hold=-1", "hold=nan", "wait=x"):
            sb, _ = call("POST", f"/press?key=2&{bad}")
            check(sb == 400, f"press: bad {bad} -> 400", str(sb))

        # ---------------------------------------------------------------- B: physical_only
        s, j = call("POST", "/press?key=3&wait=0.4")
        check(s == 403 and "physical_only" in j.get("error", "") and "sent" not in j,
              "physical_only: api press refused, nothing sent", f"{s} {j}")
        check("refused: physical_only" in logtext(tmp), "physical_only: refusal is logged")
        s, j = call("POST", "/press?key=2&wait=0.4")
        check(s == 200 and any("hotkey" in x for x in j.get("sent", [])), "normal key: api press reports sent", f"{s} {j}")
        s, j = call("POST", "/press?key=3&source=device&wait=0.4")
        check(s == 200 and any("hotkey" in x for x in j.get("sent", [])), "physical_only: device press runs (mock hook)", f"{s} {j}")
        call("POST", "/press?key=5&wait=0.4")
        call("POST", "/press?key=5&source=device&wait=0.4")
        lg = logtext(tmp)
        check("E2E_SRC api" in lg and "E2E_SRC device" in lg, "ctx.source is api / device in plugins")

        # ---------------------------------------------------------------- C: push
        t0 = time.time()
        s, j = call("POST", "/face", {"key": 4, "image_b64": png_b64((255, 0, 255)), "ttl": 3})
        check(s == 200 and j.get("keys") == [4], "face: push accepted", f"{s} {j}")
        check(center(4) == (255, 0, 255), "face: pushed colour shows in /preview", str(center(4)))
        call("POST", "/page?name=second")
        check(center(4) == (255, 0, 255), "face: survives a page change")
        call("POST", "/page?name=main")
        time.sleep(max(0, t0 + 3.6 - time.time()))
        check(center(4) != (255, 0, 255) and 4 not in status()["pushed_keys"], "face: gone after ttl")
        s, j = call("POST", "/face", {"key": 4, "image_b64": png_b64((0, 0, 255))})
        check(center(4) == (0, 0, 255), "face: push without ttl stays")
        s, j = call("DELETE", "/face?key=4")
        check(s == 200 and j.get("removed") == [4] and center(4) != (0, 0, 255), "face: DELETE /face?key= removes it")
        s, j = call("POST", "/face", {"key": 3, "image_b64": png_b64((255, 0, 255))})
        check(j.get("not_shown_physical_only") == [3] and center(3) != (255, 0, 255),
              "face: a physical_only key's face cannot be spoofed")
        call("DELETE", "/face?key=all")
        sb, _ = call("POST", "/face", {"key": 1, "face": {"bg": "red"}})
        check(sb == 400, "face: bad colour -> 400", str(sb))
        s, j = call("POST", "/face", {"key": "all", "image_b64": png_b64((10, 200, 10), (320, 192))})
        check(s == 200 and len(j.get("keys", [])) == 15 and center(14) == (10, 200, 10), "face: key=all slices a 320x192 image")
        call("DELETE", "/face?key=all")
        call("POST", "/press?key=10&wait=0.2")
        time.sleep(1.0)
        check(center(10) == (0, 255, 0), "flash race: a stale flash clear no longer removes a newer flash", str(center(10)))
        time.sleep(2.3)

        # ---------------------------------------------------------------- D: events
        seq = status()["events_seq"]
        call("POST", "/press?key=4&hold=0.6&wait=0.3")
        s, j = call("GET", f"/events?since={seq}")
        evs = j.get("events", [])
        up = [e for e in evs if not e["down"]]
        check(len(evs) == 2 and evs[0]["down"] and up and up[0].get("held_ms", 0) >= 550
              and evs[0]["source"] == "api" and up[0]["handled_by"] == "action" and evs[0]["page"] == "main",
              "events: api press gives down + up with held_ms", json.dumps(evs)[:300])
        seq = j["seq"]
        got = {}

        def poll():
            got["r"] = call("GET", f"/events?since={seq}&timeout=10")[1]
        th = threading.Thread(target=poll)
        th.start()
        time.sleep(0.5)
        t1 = time.time()
        call("POST", "/press?key=2&wait=0.1")
        th.join(8)
        check(not th.is_alive() and len(got.get("r", {}).get("events", [])) >= 1 and time.time() - t1 < 2.5,
              "events: long-poll returns as soon as a press happens")
        seq = status()["events_seq"]
        call("POST", "/press?key=2&source=device&phase=down&wait=0.1")
        call("POST", "/press?key=2&source=device&phase=down&wait=0.1")
        s, j = call("POST", "/press?key=2&source=device&phase=up&wait=0.3")
        evs = call("GET", f"/events?since={seq}")[1]["events"]
        check([e["handled_by"] for e in evs] == ["action", "ignored", "action"], "input: a second down for a held key is ignored",
              json.dumps([e["handled_by"] for e in evs]))
        raw = (tmp / "raw-events.log").read_text() if (tmp / "raw-events.log").exists() else ""
        check(len(raw.strip().splitlines()) >= 3 and len(raw.split()[0]) == 13 and len(raw.splitlines()[0].split()) == 4,
              "log_raw: raw-events.log lines are `epoch_ms hwcode state key`", raw[:80])

        # ---------------------------------------------------------------- D2: gestures (tap / double / triple / hold)
        def sent(q):
            return call("POST", "/press?" + q)[1].get("sent", [])
        call("POST", "/page?name=main")
        r = sent("key=2&source=device&phase=down&wait=0.05")
        sent("key=2&source=device&phase=up&wait=0.05")
        check(r == ["hotkey ctrl+shift+f12"], "gesture: a plain key fires on key down, before the release", str(r))
        r = sent("key=4&wait=0.05")
        check(r == ["hotkey f13"], "gesture: a hold-only key's tap fires on release without the double_ms wait", str(r))
        r = sent("key=15&wait=0.1")
        time.sleep(0.4)
        check(r == [], "gesture: a key with double waits after the first release (nothing at 100 ms)", str(r))
        r = sent("key=15&wait=0.45")
        check(r == ["hotkey f15"], "gesture: single tap on a double/triple key fires its action once after double_ms", str(r))
        time.sleep(0.3)
        r = sent("key=15&taps=2&wait=0.45")
        check(r == ["hotkey f16"], "gesture: double tap fires double only (no single)", str(r))
        time.sleep(0.3)
        r = sent("key=15&taps=3&wait=0.1")
        check(r == ["hotkey f17"], "gesture: triple tap fires triple at the third down, no wait", str(r))
        time.sleep(0.3)
        r = sent("key=15&source=device&phase=down&wait=0.6")
        r2 = sent("key=15&source=device&phase=up&wait=0.45")
        check(r == ["hotkey f18"] and r2 == [], "gesture: hold fires at hold_ms while down; its release is not a tap", f"{r} {r2}")
        r = sent("key=15&taps=2&gap=0.4&wait=0.45")
        check(r == ["hotkey f15", "hotkey f15"], "gesture: two taps further apart than double_ms are two singles", str(r))
        r = sent("key=4&page=second&taps=2&wait=0.1")
        call("POST", "/page?name=main")
        check(r == ["hotkey f20"], "gesture: double tap on a key without triple fires at the second down", str(r))
        from m18lib import config as CC
        bad = []
        for kd, setting in (({"action": {"key": "f1"}, "double": "f2"}, {}), ({"triple": {"nope": 1}}, {}),
                            ({"action": {"key": "f1"}}, {"double_ms": 5})):
            try:
                CC.validate({"settings": setting, "pages": {"p": {"keys": {1: kd}}}})
                bad.append(str(kd))
            except CC.ConfigError as e:
                if not any(x in str(e) for x in ("keys.1.double", "keys.1.triple", "double_ms")):
                    bad.append(str(e)[:80])
        check(not bad, "gesture: validation rejects a bad double/triple action and an out-of-range double_ms", "; ".join(bad))

        # ---------------------------------------------------------------- D3: preview of another page is side-effect free
        call("POST", "/page?name=main")
        time.sleep(0.4)
        st0 = status()
        main2 = tile(2)
        n0, lv0 = status()["faces_sent"], logtext(tmp).count("E2E_LIVE2")
        sec2, sec3 = tile(2, "second"), tile(3, "second")
        tile(3, "second")
        st1 = status()
        check(st1["page"] == "main" and st1["faces_sent"] == n0, "preview: another page renders without a page change or a pad write",
              f"{st1['page']} {n0}->{st1['faces_sent']}")
        empty = tile(9, "second")                    # key 3 on `second` has only `live:`, so any drawing is the live face
        check(sec2.tobytes() != main2.tobytes() and sec3.tobytes() != empty.tobytes(),
              "preview: the other page's faces and a one-off live face show")
        lv = logtext(tmp).count("E2E_LIVE2") - lv0
        check(lv <= 1, "preview: the one-off live face is cached (plugin ran at most once for three previews)", str(lv))
        check(call("GET", "/preview?page=nope")[0] == 400, "preview: unknown page -> 400")

        # ---------------------------------------------------------------- E: modal
        before = tile(2)
        s, j = call("POST", "/modal", {"faces": {"1": {"label": "Yes", "bg": "#00ff00"}}, "timeout": 2.5, "owner": "e2e"})
        check(s == 200 and status()["modal"]["owner"] == "e2e", "modal: external modal starts", f"{s} {j}")
        t2 = tile(2)
        check(set(t2.crop((24, 24, 120, 120)).tobytes()) == {0} and t2.tobytes() != before.tobytes(), "modal: keys not named in faces show blank")
        seq = status()["events_seq"]
        s, j = call("POST", "/press?key=2&wait=0.3")
        evs = call("GET", f"/events?since={seq}")[1]["events"]
        check(s == 200 and j.get("sent") == [] and j.get("handled_by") == "modal"
              and evs and all(e["handled_by"] == "modal" for e in evs), "modal: press swallowed, event handled_by modal", f"{j}")
        time.sleep(2.5)
        check(status()["modal"] is None and tile(2).tobytes() == before.tobytes(), "modal: ends on timeout and the page face is back")
        s, j = call("POST", "/press?key=2&wait=0.3")
        check(any("hotkey" in x for x in j.get("sent", [])), "modal: presses run actions again afterwards")
        s, j = call("POST", "/modal", {"faces": {}, "timeout": 999})
        check(s == 200 and j["timeout"] == 120, "modal: timeout is capped at 120 s")
        call("POST", "/page?name=second")
        call("POST", "/modal", {"faces": {}, "timeout": 30})
        seq = status()["events_seq"]
        call("POST", "/press?key=b1&source=device&wait=0.3")
        st = status()
        evs = call("GET", f"/events?since={seq}")[1]["events"]
        check(st["modal"] is None and st["page"] == "second" and all(e["handled_by"] == "modal" for e in evs) and len(evs) == 2,
              "modal: physical b1 ends it and its Home action does not run", json.dumps(st["modal"]) + st["page"])
        call("POST", "/page?name=main")
        call("POST", "/modal", {"faces": {}, "timeout": 30, "owner": "someone"})
        s, j = call("POST", "/modal", {"faces": {}, "owner": "other"})
        check(s == 409, "modal: a second owner is refused while one is up", str(s))
        call("DELETE", "/modal")
        check(status()["modal"] is None, "modal: DELETE /modal ends it")
        call("POST", "/press?key=8&wait=0.3")
        s, j = call("POST", "/modal", {"faces": {}, "timeout": 5})
        check(s == 409, "modal: refused while dark mode is on (no deadlock)", str(s))
        call("POST", "/press?key=8&wait=0.3")
        check(status()["modal"] is None, "dark: wakes again after the modal was refused")
        call("POST", "/press?key=6&wait=0.5")
        check(status()["modal"] and status()["modal"]["owner"].startswith("plugin"), "modal: plugin ctx.modal starts")
        call("POST", "/press?key=2&wait=0.5")
        check("E2E_MODALKEY 2 True api" in logtext(tmp), "modal: plugin on_key receives presses with ctx.source")
        call("DELETE", "/modal")

        # ---------------------------------------------------------------- F: ring
        s, j = call("POST", "/rgb", {"color": "#ff0000", "brightness": 50, "ttl": 1})
        check(s == 200 and status()["ring"] == ["#ff0000", 50], "rgb: set now", str(status()["ring"]))
        time.sleep(1.8)
        check(status()["ring"][0] == "#112233", "rgb: page ring colour restored after ttl", str(status()["ring"]))
        sb, _ = call("POST", "/rgb", {"color": "red"})
        check(sb == 400, "rgb: bad colour -> 400")
        call("POST", "/press?key=7&wait=0.3")
        check(status()["ring"][0] == "#ff0000", "rgb: plugin ctx.rgb sets the ring")
        time.sleep(1.5)
        s, j = call("POST", "/ringtest")
        check(s == 200 and j.get("ok"), "ringtest: path runs on the mock (not run on hardware)", f"{s} {j}")


        # ---------------------------------------------------------------- F2: per-LED ring
        from m18lib import ringmap as RM
        call("POST", "/page?name=main")
        call("DELETE", "/leds")                      # the ringtest frame above holds the ring for 6 s
        frame = ["#ff0000"] * 12 + ["#0000ff"] * 12
        s1, _ = call("POST", "/leds", {"colors": frame}, headers={"X-M18": "1", "Origin": "http://evil.example"})
        s2, _ = call("POST", "/leds", {"colors": frame}, headers={})
        check(s1 == 403 and s2 == 403 and call("GET", "/leds")[1]["owner"] is None, "leds: guard refuses Origin / missing X-M18, nothing drawn")
        check(call("POST", "/leds", {"colors": ["#ff0000"] * 23})[0] == 400 and call("POST", "/leds", {"colors": ["red"] * 24})[0] == 400,
              "leds: wrong length or bad colour -> 400")
        call("POST", "/rgb", {"color": "#ff0000"})
        def leds_settled():
            # the push runs behind a 20 fps cap, so wait until what was sent equals what is wanted
            end = time.time() + 2.0
            while True:
                g = call("GET", "/leds")[1]
                if g.get("sent") == g.get("want") or time.time() > end:
                    return g
                time.sleep(0.05)

        s, j = call("POST", "/leds", {"colors": frame, "owner": "e2e-a"})
        g = leds_settled()
        check(s == 200 and g["owner"] == "e2e-a" and g["sent"] == frame and g["frames_sent"] >= 1, "leds: frame accepted and sent", json.dumps(g)[:200])
        n0, b0 = g["frames_sent"], g["brightness_sent"]
        call("POST", "/leds", {"colors": frame, "owner": "e2e-a"})
        time.sleep(0.2)
        g = leds_settled()
        check(g["frames_sent"] == n0 and g["brightness_sent"] == b0, "leds: an identical frame is not resent (colour nor brightness)", json.dumps(g)[:200])
        time.sleep(0.1)
        call("POST", "/leds", {"colors": ["#00ff00"] * 24, "owner": "e2e-a"})
        g = leds_settled()
        check(g["frames_sent"] == n0 + 1 and g["brightness_sent"] == b0, "leds: a changed frame is sent once, unchanged brightness is not resent", json.dumps(g)[:200])
        t0 = time.time()
        for i in range(12):
            call("POST", "/leds", {"colors": [RM.to_hex((i * 20, 0, 255 - i * 20))] * 24, "owner": "e2e-a"})
        time.sleep(0.3)
        g = call("GET", "/leds")[1]
        check(g["frames_sent"] - (n0 + 1) <= int((time.time() - t0) / 0.05) + 1 and g["sent"] == g["want"], "leds: capped at 20 fps and the last frame still lands", json.dumps(g)[:200])
        call("POST", "/page?name=second")
        check(status()["ring"][0] == "#ff0000" and call("GET", "/leds")[1]["owner"] == "e2e-a", "leds: a page change does not fight the owner")
        call("POST", "/page?name=main")
        s, j = call("POST", "/leds", {"colors": frame, "owner": "e2e-low", "priority": 0})
        check(s == 409 and call("GET", "/leds")[1]["owner"] == "e2e-a", "leds: ambient frame cannot replace a normal owner (409)")
        s, j = call("POST", "/leds", {"colors": frame, "owner": "e2e-b"})
        check(s == 200 and call("GET", "/leds")[1]["owner"] == "e2e-b", "leds: latest same-priority owner wins")
        s, j = call("DELETE", "/leds")
        check(s == 200 and call("GET", "/leds")[1]["owner"] is None and status()["ring"][0] == "#112233", "leds: DELETE restores the page ring colour", str(status()["ring"]))
        call("POST", "/leds", {"colors": frame, "ttl": 1, "owner": "e2e-t"})
        check(call("GET", "/leds")[1]["owner"] == "e2e-t", "leds: ttl frame shows")
        time.sleep(1.8)
        check(call("GET", "/leds")[1]["owner"] is None and status()["ring"][0] == "#112233", "leds: page ring colour back after ttl", str(status()["ring"]))
        call("POST", "/leds", {"colors": frame, "owner": "e2e-a"})
        call("POST", "/press?key=8&wait=0.3")
        s, j = call("POST", "/leds", {"colors": frame, "owner": "e2e-a"})
        check(s == 409 and call("GET", "/leds")[1]["owner"] is None and status()["ring"][0] == "off", "leds: dark mode drops the owner, turns the ring off and refuses frames", f"{s} {status()['ring']}")
        call("POST", "/press?key=8&wait=0.3")
        check(status()["ring"][0] == "#112233", "leds: leaving dark mode restores the page colour")

        # ---------------------------------------------------------------- G: ringwalk (driven by simulated device presses)
        d0 = RM.default_map()

        def walk():
            return call("GET", "/ringwalk")[1]

        def press(k, hold=0.0):
            call("POST", f"/press?key={k}&source=device&hold={hold}&wait=0.05")
            if k == 16 and not hold:
                time.sleep(0.45)                    # a human takes this long between answers; b1 x3 inside 0.8 s aborts

        def wait_step(n, to=3.0):
            end = time.time() + to
            while time.time() < end:
                if walk().get("step") == n or walk().get("state") != "running":
                    return
                time.sleep(0.05)
        mp = tmp / "state" / "ring-map.json"
        s, j = call("POST", "/ringwalk", {"idle_timeout": 20})
        check(s == 200 and walk()["state"] == "running" and status()["modal"]["owner"] == "ringwalk", "ringwalk: starts, takes the pad", f"{s} {j}")
        s2, _ = call("POST", "/ringwalk", {})
        s3, _ = call("POST", "/leds", {"colors": frame, "owner": "e2e-x"})
        check(s2 == 409 and s3 == 409, "ringwalk: a second walk and a normal-priority frame are refused while it runs")
        lit = call("GET", "/leds")[1]["want"]
        check(lit.count("#ffffff") == 1 and lit[0] == "#ffffff", "ringwalk: LED 0 is the only lit LED, white", str(lit))
        for i in range(3):
            press(RM.nearest_key(*d0.xy[i]))
            wait_step(i + 1)
        press(1, hold=1.2)
        wait_step(2)
        check(walk()["step"] == 2, "ringwalk: holding a key 1 s goes back one LED", str(walk()["step"]))
        for i in range(2, 24):
            press(RM.nearest_key(*d0.xy[i]))
            wait_step(i + 1)
        end = time.time() + 3
        while time.time() < end and walk()["state"] == "running":
            time.sleep(0.1)
        w = walk()
        check(w["state"] == "done" and mp.exists() and status()["modal"] is None and call("GET", "/leds")[1]["owner"] is None,
              "ringwalk: finishes, writes ring-map.json, releases pad and ring", json.dumps(w)[:200])
        mj = json.loads(mp.read_text()) if mp.exists() else {"leds": [], "order": []}
        moved = sum(abs(r - i) for r, i in enumerate(mj["order"])) / max(1, len(mj["order"]))
        check(len(mj["leds"]) == 24 and all(e["key"] for e in mj["leds"]) and sorted(mj["order"]) == list(range(22)) and moved <= 1.0,
              "ringwalk: 24 entries with keys, ordering is a permutation close to the LED order", f"mean rank shift {moved:.2f}")
        mtime = mp.stat().st_mtime
        s, j = call("POST", "/ringwalk", {"idle_timeout": 20})
        for _ in range(3):
            call("POST", "/press?key=16&source=device&wait=0.05")
        time.sleep(0.4)
        w = walk()
        check(w["state"] == "aborted" and "b1" in (w["reason"] or "") and w["answers"][:2] == [16, 16] and status()["modal"] is None and mp.stat().st_mtime == mtime
              and call("GET", "/leds")[1]["owner"] is None, "ringwalk: three quick b1 presses abort, nothing saved, b1 answered before that", str(w))
        call("POST", "/ringwalk", {"idle_timeout": 1.2})
        time.sleep(2.4)
        w = walk()
        check(w["state"] == "aborted" and "no press" in (w["reason"] or "") and status()["modal"] is None and mp.stat().st_mtime == mtime,
              "ringwalk: idle timeout aborts and frees the pad", str(w))

        # ---------------------------------------------------------------- J: ring.status
        import importlib.util
        spec = importlib.util.spec_from_file_location("ring_plugin", HERE / "plugins" / "ring.py")
        ringp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ringp)
        rm = RM.load(tmp)
        pal = {"working": "#55ff55", "waiting": "#ffff55", "finished": "#55ffff"}
        ok, detail = True, []
        for cnt in ((1, 0, 0), (3, 2, 1), (40, 5, 9), (0, 5, 0)):
            fr = ringp.status_frame(rm, *cnt, pal, 1.0)
            got = [sum(1 for led in rm.clockwise if fr[led] == pal[nm]) for nm in ("working", "waiting", "finished")]
            tot = sum(cnt)
            for c, g_ in zip(cnt, got):
                if c and (g_ < 1 or abs(g_ - c / tot * 22) > 1.01):
                    ok = False
            if sum(got) != 22 or any(g_ for c, g_ in zip(cnt, got) if not c):
                ok = False
            detail.append(f"{cnt}->{got}")
        check(ok, "ring.status: arc lengths follow the shares, none empty for a nonzero count, at 1 and at 40 sessions", " ".join(detail))
        check(all(ringp.status_frame(rm, 0, 2, 1, pal, 1.0)[i] == pal["waiting"] for i in RM.STRIPS)
              and all(ringp.status_frame(rm, 3, 0, 1, pal, 1.0)[i] == pal["working"] for i in RM.STRIPS),
              "ring.status: front strips show the most urgent state (waiting, else working)")
        sdir = tmp / "state"
        sdir.mkdir(exist_ok=True)
        (sdir / "sessions-summary.json").write_text(json.dumps({"working": 3, "waiting": 2, "finished": 1}))
        time.sleep(1.8)
        a1 = call("GET", "/leds")[1]
        time.sleep(0.55)
        a2 = call("GET", "/leds")[1]
        time.sleep(0.4)
        a3 = call("GET", "/leds")[1]
        cols = a1["want"]
        arcs = [sum(1 for led in rm.clockwise if cols[led] == c) for c in ("#55ff55",)]
        check(a1["owner"] == "ring.status" and a1["priority"] == 0 and arcs == [11]
              and len({json.dumps(x["want"]) for x in (a1, a2, a3)}) > 1,
              "ring.status: live thread draws (3,2,1) as an ambient owner and the waiting arc breathes", json.dumps(a1["want"])[:160])
        (sdir / "sessions-summary.json").write_text(json.dumps({"working": 1, "waiting": 0, "finished": 0}))
        time.sleep(1.8)
        b1_ = call("GET", "/leds")[1]
        time.sleep(0.55)
        b2_ = call("GET", "/leds")[1]
        check(b1_["owner"] == "ring.status" and b1_["want"] == b2_["want"] and set(b1_["want"]) == {"#55ff55"},
              "ring.status: with nothing waiting the ring is static", json.dumps(b1_["want"])[:160])
        s, _ = call("POST", "/leds", {"colors": frame, "owner": "e2e-hi"})
        time.sleep(0.6)
        check(call("GET", "/leds")[1]["owner"] == "e2e-hi", "ring.status: ambient thread yields to another owner")
        call("DELETE", "/leds")
        time.sleep(1.2)
        check(call("GET", "/leds")[1]["owner"] == "ring.status", "ring.status: takes the ring back once the other owner releases")
        (sdir / "sessions-summary.json").write_text(json.dumps({"working": 0, "waiting": 0, "finished": 0}))
        time.sleep(1.8)
        check(call("GET", "/leds")[1]["owner"] is None and status()["ring"][0] == "#112233", "ring.status: all zero releases the ring to the page colour", str(status()["ring"]))
        (sdir / "sessions-summary.json").unlink()

        # ---------------------------------------------------------------- K: mixer layout and press mapping (fake sessions)
        spec = importlib.util.spec_from_file_location("mixer_plugin", HERE / "plugins" / "mixer.py")
        mixp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mixp)
        names = ["spotify.exe", "chrome.exe", "discord.exe", "vlc.exe", "firefox.exe", "obs64.exe", "game.exe"]

        def fake(n):
            raw = [{"name": nm, "volume": 0.5, "muted": False, "state": 1, "peak": 0.3 - 0.02 * i} for i, nm in enumerate(names[:n])]
            raw += [{"name": "silent.exe", "volume": 1.0, "muted": False, "state": 1, "peak": 0.0},     # active but never heard
                    {"name": "idle.exe", "volume": 1.0, "muted": False, "state": 0, "peak": 0.0}]      # inactive
            return raw
        mst = {"volume": 0.6, "muted": False}

        def lay(n, off=0):
            apps = mixp.analyse(fake(n), 1000.0, {})
            return apps, mixp.layout(apps, mst, off)

        bad = []
        apps, L = lay(0)
        if not (L["mode"] == "master-bar" and sorted(L["keys"]) == list(range(1, 16)) and sum(v["lit"] for v in L["keys"].values()) == 9):
            bad.append("0: master bar")
        if mixp.action_for(L, 5) != {"op": "set", "target": "master", "value": 5 / 15} or mixp.action_for(L, 9)["value"] != 8 / 15:
            bad.append("0: press mapping")
        for n in (1, 2, 3):
            apps, L = lay(n)
            ident = [L["keys"][b] for b in (1, 6, 11)]
            want = [short for short in [mixp.short(x) for x in names[:n]]] + ["VOL"] * (3 - n)
            if L["mode"] != "rows" or [i["label"] for i in ident] != want or [i["target"] == "master" for i in ident] != [False] * n + [True] * (3 - n):
                bad.append(f"{n}: rows/master fill")
            if any(L["keys"][b + j]["role"] != "level" for b in (1, 6, 11) for j in range(1, 5)) or len(L["keys"]) != 15:
                bad.append(f"{n}: level keys")
        apps, L = lay(7)
        if not (L["shown"] == names[:3] and L["hidden"] == 4 and L["keys"][11]["role"] == "more" and L["keys"][11]["label"] == "+4"
                and L["keys"][1]["role"] == "identity" and L["keys"][6]["role"] == "identity"):
            bad.append("7: three shown, +4 pager")
        L2 = mixp.layout(apps, mst, mixp.action_for(L, 11)["value"])
        if L2["shown"] != names[2:5]:
            bad.append("7: pager moves the window")
        check(not bad, "mixer: layout for 0, 1, 2, 3 and 7 fake sessions (silent and inactive ones ignored)", "; ".join(bad))

        class Stub:
            def __init__(self):
                self.calls = []

            def set_master(self, v): self.calls.append(("set_master", round(v, 4)))
            def set_master_mute(self, m): self.calls.append(("set_master_mute", m))
            def set_app(self, n, v): self.calls.append(("set_app", n, v))
            def set_app_mute(self, n, m): self.calls.append(("set_app_mute", n, m))
        be = Stub()
        apps, L = lay(2)
        for key in (4, 6, 1, 11, 14):
            mixp.apply(mixp.action_for(L, key), be)
        exp = [("set_app", "spotify.exe", 0.75), ("set_app_mute", "chrome.exe", True), ("set_app_mute", "spotify.exe", True),
               ("set_master_mute", True), ("set_master", 0.75)]
        check(be.calls == exp, "mixer: presses map to the right set-volume / mute calls (stubbed)", str(be.calls))

        # ---------------------------------------------------------------- H: countdown render
        call("POST", "/page?name=main")
        call("POST", "/press?key=1&wait=0.3")
        time.sleep(2.8)
        lg = logtext(tmp)
        check("render key 1 failed" not in lg and "color must be" not in lg, "countdown: hillside-themed render has no error")

        # ---------------------------------------------------------------- I: dryrun
        mark = tmp / "live_mark.txt"
        envd = dict(envc, E2E_LIVE_MARK=str(mark))
        p = subprocess.run([sys.executable, str(HERE / "m18ctl.py"), "dryrun", "2"], env=envd,
                           capture_output=True, text=True, timeout=90)
        check(p.returncode == 0 and '"sent"' in p.stdout and "hotkey ctrl+shift+f12" in p.stdout, "dryrun: prints sent", p.stdout[-200:] + p.stderr[-200:])
        import re
        m = re.search(r"mock on port (\d+), temp (.+)", p.stderr)
        gone = False
        if m:
            try:
                socket.create_connection(("127.0.0.1", int(m.group(1))), timeout=1).close()
            except OSError:
                gone = True
        check(bool(m) and gone and not Path(m.group(2).strip()).exists(), "dryrun: mock exited and temp folder removed")
        check(not mark.exists(), "dryrun: live: plugins did not run")
        p = subprocess.run([sys.executable, str(HERE / "m18ctl.py"), "dryrun", "5"], env=envd,
                           capture_output=True, text=True, timeout=90)
        check("plugin e2eplug.src (skipped)" in p.stdout, "dryrun: plugin actions are logged, not run", p.stdout[-200:])

        # ---------------------------------------------------------------- L: cards
        def ids():
            return [c["id"] for c in call("GET", "/cards")[1]]
        card = {"id": "c1", "feed": "e2e", "title": "First card", "lines": ["one 2"],
                "actions": [{"id": "keep", "label": "Keep"}, {"id": "drop", "label": "Drop", "color": "#aa0000"}]}
        s1, _ = call("POST", "/cards", card, headers={"X-M18": "1", "Origin": "http://evil.example"})
        s2, _ = call("POST", "/cards", card, headers={})
        s3, _ = call("GET", "/cards", headers={})
        check(s1 == 403 and s2 == 403 and s3 == 403 and ids() == [], "cards: guard refuses Origin / missing X-M18, nothing stored")
        bads = [dict(card, title=""), dict(card, actions=[{"id": str(i), "label": "x"} for i in range(5)]),
                dict(card, actions=[{"id": "a", "label": "x", "color": "red"}]), dict(card, priority=3), dict(card, icon="mdi:nope-nope"),
                dict(card, image_png_b64="bm90IGFuIGltYWdl"), dict(card, extra=1)]
        check(all(call("POST", "/cards", b)[0] == 400 for b in bads), "cards: bad cards -> 400 (title, 5 actions, colour, priority, icon, image, unknown field)")
        empty13 = tile(13).tobytes()
        call("POST", "/cards", card)
        time.sleep(0.02)
        call("POST", "/cards", dict(card, id="c2", title="Second"))
        call("POST", "/cards", dict(card, id="c3", title="Urgent", priority=2, image_png_b64=png_b64((200, 30, 30), (160, 90))))
        s, j = call("POST", "/cards", dict(card, title="First card edited"))
        lst = call("GET", "/cards")[1]
        check(s == 200 and j.get("created") is False and ids() == ["c3", "c1", "c2"] and lst[1]["title"] == "First card edited",
              "cards: upsert by id keeps its place; order = priority desc, then oldest first", str(ids()))
        time.sleep(1.5)
        check(tile(13).tobytes() != empty13 and status()["cards"] == 3, "cards: the card key shows the top card (not the empty backdrop)")
        seq = status()["events_seq"]
        call("POST", "/press?key=13&wait=0.6")
        md = status()["modal"]
        call("POST", "/press?key=12&wait=0.6")           # c3's second action: "drop"
        evs = [e for e in call("GET", f"/events?since={seq}")[1]["events"] if e.get("type") == "card_action"]
        check(md and md["owner"] == "plugin:13" and len(evs) == 1 and evs[0]["card"] == "c3" and evs[0]["feed"] == "e2e"
              and evs[0]["action"] == "drop" and evs[0]["source"] == "api" and "c3" not in ids(),
              "cards: press opens the takeover; an action key removes the card and publishes card_action on /events", json.dumps(evs)[:300])
        check(status()["modal"] is not None, "cards: the takeover stays up while cards remain")
        call("POST", "/press?key=b1&source=device&wait=0.4")
        check(status()["modal"] is None and ids() == ["c1", "c2"], "cards: physical b1 ends the takeover and leaves the cards")
        call("POST", "/press?key=14&wait=0.4")
        call("POST", "/press?key=13&wait=0.6")
        call("POST", "/press?key=11&wait=0.8")
        check("E2E_CARDACT plug ok" in logtext(tmp) and "plug" not in ids(), "cards: ctx.cards.post with a handler; the handler runs on its action")
        s, j = call("DELETE", "/cards?id=c2")
        check(s == 200 and j.get("removed") is True and ids() == ["c1"] and call("DELETE", "/cards")[0] == 400, "cards: DELETE /cards?id= removes; no id -> 400")
        call("DELETE", "/modal")
        call("POST", "/cards", dict(card, id="short", ttl_s=0.8))
        time.sleep(1.4)
        check("short" not in ids(), "cards: a ttl card drops when it expires")
        call("POST", "/cards", dict(card, id="ttl3", ttl_s=2.5))
        t_post = time.time()
        call("POST", "/quit", timeout=5)
        proc.wait(timeout=8)
        time.sleep(max(0, t_post + 2.8 - time.time()))
        proc = subprocess.Popen([sys.executable, str(HERE / "m18d.py"), "--mock", "--port", str(PORT),
                                 "--config", str(tmp / "macros.yaml")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                if call("GET", "/status")[0] == 200:
                    break
            except OSError:
                time.sleep(0.25)
        check(ids() == ["c1"] and (tmp / "state" / "cards.json").exists(), "cards: persist across a daemon restart in state/cards.json; expired ttl cards drop", str(ids()))
    finally:
        if proc:
            try:
                call("POST", "/quit", timeout=5)
                proc.wait(timeout=8)
            except Exception:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for ok, n in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
