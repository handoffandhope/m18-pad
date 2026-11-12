#!/usr/bin/env python3
"""Checks for plugins/sessions.py. Prints PASS/FAIL lines; exit 1 on any FAIL.

    python sessions_check.py            # fixtures, slots, labels, stub-CLI cache, three previews
    python sessions_check.py --real     # also scan the REAL folders once (read-only) and list them

The scanner is pointed at a temp tree through M18_SESSIONS_ROOT; the real-folder scan keeps its own
state files in a temp folder (M18_HOME), so it only reads ~/.claude, the app records and ~/.codex.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
M18_DIR = Path(os.environ.get("M18_DIR") or Path.home() / "tools" / "m18")      # render lib + themes
PREVIEW_DIR = Path(os.environ.get("M18_PREVIEW_DIR") or Path(tempfile.gettempdir()) / "m18sess" / "preview")
TMP = Path(tempfile.gettempdir()) / "m18sess" / "check"
FAILS = []


def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def load():
    spec = importlib.util.spec_from_file_location("sessions_under_test", HERE / "plugins" / "sessions.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.TTL = 0.0
    return m


def reset(m):
    """Forget everything in memory, like a daemon restart (files stay)."""
    m._ST.update(loaded=None)
    for d in (m._CL_CACHE, m._CX_CACHE, m._CX_META, m._FACES, m._IMGS, m._FAILED, m._CX_PATH, m._DFAIL):
        d.clear()
    m._DEC.update(path=None, e={}, usage={"day": "", "tokens": 0, "calls": []})
    m._DPEND.clear()
    del m._DQ[:]
    m._DWORKER["t"] = None
    m._SCAN.update(t=0.0, sessions=[], root=None)
    m._TOK.update(t=0.0, v=False)
    m._PENDING.clear()
    del m._Q[:]
    m._WORKER["t"] = None


# ------------------------------------------------------------------ fixtures (shapes taken from real tails)
def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def put(path, lines, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(o) for o in lines) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _u(t, content, **kw):
    return dict(type="user", isSidechain=False, entrypoint="claude-desktop", timestamp=iso(t),
                message={"role": "user", "content": content}, **kw)


def _a(t, blocks, stop):
    return dict(type="assistant", isSidechain=False, entrypoint="claude-desktop", timestamp=iso(t),
                message={"role": "assistant", "content": blocks, "stop_reason": stop})


def claude(root, sid, shape, age=5, title=None, reg=None, entry="claude-desktop", app=True, archived=False,
           first="Please look at the macro pad plugin and fix the thing", text=None):
    """shape: working | ask | question | finished | interrupted. Writes the transcript (mtime = now-age)."""
    now = time.time()
    t = now - age
    L = [dict(type="queue-operation", operation="enqueue", timestamp=iso(t - 30)),
         dict(type="queue-operation", operation="dequeue", timestamp=iso(t - 30)),
         _u(t - 30, first)]
    tool = {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}
    if shape == "working":
        L += [_a(t - 8, [{"type": "thinking", "thinking": "x"}], "tool_use"), _a(t - 7, [tool], "tool_use"),
              _u(t - 1, [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}])]
    elif shape == "ask":
        L += [_a(t - 3, [{"type": "tool_use", "id": "toolu_2", "name": "AskUserQuestion",
                          "input": {"questions": []}}], "tool_use")]
    elif shape == "question":
        L += [_a(t - 3, [{"type": "text", "text": "I changed two files.\n\nWhich option do you want me to keep?"}], "end_turn"),
              dict(type="system", subtype="stop_hook_summary", timestamp=iso(t - 1))]
    elif shape == "finished":
        L += [_a(t - 3, [{"type": "text", "text": text or "Done. All 12 checks pass and the change is committed."}], "end_turn"),
              dict(type="system", subtype="stop_hook_summary", timestamp=iso(t - 1))]
    elif shape == "bash":                    # a foreground Bash call with a 10-minute timeout, still running
        L += [_a(t, [{"type": "tool_use", "id": "toolu_9", "name": "Bash", "input": {"command": "sleep", "timeout": 600000}}],
                 "tool_use")]
    elif shape == "interrupted":
        L += [_a(t - 3, [tool], "tool_use"), _u(t - 1, [{"type": "text", "text": "[Request interrupted by user]"}])]
    L.append(dict(type="custom-title", customTitle=title or sid[:8], sessionId=sid))
    for o in L:
        if o.get("type") in ("user", "assistant"):
            o["entrypoint"] = entry
    put(root / "claude" / "projects" / "proj" / f"{sid}.jsonl", L, t)
    host = f"local_{sid[-12:]}-aaaa-bbbb-cccc-000000000000"
    if app:
        put(root / "app" / "prof" / "claude-code-sessions" / "acct" / "org" / f"{host}.json",
            [], now)
        (root / "app" / "prof" / "claude-code-sessions" / "acct" / "org" / f"{host}.json").write_text(
            json.dumps(dict(sessionId=host, cliSessionId=sid, title=title or sid[:8], isArchived=archived,
                            lastActivityAt=int(t * 1000), cwd="C:\\x")), encoding="utf-8")
    if reg:
        status, sts_off, wf = reg
        d = dict(pid=os.getpid(), sessionId=sid, kind="interactive", entrypoint=entry, hostSessionId=host,
                 name=sid[:6], nameSource="derived", status=status, statusUpdatedAt=int((now - sts_off) * 1000),
                 updatedAt=int((now - sts_off) * 1000))
        if wf:
            d["waitingFor"] = wf
        p = root / "claude" / "sessions" / f"{sid[-12:]}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d), encoding="utf-8")
    return host


def codex(root, tid, shape, age=5, originator="Codex Desktop", source="vscode", sub=False):
    now = time.time()
    t = now - age
    meta = dict(id=tid, cwd="C:\\proj", originator=originator, source=source,
                thread_source="subagent" if sub else "user")
    L = [dict(timestamp=iso(t - 60), type="session_meta", payload=meta),
         dict(timestamp=iso(t - 50), type="event_msg", payload=dict(type="task_started"))]
    if shape == "finished":
        L.append(dict(timestamp=iso(t), type="event_msg", payload=dict(type="task_complete",
                                                                       last_agent_message="ACCEPTED. Files changed.")))
    elif shape == "question":
        L.append(dict(timestamp=iso(t), type="event_msg", payload=dict(type="task_complete",
                                                                       last_agent_message="Want me to run it on Sol?")))
    else:
        L.append(dict(timestamp=iso(t), type="response_item", payload=dict(type="reasoning")))
    day = time.strftime("%Y/%m/%d", time.localtime(now))
    put(root / "codex" / "sessions" / day / f"rollout-x-{tid}.jsonl", L, t)
    idx = root / "codex" / "session_index.jsonl"
    with open(idx, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(id=tid, thread_name=f"codex {shape} {tid[-4:]}", updated_at=iso(t))) + "\n")


def new_root(name):
    r = TMP / name
    if r.exists():
        shutil.rmtree(r)
    r.mkdir(parents=True)
    os.environ["M18_SESSIONS_ROOT"] = str(r)
    return r


class Ctx:
    def __init__(self, theme=None, key=None):
        self.theme = theme or {}
        self.key = key
        self.modals, self.ended, self.logs = [], 0, []

    def log(self, *a):
        self.logs.append(a)

    def modal(self, faces, on_key=None, timeout=0):
        self.modals.append((faces, on_key))

    def end_modal(self):
        self.ended += 1


def by_title(ss):
    return {s["title"]: s for s in ss}


# ------------------------------------------------------------------ checks
def check_states(m):
    root = new_root("states")
    reset(m)
    claude(root, "11111111-0000-0000-0000-000000000001", "working", 4, "c-working")
    claude(root, "11111111-0000-0000-0000-000000000002", "ask", 4, "c-ask")
    claude(root, "11111111-0000-0000-0000-000000000003", "question", 4, "c-question")
    claude(root, "11111111-0000-0000-0000-000000000004", "finished", 60, "c-finished")
    claude(root, "11111111-0000-0000-0000-000000000005", "finished", 40 * 60, "c-old-finished")
    claude(root, "11111111-0000-0000-0000-000000000006", "working", 20 * 60, "c-hung-noreg")
    claude(root, "11111111-0000-0000-0000-000000000007", "finished", 30, "c-reg-busy", reg=("busy", 20, None))
    claude(root, "11111111-0000-0000-0000-000000000008", "ask", 30, "c-reg-waiting", reg=("waiting", 25, "input needed"))
    claude(root, "11111111-0000-0000-0000-000000000009", "finished", 30, "c-reg-idle", reg=("idle", 28, None))
    claude(root, "11111111-0000-0000-0000-00000000000a", "interrupted", 30, "c-interrupted")
    claude(root, "11111111-0000-0000-0000-00000000000b", "finished", 30, "c-archived", archived=True)
    claude(root, "11111111-0000-0000-0000-00000000000c", "finished", 30, "c-headless", entry="sdk-cli", app=False)
    claude(root, "11111111-0000-0000-0000-00000000000d", "working", 3, "c-reg-idle-but-active",
           reg=("idle", 60, None))                                   # transcript moved after the registry wrote
    sub = root / "claude" / "projects" / "proj" / "11111111-0000-0000-0000-000000000001" / "subagents"
    put(sub / "agent-abc.jsonl", [_u(time.time(), "sub task"), _a(time.time(), [tool_text("hi")], "end_turn")], time.time())
    codex(root, "01a1c000-0000-7000-8000-000000000001", "working")
    codex(root, "01a1c000-0000-7000-8000-000000000002", "finished", 60)
    codex(root, "01a1c000-0000-7000-8000-000000000003", "question", 60)
    codex(root, "01a1c000-0000-7000-8000-000000000004", "working", 3, sub=True)
    codex(root, "01a1c000-0000-7000-8000-000000000005", "working", 3, originator="codex_exec", source="exec")
    ss = m.scan(force=True)
    t = by_title(ss)
    want = {"c-working": "working", "c-ask": "waiting", "c-question": "waiting", "c-finished": "finished",
            "c-old-finished": "stale", "c-hung-noreg": "stale", "c-reg-busy": "working",
            "c-reg-waiting": "waiting", "c-reg-idle": "finished", "c-interrupted": "finished",
            "c-reg-idle-but-active": "working",
            "codex working 0001": "working", "codex finished 0002": "finished", "codex question 0003": "waiting"}
    for k, v in want.items():
        chk(f"state {k} -> {v}", k in t and t[k]["state"] == v, t.get(k, {}).get("state", "missing"))
    for k in ("c-archived", "c-headless", "codex working 0004", "codex working 0005"):
        chk(f"excluded {k}", k not in t)
    chk("subagent transcript not listed", not any("sub task" in s["title"] or s["id"].startswith("agent-") for s in ss))
    chk("link: claude desktop epitaxy", t["c-working"]["link"].startswith("claude://claude.ai/epitaxy/local_"))
    chk("link: codex thread", t["codex finished 0002"]["link"].startswith("codex://threads/"))
    sm = json.loads((root / "state" / "sessions-summary.json").read_text())
    pad = [s for s in ss if s["on_pad"]]
    chk("summary counts match pad", sm["working"] == sum(s["state"] == "working" for s in pad)
        and sm["waiting"] == sum(s["state"] == "waiting" for s in pad)
        and sm["finished"] == sum(s["state"] == "finished" for s in pad), sm)
    chk("stale never on pad", not any(s["on_pad"] for s in ss if s["state"] == "stale"))


def tool_text(t):
    return {"type": "text", "text": t}


def check_classify(m):
    ok = [("Which one do you want?", "waiting"), ("All good.", "finished"), ("Done.\n\nLet me know if you want more.", "waiting"),
          ("Should I push it?", "waiting"), ("Fixed it. `x = 1`", "finished"), ("", "finished"),
          ("I ran it?\n\nIt passes.", "finished")]
    for txt, want in ok:
        chk(f"classify {txt[:28]!r} -> {want}", m.classify(txt) == want, m.classify(txt))


def check_empty(m):
    root = new_root("empty")
    reset(m)
    ctx = Ctx()
    f = [m.slot(ctx, zone=[6, 7, 9, 10], slot=i) for i in range(4)]
    chk("0 sessions: every slot returns {} (theme backdrop shows)", all(x == {} for x in f))
    chk("0 sessions: scan is empty", m.scan(force=True) == [])
    sm = json.loads((root / "state" / "sessions-summary.json").read_text())
    chk("0 sessions: summary all zero", (sm["working"], sm["waiting"], sm["finished"]) == (0, 0, 0), sm)
    chk("0 sessions: press does nothing", m.press(ctx, zone=[6, 7, 9, 10], slot=0) is None and not ctx.modals)


def check_slots(m):
    root = new_root("slots")
    reset(m)
    zone = [6, 7, 9, 10]
    ids = [f"22222222-0000-0000-0000-00000000000{i}" for i in range(1, 8)]

    def at():
        s = m.snapshot(4, force=True)
        return {v["id"][-1]: k for k, v in s["slots"].items()}, s

    for i in (1, 2, 3):
        claude(root, ids[i - 1], "working", 3, f"s{i}")
    a, _ = at()
    chk("slots: first three take 0,1,2 in order", a == {"1": 0, "2": 1, "3": 2}, a)
    # remove s2: s1 and s3 keep their slots
    for p in (root / "claude" / "projects" / "proj").glob("*0002.jsonl"):
        p.unlink()
    a, _ = at()
    chk("slots: removing s2 does not move s1 or s3", a == {"1": 0, "3": 2}, a)
    claude(root, ids[3], "working", 3, "s4")
    a, _ = at()
    chk("slots: a new session takes the lowest free slot (1)", a.get("4") == 1 and a["1"] == 0 and a["3"] == 2, a)
    claude(root, ids[4], "working", 3, "s5")
    a, s = at()
    chk("slots: 4th session fills slot 3 (no overflow at exactly 4)", a.get("5") == 3 and not s["overflow"], a)
    # restart: same assignment from state/sessions.json
    before = dict(a)
    reset(m)
    a2, _ = at()
    chk("slots: persisted across a restart", a2 == before, (before, a2))
    # overflow
    claude(root, ids[5], "working", 3, "s6")
    claude(root, ids[6], "working", 3, "s7")
    a, s = at()
    chk("overflow: 6 pad sessions in 4 slots -> 3 slotted, 3 hidden", len(s["slots"]) == 3 and len(s["overflow"]) == 3, a)
    chk("overflow: sessions in slots 0-2 kept their slots", a["1"] == 0 and a["4"] == 1 and a["3"] == 2, a)
    ctx = Ctx()
    last = m.slot(ctx, zone=zone, slot=3)
    first = m.slot(ctx, zone=zone, slot=0)
    chk("overflow: last slot shows a '+N' face, not a session", "image" in last or "frames" in last)
    img = (last.get("image") or last["frames"][0]).convert("RGB")
    again = m.render_more(3, "working", 0, False)
    chk("overflow: '+3' face is the one drawn for 3 hidden", img.tobytes() == again.tobytes())
    chk("overflow: '+3' differs from '+4'", m.render_more(4, "working", 0, False).tobytes() != again.tobytes())
    m.press(ctx, zone=zone, slot=3)
    chk("overflow: pressing the last slot opens the list modal", len(ctx.modals) == 1)
    faces, on_key = ctx.modals[0]
    chk("overflow: modal lists all 6 sessions + a close key", sorted(faces) == [1, 2, 3, 4, 5, 6, 15], sorted(faces))
    os.environ["M18_SESSIONS_DRY"] = "1"
    on_key(ctx, 2, True, time.time())
    log = (root / "state" / "opened.log").read_text()
    chk("modal: pressing a session key opens it and closes the modal", ctx.ended == 1 and "claude://" in log, log[-80:])
    chk("press on a session slot opens its link", (m.press(Ctx(), zone=zone, slot=0), (root / "state" / "opened.log").read_text().count("\n"))[1] == 2)
    # finished: seen by press
    reset(m)
    claude(root, "33333333-0000-0000-0000-000000000001", "finished", 20, "fin")
    for p in (root / "claude" / "projects" / "proj").glob("22222222*"):
        p.unlink()
    s = m.snapshot(4, force=True)
    chk("finished: on the pad until pressed", [v["title"] for v in s["slots"].values()] == ["fin"])
    m.press(Ctx(), zone=zone, slot=0)
    s = m.snapshot(4, force=True)
    chk("finished: gone after one press (slot freed)", not s["slots"] and not s["pad"])
    # 30-minute expiry
    claude(root, "33333333-0000-0000-0000-000000000002", "finished", 29 * 60, "fin29")
    s29 = [x["state"] for x in m.scan(force=True) if x["title"] == "fin29"]
    claude(root, "33333333-0000-0000-0000-000000000003", "finished", 31 * 60, "fin31")
    s31 = [(x["state"], x["on_pad"]) for x in m.scan(force=True) if x["title"] == "fin31"]
    chk("finished: still on the pad at 29 min, off at 31 min", s29 == ["finished"] and s31 == [("stale", False)], (s29, s31))


def check_forty(m):
    root = new_root("forty")
    reset(m)
    shapes = ["working", "ask", "finished", "question"]
    for i in range(40):
        claude(root, f"44444444-0000-0000-0000-{i:012d}", shapes[i % 4], 5 + i, f"project {i} topic{i % 7}")
    reset(m)                                          # cold caches: the first scan reads every file
    t = time.perf_counter()
    ss = m.scan(force=True)
    cold = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    m.scan(force=True)
    warm = (time.perf_counter() - t) * 1000
    chk(f"40 sessions: cold scan under 300 ms ({cold:.0f} ms, warm {warm:.0f} ms)", cold < 300 and len(ss) == 40, len(ss))
    ctx = Ctx()
    for sl in range(4):
        m.slot(ctx, zone=[6, 7, 9, 10], slot=sl)
    m.press(ctx, zone=[6, 7, 9, 10], slot=3)
    faces, on_key = ctx.modals[0]
    chk("40 sessions: modal page 1 shows 14 sessions + NEXT on key 15", sorted(faces) == list(range(1, 16)))
    on_key(ctx, 15, True, time.time())
    chk("40 sessions: key 15 ends the modal and opens page 2", ctx.ended == 1 and len(ctx.modals) == 2)
    # modal pages cover every pad session once
    seen, page = 0, 0
    pad = len(m.snapshot(4)["pad"])
    chk("40 sessions: 3 pages of 14 cover the pad", -(-pad // 14) == 3 and pad == 40, pad)


def check_labels(m):
    existing = ["FRICTION"]
    banned = m.shared_words("Fix top five measured friction sources", ["Measure unreported friction with Decisions"])
    chk("shared words found between two titles", "friction" in banned, banned)
    chk("label sharing the word is rejected", m.validate_label("Friction", banned, existing) is not None)
    chk("label with the word inside is rejected", m.validate_label("Friction 2", banned, []) is not None)
    chk("label equal to an existing one is rejected", m.validate_label("friction", set(), existing) is not None)
    chk("label with the same main word is rejected", m.validate_label("Top Friction", set(), existing) is not None)
    chk("label over 10 chars is rejected", m.validate_label("ABCDEFGHIJK", set(), []) is not None)
    chk("a distinct label passes", m.validate_label("Top Five", banned, existing) is None)
    chk("grid with a short row is rejected", m.validate_grid(["." * 16] * 15 + ["." * 15]) is not None)
    chk("grid with an illegal letter is rejected", m.validate_grid(["Z" * 16] * 16) is not None)
    chk("grid of 16x16 legal letters passes", m.validate_grid(m.fallback_grid("x")) is None)
    t1, t2 = "Fix top five measured friction sources", "Measure unreported friction with Decisions"
    l1 = m.fallback_label("a", t1, [t2], [])
    l2 = m.fallback_label("b", t2, [t1], [l1])
    chk(f"fallback labels avoid the shared word and differ ({l1}, {l2})",
        "FRICTION" not in (l1, l2) and l1 != l2 and len(l1) <= 10 and len(l2) <= 10)
    grids = {tuple(m.fallback_grid(f"id-{i}")) for i in range(500)}
    chk("fallback art: 500 session ids give 500 different sprites", len(grids) == 500, len(grids))


def check_haiku_cache(m):
    root = new_root("haiku")
    reset(m)
    stub = TMP / "stub"
    stub.mkdir(parents=True, exist_ok=True)
    log = stub / "calls.jsonl"
    log.write_text("")
    (stub / "stub.py").write_text(f"""import json, os, sys
prompt = sys.stdin.read()
n = len(open(r"{log}").read().splitlines())
open(r"{log}", "a").write(json.dumps({{"argv": sys.argv[1:], "token_in_env": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
    "api_key_in_env": "ANTHROPIC_API_KEY" in os.environ, "has_title": "stub-title" in prompt}}) + "\\n")
rows = ["................", "....KKKKKK......", "...KRRRRRRK.....", "..KRWWRRRRRK....", "..KRWRRRRRRK....", "..KRRRRRRRRK....",
        "..KRRRRRRRRK....", "..KRRRRRRRRK....", "...KRRRRRRK.....", "....KKKKKK......", ".....KNNK.......", ".....KNNK.......",
        ".....KNNK.......", ".....KNNK.......", ".....KKKK.......", "................"]
lab = ("RETRIED" if "was rejected" in prompt else "TITLE") if "retry-me" in prompt else "STUBBY"
print(json.dumps({{"is_error": False, "result": json.dumps({{"label": lab, "icon": rows}})}}))
""", encoding="utf-8")
    exe = stub / "claude-stub.cmd"
    exe.write_text(f'@echo off\r\n"{sys.executable}" "{stub / "stub.py"}" %*\r\n', encoding="utf-8")
    os.environ["M18_SESSIONS_CLAUDE"] = str(exe)
    os.environ["ANTHROPIC_API_KEY"] = "must-not-reach-the-child"
    m._token = lambda: "stub-token-not-real"
    claude(root, "55555555-0000-0000-0000-000000000001", "working", 3, "stub-title one")
    s = m.snapshot(4, force=True)
    m.want_faces(m.scan(), s["pad"])
    fp = root / "state" / "session-faces" / "55555555-0000-0000-0000-000000000001.json"
    for _ in range(100):
        if fp.exists():
            break
        time.sleep(0.2)
    chk("haiku: face cached to state/session-faces/<id>.json", fp.exists())
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    chk("haiku: exactly one call for the first sighting", len(calls) == 1, len(calls))
    if calls:
        a = calls[0]["argv"]
        chk("haiku: CLI flags as specified", all(x in a for x in ("-p", "--safe-mode", "--strict-mcp-config", "--no-session-persistence"))
            and a[a.index("--model") + 1] == "claude-haiku-5-5" and a[a.index("--output-format") + 1] == "json"
            and a[a.index("--tools") + 1] == "", a)
        chk("haiku: token reaches the child env, API key does not", calls[0]["token_in_env"] and not calls[0]["api_key_in_env"])
    for _ in range(3):
        m.want_faces(m.scan(force=True), m.snapshot(4, force=True)["pad"])
        m.slot(Ctx(), zone=[6, 7, 9, 10], slot=0)
    reset(m)                                          # restart: memory empty, file stays
    m._token = lambda: "stub-token-not-real"
    m.want_faces(m.scan(force=True), m.snapshot(4, force=True)["pad"])
    time.sleep(1.5)
    chk("haiku: cache prevents a second call (repeat ticks and a restart)", len(log.read_text().splitlines()) == 1,
        len(log.read_text().splitlines()))
    lab, grid = m.face_for(m.scan()[0], m.scan())
    chk("haiku: cached label and icon are what the face uses", lab == "STUBBY" and grid[2] == "...KRRRRRRK.....")
    # retry once with the reason, then success
    claude(root, "55555555-0000-0000-0000-000000000002", "working", 3, "stub-title retry-me two")
    s2 = [x for x in m.scan(force=True) if x["title"].startswith("stub-title retry")][0]
    ok = m.make_face(s2, ["stub-title one", s2["title"]], ["STUBBY"], "stub-token-not-real")
    n = len(log.read_text().splitlines())
    chk("haiku: a label with the shared word is rejected, retried once with the reason, second answer cached",
        n == 3 and ok and m.get_face(s2["id"]) and m.get_face(s2["id"])["label"] == "RETRIED", (n, ok))
    # no token: silent, fallback only
    m._token = lambda: None
    m._TOK.update(t=0.0, v=False)
    claude(root, "55555555-0000-0000-0000-000000000003", "working", 3, "no token three")
    before = len(log.read_text().splitlines())
    s = m.snapshot(4, force=True)
    m.want_faces(m.scan(force=True), s["pad"])
    time.sleep(1)
    s3 = [x for x in m.scan() if x["title"] == "no token three"][0]
    lab, grid = m.face_for(s3, m.scan())
    chk("haiku: no token -> no call, fallback face", len(log.read_text().splitlines()) == before and not m._Q and lab)
    del os.environ["M18_SESSIONS_CLAUDE"]
    del os.environ["ANTHROPIC_API_KEY"]


def dec_stub():
    """A stand-in for decide.mjs (same stdin/stdout contract). Answers by keyword; logs every request; flag files
    in its folder make it fail (dec_fail) or sleep (dec_sleep holds seconds)."""
    d = TMP / "decstub"
    d.mkdir(parents=True, exist_ok=True)
    (d / "calls.jsonl").write_text("")
    for f in ("dec_fail", "dec_sleep"):
        (d / f).unlink(missing_ok=True)
    (d / "stub.py").write_text(f"""import json, sys, time, os
D = r"{d}"
req = json.loads(sys.stdin.read())
open(os.path.join(D, "calls.jsonl"), "a").write(json.dumps({{"argv": sys.argv[1:], "req": req}}) + "\\n")
if os.path.exists(os.path.join(D, "dec_sleep")):
    time.sleep(float(open(os.path.join(D, "dec_sleep")).read() or 2))
if os.path.exists(os.path.join(D, "dec_fail")):
    sys.exit(1)
text = req["input"][0]["content"][0]["text"]
names = [q["name"] for q in req["questions"]]
ans = []
if "state" in names:
    if "BLOCKED:" in text: k, u = "blocked", 3
    elif "GO-AHEAD" in text: k, u = "approval", 2
    elif "NOTE-ONLY" in text: k, u = "finished", 0
    elif "LOWCONF" in text: k, u = "blocked", 2
    elif "Which" in text or "?" in text: k, u = "question", 2.5
    else: k, u = "finished", 0
    vals = ["finished", "question", "approval", "blocked", "other"]
    ans.append({{"type": "choice", "name": "state", "choice": k, "confidence": 0.9,
                "probabilities": [{{"value": v, "probability": (0.6 if "LOWCONF" in text else 0.9) if v == k else 0.025}} for v in vals]}})
    ans.append({{"type": "predicate", "name": "has_recommendation", "probability": 0.97 if "I recommend" in text else 0.03}})
    ans.append({{"type": "score", "name": "urgency", "score": u, "confidence": 0.8, "probabilities": []}})
else:
    lab = text.split("Candidate label: ", 1)[1].split("\\n", 1)[0]
    ans.append({{"type": "predicate", "name": "distinct", "probability": 0.05 if "DUPE" in lab else 0.9}})
    ans.append({{"type": "predicate", "name": "on_topic", "probability": 0.05 if "OFFTOPIC" in lab else 0.9}})
print(json.dumps({{"model": "gpt-6-luna", "answers": ans, "usage": {{"input_tokens": 400}}}}))
""", encoding="utf-8")
    exe = d / "decide-stub.cmd"
    exe.write_text(f'@echo off\r\n"{sys.executable}" "{d / "stub.py"}" %*\r\n', encoding="utf-8")
    return d, exe


def dcalls(d):
    return [json.loads(x) for x in (d / "calls.jsonl").read_text().splitlines() if x.strip()]


def wait_for(fn, secs=12):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.15)
    return fn()


def check_decisions(m):
    d, exe = dec_stub()
    on = {"M18_SESSIONS_DECISIONS": "on", "M18_SESSIONS_DECISIONS_CMD": str(exe)}
    os.environ.update(on)
    try:
        _check_decisions(m, d)
    finally:
        os.environ["M18_SESSIONS_DECISIONS"] = "off"
        os.environ.pop("M18_SESSIONS_DECISIONS_CMD", None)
        os.environ.pop("M18_DECISIONS_JS", None)


def _check_decisions(m, d):
    root = new_root("dec")
    reset(m)
    ids = [f"77777777-0000-0000-0000-00000000000{i}" for i in range(1, 7)]
    claude(root, ids[0], "finished", 30, "d-approval", text="I need your GO-AHEAD to push the branch.")
    claude(root, ids[1], "finished", 30, "d-blocked", reg=("idle", 28, None), text="BLOCKED: the login expired, sign in again.")
    claude(root, ids[2], "finished", 30, "d-question-rec", text="Done. I recommend option B. Which one do you want?")
    claude(root, ids[3], "finished", 30, "d-note-only", reg=("idle", 28, None), text="Done. Anything else, let me know. NOTE-ONLY")
    claude(root, ids[4], "finished", 30, "d-plain", text="Done. All 12 checks pass.")
    claude(root, ids[5], "finished", 30, "d-lowconf", text="LOWCONF: maybe stuck, who knows.")
    t = by_title(m.scan(force=True))
    chk("decisions: before any answer the rules decide (finished/finished/waiting/waiting/finished/finished)",
        [t[k]["state"] for k in ("d-approval", "d-blocked", "d-question-rec", "d-note-only", "d-plain", "d-lowconf")]
        == ["finished", "finished", "waiting", "waiting", "finished", "finished"], {k: v["state"] for k, v in t.items()})
    ok = wait_for(lambda: len(m._DEC["e"]) == 6 and not m._DPEND)
    chk("decisions: one read per session, in the background (6 sessions -> 6 calls)", ok and len(dcalls(d)) == 6, len(dcalls(d)))
    t = by_title(m.scan(force=True))
    chk("decisions: GO-AHEAD message (rules said finished) -> waiting / approval",
        (t["d-approval"]["state"], t["d-approval"]["wkind"]) == ("waiting", "approval"), t["d-approval"])
    chk("decisions: blocked message -> waiting / blocked", (t["d-blocked"]["state"], t["d-blocked"]["wkind"]) == ("waiting", "blocked"))
    chk("decisions: question with recommendation -> waiting / question / has_recommendation",
        (t["d-question-rec"]["state"], t["d-question-rec"]["wkind"], t["d-question-rec"]["has_recommendation"]) == ("waiting", "question", True))
    chk("decisions: never talks a rule's waiting down ('let me know' note stays waiting, kind from the rules)",
        (t["d-note-only"]["state"], t["d-note-only"]["wkind"]) == ("waiting", "question"), t["d-note-only"])
    chk("decisions: a low-confidence blocked (0.6 < 0.7) does not turn a finished key into waiting",
        t["d-lowconf"]["state"] == "finished", t["d-lowconf"])
    chk("decisions: plain done -> finished, kind None, urgency 0", (t["d-plain"]["state"], t["d-plain"]["wkind"], t["d-plain"]["urgency"]) == ("finished", None, 0))
    sm = json.loads((root / "state" / "sessions-summary.json").read_text())
    row = {r["title"]: r for r in sm["sessions"]}
    chk("decisions: sessions-summary.json has kind, has_recommendation, urgency per session",
        row["d-approval"]["kind"] == "approval" and row["d-blocked"]["kind"] == "blocked" and row["d-blocked"]["urgency"] == 3
        and row["d-question-rec"]["has_recommendation"] is True and row["d-approval"]["has_recommendation"] is False
        and sm["kinds"] == {"question": 2, "approval": 1, "blocked": 1}, (sm.get("kinds"), row.get("d-blocked")))
    chk("decisions: summary counts still equal the pad states", sm["waiting"] == 4 and sm["finished"] == 2 and sm["working"] == 0, sm)
    c0 = dcalls(d)[0]
    body = json.dumps(c0["req"])
    qn = {q["name"]: q for q in c0["req"]["questions"]}
    chk("decisions: request asks state (5 kinds), has_recommendation and urgency (4 levels)",
        set(qn) == {"state", "has_recommendation", "urgency"} and [c["value"] for c in qn["state"]["choices"]] == list(m.KINDS)
        and len(qn["urgency"]["levels"]) == 4 and qn["has_recommendation"]["type"] == "predicate")
    chk("decisions: caching off (explicit mode, no breakpoints)", c0["req"]["prompt_cache_options"] == {"mode": "explicit"}
        and "breakpoint" not in body)
    a = c0["argv"]
    chk("decisions: per-call token cap passed, no secret in argv", int(a[a.index("--max-input-tokens") + 1]) <= 4000
        and not any("sk-" in x for x in a), a)
    # repeats and a restart add no calls; a new last message adds exactly one
    for _ in range(3):
        m.scan(force=True)
    reset(m)
    m.scan(force=True)
    time.sleep(1.2)
    chk("decisions: cached by session + message hash (repeat ticks and a restart add no call)", len(dcalls(d)) == 6, len(dcalls(d)))
    t = by_title(m.scan(force=True))
    chk("decisions: answer comes back from state/session-decisions.json after the restart", t["d-blocked"]["wkind"] == "blocked")
    claude(root, ids[0], "finished", 20, "d-approval", text="Pushed. BLOCKED: the remote refused it.")
    wait_for(lambda: m.scan(force=True) and len(dcalls(d)) == 7)
    wait_for(lambda: not m._DPEND)
    t = by_title(m.scan(force=True))
    chk("decisions: a new last message triggers one new read", len(dcalls(d)) == 7 and t["d-approval"]["wkind"] == "blocked", len(dcalls(d)))
    big = dcalls(d)
    claude(root, ids[4], "finished", 10, "d-plain", text="A" * 5000 + " done?")
    wait_for(lambda: m.scan(force=True) and len(dcalls(d)) == 8)
    last = json.dumps(dcalls(d)[-1]["req"])
    chk("decisions: message capped at 2,000 chars", last.count("A") <= 2000 + 20 and last.count("A") >= 1900, last.count("A"))
    # failures fall back to the rules without a retry storm
    (d / "dec_fail").write_text("1")
    claude(root, "77777777-0000-0000-0000-0000000000f1", "finished", 5, "d-fail", text="Want me to run it?")
    t = by_title(m.scan(force=True))
    wait_for(lambda: len(dcalls(d)) == 9 and not m._DPEND)
    n = len(dcalls(d))
    for _ in range(4):
        m.scan(force=True)
    time.sleep(0.8)
    t = by_title(m.scan(force=True))
    chk("decisions: a failing call falls back to the rules (waiting/question) and is not retried at once",
        t["d-fail"]["state"] == "waiting" and t["d-fail"]["wkind"] == "question" and t["d-fail"]["kind_src"] == "rules"
        and len(dcalls(d)) == n == 9, (t["d-fail"], len(dcalls(d))))
    (d / "dec_fail").unlink()
    # render path never waits for a call
    (d / "dec_sleep").write_text("2")
    claude(root, "77777777-0000-0000-0000-0000000000f2", "finished", 5, "d-slow", text="Done. Fine.")
    ctx = Ctx()
    t0 = time.perf_counter()
    for sl in range(4):
        m.slot(ctx, zone=[6, 7, 9, 10], slot=sl)
    ms = (time.perf_counter() - t0) * 1000
    chk(f"decisions: slot() does not wait for a pending call ({ms:.0f} ms with a 2 s call in flight)", ms < 400 and m._DPEND, ms)
    wait_for(lambda: not m._DPEND, 8)
    (d / "dec_sleep").unlink()
    # unavailable: no node/script -> no call, rules only
    root = new_root("dec-off")
    reset(m)
    os.environ.pop("M18_SESSIONS_DECISIONS_CMD")
    os.environ["M18_DECISIONS_JS"] = str(TMP / "no-such" / "decide.mjs")
    before = len(dcalls(d))
    claude(root, "88888888-0000-0000-0000-000000000001", "finished", 5, "u-q", text="Which one do you want?")
    t = by_title(m.scan(force=True))
    time.sleep(0.8)
    chk("decisions: script missing -> silent fallback to the rules, no call", t["u-q"]["state"] == "waiting"
        and t["u-q"]["wkind"] == "question" and len(dcalls(d)) == before and not m._DQ)
    os.environ["M18_SESSIONS_DECISIONS_CMD"] = str(d / "decide-stub.cmd")
    # daily cap
    root = new_root("dec-cap")
    reset(m)
    (root / "state").mkdir(parents=True)
    (root / "state" / "session-decisions.json").write_text(json.dumps(
        {"v": 1, "entries": {}, "usage": {"day": time.strftime("%Y-%m-%d"), "tokens": m.DEC_DAILY_TOKENS - 10, "calls": []}}))
    before = len(dcalls(d))
    claude(root, "88888888-0000-0000-0000-000000000002", "finished", 5, "cap-q", text="Which one do you want?")
    m.scan(force=True)
    time.sleep(1.2)
    t = by_title(m.scan(force=True))
    chk("decisions: over the daily token cap -> no call, rules decide", len(dcalls(d)) == before and t["cap-q"]["state"] == "waiting")
    # rule kinds with the classifier off
    root = new_root("dec-rules")
    reset(m)
    os.environ["M18_SESSIONS_DECISIONS"] = "off"
    claude(root, "99999999-0000-0000-0000-000000000001", "ask", 5, "k-ask")
    claude(root, "99999999-0000-0000-0000-000000000002", "finished", 30, "k-regwait", reg=("waiting", 25, "approve Bash"))
    claude(root, "99999999-0000-0000-0000-000000000003", "question", 5, "k-rule")
    t = by_title(m.scan(force=True))
    chk("kinds by rules: pending ask and a question text -> question, registry 'waiting' with nothing asked -> approval",
        (t["k-ask"]["wkind"], t["k-regwait"]["wkind"], t["k-rule"]["wkind"]) == ("question", "approval", "question"),
        {k: v["wkind"] for k, v in t.items()})
    os.environ["M18_SESSIONS_DECISIONS"] = "on"
    check_clash(m, d)


def check_clash(m, d):
    root = new_root("clash")
    reset(m)
    stub = TMP / "stub-clash"
    stub.mkdir(parents=True, exist_ok=True)
    log = stub / "calls.jsonl"
    log.write_text("")
    (stub / "stub.py").write_text(f"""import json, sys
prompt = sys.stdin.read()
open(r"{log}", "a").write(json.dumps({{"reason": "tell this session apart" in prompt or "actual topic" in prompt}}) + "\\n")
rows = ["................", "....KKKKKK......", "...KRRRRRRK.....", "..KRWWRRRRRK....", "..KRWRRRRRRK....", "..KRRRRRRRRK....",
        "..KRRRRRRRRK....", "..KRRRRRRRRK....", "...KRRRRRRK.....", "....KKKKKK......", ".....KNNK.......", ".....KNNK.......",
        ".....KNNK.......", ".....KNNK.......", ".....KKKK.......", "................"]
mine = prompt.split("Session title: ", 1)[1].split("\\n", 1)[0]
if "clash-once" in mine: lab = "GOODONE" if "was rejected" in prompt else "DUPEONE"
elif "clash-twice" in mine: lab = "DUPETWO" if "was rejected" not in prompt else "OFFTOPIC"
else: lab = "PLAINOK"
print(json.dumps({{"is_error": False, "result": json.dumps({{"label": lab, "icon": rows}})}}))
""", encoding="utf-8")
    exe = stub / "claude-stub.cmd"
    exe.write_text(f'@echo off\r\n"{sys.executable}" "{stub / "stub.py"}" %*\r\n', encoding="utf-8")
    os.environ["M18_SESSIONS_CLAUDE"] = str(exe)
    m._token = lambda: "stub-token-not-real"
    claude(root, "aaaaaaaa-0000-0000-0000-000000000001", "working", 3, "peer session about pads", first="peer first message")
    claude(root, "aaaaaaaa-0000-0000-0000-000000000002", "working", 3, "clash-once topic", first="clash-once please fix the login flow")
    claude(root, "aaaaaaaa-0000-0000-0000-000000000003", "working", 3, "clash-twice topic", first="clash-twice again")
    ss = m.scan(force=True)
    by = by_title(ss)
    m.get_face("x")
    n0 = len(dcalls(d))
    ok1 = m.make_face(by["clash-once topic"], [s["title"] for s in ss], ["PEERLAB"], "stub-token-not-real")
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    dc = dcalls(d)[n0:]
    chk("clash: label judged not distinct -> one retry carrying the reason -> second label cached",
        ok1 and len(calls) == 2 and calls[1]["reason"] and m.get_face(by["clash-once topic"]["id"])["label"] == "GOODONE", (ok1, calls))
    chk("clash: two predicates asked per label (distinct, on_topic), input has the label, first request and the others' titles",
        len(dc) == 2 and {q["name"] for q in dc[0]["req"]["questions"]} == {"distinct", "on_topic"}
        and "Candidate label: DUPEONE" in json.dumps(dc[0]["req"]) and "login flow" in json.dumps(dc[0]["req"])
        and "peer session about pads" in json.dumps(dc[0]["req"]) and "Candidate label: GOODONE" in json.dumps(dc[1]["req"]),
        len(dc))
    log.write_text("")
    ok2 = m.make_face(by["clash-twice topic"], [s["title"] for s in ss], [], "stub-token-not-real")
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    chk("clash: still bad after the retry -> no Haiku face, title-word fallback label, no further Haiku attempts",
        (not ok2) and len(calls) == 2 and m.get_face(by["clash-twice topic"]["id"]) is None
        and m._FAILED[by["clash-twice topic"]["id"]][0] >= 3, (ok2, len(calls)))
    lab, grid = m.face_for(by["clash-twice topic"], ss)
    chk(f"clash: fallback face is made from title words ({lab})", lab in ("CLASH", "TWICE", "TOPIC", "CLASH-TWICE") or lab.startswith(("CLASH", "TWICE", "TOPIC")), lab)
    # Decisions failing never blocks a label
    (d / "dec_fail").write_text("1")
    log.write_text("")
    claude(root, "aaaaaaaa-0000-0000-0000-000000000004", "working", 3, "plain topic four", first="plain")
    s4 = by_title(m.scan(force=True))["plain topic four"]
    ok3 = m.make_face(s4, ["plain topic four"], [], "stub-token-not-real")
    (d / "dec_fail").unlink()
    chk("clash: Decisions down -> the label is accepted as before", ok3 and m.get_face(s4["id"])["label"] == "PLAINOK")
    del os.environ["M18_SESSIONS_CLAUDE"]
    os.environ["M18_SESSIONS_DECISIONS"] = "off"

def check_silence(m):
    """Silence age: a working session quiet past the threshold shows its age, in a look of its own."""
    root = new_root("silence")
    reset(m)
    m._SILENT["after"] = m.SILENT_AFTER
    ids = [f"bbbbbbbb-0000-0000-0000-00000000000{i}" for i in range(1, 7)]
    claude(root, ids[0], "working", 14 * 60, "q-quiet", reg=("busy", 14 * 60, None))
    claude(root, ids[1], "working", 5 * 60, "q-recent", reg=("busy", 5 * 60, None))
    claude(root, ids[2], "bash", 630, "q-bash-in-timeout", reg=("busy", 630, None))
    claude(root, ids[3], "bash", 12 * 60, "q-bash-over", reg=("busy", 12 * 60, None))
    claude(root, ids[4], "working", 14 * 60, "q-subagent", reg=("busy", 14 * 60, None))
    sub = root / "claude" / "projects" / "proj" / ids[4] / "subagents"
    put(sub / "agent-x.jsonl", [_u(time.time() - 60, "sub")], time.time() - 60)
    claude(root, ids[5], "finished", 20 * 60, "q-finished")
    codex(root, "01a1c000-0000-7000-8000-0000000000b1", "working", 13 * 60)
    os.environ["M18_SESSIONS_DRY"] = "1"
    ctx = Ctx()
    m.slot(ctx, zone=[6, 7, 9, 10], slot=0, silent_min=8)       # the config arg sets the pad-wide threshold
    t = by_title(m.scan(force=True))
    chk("silence: working, quiet 14 min (> 8) -> silent, silent_s about 840",
        t["q-quiet"]["silent"] and abs(t["q-quiet"]["silent_s"] - 840) < 15, (t["q-quiet"]["silent"], t["q-quiet"]["silent_s"]))
    chk("silence: quiet 5 min -> not silent", t["q-recent"]["state"] == "working" and not t["q-recent"]["silent"])
    chk("silence: pending foreground Bash (10 min timeout) quiet 10.5 min -> not silent (timeout + 60 s)",
        not t["q-bash-in-timeout"]["silent"] and t["q-bash-in-timeout"]["wait_s"] == 600, t["q-bash-in-timeout"].get("wait_s"))
    chk("silence: same Bash quiet 12 min -> silent", t["q-bash-over"]["silent"])
    chk("silence: a subagent file written 1 min ago keeps the session not silent", not t["q-subagent"]["silent"]
        and t["q-subagent"]["silent_s"] < 120, t["q-subagent"]["silent_s"])
    chk("silence: finished session has silent_s None", t["q-finished"]["silent_s"] is None and not t["q-finished"]["silent"])
    cx = [s for s in t.values() if s["kind"] == "codex"][0]
    chk("silence: Codex working rollout quiet 13 min -> silent", cx["state"] == "working" and cx["silent"], cx.get("silent_s"))
    sm = json.loads((root / "state" / "sessions-summary.json").read_text())
    row = {r["title"]: r for r in sm["sessions"]}
    chk("silence: sessions-summary.json has silent_s (whole minutes) and silent per session, and a silent count",
        row["q-quiet"]["silent_s"] == 840 and row["q-quiet"]["silent"] is True and row["q-recent"]["silent"] is False
        and row["q-finished"]["silent_s"] is None and sm["silent"] == 3, (row["q-quiet"], sm.get("silent")))
    chk("silence: default threshold is 11 min", m.SILENT_AFTER == 660)
    m._SILENT["after"] = m.SILENT_AFTER
    t = by_title(m.scan(force=True))
    chk("silence: at the 11 min default the 12 min Bash and the 14 min session are silent, 10.5 min is not",
        t["q-quiet"]["silent"] and t["q-bash-over"]["silent"] and not t["q-bash-in-timeout"]["silent"])
    # the face: age plate, own colours, not gold, not red; and the age changes the face
    g = m.fallback_grid("demo")
    wk = m.render_session("BUILD", g, "working", 0, False)
    sl = m.render_session("BUILD", g, "working", 0, False, None, 14 * 60)
    sl2 = m.render_session("BUILD", g, "working", 0, False, None, 30 * 60)
    inner = sl.getpixel((4, 40))
    chk("silence: silent tile differs from working and from another age", wk.tobytes() != sl.tobytes() != sl2.tobytes())
    chk("silence: silent colour is neither gold nor red (blue >= red, interior and plate)",
        all(c[2] >= c[0] for c in (m._rgb(m._slot_colors("silent", 0)[0]), m._rgb(m._slot_colors("silent", 0)[1]), inner)), inner)
    chk("silence: age text 14M, 59M, 1H, 9H cap", [m.age_text(x) for x in (840, 3599, 3600, 50000)] == ["14M", "59M", "1H", "9H"])
    fd = None
    for sl_ in range(4):
        f = m.slot(ctx, zone=[6, 7, 9, 10], slot=sl_)
        if f and "image" in f:
            fd = fd or f
    chk("silence: silent session face is static (no pulse)", fd is not None and "frames" not in fd)
    # hold: clipboard + open, nothing typed
    (root / "state" / "opened.log").unlink(missing_ok=True)
    snap = m.snapshot(4, force=True)
    slot_q = next(k for k, v in snap["slots"].items() if v["title"] == "q-quiet")
    r = m.nudge(Ctx(), zone=[6, 7, 9, 10], slot=slot_q)
    clip = (root / "state" / "clipboard.log").read_text(encoding="utf-8")
    chk("nudge: hold puts 'still going?' on the clipboard and opens the session (dry)",
        clip.strip().endswith("still going?") and r and r.startswith("dry:claude://") and (root / "state" / "opened.log").exists(), (clip, r))
    for src, why in ((None, "not a hardware press"), ("device", "dry run")):     # never types in a check
        c = Ctx()
        c.source = src
        r = m.nudge(c, zone=[6, 7, 9, 10], slot=slot_q, send=True)
        chk(f"nudge send=true, source={src}: nothing typed ({why}); falls back to clipboard + open",
            r and r.startswith("dry:claude://") and any(why in " ".join(map(str, x)) for x in c.logs), (r, c.logs[-2:]))
    m._SILENT["after"] = m.SILENT_AFTER


def check_previews(m):
    sys.path.insert(0, str(M18_DIR))
    from m18lib import render as R
    from PIL import Image
    settings = {"theme": {"name": "hillside"}}
    mod, _ = R.load_theme(settings, M18_DIR)
    if not mod:
        chk("previews: hillside theme loads", False, "theme not found under " + str(M18_DIR))
        return
    theme = dict(getattr(mod, "PALETTE", {}))
    zone = [6, 7, 9, 10]
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    titles = [("Photo library cleanup", "working"), ("Fix the five slowest pages", "waiting"),
              ("Find duplicate invoices", "finished"), ("Recipe site redesign", "working"),
              ("Voice memo transcriber", "waiting"), ("Measure page load times", "working"),
              ("Review of the newsletter draft", "finished"), ("Thermostat bot relay", "working"),
              ("Price tracker scraper", "waiting")]
    for n, name in ((0, "sessions-0.png"), (3, "sessions-3.png"), (9, "sessions-9.png")):
        root = new_root(f"prev{n}")
        reset(m)
        for i, (t, st) in enumerate(titles[:n]):
            shape = {"working": "working", "waiting": "ask", "finished": "finished"}[st]
            claude(root, f"66666666-0000-0000-0000-{n}{i:011d}", shape, 5 + i, t, first=t)
        ctx = Ctx(theme)
        sheet = Image.new("RGB", (320, 192))
        for k in range(1, 16):
            face = {}
            if k in zone:
                fd = m.slot(ctx, zone=zone, slot=zone.index(k))
                if fd.get("frames"):
                    fd = {"image": fd["frames"][3]}           # brightest point of the pulse
                face = fd
            tile = R.render_face(face, M18_DIR, settings, size=64, key=k)
            r, c = divmod(k - 1, 5)
            sheet.paste(tile, (c * 64, r * 64))
        sheet.save(PREVIEW_DIR / name)
        print(f"     saved {PREVIEW_DIR / name}")
    # waiting pulse: the 6 frames differ, and the three states differ from each other at 64 px
    g = m.fallback_grid("demo")
    px = {st: m.render_session("DEMO", g, st, 0.5, False) for st in ("working", "waiting", "finished")}
    chk("states: working, waiting and finished tiles all differ", len({v.tobytes() for v in px.values()}) == 3)
    f = m.render_session("DEMO", g, "waiting", 0.0, False).tobytes(), m.render_session("DEMO", g, "waiting", 1.0, False).tobytes()
    chk("states: waiting pulses (frames differ), working and finished are static", f[0] != f[1]
        and "frames" not in m.slot.__globals__["_face_dict"](lambda: px["working"], lambda p: px["working"], "working", ("k",)))
    sw = Image.new("RGB", (64 * 3 + 8, 64), "#222")
    for i, st in enumerate(("working", "waiting", "finished")):
        sw.paste(px[st], (i * 68, 0))
    sw.save(TMP / "states.png")
    # waiting kinds: "?" question, check approval, "!" blocked; blocked pulses faster; legacy (no kind) unchanged
    kinds = [("working", None), ("waiting", "question"), ("waiting", "approval"), ("waiting", "blocked"), ("finished", None)]
    labels = ["BUILD", "PICK ONE", "PUSH OK", "LOGIN", "DONE"]
    fds = []
    for (st, kd), lab in zip(kinds, labels):
        k = ("kt", lab, st, kd, True)
        fds.append(m._face_dict(lambda: m.render_session(lab, g, st, 0, True, kd), lambda ph: m.render_session(lab, g, st, ph, True, kd),
                                st, k, kd))
    chk("kinds: question, approval and blocked tiles all differ at the pulse peak",
        len({m.render_session("X", g, "waiting", 0.5, False, kd).tobytes() for kd in ("question", "approval", "blocked", None)}) == 4)
    chk("kinds: every waiting kind pulses (6 frames); blocked at 5 fps, others at 3 fps; working and finished are static",
        [(f.get("fps"), len(f.get("frames", []))) for f in fds] == [(None, 0), (3, 6), (3, 6), (5, 6), (None, 0)],
        [(f.get("fps"), len(f.get("frames", []))) for f in fds])
    chk("kinds: waiting tiles stay gold, never error red (green >= 0.6 x red in interior and badge, every kind)",
        all((lambda c: c[1] >= 0.6 * c[0])(m._slot_colors("waiting", p)[i]) for p in (0, .5, 1) for i in (0, 1)))
    sheet = Image.new("RGB", (64 * 5 + 16, 64 * 2 + 8), "#222")
    for i, f in enumerate(fds):
        fr = f["frames"][3] if f.get("frames") else f["image"]
        face = {"image": fr}
        sheet.paste(R.render_face(face, M18_DIR, settings, size=64, key=zone[i % 4]), (i * 68, 0))
        sheet.paste(m.render_session(labels[i], g, kinds[i][0], 0.5, False, kinds[i][1]), (i * 68, 72))
    sheet.save(PREVIEW_DIR / "sessions-kinds.png")
    print(f"     saved {PREVIEW_DIR / 'sessions-kinds.png'}")
    # silent look next to working, themed (top) and plain (bottom), ages 14M and 2H
    cases = [("BUILD", None), ("BUILD", 14 * 60), ("RELAY", 2 * 3600), ("MCVOICE", 45 * 60)]
    sheet = Image.new("RGB", (68 * 4, 64 * 2 + 8), "#000")
    for i, (lab, q) in enumerate(cases):
        tile = m.render_session(lab, m.fallback_grid(lab), "working", 0, True, None, q)
        sheet.paste(R.render_face({"image": tile}, M18_DIR, settings, size=64, key=zone[i % 4]), (i * 68, 0))
        sheet.paste(m.render_session(lab, m.fallback_grid(lab), "working", 0, False, None, q), (i * 68, 72))
    sheet.save(PREVIEW_DIR / "sessions-silent.png")
    print(f"     saved {PREVIEW_DIR / 'sessions-silent.png'}")


def real_scan(m):
    os.environ.pop("M18_SESSIONS_ROOT", None)
    os.environ["M18_HOME"] = str(TMP / "realhome")           # our own state files stay in scratch
    reset(m)
    m.TTL = 2.0
    ss = m.scan(force=True)
    now = time.time()
    print(f"\nREAL scan: {len(ss)} sessions (stale ones hidden below); '*' = on the pad")
    for s in ss:
        if s["state"] != "stale":
            print(f" {'*' if s['on_pad'] else ' '} {s['kind']:6} {s['state']:8} {int(now - s['since']):5d}s  "
                  f"{s['title'][:44]:44} {s['id'][:8]} {'link' if s['link'] else 'no-link'}")
    print(f"   ({sum(s['state'] == 'stale' for s in ss)} stale not shown)")


def main():
    m = load()
    os.environ["M18_SESSIONS_DRY"] = "1"
    os.environ["M18_SESSIONS_DECISIONS"] = "off"            # only check_decisions turns the (stubbed) classifier on
    os.environ.setdefault("M18_SESSIONS_TOKEN_TARGET", "Claude/M18/NoSuchTarget-check")   # checks never spend real calls
    for fn in (check_states, check_classify, check_empty, check_slots, check_forty, check_labels,
               check_haiku_cache, check_decisions, check_silence, check_previews):
        print(f"-- {fn.__name__}")
        try:
            fn(m)
        except Exception as e:                                # noqa: BLE001
            import traceback
            traceback.print_exc()
            chk(f"{fn.__name__} ran without error", False, repr(e))
    if "--real" in sys.argv:
        real_scan(m)
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
