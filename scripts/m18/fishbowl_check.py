#!/usr/bin/env python3
"""Checks and previews for plugins/fishbowl.py. Prints PASS/FAIL lines; exit 1 on any FAIL.

    python fishbowl_check.py              # mapping, bar geometry, tap hit test, previews
    python fishbowl_check.py --cost       # also: mock daemon with the bowl up, CPU and USB writes per second
    python fishbowl_check.py --only preview

Previews (the pad as it looks: tiles with the black bars, case, ring) go to M18_PREVIEW_DIR
(default %TEMP%\\m18fish\\preview): bowl-0.gif, bowl-3.gif, bowl-12.gif, contact sheets, the plain look.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SKILL = HERE.parent.parent
PREVIEW = Path(os.environ.get("M18_PREVIEW_DIR") or Path(tempfile.gettempdir()) / "m18tank" / "preview")
TMP = Path(tempfile.gettempdir()) / "m18tank" / "check"
FX = {1: "lamp", 5: "buoy", 11: "gauge", 15: "chest"}        # sets-tank.yaml's fixtures
LIVE_FACES = Path.home() / "tools" / "m18" / "state" / "session-faces"     # read only, for real sprites
FAILS: list = []

from PIL import Image  # noqa: E402


def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def load():
    spec = importlib.util.spec_from_file_location("fishbowl_under_test", HERE / "plugins" / "fishbowl.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["fishbowl_under_test"] = m
    spec.loader.exec_module(m)
    return m


def theme_base():
    b = TMP / "base"
    t = b / "themes" / "hillside"
    t.mkdir(parents=True, exist_ok=True)
    shutil.copy(SKILL / "aesthetics" / "hillside" / "theme.py", t / "theme.py")
    return b


def pen(m, themed=True):
    s = {"theme": {"name": "hillside"}} if themed else {}
    return m.make_pen(m._PenHost({"settings": s}, theme_base()))


# ------------------------------------------------------------------ fixtures
def real_grids():
    out = []
    try:
        for p in sorted(LIVE_FACES.glob("*.json")):
            if p.name.startswith("_"):
                continue
            j = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(j.get("icon"), list) and len(j["icon"]) == 16:
                out.append((j.get("label") or "session", j["icon"]))
    except OSError:
        pass
    return out


def rows(n, m):
    """n session rows in mixed states (sessions-summary.json shape plus label/grid)."""
    mix = [("working", None, False), ("waiting", "question", False), ("finished", None, False),
           ("working", None, False), ("waiting", "approval", False), ("working", None, True),
           ("waiting", "blocked", False), ("finished", None, False), ("working", None, False),
           ("finished", None, False), ("working", None, False), ("working", None, False)]
    names = ["Auth fix", "Pad fish", "Mixer", "Ring map", "Deploy", "Scrape", "Voice", "Paint", "Wiki",
             "Codex run", "Bench", "Login"]
    species = ["opus", "sonnet", "opus", "haiku", "opus", "sonnet", "opus", "luna", "sol", "opus", "haiku", "sonnet"]
    tools = [None, None, None, "edit", None, None, None, None, "run", None, "read", "web"]
    rg = real_grids()
    out = []
    for i in range(n):
        st, kind, silent = mix[i % len(mix)]
        sid = f"fixture-{i:02d}-0000-0000-0000-000000000000"
        lab, grid = rg[i] if i < len(rg) else (names[i % len(names)], m.fallback_grid(sid))
        out.append(dict(id=sid, title=f"session {i}", state=st, kind=kind, silent=silent,
                        silent_s=900 if silent else 0, urgency=3.0 if st == "waiting" else 0.0,
                        label=lab, grid=grid, species=species[i % 12],
                        agent="codex" if species[i % 12] in ("luna", "sol") else "claude",
                        tool=tools[i % 12] if st == "working" and not silent else None, subagents=0))
    return out


# ------------------------------------------------------------------ checks
def check_mapping(m):
    """Fixture rows in, creature mode and behaviour out (after the tank has run 12 s)."""
    geo = m.Geo()
    tank = m.Tank(geo, seed=3)
    rs = rows(12, m)
    tank.sync(rs, 0.0, first=True)
    for i in range(int(12 * 8)):
        tank.step(1 / 8, i / 8)
    want = {"working": "swim", "finished": "rest"}
    for r in rs:
        f = tank.fish[r["id"]]
        exp = m.mode_for(r)
        chk(f"mode {r['state']}/{r['kind']}/silent={r['silent']} -> {exp}", f.mode == exp, f.mode)
    by = {}
    for f in tank.fish.values():
        by.setdefault(f.mode, []).append(f)
    top = geo.pitch                                       # bottom of the top row's screens (+ bar)
    chk("glass: waiting creatures in the top row, facing out", all(f.y < 64 and f.pose(0)[0] == "front" for f in by["glass"]),
        [(round(f.y), f.pose(0)[0]) for f in by["glass"]])
    chk("glass: each on a different key, never behind a bar",
        len({geo.key_at(f.x, f.y) for f in by["glass"]}) == len(by["glass"]) and all(geo.key_at(f.x, f.y) for f in by["glass"]))
    chk("stuck: blocked creature sits on a kelp plant in the middle row",
        all(geo.key_at(f.x, f.y) in range(6, 11) for f in by["stuck"]), [(f.x, f.y) for f in by["stuck"]])
    chk("rest: finished creatures lie on the sand of the bottom row",
        all(geo.key_at(f.x, f.y) in range(11, 16) and f.bbox()[3] >= geo.sand for f in by["rest"]),
        [f.bbox() for f in by["rest"]])
    chk("hide: silent creature is pale", all(f.pose(0)[2] == "pale" for f in by["hide"]))

    def peek(f):
        img, anc = f.sprite(f.pose(0))
        a, b, c, d = f.bbox()
        ex = a + max(x for x, _ in anc["eye"])
        ey = b + anc["eye"][0][1]
        k = geo.key_at(ex, ey)
        xs = geo.key_rect(k)[0] if k else None
        return geo.key_at(f.x, f.y) is None and k is not None and c - xs <= 14
    chk("hide: centred behind a bar, its eye (and at most 14 px of head) in a screen", all(peek(f) for f in by["hide"]),
        [tuple(round(v) for v in f.bbox()) for f in by["hide"]])
    for mode in ("glass", "rest", "stuck"):
        ks = [geo.key_at(f.x, f.y) for f in by.get(mode, [])]
        chk(f"habitat: one {mode} creature per screen", len(set(ks)) == len(ks) and None not in ks, ks)

    def inside(f):
        a, b, c, d = f.bbox()
        k = geo.key_at(f.x, f.y)
        if not k:
            return False
        x0, y0, x1, y1 = geo.key_rect(k)
        return a >= x0 - 1 and c <= x1 + 1 and b >= y0 - 1 and d <= y1 + 1
    chk("habitat: waiting and resting creatures sit fully inside their screen",
        all(inside(f) for f in by["glass"] + by["rest"]), [tuple(round(v) for v in f.bbox()) for f in by["glass"] + by["rest"]])
    # motion: swimmers move with purpose, hiders and resters not at all
    pos = {f.id: (f.x, f.y) for f in tank.fish.values()}
    for i in range(16):
        tank.step(1 / 8, 12 + i / 8)
    moved = {f.id: ((f.x - pos[f.id][0]) ** 2 + (f.y - pos[f.id][1]) ** 2) ** 0.5 / 2 for f in tank.fish.values()}
    chk("swim: working creatures move >= 6 px/s", all(moved[f.id] >= 6 for f in by["swim"] if f.tool != "web"),
        [(f.species, f.tool, round(moved[f.id], 1)) for f in by["swim"]])
    chk("hide: silent creature stays in its bar", all(moved[f.id] < 0.5 for f in by["hide"]))
    chk("rest: finished creatures stay put", all(moved[f.id] < 0.5 for f in by["rest"]))
    sp = {f.species: f.speed for f in by["swim"]}
    chk("species: haiku faster than sonnet faster than opus (base speed)",
        m.A.SPEED["haiku"] > m.A.SPEED["sonnet"] > m.A.SPEED["opus"])
    idt = m.ident_for(rs[0]["id"], rs[0]["grid"])
    sz = {s: m.A.side(s, idt)[0].size for s in ("opus", "sonnet", "haiku")}
    chk("species: opus larger than sonnet larger than haiku (area)",
        sz["opus"][0] * sz["opus"][1] > sz["sonnet"][0] * sz["sonnet"][1] > sz["haiku"][0] * sz["haiku"][1], sz)
    chk("species: 10-30 px long", all(10 <= w <= 30 for w, h in sz.values()), sz)
    # crashed -> belly-up at the top until tapped
    crash = dict(rs[2], id="fixture-crash", crashed=True, acked=False, label="Deploy")
    chk("mode crashed -> dead", m.mode_for(crash) == "dead")
    chk("mode crashed + acked -> its normal mode", m.mode_for(dict(crash, acked=True)) == "rest")
    chk("bubbles rise from swimmers", len(tank.bubbles) > 2)
    # lifecycle: a new session hatches, a removed one swims off
    new = dict(rs[0], id="fixture-new", label="New one")
    rs2 = rs[1:] + [new]
    tank.sync(rs2, 13.0)
    chk("new session -> hatch", tank.fish["fixture-new"].mode == "hatch")
    chk("removed session -> leave", any(f.id == rs[0]["id"] and f.mode == "leave" for f in tank.gone))
    for i in range(int(5 * 8)):
        tank.step(1 / 8, 13 + i / 8)
    chk("hatched creature takes its real mode after ~3 s", tank.fish["fixture-new"].mode == "swim",
        tank.fish["fixture-new"].mode)
    for i in range(int(10 * 8)):
        tank.step(1 / 8, 18 + i / 8)
    chk("left creature is gone off the side", not any(f.id == rs[0]["id"] for f in tank.gone))
    tank.sync([], 30.0)
    for i in range(int(12 * 8)):
        tank.step(1 / 8, 30 + i / 8)
    chk("zero sessions: empty tank renders", not tank.fish and not tank.gone and tank.render().size == (geo.W, geo.H))


def check_bars(m):
    """A creature crossing from key 1 to key 2 is hidden for exactly the bar's width; the crop equals tiles.split."""
    from m18lib import tiles as T
    for gap in (T.GAP, 20):
        geo = m.Geo(gap)
        chk(f"gap {gap}: world size = tiles.block_size", (geo.W, geo.H) == T.block_size(5, 3, gap))
        world = Image.new("RGB", (geo.W, geo.H))
        px = world.load()
        for y in range(geo.H):
            for x in range(geo.W):
                px[x, y] = (x % 256, y % 256, (x // 256) * 60)
        sp = T.split(world, 5, 3, gap)
        mine = geo.tiles(world)
        chk(f"gap {gap}: tiles == tiles.split", all(mine[1 + r * 5 + c].tobytes() == sp[(c, r)].tobytes()
                                                     for r in range(3) for c in range(5)))
        # a 6-px marker moving 1 px per frame along y=30: count frames where any of it shows
        vis, hidden = [], 0
        for x in range(40, 64 + gap + 30):
            w = Image.new("RGB", (geo.W, geo.H))
            w.paste((255, 255, 255), (x, 28, x + 6, 34))
            tl = geo.tiles(w)
            on1 = tl[1].getbbox() is not None
            on2 = tl[2].getbbox() is not None
            if not on1 and not on2:
                hidden += 1
            if on2:
                bb = tl[2].getbbox()
                vis.append((x, bb[0]))
        chk(f"gap {gap}: fully hidden for gap - width + 1 frames", hidden == gap - 6 + 1, hidden)
        first = vis[0] if vis else None
        chk(f"gap {gap}: reappears in key 2 at world x - (64 + gap)",
            first is not None and first[0] == 64 + gap - 5 and all(bx == max(0, x - (64 + gap)) for x, bx in vis), first)
        chk(f"gap {gap}: key_at is None behind the bar", geo.key_at(64 + gap / 2, 30) is None and geo.key_at(10, 10) == 1
            and geo.key_at(64 + gap + 1, 64 + gap + 1) == 7)


def check_taps(m):
    geo = m.Geo()
    tank = m.Tank(geo, seed=5)
    rs = rows(3, m)                                     # working, waiting(question), finished
    tank.sync(rs, 0.0, first=True)
    for i in range(80):
        tank.step(1 / 8, i / 8)
    waiting = next(f for f in tank.fish.values() if f.mode == "glass")
    k = geo.key_at(waiting.x, waiting.y)
    chk("hit: tap on the waiting creature's key finds it", tank.hit(k) == waiting.id, (k, tank.hit(k)))
    rest = next(f for f in tank.fish.values() if f.mode == "rest")
    kr = geo.key_at(rest.x, rest.y)
    chk("hit: tap on the resting creature's key finds it", tank.hit(kr) == rest.id)
    # half behind a bar: a creature centred on the bar between keys 7 and 8 belongs to both neighbours
    sw = next(f for f in tank.fish.values() if f.mode == "swim")
    sw.target = (64 * 2 + geo.gap * 1.5, geo.centre(7)[1])
    sw.x, sw.y = sw.target
    sw.speed = 0.0
    chk("hit: creature behind the bar counts for the key beside it", tank.hit(7) == sw.id and tank.hit(8) == sw.id,
        (tank.hit(7), tank.hit(8)))
    empty = next(k for k in range(1, 16) if tank.hit(k) is None)
    chk("tap on empty water -> food + ripple", tank.tap(empty, 10.0) == ("food", None) and tank.food and tank.ripples)
    chk("first tap on a creature -> name plate", tank.tap(k, 10.0) == ("plate", waiting.id) and tank.plate["key"] == k)
    chk("second tap within 3 s -> open", tank.tap(k, 11.0) == ("open", waiting.id))
    tank.tap(k, 12.0)
    chk("second tap after the plate is gone -> plate again", tank.tap(k, 16.0) == ("plate", waiting.id))
    faces = tank.faces(16.0, pen(m))
    plain = tank.geo.tiles(tank.render(16.0))[k]
    chk("plate is drawn on that key only", faces[k]["image"].tobytes() != plain.tobytes()
        and all(faces[j]["image"].tobytes() == tank.geo.tiles(tank.render(16.0))[j].tobytes() for j in range(1, 16) if j != k))
    chk("hold on a creature -> nudge", tank.tap(k, 20.0, held=True) == ("nudge", waiting.id))
    chk("hold on empty water -> food, not a nudge", tank.tap(empty, 20.0, held=True) == ("food", None))
    # belly-up: tap acknowledges and the creature takes its real mode
    crash = dict(rs[2], id="fixture-crash", crashed=True, acked=False, label="Deploy")
    tank.sync(rs + [crash], 21.0)
    for i in range(120):
        tank.step(1 / 8, 21 + i / 8)
    dead = tank.fish["fixture-crash"]
    kd = geo.key_at(dead.x, dead.y)
    chk("dead: crashed creature floats in the top row", dead.mode == "dead" and kd in range(1, 6), (dead.mode, kd))
    chk("dead: tap acknowledges it", kd is not None and tank.tap(kd, 36.0) == ("ack", dead.id) and dead.mode == "rest",
        dead.mode)


def check_facts(m):
    """Transcript lines in (fixture jsonl), facts out: model, tool class, commits, crash, subagents, codex model."""
    d = TMP / "facts"
    if d.exists():
        shutil.rmtree(d)
    (d / "sid1" / "subagents").mkdir(parents=True)
    now = time.time()
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 5)) + "Z"

    def a(content, model="claude-sonnet-5", **kw):
        return dict(type="assistant", timestamp=ts, message={"model": model, "content": content}, **kw)

    def u(content):
        return dict(type="user", timestamp=ts, message={"content": content})
    lines = [a([{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git commit -m 'x'"}}]),
             u([{"type": "tool_result", "tool_use_id": "t1", "content": "[main abc123] x"}]),
             a([{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "git commit -m 'y'"}}]),
             u([{"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "nothing to commit"}]),
             a([{"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "x"}}])]
    p = d / "sid1.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    (d / "sid1" / "subagents" / "agent-a.jsonl").write_text("{}\n", encoding="utf-8")
    (d / "sid1" / "subagents" / "agent-b.jsonl").write_text("{}\n", encoding="utf-8")
    old = d / "sid1" / "subagents" / "agent-old.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    os.utime(old, (now - 600, now - 600))
    f = m.session_facts(p, "claude", now)
    chk("facts: model from the last assistant record", f["model"] == "claude-sonnet-5", f["model"])
    chk("facts: species sonnet", m.species_for("claude", f["model"]) == "sonnet")
    chk("facts: last tool Read -> read", f["tool"] == "read", f["tool"])
    chk("facts: only the successful git commit counts", f["commits"] == ["t1"], f["commits"])
    chk("facts: no crash without an API error", f["error"] is None)
    chk("facts: 2 live subagents (the 10-min-old one is not)", f["subagents"] == 2, f["subagents"])
    p2 = d / "sid2.jsonl"
    p2.write_text("\n".join(json.dumps(x) for x in lines[:1] + [
        a([{"type": "text", "text": "API Error: 500"}], model="<synthetic>", isApiErrorMessage=True, error="unknown")]) + "\n",
        encoding="utf-8")
    f2 = m.session_facts(p2, "claude", now)
    chk("facts: trailing API error -> crash", f2["error"] is not None and f2["model"] == "claude-sonnet-5", f2)
    p3 = d / "sid3.jsonl"
    p3.write_text("\n".join(json.dumps(x) for x in lines[:1] + [
        a([{"type": "text", "text": "limit"}], model="<synthetic>", isApiErrorMessage=True, apiError="usage_limit_reached")])
        + "\n", encoding="utf-8")
    chk("facts: a usage-limit error is not a crash", m.session_facts(p3, "claude", now)["error"] is None)
    old_ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 600)) + "Z"
    p4 = d / "sid4.jsonl"
    p4.write_text(json.dumps(dict(lines[4], timestamp=old_ts)) + "\n", encoding="utf-8")
    chk("facts: a tool call 10 min old says nothing", m.session_facts(p4, "claude", now)["tool"] is None)
    cx = d / "rollout.jsonl"
    cx.write_text("\n".join(json.dumps(x) for x in [
        {"type": "turn_context", "timestamp": ts, "payload": {"model": "gpt-6-luna"}},
        {"type": "response_item", "timestamp": ts, "payload": {"type": "custom_tool_call", "name": "exec",
                                                               "input": "rg foo"}}]) + "\n", encoding="utf-8")
    fc = m.session_facts(cx, "codex", now)
    chk("facts: codex model gpt-6-luna -> moon jelly", m.species_for("codex", fc["model"]) == "luna", fc["model"])
    chk("facts: codex exec -> run", fc["tool"] == "run", fc["tool"])
    # environment
    sd = d / "state"
    sd.mkdir()
    (sd / "claude-limit.json").write_text(json.dumps({"last": {"state": "normal", "fh": 50}, "updated": now}), encoding="utf-8")
    e = m.read_env(sd, now, night="off")
    chk("env: fh read from claude-limit.json", e["fh"] == 50 and e["frozen"] is None, e)
    (sd / "claude-limit.json").write_text(json.dumps({"last": {"state": "hit", "resets_at": now + 2.5 * 3600},
                                                      "updated": now}), encoding="utf-8")
    e = m.read_env(sd, now, night="off")
    chk("env: limit hit -> frozen, half the 5-hour window left", e["frozen"] is not None and abs(e["frozen"] - 0.5) < 0.01, e)
    (sd / "claude-limit.json").write_text(json.dumps({"last": {"state": "hit", "resets_at": now - 60},
                                                      "updated": now}), encoding="utf-8")
    chk("env: past the reset -> thawed", m.read_env(sd, now, night="off")["frozen"] is None)
    (sd / "away.json").write_text("{}", encoding="utf-8")
    chk("env: away mode -> night", m.read_env(sd, now, night="auto")["night"] is True)
    # reef
    reef = {"blocks": [], "keeps": [], "acked": {}}
    r = dict(id="sid1", grid=m.fallback_grid("sid1"), commits=["t1"])
    chk("reef: a commit adds one block", m.reef_update(reef, [r], now) and len(reef["blocks"]) == 1)
    chk("reef: the same commit again adds nothing", not m.reef_update(reef, [r], now) and len(reef["blocks"]) == 1)
    reef["blocks"].append({"id": "old", "sid": "x", "c": "#888888", "t": now - 8 * 86400})
    m.reef_update(reef, [], now, ended=[r])
    chk("reef: blocks older than 7 days erode; an ended session leaves a keepsake",
        [b["id"] for b in reef["blocks"]] == ["sid1:t1"] and len(reef["keeps"]) == 1)
    many = [dict(id=f"s{i}", grid=r["grid"], commits=[f"c{j}" for j in range(20)]) for i in range(10)]
    m.reef_update(reef, many, now)
    chk("reef: capped at REEF_CAP blocks", len(reef["blocks"]) == m.REEF_CAP, len(reef["blocks"]))
    m.reef_save(d / "reef.json", reef)
    chk("reef: save and load round trip", m.reef_load(d / "reef.json") == reef)


def check_tank_page(m):
    """The home-page layout: fixtures on 1, 5, 11, 15. Nothing parks or speaks on them, and their readable art is
    never covered. States map to postures; identity colours are stable per session id."""
    geo = m.Geo()
    rs = rows(12, m) + [dict(rows(3, m)[2], id=f"fixture-extra-{i}", label=f"Extra {i}") for i in range(4)]
    crash = dict(rs[2], id="fixture-crash", crashed=True, acked=False, label="Deploy")
    rs.append(crash)
    docs = {rs[0]["id"]: {"id": rs[0]["id"], "status": {"text": "TESTS", "tone": "ok"},
                          "progress": {"value": 0.6, "ring": False}, "buttons": []},
            rs[3]["id"]: {"id": rs[3]["id"], "buttons": [{"id": "stop", "label": "STOP"}]},
            rs[9]["id"]: {"id": rs[9]["id"], "face": {"frames": [rs[1]["grid"]], "palette": {}}}}
    covered, spots_bad = [], []
    for env in ({}, {"fh": 90.0}, {"frozen": 0.6}, {"night": True}):
        tank = m.Tank(geo, seed=4)
        tank.set_layout(None, FX)
        tank.set_env(env)
        tank.set_docs(docs, attn={rs[8]["id"]})
        tank.set_fixture_data(sys_=(97, 64, 91), cards=(3, 2))
        rs2 = [dict(r, attn=(r["id"] == rs[8]["id"])) for r in rs]
        tank.sync(rs2, 0.0, first=True)
        for i in range(120):
            t = i / 5
            tank.step(0.2, t)
            if i % 3:
                continue
            faces = tank.faces(t)
            for k, name in FX.items():
                ov = tank.fixture_overlay(k, name, t)
                got = faces[k]["image"].convert("RGBA")
                op, gp = ov.load(), got.load()
                x0, y0, x1, y1 = m.A.READABLE[name]
                bad = sum(1 for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
                          if op[x, y][3] == 255 and op[x, y][:3] != gp[x, y][:3])
                if bad:
                    covered.append((env, t, k, bad))
            for f in tank.fish.values():
                if f.spot and f.mode in ("glass", "rest", "dead") and geo.key_at(*f.spot) in FX:
                    spots_bad.append((f.mode, geo.key_at(*f.spot)))
    chk("fixtures: readable art never covered (4 water states, 17 creatures, signs, pump bubbles)", not covered,
        covered[:4])
    chk("fixtures: no creature waits, rests or floats dead on a fixture key", not spots_bad, spots_bad[:4])
    tank = m.Tank(geo, seed=4)
    tank.set_layout(None, FX)
    tank.sync(rs, 0.0, first=True)
    chk("fixtures: glass order skips them (3, 2, 4, then the middle row)", tank.glass_keys[:4] == [3, 2, 4, 8],
        tank.glass_keys)
    chk("fixtures: rest keys skip them (13, 12, 14)", tank.rest_keys == [13, 12, 14], tank.rest_keys)
    chk("fixtures: the bubble column left key 15", geo.key_at(tank.kelp[-1]["col_x"], geo.sand - 4) == 14,
        geo.key_at(tank.kelp[-1]["col_x"], geo.sand - 4))
    chk("fixtures: reef only on free sand", all(geo.key_at(x, geo.sand - 2) in (12, 13, 14) for x in tank._reef_slots()))
    # postures: each state has its own look
    want = {"swim": ("side", "side", False, False), "glass": ("front", "side", None, False),
            "stuck": ("side", "side", False, False), "rest": ("side", "side", True, False),
            "hide": ("side", "pale", False, False), "dead": ("side", "belly", False, True)}
    f = next(iter(tank.fish.values()))
    m0 = f.mode
    got, imgs = {}, {}
    for mode in want:
        f.mode = mode
        v, fr, look, closed, xeye = f.pose(1.0)
        got[mode] = (v, look, closed if mode != "glass" else None, xeye)
        imgs[mode] = f.sprite(f.pose(1.0))[0].tobytes()
    f.mode = m0
    chk("postures: swim / glass (front) / stuck / rest (eyes shut) / hide (pale) / dead (belly, x eyes)", got == want, got)
    chk("postures: all six look different", len(set(imgs.values())) == 6)
    # identity: same id -> same colours, every time; different ids differ
    a1 = dict(m.ident_for(rs[0]["id"], rs[0]["grid"]))
    m._IDENT.clear()
    a2 = m.ident_for(rs[0]["id"], rs[0]["grid"])
    chk("identity: stable for a session id", a1 == a2)
    ids = {tuple(sorted(m.ident_for(r["id"], r["grid"]).items())) for r in rs[:12]}
    chk("identity: 12 sessions -> 12 distinct looks", len({i[1:] for i in ids}) >= 10, len(ids))


def species_sheet(m):
    """Every species x every state at 4x, drawn by the tank itself on 64 px water cells."""
    from PIL import ImageDraw
    sp = ["opus", "sonnet", "haiku", "fish", "sol", "luna"]
    states = [("swim", 0.0), ("swim", 0.35), ("glass", 1.0), ("stuck", 1.0), ("rest", 1.0), ("hide", 1.0),
              ("dead", 1.0), ("frozen", 1.0), ("night", 1.0)]
    rg = real_grids()

    class G:
        sand, gap, pitch, W, H = 56, 0, 64, 64, 64

        def key_at(self, x, y):
            return 1

        def key_rect(self, k):
            return 0, 0, 64, 64
    tank = m.Tank(m.Geo(), seed=1)
    tank.geo = G()
    cell, lab = 64, 30
    sheet = Image.new("RGB", (lab + len(states) * (cell + 2), 12 + len(sp) * (cell + 2)), (20, 20, 26))
    for j, (st, _) in enumerate(states):
        m.A.draw_text(sheet, lab + j * (cell + 2) + 2, 2, st.upper()[:6], (220, 220, 220))
    for i, s in enumerate(sp):
        m.A.draw_text(sheet, 1, 12 + i * (cell + 2) + 28, s.upper()[:4], (220, 220, 220))
        grid = rg[i][1] if i < len(rg) else m.fallback_grid(f"sheet-{i}")
        for j, (st, t) in enumerate(states):
            im = Image.new("RGB", (cell, cell), m._rgb(m.WATER[2]))
            d = ImageDraw.Draw(im)
            d.rectangle((0, 56, 63, 63), fill=m._rgb(m.SAND[0]))
            mode = {"frozen": "swim", "night": "swim"}.get(st, st)
            f = m.Fish(dict(id=f"sheet-{i}", label=s, grid=grid, species=s, state="working"), 32, 32, mode, 0.0,
                       tank.rng)
            f.face, f.kind = 1, "question"
            if mode == "rest":
                f.y = 56 + 4 - f.size()[1] / 2
            elif mode == "dead":
                f.y = 4 + f.size()[1] / 2
            elif mode == "hide":
                f.x = 14
            tank._draw_fish(im, d, f, t, night=(st == "night"), frozen=(st == "frozen"))
            if st == "night":
                im = im.point(lambda v: int(v * 0.6))
            sheet.paste(im, (lab + j * (cell + 2), 12 + i * (cell + 2)))
    PREVIEW.mkdir(parents=True, exist_ok=True)
    sheet.resize((sheet.width * 4, sheet.height * 4), Image.NEAREST).save(PREVIEW / "species-states-4x.png")


# ------------------------------------------------------------------ previews
def run_scene(m, n, secs, fps=8, events=(), themed=True, seed=11, rs=None, env=None, reef=None, fixtures=None,
              docs=None, attn=(), fx=None):
    from m18lib import ringmap as RM
    rm = RM.load(Path.home() / "tools" / "m18")                   # the measured ring map (read only)
    geo = m.Geo()
    tank = m.Tank(geo, seed=seed)
    if fixtures:
        tank.set_layout(None, fixtures)
    if docs:
        tank.set_docs(docs, attn)
    if fx:
        tank.set_fixture_data(**fx)
    rs = rows(n, m) if rs is None else rs
    if attn:
        rs = [dict(r, attn=r["id"] in attn) for r in rs]
    if env:
        tank.set_env(env)
    if reef:
        tank.set_reef(reef)
    tank.sync(rs, 0.0, first=True)
    p = pen(m, themed)
    frames = []
    ev = sorted(events)
    for i in range(int(secs * fps)):
        t = i / fps
        while ev and ev[0][0] <= t:
            _, what, arg = ev.pop(0)
            if what == "tap":
                tank.tap(arg(tank) if callable(arg) else arg, t)
            elif what == "add":
                rs = rs + [arg]
                tank.sync(rs, t)
            elif what == "drop":
                rs = [r for r in rs if r["id"] != arg]
                tank.sync(rs, t)
            elif what == "set":
                rid, fields = arg
                rs = [dict(r, **fields) if r["id"] == rid else r for r in rs]
                tank.sync(rs, t)
        tank.step(1 / fps, t)
        frames.append(tank.pad_view(t, rm, tank.ring_frame(rm, t), scale=2, pen=p))
    return frames, tank


def tight(frame):
    """Crop a pad_view frame (scale 2) to the 15 screens and their bars, plus the top ring row."""
    from m18lib import ringmap as RM
    import fishbowl_under_test as _m  # noqa: F401  (loaded by load())
    g = _m.Geo()
    kx, ky = RM.KEY_X, RM.KEY_Y
    cw = (4 * g.pitch) / (kx[4] - kx[0])
    ch = (2 * g.pitch) / (ky[2] - ky[0])
    ox, oy = kx[0] * cw - 32 + 14, ky[0] * ch - 32 + 14
    return frame.crop((int(ox - 12) * 2, int(oy - 12) * 2, int(ox + g.W + 12) * 2, int(oy + g.H + 12) * 2))


def save(frames, name, fps=8, sheet_at=None, cols=3):
    PREVIEW.mkdir(parents=True, exist_ok=True)
    tf = [tight(f) for f in frames]
    tf[0].save(PREVIEW / f"{name}.gif", save_all=True, append_images=tf[1:], duration=int(1000 / fps), loop=0)
    if sheet_at is not None:
        pick = [tight(frames[i]) for i in sheet_at if i < len(frames)]
        w, h = pick[0].size
        rows_ = (len(pick) + cols - 1) // cols
        sc = 0.5 if cols > 3 else 1.0 if cols < 3 else 0.75
        tw, th = int(w * sc), int(h * sc)
        sh = Image.new("RGB", (tw * cols + 4 * (cols - 1), th * rows_ + 4 * (rows_ - 1)), (90, 90, 90))
        for j, f in enumerate(pick):
            sh.paste(f.resize((tw, th), Image.NEAREST), ((j % cols) * (tw + 4), (j // cols) * (th + 4)))
        sh.save(PREVIEW / f"{name}-sheet.png")


def tank_previews(m):
    """The home page as it really looks: 15 screens, 30 px bars, the four fixtures, agents' signs."""
    species_sheet(m)
    rs12 = rows(12, m)
    docs = {rs12[0]["id"]: {"id": rs12[0]["id"], "status": {"text": "TESTS", "tone": "ok"},
                            "progress": {"value": 0.6, "ring": False}, "buttons": []},
            rs12[3]["id"]: {"id": rs12[3]["id"], "buttons": [{"id": "stop", "label": "STOP"}]},
            rs12[9]["id"]: {"id": rs12[9]["id"], "face": {"frames": [rs12[1]["grid"]], "palette": {}}}}
    f0, _ = run_scene(m, 0, 6, fixtures=FX, fx={"sys_": (12, 48, 6), "cards": (0, 1)})
    save(f0, "tank-0", sheet_at=[0, 24, 47])
    tight(f0[20]).save(PREVIEW / "tank-0-frame.png")
    f3, _ = run_scene(m, 3, 10, fixtures=FX, fx={"sys_": (34, 61, 22), "cards": (1, 1)},
                      events=[(3.0, "tap", lambda tk: tk.geo.key_at(
                          *next((f.x, f.y) for f in tk.fish.values() if f.mode == "glass")))])
    save(f3, "tank-3", sheet_at=[8, 24, 30, 44, 60, 79])
    tight(f3[30]).save(PREVIEW / "tank-3-plate.png")
    f12, _ = run_scene(m, 12, 12, fixtures=FX, docs=docs, attn={rs12[8]["id"]},
                       fx={"sys_": (92, 71, 88), "cards": (4, 2)})
    save(f12, "tank-12", sheet_at=[16, 32, 48, 64, 80, 95])
    tight(f12[60]).save(PREVIEW / "tank-12-frame.png")
    for name, env in (("tank-12-night", {"night": True}), ("tank-12-frozen", {"frozen": 0.7}),
                      ("tank-12-low", {"fh": 85.0})):
        fe, _ = run_scene(m, 12, 3, fixtures=FX, docs=docs, env=env, fx={"sys_": (40, 60, 10), "cards": (0, 1)})
        tight(fe[16]).save(PREVIEW / f"{name}.png")
    print(f"tank previews -> {PREVIEW}")


def previews(m):
    tank_previews(m)
    f0, _ = run_scene(m, 0, 6)
    save(f0, "bowl-0", sheet_at=[0, 16, 32])
    tight(f0[0]).save(PREVIEW / "bowl-0-frame.png")
    f3, _ = run_scene(m, 3, 10, events=[(3.0, "tap", 8), (5.0, "tap", lambda tk: tk.geo.key_at(
        *next((f.x, f.y) for f in tk.fish.values() if f.mode == "glass")))])
    save(f3, "bowl-3", sheet_at=[8, 24, 25, 26, 27, 44])
    tight(f3[44]).save(PREVIEW / "bowl-3-plate.png")
    m_ = m
    new = dict(rows(1, m_)[0], id="fixture-hatch", label="Hatchling")
    f12, tank = run_scene(m, 12, 14, events=[(2.0, "add", new), (7.0, "drop", "fixture-02-0000-0000-0000-000000000000")])
    save(f12, "bowl-12", sheet_at=[16, 24, 36, 40, 60, 80])
    tight(f12[100]).save(PREVIEW / "bowl-12-frame.png")
    # bar crossing: consecutive frames of one swimmer
    save(f12[40:52], "bowl-12-cross", sheet_at=list(range(0, 12)), cols=4)
    fp, _ = run_scene(m, 3, 6, themed=False, events=[(1.0, "tap", lambda tk: tk.geo.key_at(
        *next((f.x, f.y) for f in tk.fish.values() if f.mode == "glass")))])
    tight(fp[16]).save(PREVIEW / "bowl-plain-plate.png")
    features(m)
    print(f"previews -> {PREVIEW}")


def features(m):
    """One scene per data layer a-g, GAP from tiles."""
    base = rows(12, m)
    sw = lambda i, **kw: dict(base[i], state="working", kind=None, silent=False, **kw)    # noqa: E731
    # a. species: opus, sonnet, haiku, luna, sol all swimming
    a = [sw(0, species="opus", tool=None), sw(1, species="sonnet", tool=None), sw(3, species="haiku", tool=None),
         sw(7, species="luna", agent="codex", tool=None), sw(8, species="sol", agent="codex", tool=None)]
    fa, _ = run_scene(m, 0, 8, rs=a, seed=21)
    save(fa, "feat-a-species", sheet_at=[8, 24, 40, 56])
    # b. fry: one parent with 3 live subagents, one with 1
    b = [sw(0, species="opus", tool=None, subagents=3), sw(1, species="sonnet", tool=None, subagents=1),
         dict(base[4])]
    fb, _ = run_scene(m, 0, 8, rs=b, seed=22)
    save(fb, "feat-b-fry", sheet_at=[8, 24, 40, 56])
    # c. water: 0 %, 60 %, 95 % of the 5-hour window, then a limit hit (frozen, 80 % and 15 % left)
    mix = rows(6, m)
    for name, env in (("feat-c-water-60", {"fh": 60.0}), ("feat-c-water-95", {"fh": 95.0}),
                      ("feat-c-frozen-80", {"frozen": 0.8}), ("feat-c-frozen-15", {"frozen": 0.15})):
        fc, _ = run_scene(m, 0, 3, rs=mix, env=env, seed=23)
        tight(fc[16]).save(PREVIEW / f"{name}.png")
    # d. tools: read (nibbling the sand), edit (carrying a block), run (bubble bursts), web (out and back)
    d_ = [sw(0, species="sonnet", tool="read"), sw(1, species="opus", tool="edit"), sw(3, species="sonnet", tool="run"),
          sw(4, species="haiku", tool="web")]
    fd, _ = run_scene(m, 0, 12, rs=d_, seed=24)
    save(fd, "feat-d-tools", sheet_at=[8, 20, 32, 44, 56, 68, 80, 92, 94])
    # e. reef: 40 commit blocks of mixed age + 3 keepsakes
    now = time.time()
    cols = ["#cc3a30", "#3a5bd2", "#72cc40", "#f2d33a", "#8e3fca"]
    reef = {"blocks": [{"id": f"b{i}", "sid": f"s{i % 5}", "c": cols[i % 5], "t": now - (i % 8) * 86400 * 0.85}
                       for i in range(40)],
            "keeps": [{"sid": f"k{i}", "grid": base[i]["grid"], "t": now} for i in range(3)], "acked": {}}
    fe, _ = run_scene(m, 0, 3, rs=rows(5, m), reef=reef, seed=25)
    tight(fe[16]).save(PREVIEW / "feat-e-reef.png")
    # f. belly-up: a crashed session floats at the top; tap acknowledges it at 4 s
    crash = dict(base[2], id="fixture-crash", crashed=True, acked=False, label="Deploy")
    ff, tk = run_scene(m, 0, 8, rs=rows(3, m) + [crash], seed=26, events=[(4.0, "tap", lambda tk: tk.geo.key_at(
        tk.fish["fixture-crash"].x, tk.fish["fixture-crash"].y))])
    save(ff, "feat-f-bellyup", sheet_at=[16, 31, 33, 60])
    # g. night: 8 mixed sessions, lights out
    fg, _ = run_scene(m, 8, 6, env={"night": True}, seed=27)
    save(fg, "feat-g-night", sheet_at=[8, 24, 40])
    tight(fg[24]).save(PREVIEW / "feat-g-night.png")


# ------------------------------------------------------------------ cost (mock daemon)
def cost(m, secs=60, fps=None):
    """Runs the repo daemon in-process on a mock device that encodes JPEGs like the real one, opens the bowl with
    12 fixture sessions (summary file), and measures CPU of this process and face writes per second."""
    import psutil
    import io
    import threading
    os.environ.setdefault("M18_SESSIONS_DECISIONS", "off")
    base = TMP / "cost"
    if base.exists():
        shutil.rmtree(base)
    (base / "state" / "session-faces").mkdir(parents=True)
    (base / "themes").mkdir()
    shutil.copytree(theme_base() / "themes" / "hillside", base / "themes" / "hillside")
    rs = rows(12, m)
    summ = {"working": 6, "waiting": 3, "finished": 3, "total": 12, "silent": 1,
            "sessions": [{k: r[k] for k in ("id", "title", "state", "kind", "silent", "silent_s", "urgency")} for r in rs]}
    (base / "state" / "sessions-summary.json").write_text(json.dumps(summ), encoding="utf-8")
    for r in rs:
        (base / "state" / "session-faces" / f"{r['id']}.json").write_text(
            json.dumps({"label": r["label"], "icon": r["grid"]}), encoding="utf-8")
    args = {"source": "summary"} if fps is None else {"source": "summary", "fps": fps}
    cfg = base / "macros.yaml"
    cfg.write_text("settings:\n  home: main\n  theme: {name: hillside}\npages:\n  main:\n    keys:\n"
                   f"      1: {{icon: mdi:fish, label: Bowl, action: {{plugin: fishbowl.open, args: {json.dumps(args)}}}}}\n",
                   encoding="utf-8")
    os.environ["M18_HOME"] = str(base)
    os.environ["M18_SESSIONS_ROOT"] = str(base / "no-sessions")   # the sessions plugin scans nothing and writes its summary there
    import m18d
    from m18lib.device import MockDevice

    class CountingMock(MockDevice):
        def __init__(self):
            super().__init__()
            self.writes, self.leds_n, self._lk = 0, 0, threading.Lock()

        def set_face(self, key, img=None, h=None):
            if img is not None:                       # the real device's work: 64 px + JPEG q90
                buf = io.BytesIO()
                img.convert("RGB").resize((64, 64)).save(buf, "JPEG", quality=90)
            with self._lk:
                self.writes += 1
            super().set_face(key, img, h)

        def set_leds(self, colors):
            self.leds_n += 1
            return super().set_leds(colors)

    dev = CountingMock()
    d = m18d.Daemon(cfg, dev, 8743, mock=True)
    th = threading.Thread(target=d.run, daemon=True)
    th.start()
    time.sleep(2.0)
    proc = psutil.Process()
    idle0 = proc.cpu_times()
    time.sleep(5.0)
    idle1 = proc.cpu_times()
    idle_pct = ((idle1.user + idle1.system) - (idle0.user + idle0.system)) / 5.0 * 100
    d.on_press(1, True, source="api")
    d.on_press(1, False, source="api")
    time.sleep(2.0)
    st = d.plugin_state.get("fishbowl") or {}
    chk("cost: bowl opened in the mock daemon", bool(st.get("running")), d.logbuf[-5:])
    w0, l0, c0, f0 = dev.writes, dev.leds_n, proc.cpu_times(), st.get("stats", {}).get("frames", 0)
    t0 = time.time()
    time.sleep(secs)
    dt = time.time() - t0
    c1 = proc.cpu_times()
    cpu = ((c1.user + c1.system) - (c0.user + c0.system)) / dt * 100
    wps = (dev.writes - w0) / dt
    lps = (dev.leds_n - l0) / dt
    fr = (st["stats"]["frames"] - f0) / dt
    up = d.modal is not None
    sx = st["stats"]
    print("cost split (bowl thread CPU, pct of one core since open): draw %.1f  push %.1f  ring %.1f" % tuple(
        sx.get(k, 0.0) / max(1e-6, time.time() - sx["opened"]) * 100 for k in ("cpu_draw", "cpu_push", "cpu_ring")))
    print(f"cost: fps {fr:.1f}  cpu {cpu:.1f}% of one core (daemon idle {idle_pct:.1f}%)  face writes {wps:.1f}/s "
          f"({wps / 15:.2f} per key)  ring frames {lps:.1f}/s  over {dt:.0f} s")
    chk("cost: modal still up past 120 s cap" if secs > 125 else "cost: modal still up", up)
    chk("cost: under 10% of one core", cpu < 10, f"{cpu:.1f}%")
    d.on_press(16, True, source="device")
    d.on_press(16, False, source="device")
    time.sleep(1.0)
    chk("cost: physical b1 ends the bowl and frees the ring",
        d.modal is None and not st.get("running") and (d.leds_owner is None), (d.modal, st.get("running"), d.leds_owner))
    d.stop.set()
    return {"fps": fr, "cpu": cpu, "idle": idle_pct, "writes": wps, "leds": lps}


def page(m, secs=60):
    """sets-tank.yaml in the repo daemon (in-process, mock device that JPEG-encodes like the real one, port 8745,
    M18_HOME = a temp dir with 12 fixture sessions). Measures the page's frame rate, CPU and face writes, and checks
    that it pauses for the tools page, dark mode and a takeover, taps reach the tank, and the ring yields to agents."""
    import psutil
    import io
    import threading
    os.environ.setdefault("M18_SESSIONS_DECISIONS", "off")
    base = TMP / "page"
    if base.exists():
        shutil.rmtree(base)
    (base / "state" / "session-faces").mkdir(parents=True)
    (base / "state" / "agents").mkdir(parents=True)
    shutil.copytree(theme_base() / "themes" / "hillside", base / "themes" / "hillside")
    rs = rows(12, m)
    summ = {"sessions": [{k: r[k] for k in ("id", "title", "state", "kind", "silent", "silent_s", "urgency")} for r in rs]}
    (base / "state" / "sessions-summary.json").write_text(json.dumps(summ), encoding="utf-8")
    for r in rs:
        (base / "state" / "session-faces" / f"{r['id']}.json").write_text(
            json.dumps({"label": r["label"], "icon": r["grid"]}), encoding="utf-8")
    now = time.time()
    doc = {"id": rs[0]["id"], "agent": "claude", "updated": now, "created": now,
           "status": {"text": "TESTS", "tone": "ok", "until": now + 3600}}
    (base / "state" / "agents" / f"{rs[0]['id']}.json").write_text(json.dumps(doc), encoding="utf-8")
    y = (HERE / "sets-tank.yaml").read_text(encoding="utf-8")
    y = y.replace("{plugin: fishbowl.tile, interval: 1}", "{plugin: fishbowl.tile, interval: 1, args: {source: summary}}")
    cfg = base / "macros.yaml"
    cfg.write_text(y, encoding="utf-8")
    os.environ["M18_HOME"] = str(base)
    os.environ["M18_SESSIONS_ROOT"] = str(base / "no-sessions")   # the sessions plugin scans nothing and writes its summary there
    import m18d
    from m18lib.device import MockDevice

    class CountingMock(MockDevice):
        def __init__(self):
            super().__init__()
            self.writes, self._lk = 0, threading.Lock()

        def set_face(self, key, img=None, h=None):
            if img is not None:                       # the real device's work: 64 px + JPEG q90
                buf = io.BytesIO()
                img.convert("RGB").resize((64, 64)).save(buf, "JPEG", quality=90)
            with self._lk:
                self.writes += 1
            super().set_face(key, img, h)

    dev = CountingMock()
    d = m18d.Daemon(cfg, dev, 8745, mock=True)
    threading.Thread(target=d.run, daemon=True).start()
    time.sleep(6.0)
    st = d.plugin_state.get("fishbowl.page") or {}
    fid = lambda k, pg="main": (d.live_faces.get((pg, k)) or {}).get("_fid", ("",))[0]      # noqa: E731
    chk("page: main shows the tank on all 15 keys", d.page == "main" and all(fid(k) == "fishbowl" for k in range(1, 16)),
        [k for k in range(1, 16) if fid(k) != "fishbowl"] or d.logbuf[-5:])
    tank = st.get("tank")
    chk("page: the 12 fixture sessions in the tank, the agent's doc on its creature", tank is not None
        and sorted(tank.fish) == sorted(r["id"] for r in rs) and rs[0]["id"] in tank.docs,
        (len(tank.fish), sorted(tank.fish)[:2]) if tank else None)
    proc = psutil.Process()
    sx = st["stats"]
    w0, f0, p0, c0 = dev.writes, sx["frames"], sx["pushed"], proc.cpu_times()
    d0 = (sx["cpu_draw"], sx["cpu_push"], sx["cpu_ring"])
    t0 = time.time()
    time.sleep(secs)
    dt = time.time() - t0
    c1 = proc.cpu_times()
    cpu = ((c1.user + c1.system) - (c0.user + c0.system)) / dt * 100
    fps = (sx["frames"] - f0) / dt
    wps = (dev.writes - w0) / dt
    pps = (sx["pushed"] - p0) / dt
    split = tuple((sx[k] - d0[i]) / dt * 100 for i, k in enumerate(("cpu_draw", "cpu_push", "cpu_ring")))
    print("page cost: fps %.2f  cpu %.1f%% of one core (engine: draw %.1f, push %.1f, ring %.1f)  "
          "faces pushed %.1f/s  device writes %.1f/s (%.2f per key)  over %.0f s"
          % ((fps, cpu) + split + (pps, wps, wps / 15, dt)))
    chk("page: animates at about 30 fps", 28 <= fps <= 31, f"{fps:.2f}")
    chk("page: under 7% of one core", cpu < 7, f"{cpu:.1f}%")
    chk("page: the ring is the waterline while the tank shows", (d.leds_owner or {}).get("owner") == "fishbowl",
        d.leds_owner)
    # tap: a creature's key -> its plate on that key
    k = next((k for k in range(1, 16) if k not in FX and tank.hit(k)), None)
    d.on_press(k, True, source="api")
    d.on_press(k, False, source="api")
    time.sleep(0.6)
    chk("page: a tap on a creature's key shows its name plate", tank.plate is not None and tank.plate["key"] == k,
        (k, tank.plate))
    # tools page: the engine sleeps, tools keys are their own; back: it resumes
    d.on_press(17, True, source="device")
    d.on_press(17, False, source="device")
    time.sleep(1.0)
    fa = sx["frames"]
    time.sleep(2.0)
    chk("page: b2 -> tools, the tank sleeps", d.page == "tools" and not st["active"] and sx["frames"] == fa,
        (d.page, st["active"], sx["frames"] - fa))
    chk("page: tools no longer inherits main (all 15 keys its own)",
        all(not fid(k, "tools") for k in range(1, 16)) and len(m18d.C.resolved_keys(d.cfg, "tools")) >= 18)
    d.on_press(17, True, source="device")
    d.on_press(17, False, source="device")
    time.sleep(2.0)
    chk("page: b2 again -> back to the tank, animating", d.page == "main" and st["active"] and sx["frames"] > fa + 5)
    # dark mode via the lamp
    d.on_press(1, True, source="api")
    d.on_press(1, False, source="api")
    time.sleep(1.0)
    fa = sx["frames"]
    time.sleep(1.0)
    chk("page: lamp key -> dark mode, the tank sleeps and lets go of the ring",
        d.dark_key == 1 and not st["active"] and sx["frames"] == fa and (d.leds_owner or {}).get("owner") != "fishbowl",
        (d.dark_key, st["active"], d.leds_owner))
    d.on_press(1, True, source="api")
    d.on_press(1, False, source="api")
    time.sleep(1.5)
    chk("page: lamp again -> lights back, tank animating", d.dark_key is None and st["active"])
    # a card: the chest opens with the count; pressing it takes over and the tank sleeps; b1 ends it
    m18d.CardsApi(d, "api").post({"id": "t1", "feed": "check", "title": "Test card", "lines": ["one"]})
    time.sleep(SCAN_WAIT)
    chk("page: the chest counts the card", tank.fx["cards"][0] == 1, tank.fx["cards"])
    d.on_press(15, True, source="api")
    d.on_press(15, False, source="api")
    time.sleep(1.0)
    chk("page: chest press -> card takeover, the tank sleeps", d.modal is not None and not st["active"], d.modal and d.modal.get("owner"))
    d.on_press(16, True, source="device")
    d.on_press(16, False, source="device")
    time.sleep(1.5)
    chk("page: b1 ends the takeover, tank animating again", d.modal is None and st["active"])
    # the ring yields to an agent's progress ring
    doc["progress"] = {"value": 0.4, "ring": True, "until": time.time() + 3600, "set": time.time()}
    doc["updated"] = time.time()
    (base / "state" / "agents" / f"{rs[0]['id']}.json").write_text(json.dumps(doc), encoding="utf-8")
    time.sleep(SCAN_WAIT + 1)
    chk("page: the ring yields to an agent's progress --ring", (d.leds_owner or {}).get("owner") == "agents", d.leds_owner)
    d.stop.set()
    st["stop"].set()
    return {"fps": fps, "cpu": cpu, "writes": wps, "pushed": pps}


SCAN_WAIT = 2.6


def check_display_list(m):
    """The 60 fps path draws only the keys whose draw calls changed. A wrong skip would leave a stale picture on a
    key, so every frame of four scenes is compared, pixel for pixel, with the full render."""
    import random
    bad = total = sent = frames = 0
    for n_sess, seed, env in ((12, 11, None), (4, 3, None), (12, 5, {"night": True}), (6, 7, {"frozen": 0.5})):
        fps = 60
        tank = m.Tank(m.Geo(), seed=seed)
        tank.set_layout(None, {1: "lamp", 5: "buoy", 11: "gauge", 15: "chest"})
        tank.set_fixture_data(sys_=(95, 60, 10), cards=(2, 2))
        if env:
            tank.set_env(env)
        rs = rows(n_sess, m)
        rs[0]["subagents"], rs[3]["subagents"] = 3, 2             # fry schooling behind two creatures
        tank.set_docs({rs[0]["id"]: {"status": {"text": "TESTS", "tone": "warn"}, "progress": {"value": 0.4}},
                       rs[1]["id"]: {"buttons": [{"label": "STOP"}]}}, set())
        tank.sync(rs, 0.0, first=True)
        p, shown, rng = pen(m), {}, random.Random(seed)
        for i in range(fps * 10):
            t = i / fps
            if i % 97 == 50:
                tank.tap(rng.randrange(1, 16), t)                 # name plates, ripples
            if i == 300:
                tank.sync(rs[:-2], t)                             # two sessions end
            tank.step(1 / fps, t)
            dirty = tank.dirty_faces(t, p)
            shown.update(dirty)
            if not env or "frozen" not in env:
                frames += 1
                sent += len(dirty)
            elif not dirty:
                continue                                          # under ice the page refreshes 4 times a second
            ref = tank.faces(t, p)
            for k in range(1, 16):
                total += 1
                a = shown.get(k)
                bad += a is None or a.convert("RGB").tobytes() != ref[k]["image"].convert("RGB").tobytes()
    chk("display list: every key shows the full render's pixels on every frame", bad == 0, (bad, total))
    chk("display list: under 6 of 15 keys redrawn per frame at 60 fps", sent / frames < 6, round(sent / frames, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", action="store_true")
    ap.add_argument("--secs", type=float, default=60)
    ap.add_argument("--fps", type=float, default=None)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    m = load()
    steps = {"mapping": check_mapping, "bars": check_bars, "taps": check_taps, "facts": check_facts,
             "tankpage": check_tank_page, "displaylist": check_display_list, "tank": tank_previews, "preview": previews, "features": features}
    for name, fn in steps.items():
        if (a.only and a.only != name) or (not a.only and name in ("features", "tank")):
            continue
        fn(m)
    if a.cost or a.only == "cost":
        cost(m, a.secs, a.fps)
    if a.only == "page":
        page(m, a.secs)
    print("ALL PASS" if not FAILS else f"{len(FAILS)} FAIL")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
