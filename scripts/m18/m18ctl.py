#!/usr/bin/env python3
"""m18ctl -- command-line control for the M18 daemon. Built for agents.

  m18ctl validate                 check macros.yaml, print every error with its path
  m18ctl icons WORD [WORD..]      search the bundled Material Design Icons (use as icon: mdi:NAME)
  m18ctl keys [--page P]          table of what each key does on a page
  m18ctl preview [--page P|--all] render PNG(s) of the pad (works with or without daemon)
  m18ctl record OUT.gif [--page P] [--seconds S] [--fps F] [--width W]   GIF of a running (mock) page, for sharing
  m18ctl press KEY [--page P] [--hold SEC] [--taps N]   simulate a press (N=2/3: double/triple tap); prints what was sent
  m18ctl page NAME                switch page on the live pad
  m18ctl status                   daemon status, current page, config errors, log tail
  m18ctl reload                   force config reload (normally automatic on save)
  m18ctl identify                 show key numbers on the pad for 8s
  m18ctl dryrun KEY [--page P] [--hold S] [--taps N]   press KEY on a throwaway mock (live: removed); never touches the real pad
  m18ctl face KEY [--label L --icon I --glyph G --bg C --fg C --border C --image F --ttl S] | face --clear KEY
  m18ctl events [--since N] [--timeout S]   long-poll press/release events
  m18ctl modal [--faces JSON] [--image F] [--timeout S] [--owner NAME] | modal --end
  m18ctl rgb COLOR [--brightness N] [--ttl S]   set the ring; it returns to the page colour after ttl
  m18ctl ringtest                 per-LED probe on the real pad (0-11 red, 12-23 blue for 6 s); look at it
  m18ctl ringwalk [--abort] [--idle S]   map each LED to a key by pressing the key nearest the lit one
                                  (modal; hold a key 1 s = back, b1 x3 or 60 s idle = abort); saves state/ring-map.json
  m18ctl ringshow sweep|halves|meter [FRACTION]   debug patterns drawn through the ring map (judge the map by eye)
  m18ctl start [--mock]           start daemon in background (pythonw on Windows)
  m18ctl stop                     stop daemon
  m18ctl doctor                   check python deps, SDK, VSD Craft conflict, device
Exit code is non-zero on any failure so scripts can chain on it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from m18lib import config as C  # noqa: E402

PORT = int(os.environ.get("M18_PORT", 8718))
CFG = Path(os.environ.get("M18_CONFIG", HERE / "macros.yaml"))


def api(method, path, timeout=30, body=None, port=None):
    """Every request carries X-M18: 1; the daemon refuses anything without it (browser guard)."""
    hdr = {"X-M18": "1"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port or PORT}{path}", method=method,
                                 data=data, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
    except (urllib.error.URLError, ConnectionError):
        return None


def need_daemon(res):
    if res is None:
        print("daemon not running (start it with: m18ctl start   or   m18ctl start --mock)")
        sys.exit(3)
    return res


def describe(a) -> str:
    if a is None:
        return "-"
    if isinstance(a, list):
        return " -> ".join(describe(x) for x in a)
    k = next((x for x in a if x in C.ACTION_TYPES), "?")
    v = a.get(k)
    s = f"{k}:{v}" if not isinstance(v, (dict, list)) else f"{k}:{json.dumps(v)}"
    return s if len(s) < 70 else s[:67] + "..."


def cmd_icons(a):
    from m18lib.render import mdi_names
    words = [w.lower() for w in a.words]
    hits = [n for n in mdi_names() if all(w in n for w in words)]
    hits.sort(key=lambda n: (len(n), n))
    for n in hits[:a.limit]:
        print(f"mdi:{n}")
    if not hits:
        print("no match; try a shorter word (icons are named like content-cut, monitor, cellphone)")
        sys.exit(1)


def cmd_validate(_):
    try:
        cfg = C.load(CFG)
    except C.ConfigError as e:
        print(f"INVALID {CFG}")
        for er in e.errors:
            print("  -", er)
        sys.exit(1)
    n = sum(len(p["keys"]) for p in cfg["pages"].values())
    print(f"OK {CFG}: {len(cfg['pages'])} page(s), {n} key binding(s), home={cfg['settings']['home']}")


def cmd_keys(a):
    cfg = C.load(CFG)
    pages = list(cfg["pages"]) if a.all else [a.page or cfg["settings"]["home"]]
    for p in pages:
        if p not in cfg["pages"]:
            print(f"no page {p!r}; pages: {list(cfg['pages'])}"); sys.exit(1)
        page = cfg["pages"][p]
        extra = []
        if page.get("follow"): extra.append(f"follow={page['follow']}")
        if page.get("inherit"): extra.append(f"inherit={page['inherit']}")
        print(f"== page {p} {' '.join(extra)}")
        rk = C.resolved_keys(cfg, p)
        for k in C.ALL_KEYS:
            kd = rk.get(k)
            name = f"b{k-15}" if k > 15 else str(k)
            if not kd:
                print(f"  {name:>3}  (free)")
                continue
            vis = kd.get("label") or kd.get("glyph") or kd.get("emoji") or ""
            act = describe(kd.get("action"))
            if "states" in kd:
                act = "states: " + " | ".join(describe(s.get("action")) for s in kd["states"])
            for g in C.TAP_FIELDS:
                if g in kd:
                    act += f"   [{g}: {describe(kd[g])}]"
            if "live" in kd:
                act += f"   [live: {kd['live']['plugin']}]"
            if kd.get("physical_only"):
                act += "   [physical_only]"
            print(f"  {name:>3}  {str(vis)[:18]:<18}  {act}")


def cmd_preview(a):
    st = None if a.offline else api("GET", "/status")
    if st and st.get("pages"):                     # the daemon renders any page without touching the pad
        for p in (st["pages"] if a.all else [a.page or st["page"]]):
            res = api("GET", "/preview?page=" + urllib.parse.quote(p))
            print(res["path"] if res and "path" in res else f"{p}: {res}")
        return
    # offline render: build a daemon around a mock device, never start it
    from m18d import Daemon
    from m18lib.device import MockDevice
    d = Daemon(CFG, MockDevice(), 0)
    d.log = lambda *x: None
    try:
        d.load_config(initial=True)
    except C.ConfigError as e:
        print("INVALID config; fix first:"); [print("  -", x) for x in e.errors]; sys.exit(1)
    pages = list(d.cfg["pages"]) if a.all else [a.page or d.page]
    for p in pages:
        print(d.preview(p))


def cmd_signals(a):
    """List the signals a scene can use, with a reading of each."""
    from m18lib import signals as SG
    hub = SG.Hub(CFG.parent, sim=a.sim)
    names = hub.names()
    snap = hub.snapshot(names)
    time.sleep(1.2); snap = hub.snapshot(names)        # rates (cpu, net) need a second reading
    for n in names:
        s = getattr(snap, n)
        v = "  -- " if s.value is None else f"{s.value:5.2f}"
        print(f"{n:12} {v}  {s.text:9} {'' if s.live else '(simulated) '}{hub.describe(n)}")


def cmd_scene(a):
    """scene list | scene new NAME | scene shot NAME: work on a scene file without a daemon or a pad."""
    from PIL import Image
    from m18lib import scene as S, signals as SG
    if a.what == "list":
        print("\n".join(S.names(CFG.parent)) or "(no scenes)"); return
    if not a.name:
        print("scene name needed"); sys.exit(1)
    if a.what == "new":
        dst = CFG.parent / "scenes" / f"{a.name}.py"
        if dst.exists():
            print(f"exists: {dst}"); sys.exit(1)
        dst.parent.mkdir(exist_ok=True)
        dst.write_text((S.HERE / "scenes" / "_template.py").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"{dst}\nadd to macros.yaml under pages:   {a.name}: {{scene: {a.name}}}"); return
    hub = SG.Hub(CFG.parent, sim=True)                    # shot: simulated signals unless pinned with --set
    try:
        hub.fix(**{k: float(v) for k, v in (x.split("=", 1) for x in (a.set or []))})
    except ValueError:
        print("--set takes name=number pairs, e.g. --set cpu=0.9 ram=0.2"); sys.exit(1)
    errors = []
    opts = {}
    try:                                            # the options of the first page that uses this scene
        import yaml
        for pg in ((yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}).get("pages") or {}).values():
            sc = (pg or {}).get("scene")
            if isinstance(sc, dict) and sc.get("name") == a.name:
                opts = {k: v for k, v in sc.items() if k not in ("name", "hold")}
                break
    except Exception:                               # noqa: BLE001
        pass
    pl = S.Player(CFG.parent, a.name, hub, log=errors.append, opts=opts)
    times = [float(x) for x in a.t.split(",")]
    frames = []
    for i, t in enumerate(times):
        steps = max(1, int((t - (times[i - 1] if i else 0)) * 4))      # advance stateful scenes at ~4 fps
        t_prev = times[i - 1] if i else 0.0
        for j in range(1, steps + 1):
            im = pl.frame(t_prev + (t - t_prev) * j / steps)
            if a.tap and i == 0 and j == steps:
                pl.tap(a.tap); im = pl.frame(t)
        im = im if a.raw else S.bars(im)
        if a.far:                                   # as the eye gets it from the chair: detail under 3 px merges
            im = im.resize((im.width // 3, im.height // 3), Image.BOX).resize(im.size, Image.BILINEAR)
        frames.append(im)
    sheet = Image.new("RGB", (S.W, len(frames) * S.H + (len(frames) - 1) * 12), (40, 40, 40))
    for i, im in enumerate(frames):
        sheet.paste(im, (0, i * (S.H + 12)))
    if a.scale and a.scale != 1:
        sheet = sheet.resize((int(sheet.width * a.scale), int(sheet.height * a.scale)), Image.NEAREST)
    tag = "".join(f"-{x.replace('=', '')}" for x in (a.set or [])) + (f"-tap{a.tap}" if a.tap else "") + (f"-t{a.t.replace(',', '_')}" if a.t != "3" else "")
    out = Path(a.out) if a.out else CFG.parent / f"scene-{a.name}{tag}.png"
    sheet.save(out)
    print(out)
    for e in errors:
        print("ERROR", e)
    if errors:
        sys.exit(1)


def cmd_record(a):
    """Poll the running daemon's preview and save the frames as a looping GIF (needs a started daemon,
    usually `start --mock`; threaded scenes only animate there)."""
    from PIL import Image
    st = need_daemon(api("GET", "/status"))
    page = a.page or st["page"]
    if st["page"] != page:
        api("POST", "/page?name=" + urllib.parse.quote(page)); time.sleep(1.0)
    frames, step, t_end = [], 1.0 / a.fps, time.time() + a.seconds
    while time.time() < t_end:
        t0 = time.time()
        res = api("GET", "/preview?page=" + urllib.parse.quote(page))
        if not res or "path" not in res:
            print(f"preview failed: {res}"); sys.exit(1)
        with Image.open(res["path"]) as im:
            im = im.convert("RGB")
            if a.width and im.width > a.width:
                im = im.resize((a.width, round(im.height * a.width / im.width)), Image.LANCZOS)
            frames.append(im.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
        time.sleep(max(0.0, step - (time.time() - t0)))
    if len(frames) < 2:
        print("too few frames"); sys.exit(1)
    real = a.seconds / len(frames)
    out = Path(a.out).resolve()
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=int(real * 1000), loop=0, optimize=True)
    print(f"{out}  {len(frames)} frames, {1 / real:.1f} fps, {out.stat().st_size // 1024} KB")


def cmd_press(a):
    q = f"/press?key={a.key}&hold={a.hold}&wait={a.wait}&taps={a.taps}" + (f"&page={a.page}" if a.page else "")
    r = need_daemon(api("POST", q, timeout=60))
    print(json.dumps(r, indent=2))
    if "error" in r: sys.exit(1)


def cmd_dryrun(a):
    """Throwaway mock: copy of the config with every `live:` removed, free port, press, quit, clean up."""
    import yaml
    raw = yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}
    for pg in (raw.get("pages") or {}).values():
        for kd in ((pg or {}).get("keys") or {}).values():
            if isinstance(kd, dict):
                kd.pop("live", None)
    tmp = Path(tempfile.mkdtemp(prefix="m18dry_"))
    proc = None
    try:
        (tmp / "macros.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
                                         encoding="utf-8")
        for sub in ("themes", "icons", "plugins"):
            if (CFG.parent / sub).is_dir():
                shutil.copytree(CFG.parent / sub, tmp / sub, ignore=shutil.ignore_patterns("__pycache__"))
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        proc = subprocess.Popen([sys.executable, str(HERE / "m18d.py"), "--mock", "--skip-plugin-actions",
                                 "--port", str(port), "--config", str(tmp / "macros.yaml")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        print(f"dryrun: mock on port {port}, temp {tmp}", file=sys.stderr)
        for _ in range(60):
            if api("GET", "/status", port=port):
                break
            if proc.poll() is not None:
                print("dryrun: mock daemon exited early (config invalid? run m18ctl validate)"); sys.exit(1)
            time.sleep(0.25)
        else:
            print("dryrun: mock daemon did not come up"); sys.exit(1)
        q = f"/press?key={a.key}&hold={a.hold}&wait=0.5&taps={a.taps}" + (f"&page={a.page}" if a.page else "")
        r = api("POST", q, timeout=60, port=port)
        print(json.dumps(r, indent=2))
        api("POST", "/quit", port=port)
        if r is None or "error" in r:
            sys.exit(1)
    finally:
        if proc:
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)


def _img_b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def cmd_face(a):
    if a.clear:
        r = need_daemon(api("DELETE", f"/face?key={a.key}"))
    else:
        face = {f: getattr(a, f) for f in ("label", "icon", "glyph", "emoji", "bg", "fg", "border")
                if getattr(a, f)}
        body = {"key": int(a.key) if a.key.isdigit() else a.key, "face": face, "ttl": a.ttl}
        if a.image:
            body["image_b64"] = _img_b64(a.image)
        r = need_daemon(api("POST", "/face", body=body))
    print(json.dumps(r, indent=2))
    if "error" in r: sys.exit(1)


def cmd_events(a):
    r = need_daemon(api("GET", f"/events?since={a.since}&timeout={a.timeout}", timeout=a.timeout + 10))
    print(json.dumps(r, indent=2))


def cmd_modal(a):
    if a.end:
        r = need_daemon(api("DELETE", "/modal"))
    else:
        body = {"faces": json.loads(a.faces or "{}"), "timeout": a.timeout, "owner": a.owner}
        if a.image:
            body["image_b64"] = _img_b64(a.image)
        r = need_daemon(api("POST", "/modal", body=body))
    print(json.dumps(r, indent=2))
    if "error" in r: sys.exit(1)


def cmd_rgb(a):
    r = need_daemon(api("POST", "/rgb", body={"color": a.color, "brightness": a.brightness, "ttl": a.ttl}))
    print(json.dumps(r, indent=2))
    if "error" in r: sys.exit(1)


def cmd_ringtest(_):
    r = need_daemon(api("POST", "/ringtest"))
    print(json.dumps(r, indent=2))
    if "error" in r: sys.exit(1)
    print("Look at the ring for 6 s: LEDs 0-11 should be red and 12-23 blue. All one colour, "
          "or unchanged, means this firmware ignores the per-LED frame.")


def cmd_ringwalk(a):
    if a.abort:
        print(json.dumps(need_daemon(api("DELETE", "/ringwalk")), indent=2)); return
    body = {"idle_timeout": a.idle} if a.idle else {}
    r = need_daemon(api("POST", "/ringwalk", body=body))
    if "error" in r:
        print(json.dumps(r, indent=2)); sys.exit(1)
    print("ringwalk started. One LED glows white; press the pad key NEAREST to it (b1/b2/b3 for the "
          "bottom/front). Hold any key 1 s = back one LED. b1 three times quickly = quit.", flush=True)
    last = -1
    while True:
        time.sleep(0.4)
        r = need_daemon(api("GET", "/ringwalk"))
        if r.get("step") != last and r.get("state") == "running":
            last = r["step"]
            print(f"  LED {last + 1}/{r['total']}", flush=True)
        if r.get("state") == "done":
            print(f"done: {r['path']}   keys by LED: {r['answers']}")
            return
        if r.get("state") == "aborted":
            print(f"aborted: {r.get('reason')} (nothing saved)"); sys.exit(1)


def cmd_ringshow(a):
    from m18lib import ringmap as RM
    rm = RM.load(CFG.parent)
    print(f"ring map: {rm.source}" + (f" ({rm.created})" if rm.created else " (default; run ringwalk to measure)"))

    def show(frame, ttl=1.5):
        r = api("POST", "/leds", body={"colors": frame, "ttl": ttl, "owner": "ringshow", "priority": 2})
        if r is None:
            need_daemon(None)
        if "error" in r:
            print(r["error"]); sys.exit(1)
    try:
        if a.name == "sweep":
            print("one LED travels along the perimeter order (as the map sees it): twice")
            for _ in range(2):
                for led in rm.order:
                    f = [None] * 24
                    f[led] = "#ffffff"
                    show(f)
                    time.sleep(0.18)
        elif a.name == "halves":
            print("top (y < 0.5) blue, bottom amber, front strips green, by the map's y")
            f = [None] * 24
            for i in range(24):
                f[i] = "#00a0ff" if rm.xy[i][1] < 0.5 else "#ff8000"
            for i in RM.STRIPS:
                f[i] = "#00ff60"
            show(f, 6)
            time.sleep(6)
        elif a.name == "meter":
            fr = float(a.fraction)
            print(f"meter {fr:g}: fills clockwise from the top-left corner, marker at 0.90")
            show(RM.meter_frame(rm, fr, "#3ddc84", stop_at=0.9, track="#101010"), 6)
            time.sleep(6)
    finally:
        api("DELETE", "/leds?owner=ringshow")


def cmd_simple(method, path):
    def f(_):
        r = need_daemon(api(method, path))
        print(json.dumps(r, indent=2))
        if r.get("error") or r.get("ok") is False: sys.exit(1)
    return f


def cmd_page(a):
    print(json.dumps(need_daemon(api("POST", f"/page?name={a.name}")), indent=2))


def cmd_start(a):
    if api("GET", "/status"):
        print("already running"); return
    py = sys.executable
    if sys.platform == "win32" and not a.foreground:
        pyw = Path(py).with_name("pythonw.exe")
        py = str(pyw) if pyw.exists() else py
    argv = [py, str(HERE / "m18d.py"), "--config", str(CFG)] + (["--mock"] if a.mock else [])
    if a.foreground:
        os.execv(py, argv)
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kw["start_new_session"] = True
    log = open(HERE / "m18d.stdout.log", "a")
    subprocess.Popen(argv, stdout=log, stderr=log, cwd=str(HERE), **kw)
    for _ in range(40):
        time.sleep(0.25)
        s = api("GET", "/status")
        if s:
            print(f"started: device={s['device']} connected={s['connected']} page={s['page']}")
            return
    print("daemon did not come up; see", HERE / "m18d.stdout.log"); sys.exit(1)


def cmd_doctor(_):
    ok = True
    def chk(name, cond, fix=""):
        nonlocal ok
        print(f"[{'ok' if cond else 'XX'}] {name}" + ("" if cond else f"  -> {fix}"))
        ok &= bool(cond)
    for mod in ("PIL", "yaml"):
        try: __import__(mod); chk(f"python module {mod}", True)
        except ImportError: chk(f"python module {mod}", False, "pip install pillow pyyaml")
    sdk = Path(os.environ.get("M18_SDK", HERE / "sdk" / "Python-SDK" / "src"))
    chk(f"VSDinside SDK at {sdk}", (sdk / "StreamDock" / "Devices" / "StreamDockM18.py").exists(),
        "run setup.ps1 (clones github.com/VSDinside/VSDinside-Plugin-SDK)")
    try: C.load(CFG); chk(f"config {CFG}", True)
    except Exception as e: chk(f"config {CFG}", False, str(e).splitlines()[0])
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout.lower()
        chk("VSD Craft not running", "vsd" not in out and "streamdock" not in out,
            "quit VSD Craft from the tray; it holds the device")
        if sdk.exists():
            sys.path.insert(0, str(sdk))
            try:
                from StreamDock.DeviceManager import DeviceManager
                devs = [type(d).__name__ for d in DeviceManager().enumerate()]
                chk(f"M18 on USB (found: {devs or 'nothing'})", "StreamDockM18" in devs,
                    "plug it in / try another port / check it shows in Device Manager > HID")
            except Exception as e:
                chk("SDK loads", False, str(e))
    s = api("GET", "/status")
    print(f"[..] daemon: {'running, page=' + s['page'] + ', device=' + s['device'] if s else 'not running'}")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("validate").set_defaults(f=cmd_validate)
    p = sp.add_parser("icons"); p.add_argument("words", nargs="+"); p.add_argument("--limit", type=int, default=25)
    p.set_defaults(f=cmd_icons)
    p = sp.add_parser("keys"); p.add_argument("--page"); p.add_argument("--all", action="store_true"); p.set_defaults(f=cmd_keys)
    p = sp.add_parser("preview"); p.add_argument("--page"); p.add_argument("--all", action="store_true")
    p.add_argument("--offline", action="store_true"); p.set_defaults(f=cmd_preview)
    p = sp.add_parser("signals"); p.add_argument("--sim", action="store_true"); p.set_defaults(f=cmd_signals)
    p = sp.add_parser("scene"); p.add_argument("what", choices=["list", "new", "shot"]); p.add_argument("name", nargs="?")
    p.add_argument("--set", nargs="*", metavar="NAME=0..1"); p.add_argument("--t", default="3", help="seconds, e.g. 0,5,20 for a strip")
    p.add_argument("--out"); p.add_argument("--raw", action="store_true", help="do not black out the bars")
    p.add_argument("--tap", type=int, metavar="KEY", help="tap this key at the first --t time")
    p.add_argument("--far", action="store_true", help="blur to what reads from 2-3 feet away")
    p.add_argument("--scale", type=float, default=2); p.set_defaults(f=cmd_scene)
    p = sp.add_parser("record"); p.add_argument("out"); p.add_argument("--page"); p.add_argument("--seconds", type=float, default=6)
    p.add_argument("--fps", type=float, default=10); p.add_argument("--width", type=int, default=480); p.set_defaults(f=cmd_record)
    p = sp.add_parser("press"); p.add_argument("key"); p.add_argument("--page"); p.add_argument("--hold", type=float, default=0)
    p.add_argument("--taps", type=int, default=1, choices=(1, 2, 3))
    p.add_argument("--wait", type=float, default=0.4); p.set_defaults(f=cmd_press)
    p = sp.add_parser("page"); p.add_argument("name"); p.set_defaults(f=cmd_page)
    p = sp.add_parser("dryrun"); p.add_argument("key"); p.add_argument("--page"); p.add_argument("--hold", type=float, default=0)
    p.add_argument("--taps", type=int, default=1, choices=(1, 2, 3))
    p.set_defaults(f=cmd_dryrun)
    p = sp.add_parser("face"); p.add_argument("key"); p.add_argument("--clear", action="store_true")
    for f in ("label", "icon", "glyph", "emoji", "bg", "fg", "border", "image"):
        p.add_argument(f"--{f}")
    p.add_argument("--ttl", type=float); p.set_defaults(f=cmd_face)
    p = sp.add_parser("events"); p.add_argument("--since", type=int, default=0)
    p.add_argument("--timeout", type=float, default=0); p.set_defaults(f=cmd_events)
    p = sp.add_parser("modal"); p.add_argument("--faces"); p.add_argument("--image"); p.add_argument("--end", action="store_true")
    p.add_argument("--timeout", type=float, default=120); p.add_argument("--owner", default="m18ctl"); p.set_defaults(f=cmd_modal)
    p = sp.add_parser("rgb"); p.add_argument("color"); p.add_argument("--brightness", type=int)
    p.add_argument("--ttl", type=float); p.set_defaults(f=cmd_rgb)
    sp.add_parser("ringtest").set_defaults(f=cmd_ringtest)
    p = sp.add_parser("ringwalk"); p.add_argument("--abort", action="store_true")
    p.add_argument("--idle", type=float); p.set_defaults(f=cmd_ringwalk)
    p = sp.add_parser("ringshow"); p.add_argument("name", choices=["sweep", "halves", "meter"])
    p.add_argument("fraction", nargs="?", default="0.35"); p.set_defaults(f=cmd_ringshow)
    sp.add_parser("status").set_defaults(f=cmd_simple("GET", "/status"))
    sp.add_parser("reload").set_defaults(f=cmd_simple("POST", "/reload"))
    sp.add_parser("identify").set_defaults(f=cmd_simple("POST", "/identify"))
    sp.add_parser("stop").set_defaults(f=cmd_simple("POST", "/quit"))
    p = sp.add_parser("start"); p.add_argument("--mock", action="store_true"); p.add_argument("--foreground", action="store_true")
    p.set_defaults(f=cmd_start)
    sp.add_parser("doctor").set_defaults(f=cmd_doctor)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
