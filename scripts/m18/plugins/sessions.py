"""Session keys: one key per running AI coding session (Claude Code desktop/CLI and Codex).

    6:  {live: {plugin: sessions.slot, interval: 1, args: {zone: [6, 7, 9, 10], slot: 0}},
         action: {plugin: sessions.press, args: {zone: [6, 7, 9, 10], slot: 0}}}
    7:  ... same with slot: 1, 9: slot 2, 10: slot 3   (the LAST slot turns into "+N" on overflow)

State comes from disk only (no hooks): ~/.claude/sessions/<pid>.json (live process registry),
the desktop app's session records, ~/.claude/projects transcripts (last 64 KB) and
~/.codex/sessions rollouts. See references/sessions.md.
"""
from __future__ import annotations

import ctypes
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

# ------------------------------------------------------------------ tunables
WINDOW = 2 * 3600          # only files modified in the last 2 h are read
TAIL = 64 * 1024           # bytes read from the end of a file
TTL = 2.0                  # scanner refresh interval, seconds
FINISHED_KEEP = 30 * 60    # a finished session stays on the pad this long unless pressed
NOREG_STALE = 10 * 60      # in-turn with no live process record and no writes this long: stale
CODEX_STALE = 15 * 60
HAIKU_PER_HOUR = 20
HAIKU_MODEL = "claude-haiku-5-5"
TOKEN_TARGET = "Claude/M18/OAuth"
PRIO = {"waiting": 0, "working": 1, "finished": 2, "stale": 3}
# Decisions API (OpenAI gpt-6-luna classifier). The user's grant: event-triggered only, 1M input tokens a day.
DEC_DAILY_TOKENS = 950_000   # shared by the waiting-kind and label-clash calls, kept under the 1M grant
DEC_CALL_TOKENS = 4000       # --max-input-tokens for one call; a request estimated above this is never sent
DEC_PER_HOUR = 90            # call-count guard on top of the token cap
DEC_MIN_CONF = 0.5           # the waiting kind (?, check, !) is taken from Decisions at this probability or more
DEC_ESCALATE = 0.7           # Decisions may turn a rule's "finished" into "waiting" only at this probability or more
# Measured on 30 labelled real messages (references/sessions.md): Decisions ties the rules on ordinary messages and
# beats them only on blocked/approval, which the rules cannot see. So the rules stay primary for finished vs waiting;
# Decisions escalates and names the kind, and never talks a rule's "waiting" down.
CLASH_DISTINCT = 0.5         # label clash check: both predicates must reach their cut. Picked from 63 labelled labels
CLASH_TOPIC = 0.3            # (AUC 0.97 distinct, 0.82 topic) and checked on 24 held-out ones: see references/sessions.md
# Silence: a working session with no transcript/rollout write for this long shows its silence age ("14M").
# Picked 2026-11-11 from in-turn gaps in 20 real sessions (references/sessions.md): foreground shell calls run up to
# their 10-minute cap and cluster at 9.6-10.0 min, so 8 min would flag about 0.7 of them per working hour; above 11 min
# only 3 gaps were left in 23 working hours. A pending foreground tool with a longer timeout extends it (timeout + 60 s).
SILENT_AFTER = 11 * 60
_SILENT = {"after": SILENT_AFTER}
KINDS = ("finished", "question", "approval", "blocked", "other")
WAIT_KINDS = ("question", "approval", "blocked")


# ------------------------------------------------------------------ paths (one overridable root)
def paths() -> dict:
    """M18_SESSIONS_ROOT swaps every location at once (used by sessions_check.py)."""
    root = os.environ.get("M18_SESSIONS_ROOT")
    if root:
        r = Path(root)
        return dict(projects=r / "claude" / "projects", registry=r / "claude" / "sessions",
                    app=r / "app", codex=r / "codex" / "sessions",
                    codex_index=r / "codex" / "session_index.jsonl", state=r / "state")
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    m18 = Path(os.environ.get("M18_HOME") or home / "tools" / "m18")
    return dict(projects=home / ".claude" / "projects", registry=home / ".claude" / "sessions",
                app=local / "Claude-Data", codex=home / ".codex" / "sessions",
                codex_index=home / ".codex" / "session_index.jsonl", state=m18 / "state")


# ------------------------------------------------------------------ classify (replace later)
_CUES = re.compile(
    r"\b(let me know|which (one|do you|would you|should)|do you want|would you like|want me to|"
    r"should i|shall i|please confirm|your call|reply with|tell me (which|if|what)|"
    r"awaiting your|waiting (for|on) (you|your)|ready for your|over to you)\b", re.I)


def classify(last_text: str) -> str:
    """Plain heuristic: does a finished turn's last message ask the user something? -> waiting|finished.
    A fast classifier API will replace this one function."""
    t = (last_text or "").strip()
    if not t or t.endswith("```"):
        return "finished"
    paras = [p.strip() for p in re.split(r"\n\s*\n", t[-700:]) if p.strip()]
    last = paras[-1] if paras else t
    bare = re.sub(r"[\s\)\]\}\"'*_`>]+$", "", last)
    if bare.endswith("?"):
        return "waiting"
    sents = re.split(r"(?<=[.!?:])\s+", last)
    return "waiting" if _CUES.search(" ".join(sents[-2:])) else "finished"


# ------------------------------------------------------------------ small readers
def _ts(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _tail_objs(path, n=TAIL) -> list:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            b = f.read()
    except OSError:
        return []
    lines = b.split(b"\n")
    if size > n:
        lines = lines[1:]                      # first line is cut
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out


def _json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


_POOL = {"p": None}


def _pmap(fn, items) -> list:
    """Map over files with a small thread pool: opening a file costs ~4 ms here (antivirus), reads overlap."""
    items = list(items)
    if len(items) < 4:
        return [fn(x) for x in items]
    if _POOL["p"] is None:
        from concurrent.futures import ThreadPoolExecutor
        _POOL["p"] = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sessions-scan")
    return list(_POOL["p"].map(fn, items))


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _ask_text(name, inp) -> str:
    """The question and option labels of a pending ask (AskUserQuestion, wrap_up ask, ExitPlanMode), as plain text."""
    out = []
    try:
        if isinstance(inp.get("questions"), list):
            for q in inp["questions"]:
                if isinstance(q, dict):
                    opts = ", ".join(str(o.get("label", "")) for o in q.get("options") or [] if isinstance(o, dict))
                    out.append(f"{q.get('question', '')}" + (f" Options: {opts}" if opts else ""))
        if inp.get("ask"):
            acts = ", ".join(str(a.get("label", "")) for a in inp.get("actions") or [] if isinstance(a, dict))
            out.append(str(inp["ask"]) + (f" Buttons: {acts}" if acts else ""))
        if name == "ExitPlanMode":
            out.append("Plan approval requested: " + str(inp.get("plan", ""))[:400])
    except Exception:                                        # noqa: BLE001 - a odd tool shape never breaks the scan
        pass
    return _clip(" | ".join(x for x in out if x), 700)


# ------------------------------------------------------------------ Claude transcript tail
_ASK_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
_CL_CACHE: dict = {}


def parse_claude_tail(objs) -> dict:
    """Reduce the tail of a transcript to what the state needs."""
    pending: dict = {}
    pto: dict = {}              # pending foreground tool id -> its timeout in seconds (Bash/PowerShell `timeout`)
    kind = None                 # prompt | tool_result | assistant | interrupted
    stop = None
    text = ""
    ask = False
    ask_text = ""
    last_ts = None
    entry = title = prompt = None
    queued = 0
    for o in objs:
        t = o.get("type")
        if o.get("entrypoint"):
            entry = o["entrypoint"]
        if t == "custom-title":
            title = o.get("customTitle") or title
        elif t == "last-prompt":
            prompt = o.get("lastPrompt") or prompt
        elif t == "queue-operation":
            op = o.get("operation")
            queued = queued + 1 if op == "enqueue" else (max(0, queued - 1) if op == "dequeue" else queued)
        elif t in ("user", "assistant"):
            if o.get("isSidechain"):
                continue
            m = o.get("message") or {}
            c = m.get("content")
            ts = _ts(o.get("timestamp"))
            if t == "user":
                blocks = c if isinstance(c, list) else [{"type": "text", "text": c or ""}]
                results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
                if results:
                    for b in results:
                        pending.pop(b.get("tool_use_id"), None)
                    kind = "tool_result"
                elif o.get("isMeta"):
                    continue
                else:
                    first = next((b.get("text", "") for b in blocks if isinstance(b, dict)
                                  and b.get("type") == "text"), "")
                    if first.startswith("[Request interrupted"):
                        kind = "interrupted"
                    else:
                        kind, text, ask, pending, ask_text, pto = "prompt", "", False, {}, "", {}
                        queued = max(0, queued - 1)
            else:
                kind, stop = "assistant", m.get("stop_reason")
                for b in c if isinstance(c, list) else []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        text = b["text"]
                    elif b.get("type") == "tool_use":
                        pending[b.get("id")] = b.get("name", "")
                        inp_ = b.get("input") or {}
                        if isinstance(inp_, dict) and inp_.get("timeout") and not inp_.get("run_in_background"):
                            try:
                                pto[b.get("id")] = float(inp_["timeout"]) / 1000.0
                            except (TypeError, ValueError):
                                pass
                        if b.get("name", "").endswith("wrap_up") and (b.get("input") or {}).get("ask"):
                            ask = True
                            ask_text = _ask_text("wrap_up", b.get("input") or {})
                        elif b.get("name", "") in _ASK_TOOLS:
                            ask_text = _ask_text(b["name"], b.get("input") or {})
            last_ts = ts or last_ts
    asking = ask or any(n in _ASK_TOOLS for n in pending.values())
    ended = kind == "interrupted" or (kind == "assistant" and stop not in (None, "tool_use") and not pending)
    wait_s = max((v for k, v in pto.items() if k in pending), default=0.0)
    return dict(entry=entry, title=title, prompt=prompt, text=text, ask_text=ask_text if asking else "",
                last_ts=last_ts, asking=asking, in_turn=(not ended) and kind is not None, ended=ended,
                queued=queued, has_msgs=kind is not None, wait_s=wait_s)


def rule_kind(state, asking=False, reg_waiting=False, text="") -> str | None:
    """Kind of a waiting state without Decisions: a pending ask or a rule hit is a question; a registry
    'waiting' with nothing asked is a permission prompt (approval)."""
    if state != "waiting":
        return None
    return "question" if (asking or not reg_waiting) else "approval"


def dec_state(dec, text) -> str:
    """finished|waiting. The rules decide; a confident Decisions answer naming a waiting kind can only escalate."""
    if classify(text) == "waiting":
        return "waiting"
    if dec and dec.get("state") in WAIT_KINDS and dec.get("conf", 0) >= DEC_ESCALATE:
        return "waiting"
    return "finished"


def claude_state(info: dict, reg: dict | None, mtime: float, now: float, dec: dict | None = None,
                 why: dict | None = None):
    """-> (state, since). Registry status (busy/idle/waiting) is the best signal when the
    process is alive; the transcript tail decides when it is missing or older than the transcript.
    `dec` is a cached Decisions answer for the last message; on the ended path it can escalate the rule guess (see dec_state).
    `why` (optional dict) receives path='asking'|'registry-waiting'|'ended' for the kind and the Decisions trigger."""
    why = why if why is not None else {}
    last_ts = info["last_ts"] or mtime
    sts = (reg or {}).get("statusUpdatedAt")
    sts = sts / 1000.0 if sts else None
    st = (reg or {}).get("status")
    fresh = sts is not None and mtime > sts + 5          # transcript moved after the registry wrote
    active = info["in_turn"] or info["queued"] > 0
    if info["asking"] and not (st == "busy" and fresh):
        why["path"] = "asking"
        return "waiting", last_ts
    if reg:
        if st == "waiting" and not (active and fresh):
            why["path"] = "registry-waiting"
            return "waiting", sts or last_ts
        if st == "busy" or (active and (fresh or st == "waiting")):
            return "working", sts if st == "busy" and sts else last_ts
        # idle (or unknown): fall through to the ended path
    elif active:
        return ("working", last_ts) if now - mtime < NOREG_STALE else ("stale", last_ts)
    since = sts if (reg and st == "idle" and sts) else last_ts
    why["path"] = "ended"
    return dec_state(dec, info["text"]), since


# ------------------------------------------------------------------ Codex rollout tail
_CX_CACHE: dict = {}
_CX_META: dict = {}


def parse_codex_tail(objs) -> dict:
    life = None
    last_msg = ""
    last_ts = None
    pend: dict = {}
    for o in objs:
        ts = _ts(o.get("timestamp"))
        last_ts = ts or last_ts
        pl = o.get("payload") or {}
        ty = pl.get("type")
        if o.get("type") == "event_msg":
            if ty == "task_started":
                life, last_msg, pend = "started", "", {}
            elif ty == "task_complete":
                life, last_msg = "complete", pl.get("last_agent_message") or ""
            elif ty == "turn_aborted":
                life, last_msg = "aborted", ""
        elif o.get("type") == "response_item":
            if ty in ("function_call", "custom_tool_call") and pl.get("name") == "request_user_input":
                pend[pl.get("call_id")] = ts
            elif ty in ("function_call_output", "custom_tool_call_output"):
                pend.pop(pl.get("call_id"), None)
    return dict(life=life, text=last_msg, last_ts=last_ts, asking=bool(pend))


def codex_state(info: dict, now: float, dec: dict | None = None, why: dict | None = None):
    why = why if why is not None else {}
    last = info["last_ts"] or now
    if info["asking"]:
        why["path"] = "asking"
        return "waiting", last
    if info["life"] in ("complete", "aborted"):
        why["path"] = "ended"
        return dec_state(dec, info["text"]), last
    return ("working", last) if now - last < CODEX_STALE else ("stale", last)


def _codex_meta(path):
    m = _CX_META.get(path)
    if m is None:
        try:
            with open(path, "rb") as f:
                line = f.readline(3_000_000)
            pl = (json.loads(line).get("payload") or {})
        except (OSError, ValueError):
            pl = {}
        src = pl.get("source")
        sub = pl.get("thread_source") == "subagent" or (isinstance(src, dict) and "subagent" in src)
        m = dict(id=pl.get("id"), cwd=pl.get("cwd") or "", originator=pl.get("originator") or "",
                 source=src if isinstance(src, str) else "", sub=sub)
        _CX_META[path] = m
    return m


_CXI = {"sig": None, "map": {}, "upd": {}}


def _codex_titles(p, recent=None):
    """{thread id: name} from the tail of session_index.jsonl; with `recent` (epoch): ids updated since then."""
    try:
        st = os.stat(p["codex_index"])
    except OSError:
        return {}
    sig = (str(p["codex_index"]), st.st_mtime, st.st_size)
    if _CXI["sig"] != sig:
        mp, up = {}, {}
        for o in _tail_objs(p["codex_index"], 256 * 1024):
            if o.get("id"):
                mp[o["id"]] = o.get("thread_name") or mp.get(o["id"]) or ""
                up[o["id"]] = _ts(o.get("updated_at")) or 0
        _CXI.update(sig=sig, map=mp, upd=up)
    if recent is not None:
        return {k: v for k, v in _CXI["map"].items() if _CXI["upd"].get(k, 0) >= recent}
    return _CXI["map"]


# ------------------------------------------------------------------ the scanner
_LOCK = threading.RLock()
_SCAN = {"t": 0.0, "sessions": [], "root": None}
_APP_CACHE: dict = {}
_ST: dict = {"loaded": None, "slots": {}, "seen": {}, "labels": {}, "summary": None}


FULL_SCAN_S = 10.0
_HOT = {"root": None, "t": 0.0, "dirs": set()}


def _scan_claude(p, now, full=False) -> list:
    reg = {}
    for f in glob.glob(str(p["registry"] / "*.json")):
        d = _json(f)
        if d and d.get("sessionId") and _pid_alive(d.get("pid")) and d.get("kind", "interactive") == "interactive":
            reg[d["sessionId"]] = d
    # desktop app records: local_<uuid>.json (index by file name, parse only recent or registry-linked)
    idx = {}
    base = p["app"]
    for prof in glob.glob(str(base / "*" / "claude-code-sessions" / "*" / "*")):
        try:
            for e in os.scandir(prof):
                if e.name.startswith("local_") and e.name.endswith(".json"):
                    idx[e.name[:-5]] = (e.path, e.stat().st_mtime)
        except OSError:
            pass
    want = {k for k, (_, m) in idx.items() if now - m < WINDOW}
    want |= {d.get("hostSessionId") for d in reg.values() if d.get("hostSessionId") in idx}
    apps = {}
    todo = [(l, idx[l]) for l in want if _APP_CACHE.get(idx[l][0], (None,))[0] != idx[l][1]]

    def load_app(it):
        d = _json(it[1][0]) or {}
        return it[1][0], (it[1][1], {k: d.get(k) for k in ("sessionId", "cliSessionId", "lastAssistantCliSessionId",
                                                           "title", "isArchived", "lastActivityAt", "cwd")})
    for path, c in _pmap(load_app, todo):
        _APP_CACHE[path] = c
    for local in want:
        rec = _APP_CACHE[idx[local][0]][1]
        for cid in (rec.get("cliSessionId"), rec.get("lastAssistantCliSessionId")):
            if cid and (cid not in apps or (rec.get("lastActivityAt") or 0) > (apps[cid].get("lastActivityAt") or 0)):
                apps[cid] = rec
    cands = []
    # Listing every project folder costs real kernel time (hundreds of folders). Do that every FULL_SCAN_S; in
    # between, list only the folders that held a recent transcript last time. A session in a folder that was
    # quiet shows up at the next full pass.
    root = str(p["projects"])
    wall = time.time()
    if full or _HOT["root"] != root or wall - _HOT["t"] >= FULL_SCAN_S:
        try:
            dirs = [e.path for e in os.scandir(root) if e.is_dir()]
        except OSError:
            dirs = []
        _HOT.update(root=root, t=wall)
    else:
        dirs = sorted(_HOT["dirs"])
    hot = set()
    for d in dirs:
        try:
            ents = list(os.scandir(d))
        except OSError:
            continue
        for e in ents:
            if not e.name.endswith(".jsonl"):
                continue          # subagent transcripts live in <session>/subagents/: never scanned
            try:
                st = e.stat()
            except OSError:
                continue
            if now - st.st_mtime <= WINDOW:
                cands.append((e.path, e.name[:-6], st))
                hot.add(d)
    _HOT["dirs"] = hot

    def load_tail(c):
        return c[0], (c[2].st_mtime, c[2].st_size), parse_claude_tail(_tail_objs(c[0]))
    miss = [c for c in cands if (_CL_CACHE.get(c[0]) or (None,))[0] != (c[2].st_mtime, c[2].st_size)]
    for path, key, info in _pmap(load_tail, miss):
        _CL_CACHE[path] = (key, info)
    out = []
    for path, sid, st in cands:
        info = _CL_CACHE[path][1]
        rg, app = reg.get(sid), apps.get(sid)
        if (app or {}).get("isArchived"):
            continue
        if info["entry"] == "sdk-cli" and not rg and not app:
            continue          # headless one-shot run
        if not info["has_msgs"] and not rg:
            continue
        dec = decision_cached(sid, info["text"], info["ask_text"])
        why: dict = {}
        state, since = claude_state(info, rg, st.st_mtime, now, dec, why)
        kind_ = _kind_of(state, why, dec, info["text"], rg)
        host = (app or {}).get("sessionId") or (rg or {}).get("hostSessionId")
        nm = (rg or {}).get("name") if (rg or {}).get("nameSource") == "user" else None
        title = (app or {}).get("title") or nm or info["title"] or _clip(info["prompt"], 50) or sid[:8]
        act = max(st.st_mtime, info["last_ts"] or 0)
        if state == "working":
            act = max(act, _sub_mtime(path))             # a foreground subagent writes to <session>/subagents/
        out.append(dict(id=sid, kind="claude", title=_clip(title, 80), state=state, since=since, act=act,
                        wait_s=info.get("wait_s") or 0.0,
                        link=f"claude://claude.ai/epitaxy/{host}" if host else None,
                        app_title=(app or {}).get("title"),       # what the app titles the view (send check)
                        cwd=(app or {}).get("cwd") or (rg or {}).get("cwd") or "", path=path,
                        hint=_clip(info["prompt"], 300), pid=(rg or {}).get("pid"), mtime=st.st_mtime,
                        text=info["text"], ask_text=info["ask_text"], **_dec_fields(state, kind_, dec)))
    return out


def _sub_mtime(path) -> float:
    """Newest write under <session>/subagents/ (two levels: agent files and workflows/<run>/), 0 if none."""
    best = 0.0
    stack = [(Path(path[:-6]) / "subagents", 0)]
    while stack:
        d, depth = stack.pop()
        try:
            for e in os.scandir(d):
                if e.is_dir() and depth < 2:
                    stack.append((e.path, depth + 1))
                elif e.name.endswith(".jsonl"):
                    best = max(best, e.stat().st_mtime)
        except OSError:
            pass
    return best


def silence(s, now, after=None) -> tuple:
    """(silent_s, silent) for a session: seconds since its last transcript/rollout write while working, and whether
    that is past the threshold (after, or a pending foreground tool's timeout + 60 s, whichever is longer)."""
    if s["state"] != "working":
        return None, False
    q = max(0.0, now - (s.get("act") or now))
    lim = max(_SILENT["after"] if after is None else after, (s.get("wait_s") or 0) + 60)
    return int(q), q > lim


_CX_PATH: dict = {}


def _codex_path(p, tid):
    """Rollout path for a thread id. Rollouts live in the folder of the day the thread STARTED, which can be
    days ago for a thread still running; the id is a UUIDv7, so its first 48 bits give that day."""
    if tid in _CX_PATH:
        return _CX_PATH[tid]
    try:
        ms = int(tid.replace("-", "")[:12], 16) / 1000.0
    except ValueError:
        return None
    for d in (0, -86400, 86400):
        day = time.strftime("%Y/%m/%d", time.localtime(ms + d))
        g = glob.glob(str(p["codex"] / day / f"rollout-*-{tid}.jsonl"))
        if g:
            _CX_PATH[tid] = g[0]
            return g[0]
    return None


def _codex_candidates(p, now) -> list:
    paths_ = set()
    for i in range(3):                                     # files started in the last three days
        for fmt in (time.localtime, time.gmtime):
            day = time.strftime("%Y/%m/%d", fmt(now - i * 86400))
            try:
                paths_.update(e.path for e in os.scandir(p["codex"] / day) if e.name.startswith("rollout-"))
            except OSError:
                pass
    ids = set()
    try:                                                   # threads the desktop app holds open (any age)
        ids |= {e.name[:-5] for e in os.scandir(p["codex"].parent / "thread-writer-locks") if e.name.endswith(".lock")}
    except OSError:
        pass
    ids |= set(_codex_titles(p, recent=now - WINDOW))
    for tid in ids:
        fp = _codex_path(p, tid)
        if fp:
            paths_.add(fp)
    return list(paths_)


def _scan_codex(p, now) -> list:
    out = []
    tmap = _codex_titles(p)
    for path in _codex_candidates(p, now):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if now - st.st_mtime > WINDOW:
            continue
        meta = _codex_meta(path)
        if meta["sub"] or not meta["id"]:
            continue
        if meta["originator"] in ("", "codex_exec", "launch_sol_steer") or meta["source"] == "exec":
            continue          # headless one-shot / steered exec runs
        key = (st.st_mtime, st.st_size)
        c = _CX_CACHE.get(path)
        if not c or c[0] != key:
            c = (key, parse_codex_tail(_tail_objs(path)))
            _CX_CACHE[path] = c
        info = c[1]
        dec = decision_cached(meta["id"], info["text"], "")
        why = {}
        state, since = codex_state(info, now, dec, why)
        kind_ = _kind_of(state, why, dec, info["text"], None)
        title = tmap.get(meta["id"]) or Path(meta["cwd"]).name or meta["id"][:8]
        out.append(dict(id=meta["id"], kind="codex", title=_clip(title, 80), state=state, since=since,
                        act=max(st.st_mtime, info["last_ts"] or 0), wait_s=0.0,
                        link=f"codex://threads/{meta['id']}", cwd=meta["cwd"], path=path,
                        hint="", pid=None, mtime=st.st_mtime, text=info["text"], ask_text="",
                        **_dec_fields(state, kind_, dec)))
    return out


def _kind_of(state, why, dec, text, reg) -> str | None:
    """Kind of a waiting session: the Decisions answer when it names a waiting kind, else the rules."""
    if state != "waiting":
        return None
    if dec and dec.get("state") in WAIT_KINDS and dec.get("conf", 1) >= DEC_MIN_CONF:
        return dec["state"]
    return rule_kind(state, asking=why.get("path") == "asking", reg_waiting=why.get("path") == "registry-waiting",
                     text=text)


def _dec_fields(state, kind, dec) -> dict:
    """Per-session fields for the pad and sessions-summary.json: wkind (the waiting kind; `kind` is claude|codex), has_recommendation, urgency, source."""
    use = bool(dec and dec.get("conf", 1) >= DEC_MIN_CONF)
    if use:
        return dict(wkind=kind, has_recommendation=bool(dec.get("rec", 0) >= 0.5), urgency=dec.get("urgency"),
                    kind_src="decisions")
    urg = {"waiting": 3.0, "working": 0.0, "finished": 0.0}.get(state)
    return dict(wkind=kind, has_recommendation=None, urgency=urg, kind_src="rules")


def _load_state(p):
    sp = Path(p["state"]) / "sessions.json"
    if _ST["loaded"] == str(sp):
        return
    d = _json(sp) or {}
    _ST.update(loaded=str(sp), slots={k: int(v) for k, v in (d.get("slots") or {}).items()},
               seen={k: float(v) for k, v in (d.get("seen") or {}).items()},
               labels=dict(d.get("labels") or {}), dirty=False, summary=None)


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _save_state(p):
    try:
        _write_json(Path(p["state"]) / "sessions.json",
                    {"slots": _ST["slots"], "seen": _ST["seen"], "labels": _ST["labels"]})
        _ST["dirty"] = False
    except OSError:
        pass


def on_pad(s, now) -> bool:
    if s["state"] in ("working", "waiting"):
        return True
    if s["state"] != "finished":
        return False
    seen = _ST["seen"].get(s["id"])
    return not (seen and seen >= s["since"]) and now - s["since"] < FINISHED_KEEP


def scan(force=False, now=None) -> list:
    """Sessions (dicts: id, kind, title, state, since, link, on_pad, ...), cached for TTL seconds."""
    with _LOCK:
        p = paths()
        t = time.time()
        if not force and _SCAN["root"] == str(p["state"]) and t - _SCAN["t"] < TTL:
            return _SCAN["sessions"]
        now = now if now is not None else t
        _load_state(p)
        ss = _scan_claude(p, now, full=force) + _scan_codex(p, now)
        for s in ss:
            if s["state"] == "finished" and now - s["since"] >= FINISHED_KEEP:
                s["state"] = "stale"
            s["on_pad"] = on_pad(s, now)
            s["silent_s"], s["silent"] = silence(s, now)
        ss.sort(key=lambda s: (PRIO[s["state"]], s["since"]))
        # forget bookkeeping for sessions that left the window
        ids = {s["id"] for s in ss}
        for k in [k for k in _ST["seen"] if k not in ids]:
            del _ST["seen"][k]
            _ST["dirty"] = True
        _SCAN.update(t=t, sessions=ss, root=str(p["state"]))
        try:
            want_decisions(ss)                       # queues background reads only; never waits on one
        except Exception:                            # noqa: BLE001
            pass
        _write_summary(p, ss)
        if _ST.get("dirty"):
            _save_state(p)
        return ss


def _write_summary(p, ss):
    pad = [s for s in ss if s["on_pad"]]
    sm = {"working": sum(s["state"] == "working" for s in pad),
          "waiting": sum(s["state"] == "waiting" for s in pad),
          "finished": sum(s["state"] == "finished" for s in pad)}
    kinds = {k: sum(s.get("wkind") == k for s in pad) for k in WAIT_KINDS}
    # silent_s is rounded down to the minute so the file is rewritten at most once a minute per working session
    lst = [dict(id=s["id"], agent=s["kind"], title=s["title"], state=s["state"], kind=s.get("wkind"),
                has_recommendation=s.get("has_recommendation"), urgency=s.get("urgency"), kind_src=s.get("kind_src"),
                silent_s=None if s.get("silent_s") is None else s["silent_s"] // 60 * 60, silent=bool(s.get("silent")))
           for s in pad]
    silent = sum(bool(s.get("silent")) for s in pad)
    sig = (sm, kinds, lst)
    if sig != _ST.get("summary"):
        try:
            _write_json(Path(p["state"]) / "sessions-summary.json", dict(sm, total=sum(sm.values()), silent=silent, kinds=kinds,
                                                                          sessions=lst, updated=round(time.time(), 1)))
            _ST["summary"] = sig
        except OSError:
            pass


# ------------------------------------------------------------------ Decisions API (waiting kind, label clash)
# One classifier call per event, never on a timer: a session whose last message is new (hash) gets one read;
# the answer is cached in state/session-decisions.json. Everything runs in background threads; any failure
# (no node, no key, over the cap, bad reply) silently leaves the rule-based state in place.
DEC_TEXT = 2000
_DEC: dict = {"path": None, "e": {}, "usage": {"day": "", "tokens": 0, "calls": []}}
_DLOCK = threading.RLock()
_DQ: list = []
_DPEND: set = set()
_DFAIL: dict = {}
_DWORKER = {"t": None}

_STATE_QUESTIONS = [
    {"type": "choice", "name": "state",
     "instructions": "What does this last message ask of the human user? Pick the single best kind.",
     "choices": [
         {"value": "finished", "description": "The agent reports what it did, or answers a question. Nothing is asked of the user. "
                                              "Closing offers such as 'tell me if you want X' or things the user may do later on his own time "
                                              "do not count as asking."},
         {"value": "question", "description": "The agent asks the user to choose between options, supply information or answer "
                                              "something before the task can continue or finish: 'Which one?', 'Tell me what you want "
                                              "changed', or a pending multiple-choice prompt."},
         {"value": "approval", "description": "The agent wants a go-ahead for one specific action it proposes (install, delete, run, "
                                              "send, merge, spend, push): 'Want me to install it?', 'OK to proceed?'. Yes or no answers it."},
         {"value": "blocked", "description": "The agent cannot continue: an error, expired login, usage or session limit, missing access "
                                             "or file, or a step only the user can do on the PC (run a command, sign in, approve a prompt) "
                                             "before work can resume."},
         {"value": "other", "description": "None of the above fit, or the message is empty or unreadable."}]},
    {"type": "predicate", "name": "has_recommendation",
     "instructions": "Does the agent say which option it recommends or would pick (for example 'I recommend B', 'my pick is the first', "
                     "'I'd go with ...')? Answer no when it lists options without a preference or asks nothing."},
    {"type": "score", "name": "urgency",
     "instructions": "How urgently does this message need the user's attention?",
     "levels": [
         {"label": "nothing waits", "description": "Work is done or the message is informational; nothing waits on the user and he can ignore it."},
         {"label": "optional", "description": "An optional offer or a later chore; the task itself is complete and nothing is stopped."},
         {"label": "needed soon", "description": "A reply or action is needed to finish or continue, but some work can proceed without it."},
         {"label": "stopped", "description": "Work is stopped until the user answers or acts."}]},
]


def _dec_path() -> Path:
    return Path(paths()["state"]) / "session-decisions.json"


def _dec_load():
    sp = str(_dec_path())
    with _DLOCK:
        if _DEC["path"] == sp:
            return
        d = _json(sp) or {}
        u = d.get("usage") or {}
        _DEC.update(path=sp, e=dict(d.get("entries") or {}),
                    usage={"day": str(u.get("day", "")), "tokens": int(u.get("tokens", 0) or 0),
                           "calls": [float(x) for x in u.get("calls") or []]})


def _dec_save():
    with _DLOCK:
        e = _DEC["e"]
        if len(e) > 300:
            for k in sorted(e, key=lambda k: e[k].get("t", 0))[: len(e) - 300]:
                del e[k]
        try:
            _write_json(Path(_DEC["path"]), {"v": 1, "entries": e, "usage": _DEC["usage"]})
        except OSError:
            pass


def _dec_hash(text, ask) -> str:
    return hashlib.sha1(f"{(text or '')[-DEC_TEXT:]}\x00{ask or ''}".encode("utf-8", "replace")).hexdigest()[:16]


def decision_cached(sid, text, ask):
    """The cached Decisions answer for this session's CURRENT last message, or None. Cheap: no I/O after the first load."""
    if not (text or ask):
        return None
    _dec_load()
    e = _DEC["e"].get(sid)
    return e["d"] if e and e.get("h") == _dec_hash(text, ask) else None


def _decide_argv() -> list | None:
    """Command that runs one Decisions request (JSON on stdin, response JSON on stdout). None when it can't run."""
    if os.environ.get("M18_SESSIONS_DECISIONS", "").lower() in ("off", "0", "no"):
        return None
    stub = os.environ.get("M18_SESSIONS_DECISIONS_CMD")
    if stub:
        return [stub, "-", "--max-input-tokens", str(DEC_CALL_TOKENS)]
    node = shutil.which("node")
    js = Path(os.environ.get("M18_DECISIONS_JS") or Path.home() / ".agents" / "skills" / "decisions-api" / "scripts" / "decide.mjs")
    if not node or not js.exists():
        return None
    return [node, str(js), "-", "--max-input-tokens", str(DEC_CALL_TOKENS)]     # the script reads the key itself


def _dec_budget(est) -> bool:
    u = _DEC["usage"]
    day = time.strftime("%Y-%m-%d")
    if u["day"] != day:
        u["day"], u["tokens"] = day, 0
    now = time.time()
    u["calls"] = [t for t in u["calls"] if now - t < 3600]
    return u["tokens"] + est <= DEC_DAILY_TOKENS and len(u["calls"]) < DEC_PER_HOUR


def _decide(req: dict, timeout=25) -> dict:
    """One Decisions call through decide.mjs. Raises on anything wrong (callers fall back to the rules)."""
    argv = _decide_argv()
    if not argv:
        raise RuntimeError("decisions unavailable")
    body = json.dumps(req)
    est = len(body) // 3                                       # generous tokens estimate
    with _DLOCK:
        _dec_load()
        if est > DEC_CALL_TOKENS or not _dec_budget(est):
            raise RuntimeError("decisions over cap")
        _DEC["usage"]["calls"].append(time.time())
    cwd = os.environ.get("M18_DECISIONS_CWD") or str(Path.home() / ".agents" / "skills" / "m18-macropad")   # the grant is per project
    r = subprocess.run(argv, input=body, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=timeout,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    try:
        resp = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f"decide exit {r.returncode}: no JSON") from None
    if not isinstance(resp, dict) or not isinstance(resp.get("answers"), list):
        raise RuntimeError("decide: no answers")
    used = int((resp.get("usage") or {}).get("input_tokens") or est)
    with _DLOCK:
        _DEC["usage"]["tokens"] += used
        _dec_save()
    return resp


def _answers(resp) -> dict:
    return {a.get("name"): a for a in resp.get("answers", []) if isinstance(a, dict)}


def build_state_request(text, ask) -> dict:
    t = (text or "").strip()
    cut = len(t) > DEC_TEXT
    t = t[-DEC_TEXT:]
    msg = ("A coding agent (Claude Code or Codex) stopped working on a session and sent its last message to the user, "
           "the human user.\n"
           f"Last assistant message{' (start cut off)' if cut else ''}:\n<<<\n{t or '(no text)'}\n>>>\n"
           f"Pending prompt shown to the user, with its options: {ask or '(none)'}")
    return {"input": [{"role": "user", "content": [{"type": "input_text", "text": msg}]}],
            "prompt_cache_options": {"mode": "explicit"},            # inputs differ every call: caching off (no breakpoints)
            "questions": _STATE_QUESTIONS}


def parse_state_answers(resp) -> dict:
    ans = _answers(resp)
    st = ans.get("state")
    if not st or st.get("type") != "choice" or st.get("choice") not in KINDS:
        raise ValueError("no kind in the answer")
    probs = {x.get("value"): float(x.get("probability", 0)) for x in st.get("probabilities") or [] if isinstance(x, dict)}
    rec, urg = ans.get("has_recommendation") or {}, ans.get("urgency") or {}
    return dict(state=st["choice"], conf=round(probs.get(st["choice"], float(st.get("confidence") or 0)), 3),
                rec=round(float(rec.get("probability", 0) or 0), 3),
                urgency=round(float(urg["score"]), 2) if "score" in urg else None,
                probs={k: round(v, 3) for k, v in probs.items()})


def read_state(text, ask) -> dict:
    return parse_state_answers(_decide(build_state_request(text, ask)))


def want_decisions(sessions):
    """Queue one Decisions read for each pad session in a waiting/finished state whose last message has none yet."""
    todo = []
    now = time.time()
    for s in sessions:
        if not s.get("on_pad") or s["state"] == "working" or not (s.get("text") or s.get("ask_text")):
            continue
        if s["id"] in _DPEND or decision_cached(s["id"], s.get("text"), s.get("ask_text")) is not None:
            continue
        n, nxt = _DFAIL.get(s["id"], (0, 0))
        if n >= 3 or now < nxt:
            continue
        todo.append(s)
    if not todo or not _decide_argv():
        return
    with _DLOCK:
        for s in todo[:8]:
            _DQ.append((s["id"], s.get("text") or "", s.get("ask_text") or ""))
            _DPEND.add(s["id"])
        if _DWORKER["t"] is None or not _DWORKER["t"].is_alive():
            _DWORKER["t"] = threading.Thread(target=_dec_loop, name="sessions-decisions", daemon=True)
            _DWORKER["t"].start()


def _dec_loop():
    idle = 0
    while idle < 30:
        with _DLOCK:
            job = _DQ.pop(0) if _DQ else None
        if job is None:
            time.sleep(1)
            idle += 1
            continue
        idle = 0
        sid, text, ask = job
        try:
            d = read_state(text, ask)
            with _DLOCK:
                _DEC["e"][sid] = {"h": _dec_hash(text, ask), "d": d, "t": round(time.time())}
                _dec_save()
            _DFAIL.pop(sid, None)
            _SCAN["t"] = 0.0                                   # the next live tick rescans: the key changes at once
        except Exception:                                      # noqa: BLE001 - never escapes the thread
            n = _DFAIL.get(sid, (0, 0))[0] + 1
            _DFAIL[sid] = (n, time.time() + 120 * 2 ** min(n, 4))
        finally:
            _DPEND.discard(sid)
    _DWORKER["t"] = None


# --- label clash check: two predicates after Haiku names a session
def build_clash_request(label, title, first, peers) -> dict:
    """peers: [(title, label)] of the other sessions on the pad."""
    lines = "\n".join(f"- {_clip(t, 70)} => {l or '(no label yet)'}" for t, l in peers[:12]) or "(none)"
    msg = ("A macro pad shows one key per running AI coding session. Each key shows a short label. "
           "Judge the candidate label for ONE session.\n"
           f"Candidate label: {label}\n"
           f"This session: {_clip(f'{title}. First request: {first}', 500)}\n"
           f"Other sessions on the pad (title => label):\n{lines}")
    return {"input": [{"role": "user", "content": [{"type": "input_text", "text": msg}]}],
            "prompt_cache_options": {"mode": "explicit"},
            "questions": [
                {"type": "predicate", "name": "distinct",
                 "instructions": "Would this label let the user tell this session apart from the other listed sessions at a glance? "
                                 "Answer no if the label is generic (Chat, Work, Misc), repeats or nearly repeats another session's "
                                 "label, or would fit another listed session as well as this one."},
                {"type": "predicate", "name": "on_topic",
                 "instructions": "Does this label name what this session is actually about, going by its title and first request? "
                                 "Answer no if it names something unrelated to them, or is generic."}]}


def clash_scores(label, title, first, peers):
    """(distinct, on_topic) probabilities, or None when Decisions can't answer (the label is then accepted)."""
    try:
        a = _answers(_decide(build_clash_request(label, title, first, peers)))
        return float(a["distinct"]["probability"]), float(a["on_topic"]["probability"])
    except Exception:                                          # noqa: BLE001
        return None


def clash_reason(label, title, first, peers) -> str | None:
    sc = clash_scores(label, title, first, peers)
    if sc is None:
        return None
    d, t = sc
    if d < CLASH_DISTINCT:
        return (f"label {label!r} would not let the user tell this session apart from the other sessions listed "
                f"(it is generic or too close to another label); name what is specific to this session")
    if t < CLASH_TOPIC:
        return f"label {label!r} does not name this session's actual topic; use words from its title or first request"
    return None


# ------------------------------------------------------------------ stable slots
def snapshot(nslots: int, force=False) -> dict:
    """Assign pad sessions to slots 0..nslots-1. -> {"slots": {i: session}, "overflow": [sessions],
    "pad": [sessions in pad order]}. A session keeps its slot while on the pad; a new one takes the
    lowest free slot; with more sessions than slots the last slot is reserved for "+N"."""
    nslots = max(1, int(nslots))
    with _LOCK:
        ss = scan(force)
        pad = [s for s in ss if s["on_pad"]]
        slots = _ST["slots"]
        ids = {s["id"] for s in pad}
        before = dict(slots)
        for sid in [k for k in slots if k not in ids]:
            del slots[sid]
        cap = nslots - 1 if len(pad) > nslots else nslots
        for sid in [k for k, v in slots.items() if v >= cap]:
            del slots[sid]
        used = set(slots.values())
        for s in pad:
            if s["id"] in slots:
                continue
            free = next((i for i in range(cap) if i not in used), None)
            if free is None:
                break
            slots[s["id"]] = free
            used.add(free)
        if slots != before:
            _ST["dirty"] = True
            _save_state(paths())
        by_id = {s["id"]: s for s in pad}
        placed = {v: by_id[k] for k, v in slots.items() if k in by_id}
        over = [s for s in pad if s["id"] not in slots]
        return dict(slots=placed, overflow=over, pad=pad, cap=cap, nslots=nslots)


def mark_seen(sid, now=None):
    with _LOCK:
        _ST["seen"][sid] = (now or time.time()) + 0.001
        _ST["dirty"] = True
        _save_state(paths())
        _SCAN["t"] = 0.0           # next live tick rescans: the finished key clears at once


# ------------------------------------------------------------------ labels: words, fallback, validation
_STOP = set("""the a an and or of to for in on with from by at as is are be this that it its into your my our
fix add make build create update use using new session task work about check run find get set show
please can could would should will skill skills code claude codex chat help need want""".split())


def words(s) -> list:
    return [w for w in re.findall(r"[a-z0-9]+", str(s or "").lower()) if len(w) >= 3 and w not in _STOP]


def _stem(w):
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def shared_words(title, others) -> set:
    mine = {_stem(w) for w in words(title)}
    theirs = set()
    for t in others:
        theirs |= {_stem(w) for w in words(t)}
    return mine & theirs


def main_word(label) -> str:
    ws = re.findall(r"[a-z0-9]+", str(label).lower())
    return _stem(max(ws, key=len)) if ws else ""


def validate_label(label, banned, existing) -> str | None:
    """None when the label is fine, else the reason it was rejected."""
    if not isinstance(label, str) or not label.strip():
        return "label is empty"
    lab = label.strip()
    if len(lab) > 10:
        return f"label {lab!r} is longer than 10 characters"
    if not re.fullmatch(r"[A-Za-z0-9 .+-]+", lab):
        return "label may only use letters, digits and spaces"
    toks = {_stem(w) for w in re.findall(r"[a-z0-9]+", lab.lower())}
    hit = toks & {_stem(b) for b in banned}
    if hit:
        return f"label uses the shared word {sorted(hit)[0]!r}, which other sessions also have"
    ex = {e.lower() for e in existing}
    if lab.lower() in ex:
        return f"label {lab!r} is already used by another session"
    mw = main_word(lab)
    if mw and any(main_word(e) == mw for e in existing):
        return f"label's main word {mw!r} is already the main word of another session's label"
    return None


def fallback_label(sid, title, other_titles, existing) -> str:
    ws = [w for w in re.findall(r"[a-z0-9]+", str(title or "").lower()) if w not in _STOP and len(w) >= 2]
    shared = shared_words(title, other_titles)
    cands = sorted(dict.fromkeys(ws), key=lambda w: (_stem(w) in shared, -min(len(w), 10), ws.index(w)))
    ex = {e.lower() for e in existing}
    for w in cands:
        lab = w[:10].upper()
        if lab.lower() not in ex and main_word(lab) not in {main_word(e) for e in existing}:
            return lab
    base = (cands[0] if cands else "CHAT")[:7].upper()
    return f"{base}{hashlib.sha1(sid.encode()).hexdigest()[:2].upper()}"[:10]


_BRIGHT = "ROYLCBPTEN"


def fallback_grid(sid) -> list:
    """Deterministic 16x16 symmetric sprite from the session id: colour pair + shape both depend on it."""
    h = hashlib.sha256(sid.encode()).digest()
    body, accent = _BRIGHT[h[0] % 10], _BRIGHT[(h[0] // 10 + h[1] % 9 + 1 + h[0] % 10) % 10]
    if accent == body:
        accent = "W"
    cells = [[False] * 8 for _ in range(12)]
    bit = 16
    for y in range(12):
        for x in range(8):
            v = (h[bit // 8 % 32] >> (bit % 8)) & 1
            bit += 1
            edge = x == 0 or y in (0, 11)
            cells[y][x] = bool(v) if not edge else bool(v and (h[(bit + 7) % 32] & 3))
    for y in range(12):
        cells[y][7] = True if 2 <= y <= 9 else cells[y][7]
        if not any(cells[y]):
            cells[y][7] = h[(y * 3) % 32] & 1 == 0
    grid = [["."] * 16 for _ in range(16)]
    for y in range(12):
        for x in range(8):
            if cells[y][x]:
                tone = accent if (h[(x + y * 8) % 32 + 0] % 5 == 0) else body
                grid[y + 2][4 + x] = grid[y + 2][11 - x] = tone
    out = [r[:] for r in grid]
    for y in range(16):
        for x in range(16):
            if grid[y][x] == ".":
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < 16 and 0 <= xx < 16 and grid[yy][xx] != ".":
                        out[y][x] = "K"
                        break
    return ["".join(r) for r in out]


# ------------------------------------------------------------------ faces: cache + Haiku
PALETTE = {"K": "#161616", "W": "#f4f4f4", "G": "#b4b4b4", "S": "#7c7c7c", "D": "#454545", "R": "#cc3a30",
           "O": "#ee8c2c", "Y": "#f2d33a", "L": "#72cc40", "E": "#2f7d2c", "C": "#3fcbdc", "B": "#3a5bd2",
           "P": "#8e3fca", "N": "#7d4c2a", "T": "#dbb47e"}
LEGAL = set(".") | set(PALETTE)
_FACES: dict = {}
_FAILED: dict = {}
_CALLS: list = []
_Q: list = []
_PENDING: set = set()          # ids queued or being made: never queued twice
_WORKER = {"t": None}
_FQ_LOCK = threading.Lock()


def _faces_dir(p=None) -> Path:
    return Path((p or paths())["state"]) / "session-faces"


def get_face(sid):
    """{'label','icon'} from the cache (memory, then disk) or None."""
    f = _FACES.get(sid)
    if f is None:
        d = _json(_faces_dir() / f"{sid}.json")
        if d and validate_grid(d.get("icon")) is None and d.get("label"):
            f = _FACES[sid] = d
    return f


def validate_grid(g) -> str | None:
    if not isinstance(g, list) or len(g) != 16:
        return "icon must be a list of exactly 16 strings"
    for i, r in enumerate(g):
        if not isinstance(r, str) or len(r) != 16:
            return f"icon row {i + 1} must be exactly 16 characters"
        bad = set(r) - LEGAL
        if bad:
            return f"icon row {i + 1} uses illegal letters {sorted(bad)}"
    if sum(c != "." for r in g for c in r) < 30:
        return "icon is nearly empty; draw a filled item of at least 30 pixels"
    return None


_EXAMPLE = ["................", "....KKKKKK......", "...KYYYYYYK.....", "..KYYKKKKYYK....",
            "..KYK....KYK....", "..KYK....KYK....", "..KYYKKKKYYK....", "...KYYYYYYK.....",
            "....KKYYKK......", ".....KYYK.......", ".....KYYKKK.....", ".....KYYYYK.....",
            ".....KYYKKK.....", ".....KYYYYK.....", ".....KYYKKK.....", "......KKK......."]


def build_prompt(title, summary, banned, existing, other_titles, reason=None) -> str:
    s = ("You design one key icon for a macro pad. Each key stands for one running AI coding session.\n"
         f"Session title: {title}\n"
         f"What it is doing: {summary or '(no summary)'}\n"
         f"Titles of the other sessions on the pad: {'; '.join(other_titles[:12]) or '(none)'}\n"
         f"Labels already taken: {', '.join(existing[:20]) or '(none)'}\n"
         f"Words you must NOT use in the label (other sessions share them): {', '.join(sorted(banned)) or '(none)'}\n"
         "The label must name what makes THIS session different from the others, not what they have in common.\n\n"
         'Reply with ONLY a JSON object, no prose, no code fence: {"label": "...", "icon": [16 strings]}\n'
         "- label: at most 10 characters, letters digits and spaces only.\n"
         "- icon: 16 strings of exactly 16 characters: a 16x16 pixel-art inventory item (like a game item sprite) "
         "of one clear object that fits this session's subject, centered, 1-pixel outline, two or three tones per "
         "colour, light from the top left. Letters: . transparent, K near-black outline, W white, G light grey, "
         "S stone grey, D dark grey, R red, O orange, Y yellow, L lime, E dark green, C cyan, B blue, P purple, "
         "N brown, T tan.\n"
         "Format example only (a key; draw something that fits the session instead):\n"
         + json.dumps(_EXAMPLE))
    if reason:
        s += f"\n\nYour previous answer was rejected: {reason}. Fix exactly that and answer again."
    return s


def _token() -> str | None:
    """Read the OAuth token from Windows Credential Manager (generic credential). None if missing."""
    if os.name != "nt":
        return None
    target = os.environ.get("M18_SESSIONS_TOKEN_TARGET") or TOKEN_TARGET

    class CRED(ctypes.Structure):
        _fields_ = [("Flags", ctypes.c_ulong), ("Type", ctypes.c_ulong), ("TargetName", ctypes.c_wchar_p),
                    ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_ulonglong),
                    ("Size", ctypes.c_ulong), ("Blob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", ctypes.c_ulong), ("Count", ctypes.c_ulong), ("Attrs", ctypes.c_void_p),
                    ("Alias", ctypes.c_wchar_p), ("User", ctypes.c_wchar_p)]
    adv = ctypes.windll.advapi32
    pc = ctypes.POINTER(CRED)()
    adv.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.POINTER(CRED))]
    if not adv.CredReadW(target, 1, 0, ctypes.byref(pc)):
        return None
    try:
        raw = bytes(bytearray(pc.contents.Blob[: pc.contents.Size]))
    finally:
        adv.CredFree(pc)
    try:
        txt = raw.decode("utf-16-le") if len(raw) % 2 == 0 and b"\x00" in raw else raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    txt = txt.strip().strip("\x00")
    return txt or None


def _claude_exe() -> str | None:
    return os.environ.get("M18_SESSIONS_CLAUDE") or shutil.which("claude") or shutil.which("claude.exe")


def _call_cli(prompt, token, timeout=120) -> str:
    """One standalone Haiku call. The token goes only into the child's environment."""
    exe = _claude_exe()
    if not exe:
        raise RuntimeError("claude CLI not found")
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    cwd = _faces_dir() / "_cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-p", "--model", HAIKU_MODEL, "--safe-mode", "--strict-mcp-config", "--tools", "",
           "--no-session-persistence", "--output-format", "json"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", env=env,
                       cwd=str(cwd), timeout=timeout, creationflags=0x08000000 if os.name == "nt" else 0)
    out = (r.stdout or "").strip()
    try:
        obj = json.loads(out)
    except ValueError:
        raise RuntimeError(f"claude exit {r.returncode}: non-JSON output")
    if obj.get("is_error"):
        raise RuntimeError("claude reported an error")
    return str(obj.get("result") or "")


def parse_haiku(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    d = json.loads(m.group(0))
    return str(d.get("label", "")).strip(), d.get("icon")


def _rate_ok() -> bool:
    now = time.time()
    f = _faces_dir() / "_calls.json"
    calls = [t for t in (_json(f) or []) if now - t < 3600]
    return len(calls) < HAIKU_PER_HOUR


def _note_call():
    f = _faces_dir() / "_calls.json"
    now = time.time()
    calls = [t for t in (_json(f) or []) if now - t < 3600] + [now]
    try:
        _write_json(f, calls)
    except OSError:
        pass


def _first_user_text(s) -> str:
    """First real user message of the session (read from the file head, once, off the live path)."""
    try:
        with open(s["path"], "rb") as f:
            head = f.read(400_000).decode("utf-8", "replace").split("\n")[:-1]
    except OSError:
        return s.get("hint", "")
    for ln in head:
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if s["kind"] == "claude" and o.get("type") == "user" and not o.get("isMeta"):
            c = (o.get("message") or {}).get("content")
            txt = c if isinstance(c, str) else " ".join(b.get("text", "") for b in c or []
                                                         if isinstance(b, dict) and b.get("type") == "text")
            if txt.strip() and not txt.lstrip().startswith("<"):
                return _clip(txt, 300)
        if s["kind"] == "codex" and o.get("type") == "response_item":
            pl = o.get("payload") or {}
            if pl.get("type") == "message" and pl.get("role") == "user":
                txt = " ".join(b.get("text", "") for b in pl.get("content") or [] if isinstance(b, dict))
                if txt.strip() and not txt.lstrip().startswith("<"):
                    return _clip(txt, 300)
    return s.get("hint", "")


def _peer_labels(exclude_id) -> list:
    """[(title, label)] of the other live sessions, for the label clash check."""
    out = []
    for x in list(_SCAN["sessions"]):
        if x["id"] != exclude_id and x["state"] != "stale":
            f = get_face(x["id"])
            out.append((x["title"], (f or {}).get("label") or _ST["labels"].get(x["id"]) or ""))
    return out


def make_face(s, all_titles, existing, token) -> bool:
    """Ask Haiku for this session's face (one retry with the reason). True when a face was cached."""
    others = [t for t in all_titles if t != s["title"]]
    banned = shared_words(s["title"], others)
    summary = _first_user_text(s)
    reason = None
    for attempt in range(2):
        if not _rate_ok():
            return False
        _note_call()
        try:
            label, icon = parse_haiku(_call_cli(build_prompt(s["title"], summary, banned, existing, others, reason),
                                                token))
        except Exception as e:                               # noqa: BLE001 - one failure never escapes the thread
            reason = f"could not use the reply ({_clip(str(e), 80).replace(token, '')})"
            continue
        reason = validate_label(label, banned, existing) or validate_grid(icon)
        if reason is None:                                   # rules passed: ask Decisions whether it tells sessions apart
            reason = clash_reason(label.strip(), s["title"], summary, _peer_labels(s["id"]))
            if reason is not None and attempt == 1:
                _FAILED[s["id"]] = (3, time.time() + 3600)   # still bad after the retry: title-word fallback, stop asking
        if reason is None:
            face = {"id": s["id"], "label": label.strip(), "icon": icon, "title": s["title"],
                    "made": round(time.time()), "model": HAIKU_MODEL}
            try:
                _write_json(_faces_dir() / f"{s['id']}.json", face)
            except OSError:
                return False
            _FACES[s["id"]] = face
            return True
    return False


def _worker_loop():
    idle = 0
    while idle < 60:
        with _FQ_LOCK:
            job = _Q.pop(0) if _Q else None
        if job is None:
            time.sleep(1)
            idle += 1
            continue
        idle = 0
        s, titles = job
        lock = _faces_dir() / "_busy.lock"          # one call at a time even across plugin reloads
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            busy = lock.exists() and time.time() - lock.stat().st_mtime < 300
        except OSError:
            busy = True
        if busy:
            with _FQ_LOCK:
                _Q.insert(0, job)
            time.sleep(5)
            continue
        try:
            lock.write_text(str(os.getpid()))
            token = _token()
            if not (token and get_face(s["id"]) is None and make_face(s, titles, _existing_labels(s["id"]), token)
                    ) and get_face(s["id"]) is None:
                n = _FAILED.get(s["id"], (0, 0))[0] + 1
                _FAILED[s["id"]] = (n, time.time() + 600 * 2 ** min(n, 4))
        except Exception:                                    # noqa: BLE001
            _FAILED[s["id"]] = (9, time.time() + 3600)
        finally:
            _PENDING.discard(s["id"])
            try:
                lock.unlink()
            except OSError:
                pass
    _WORKER["t"] = None


_TOK = {"t": 0.0, "v": False}


def _has_token() -> bool:
    if time.time() - _TOK["t"] > 60:
        _TOK.update(t=time.time(), v=_token() is not None)
    return _TOK["v"]


def _existing_labels(exclude_id) -> list:
    out = [f["label"] for k, f in _FACES.items() if k != exclude_id]
    return out + [v for k, v in _ST["labels"].items() if k != exclude_id and v not in out]


def want_faces(sessions, pad_first):
    """Queue a Haiku face for pad sessions that have none yet. Never blocks; skips silently with no token."""
    if _claude_exe() is None:
        return
    titles = [s["title"] for s in sessions if s["state"] != "stale"]
    todo = []
    for s in pad_first:
        if s["id"] in _PENDING or get_face(s["id"]) is not None:
            continue
        n, nxt = _FAILED.get(s["id"], (0, 0))
        if n >= 3 or time.time() < nxt:
            continue
        todo.append(s)
    if not todo:
        return
    if not _has_token():                    # no token: fallback faces only, silently
        return
    with _FQ_LOCK:
        for s in todo[:5]:
            _Q.append((s, titles))
            _PENDING.add(s["id"])
        if _WORKER["t"] is None or not _WORKER["t"].is_alive():
            _WORKER["t"] = threading.Thread(target=_worker_loop, name="sessions-faces", daemon=True)
            _WORKER["t"].start()


def face_for(s, sessions) -> tuple:
    """(label, grid) for a session: cached Haiku face, else the code-made fallback."""
    f = get_face(s["id"])
    if f:
        return f["label"], f["icon"]
    cache = _ST["labels"]
    others = [x["title"] for x in sessions if x["id"] != s["id"] and x["state"] != "stale"]
    lab = cache.get(s["id"])
    if not lab:
        existing = [v for k, v in cache.items() if k != s["id"]] + [x["label"] for x in _FACES.values()]
        lab = cache[s["id"]] = fallback_label(s["id"], s["title"], others, existing)
        _ST["dirty"] = True
    return lab, fallback_grid(s["id"])


# ------------------------------------------------------------------ drawing (64x64 native, nearest-neighbour)
_F = {
    "A": ".###.|#...#|#...#|#...#|#####|#...#|#...#", "B": "####.|#...#|#...#|####.|#...#|#...#|####.",
    "C": ".###.|#...#|#....|#....|#....|#...#|.###.", "D": "####.|#...#|#...#|#...#|#...#|#...#|####.",
    "E": "#####|#....|#....|####.|#....|#....|#####", "F": "#####|#....|#....|####.|#....|#....|#....",
    "G": ".####|#....|#....|#..##|#...#|#...#|.###.", "H": "#...#|#...#|#...#|#####|#...#|#...#|#...#",
    "I": "###|.#.|.#.|.#.|.#.|.#.|###", "J": "....#|....#|....#|....#|....#|#...#|.###.",
    "K": "#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#", "L": "#....|#....|#....|#....|#....|#....|#####",
    "M": "#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#", "N": "#...#|##..#|#.#.#|#..##|#...#|#...#|#...#",
    "O": ".###.|#...#|#...#|#...#|#...#|#...#|.###.", "P": "####.|#...#|#...#|####.|#....|#....|#....",
    "Q": ".###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#", "R": "####.|#...#|#...#|####.|#.#..|#..#.|#...#",
    "S": ".####|#....|#....|.###.|....#|....#|####.", "T": "#####|..#..|..#..|..#..|..#..|..#..|..#..",
    "U": "#...#|#...#|#...#|#...#|#...#|#...#|.###.", "V": "#...#|#...#|#...#|#...#|.#.#.|.#.#.|..#..",
    "W": "#...#|#...#|#...#|#.#.#|#.#.#|##.##|#...#", "X": "#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#",
    "Y": "#...#|#...#|.#.#.|..#..|..#..|..#..|..#..", "Z": "#####|....#|...#.|..#..|.#...|#....|#####",
    "0": ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.", "1": "..#..|.##..|..#..|..#..|..#..|..#..|#####",
    "2": ".###.|#...#|....#|..##.|.#...|#....|#####", "3": ".###.|#...#|....#|..##.|....#|#...#|.###.",
    "4": "...##|..#.#|.#..#|#...#|#####|....#|....#", "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.", "7": "#####|....#|...#.|..#..|..#..|..#..|..#..",
    "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.", "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    "+": ".....|..#..|..#..|#####|..#..|..#..|.....", "-": "....|....|....|####|....|....|....",
    ".": ".|.|.|.|.|.|#", "/": "....#|...#.|...#.|..#..|.#...|.#...|#....",
}
_G = {c: v.split("|") for c, v in _F.items()}


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lerp(a, b, t):
    a, b = _rgb(a), _rgb(b)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def text_w(s, scale=1):
    w = 0
    for ch in s.upper():
        g = _G.get(ch)
        w += ((len(g[0]) if g else 3) + 1) * scale
    return max(0, w - scale)


def draw_text(d, x, y, s, col, scale=1, shadow=True):
    sh = tuple(c // 4 for c in col)
    for off, c in (((scale, sh),) if shadow else ()) + ((0, col),):
        cx = x
        for ch in s.upper():
            g = _G.get(ch)
            if not g:
                cx += 4 * scale
                continue
            for ry, row in enumerate(g):
                for rx, b in enumerate(row):
                    if b == "#":
                        px, py = cx + rx * scale + off, y + ry * scale + off
                        d.rectangle((px, py, px + scale - 1, py + scale - 1), fill=c)
            cx += (len(g[0]) + 1) * scale


STATE_COL = {"waiting": "#ffff55", "working": "#55ffff", "finished": "#55ff55", "silent": "#d8b8ff"}
_BADGE = {
    "finished": ["......#", ".....##", "#...##.", "##.##..", ".###...", "..#...."],
    "working": ["##.##.##", "##.##.##"],
    "waiting": ["##", "##", "##", "##", "..", "##"],
}
# waiting kinds: "?" asks a question, a check mark wants a go-ahead, "!" is blocked (needs the user before it can go on)
_KIND_BADGE = {
    "question": [".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."],
    "approval": ["......#", ".....##", "#...##.", "##.##..", ".###...", "..#...."],
    "blocked": ["##", "##", "##", "##", "..", "##"],
}
PULSE_FPS = {"blocked": 5}          # waiting pulses at 3 fps; a blocked key pulses a little faster


def _slot_colors(state, phase):
    """Interior colour and badge colour for a state; phase 0..1 only used by waiting."""
    if state == "waiting":
        return _lerp("#a88a1c", "#f6d03c", phase), _lerp("#7a5c08", "#e0a800", phase), "#161616"
    if state == "working":
        return "#27345f", "#2b86c8", "#ffffff"
    if state == "silent":              # working but quiet too long: dusk violet, never gold (waiting) or red (error)
        return "#3e3456", "#b9a3ea", "#161616"
    return "#8b8b8b", "#2f9e44", "#ffffff"


def _slot(state, phase=0.0):
    img = Image.new("RGB", (64, 64), _slot_colors(state, phase)[0])
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 63, 1), fill="#373737")
    d.rectangle((0, 0, 1, 63), fill="#373737")
    d.rectangle((0, 62, 63, 63), fill="#ffffff")
    d.rectangle((62, 0, 63, 63), fill="#ffffff")
    d.point((0, 63), fill="#8b8b8b")
    d.point((63, 0), fill="#8b8b8b")
    return img, d


def _tooltip(d, label, state):
    label = label.upper()
    while text_w(label) > 58 and len(label) > 1:
        label = label[:-1]
    w = text_w(label) + 6
    x0 = (64 - w) // 2
    d.rectangle((x0, 52, x0 + w - 1, 62), fill="#100010")
    d.rectangle((x0, 52, x0 + w - 1, 62), outline="#3a00a8")
    for cx, cy in ((x0, 52), (x0 + w - 1, 52), (x0, 62), (x0 + w - 1, 62)):
        d.point((cx, cy), fill="#100010")                 # notched corners
    draw_text(d, x0 + 3, 54, label, _rgb(STATE_COL.get(state, "#ffffff")))


def _badge(d, state, phase, kind=None):
    _, fill, ink = _slot_colors(state, phase)
    x0, y0 = 49, 3
    d.rectangle((x0, y0, x0 + 10, y0 + 10), fill="#161616")
    d.rectangle((x0 + 1, y0 + 1, x0 + 9, y0 + 9), fill=fill)
    kb = state == "waiting" and kind in _KIND_BADGE
    g = _KIND_BADGE[kind] if kb else _BADGE[state]
    gw = len(g[0])
    ox, oy = x0 + 1 + (9 - gw) // 2, y0 + 1 + (9 - len(g)) // 2 + (1 if state == "waiting" and not kb else 0)
    for ry, row in enumerate(g):
        for rx, b in enumerate(row):
            if b == "#":
                d.point((ox + rx, oy + ry), fill=ink)


def age_text(secs) -> str:
    """Silence age for the badge: 14M under an hour, then 1H, 2H ... (9H+ caps at 9H)."""
    m = int(secs // 60)
    return f"{m}M" if m < 60 else f"{min(m // 60, 9)}H"


def _age_badge(d, txt):
    """A lavender plate with the silence age in the top-right corner (replaces the state badge)."""
    _, fill, ink = _slot_colors("silent", 0)
    w = text_w(txt) + 4
    x0 = 61 - w
    d.rectangle((x0 - 1, 2, 61, 14), fill="#161616")
    d.rectangle((x0, 3, 60, 13), fill=fill)
    draw_text(d, x0 + 2, 5, txt, _rgb(ink), shadow=False)


def _paint_grid(img, grid, dim=False):
    px = img.load()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            c = _rgb(PALETTE[ch])
            if dim:
                c = tuple(int(v * (0.55 if dim == "silent" else 0.78)) for v in c)
            for dy in range(3):
                for dx in range(3):
                    px[8 + x * 3 + dx, 3 + y * 3 + dy] = c


def render_session(label, grid, state, phase=0.0, themed=False, kind=None, quiet=None):
    """`quiet` (seconds) with state "working" draws the silent look: dusk violet slot, dimmer item, age plate."""
    if state == "working" and quiet is not None:
        img, d = _slot("silent", phase)
        _paint_grid(img, grid, dim="silent")
        d = ImageDraw.Draw(img)
        _age_badge(d, age_text(quiet))
        _tooltip(d, label, "silent")
        return _out(img, themed)
    img, d = _slot(state, phase)
    _paint_grid(img, grid, dim=(state == "working"))
    d = ImageDraw.Draw(img)
    _badge(d, state, phase, kind)
    _tooltip(d, label, state)
    return _out(img, themed)


def render_more(n, state, phase=0.0, themed=False):
    img, d = _slot(state, phase)
    txt = f"+{min(n, 99)}"
    sc = 3 if len(txt) <= 3 else 2
    draw_text(d, (64 - text_w(txt, sc)) // 2, 14, txt, _rgb("#ffffff"), scale=sc)
    _tooltip(d, "MORE", state if state in STATE_COL else "finished")
    return _out(img, themed)


def render_misc(big, label, themed=False, state="finished"):
    img, d = _slot(state)
    sc = 3 if len(big) <= 3 else 2
    draw_text(d, (64 - text_w(big, sc)) // 2, 14, big, _rgb("#ffffff"), scale=sc)
    _tooltip(d, label, "finished")
    return _out(img, themed)


def _out(img, themed):
    """Themed pads composite RGBA at 3x (daemon resizes to 192 then box-downsamples: crisp);
    a plain pad takes the 64 px RGB image as is."""
    if themed:
        return img.resize((192, 192), Image.NEAREST).convert("RGBA")
    return img


_IMGS: dict = {}


def _frames(key, maker):
    v = _IMGS.get(key)
    if v is None:
        if len(_IMGS) > 600:
            _IMGS.clear()
        v = _IMGS[key] = maker()
    return v


def _face_dict(maker_one, maker_phase, state, key, kind=None):
    if state == "waiting":
        frames = _frames(key + ("pulse",), lambda: [maker_phase(0.5 - 0.5 * math.cos(2 * math.pi * i / 6))
                                                    for i in range(6)])
        return {"frames": frames, "fps": PULSE_FPS.get(kind, 3)}
    return {"image": _frames(key, lambda: maker_one())}


# ------------------------------------------------------------------ live + press
def _themed(ctx) -> bool:
    try:
        return bool(ctx.theme)
    except Exception:                                        # noqa: BLE001
        return False


def _zone(zone):
    return list(zone) if zone else [6, 7, 9, 10]


def _quiet(s):
    return s.get("silent_s") if s.get("silent") else None


def slot(ctx, zone=None, slot=0, silent_min=None, **_):
    """Live face for slot `slot` of `zone`: a session key, the "+N" overflow key, or {} (theme shows).
    `silent_min` (default 11) sets when a working session counts as silent; the last value set wins pad-wide."""
    if silent_min is not None:
        try:
            _SILENT["after"] = max(60.0, float(silent_min) * 60)
        except (TypeError, ValueError):
            pass
    n = len(_zone(zone))
    snap = snapshot(n)
    ss = scan()
    th = _themed(ctx)
    try:
        want_faces(ss, snap["pad"])
    except Exception as e:                                   # noqa: BLE001
        ctx.log(f"sessions: faces: {e}")
    if snap["overflow"] and slot == n - 1:
        hid = snap["overflow"]
        st = "waiting" if any(s["state"] == "waiting" for s in hid) else \
             "working" if any(s["state"] == "working" for s in hid) else "finished"
        return _face_dict(lambda: render_more(len(hid), st, 0, th), lambda ph: render_more(len(hid), st, ph, th),
                          st, ("more", len(hid), st, th))
    s = snap["slots"].get(slot)
    if not s:
        return {}
    label, grid = face_for(s, ss)
    kd = s.get("wkind")
    qt = _quiet(s)
    k = ("s", label, "".join(grid), s["state"], th, kd, age_text(qt) if qt is not None else None)
    return _face_dict(lambda: render_session(label, grid, s["state"], 0, th, kd, qt),
                      lambda ph: render_session(label, grid, s["state"], ph, th, kd, qt), s["state"], k, kd)


def open_session(ctx, s) -> str:
    """Open a session's window. Returns what happened (also logged)."""
    mark_seen(s["id"]) if s["state"] == "finished" else None
    link = s.get("link")
    if os.environ.get("M18_SESSIONS_DRY"):
        try:
            with open(Path(paths()["state"]) / "opened.log", "a", encoding="utf-8") as f:
                f.write(f"{time.time():.0f} {s['id']} {link}\n")
        except OSError:
            pass
        return f"dry:{link}"
    if link:
        try:
            os.startfile(link)                                # noqa: S606 - protocol handler of the app
            return f"opened:{link}"
        except OSError as e:
            ctx.log(f"sessions: startfile {link}: {e}")
            return "error"
    if s.get("pid") and _focus_pid(int(s["pid"])):
        return "focused"
    ctx.log(f"sessions: {s['id']} has no link (CLI session); nothing to open")
    return "no-link"


def _focus_pid(pid) -> bool:
    """Best effort for sessions without a link: raise the window of the process or its parents."""
    if os.name != "nt":
        return False
    try:
        import psutil
        chain = [pid] + [p.pid for p in psutil.Process(pid).parents()]
    except Exception:                                        # noqa: BLE001
        return False
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _l):
        pid_ = ctypes.c_ulong()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid_))
        found.append((h, pid_))
        return True
    u.EnumWindows(cb, 0)
    for want in chain:
        for h, p in found:
            if p.value == want and u.IsWindowVisible(h) and u.GetWindowTextLengthW(h) > 0:
                u.ShowWindow(h, 9)
                return bool(u.SetForegroundWindow(h))
    return False


def press(ctx, zone=None, slot=0, **_):
    """Open the session on this slot (marks a finished one as seen); the "+N" key opens the list."""
    n = len(_zone(zone))
    snap = snapshot(n, force=True)
    if snap["overflow"] and slot == n - 1:
        return _modal(ctx, 0, n)
    s = snap["slots"].get(slot)
    if s:
        open_session(ctx, s)


NUDGE_TEXT = "still going?"


def put_clipboard(text) -> bool:
    """Text on the Windows clipboard through the daemon's m18lib.winclip; in dry mode only logged."""
    if os.environ.get("M18_SESSIONS_DRY"):
        try:
            with open(Path(paths()["state"]) / "clipboard.log", "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        return True
    try:
        from m18lib import winclip
        winclip.set_text(text)
        return True
    except Exception:                                        # noqa: BLE001
        return False


def nudge(ctx, zone=None, slot=0, text=NUDGE_TEXT, send=False, **_):
    """Hold action: "still going?" for the session on this slot.

    Default: the text on the clipboard and the session opened; the user pastes and sends.
    `send: true`: really send it (m18lib/sendtext.py): for a hardware press only, with the user's hands off
    the keyboard and mouse for 1.5 s, into the Claude app (paste into the composer after checking the shown
    session is this one) or the Codex app (prefilled link, then Enter), with the clipboard saved and
    restored. Any guard failing falls back to the default and flashes PASTE on the key."""
    n = len(_zone(zone))
    snap = snapshot(n, force=True)
    if snap["overflow"] and slot == n - 1:
        return _modal(ctx, 0, n)
    s = snap["slots"].get(slot)
    if not s:
        return None
    opened = False
    if send:
        from m18lib import sendtext
        r = sendtext.to_session(ctx, str(text), s.get("kind"), s.get("link"), s.get("app_title"),
                                opener=lambda url: open_session(ctx, dict(s, link=url)),
                                dry=bool(os.environ.get("M18_SESSIONS_DRY")))
        ctx.log(f"sessions: send {s['id'][:8]} sent={r['sent']} ({r['why']}) steps={r['steps']}")
        if r["sent"]:
            sendtext.flash_sent(ctx)
            return "sent"
        sendtext.flash_fallback(ctx, r["why"])
        opened = "opened" in r["steps"]
    ok = put_clipboard(str(text))
    r = "opened" if opened else open_session(ctx, s)
    ctx.log(f"sessions: nudge {s['id'][:8]} clipboard={'ok' if ok else 'failed'} {r}")
    return r


def _modal(ctx, page, nslots):
    th = _themed(ctx)
    snap = snapshot(nslots)
    pad = snap["pad"]
    ss = scan()
    per = 14
    pages = max(1, math.ceil(len(pad) / per))
    page = page % pages
    chunk = pad[page * per:(page + 1) * per]
    faces, byk = {}, {}
    for i, s in enumerate(chunk):
        label, grid = face_for(s, ss)
        faces[i + 1] = {"image": render_session(label, grid, s["state"], 0.5, th, s.get("wkind"), _quiet(s))}
        byk[i + 1] = s
    faces[15] = {"image": render_misc(f"{page + 1}/{pages}" if pages > 1 else "X", "NEXT" if pages > 1 else "BACK", th)}
    ctx.log(f"sessions: list modal page {page + 1}/{pages}, {len(pad)} sessions")

    def on_key(c2, key, down, t):
        if not down:
            return
        if key == 15:
            c2.end_modal()
            if pages > 1:
                _modal(c2, page + 1, nslots)
        elif key in byk:
            c2.end_modal()
            open_session(c2, byk[key])
    ctx.modal(faces, on_key=on_key, timeout=60)
