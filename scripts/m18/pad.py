#!/usr/bin/env python3
"""pad: the macro pad for AI agents. One line to show state on your own key, park a button and wait for
The user to press it, or flash for attention. Read references/agents.md (one minute) for the rules.

    pad status "TESTS" --tone ok          short word on your key (A-Z 0-9 + - . / %, 8 chars)
    pad progress 37/120 --ring            bar on your key (+ an arc on the LED ring)
    pad face --file me.json               your own 16x16 sprite (rows, palette, label, frames)
    pad flash                             attention: your key's border pulses until the user taps it
    pad button add oven --label OVEN      park a button on your key (it only sends you an event)
    pad wait oven --timeout 3600          block until the user presses it (run in the background)
    pad inbox                             presses since you last looked (for "stop" / "skip" between steps)
    pad ask "Keep render?" --action keep:KEEP --action redo:REDO --wait 600   a card on the card key
    pad whoami | pad clear

Identity: --as ID, else $M18_AGENT_ID, $CLAUDE_CODE_SESSION_ID, $CODEX_THREAD_ID. Output: one JSON object.
Exit codes: 0 ok, 2 bad input, 3 timed out, 4 refused (quota or rate), 5 gone (button removed or expired).
Nothing you post is ever run: buttons only send events back to you.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m18lib import agentpad as A  # noqa: E402

PORT = int(os.environ.get("M18_PORT") or 8718)


def out(obj, code=0):
    print(json.dumps(obj, separators=(",", ":")))
    sys.exit(code)


def api(method, path, body=None, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-M18": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {"error": str(e)}


def identity(a) -> tuple[str, str, str]:
    for src, v in (("--as", a.as_), ("M18_AGENT_ID", os.environ.get("M18_AGENT_ID")),
                   ("CLAUDE_CODE_SESSION_ID", os.environ.get("CLAUDE_CODE_SESSION_ID")),
                   ("CODEX_THREAD_ID", os.environ.get("CODEX_THREAD_ID"))):
        if v:
            kind = "claude" if src.startswith("CLAUDE") else "codex" if src.startswith("CODEX") else "agent"
            return A.check_id(v), kind, src
    raise A.PadError("no agent id: pass --as <id> (no CLAUDE_CODE_SESSION_ID or CODEX_THREAD_ID in the environment)")


class Pad:
    def __init__(self, a):
        self.a = a
        self.base = A.home()
        self.sid, self.kind, self.src = identity(a)
        self.path = A.doc_path(self.base, self.sid)

    def ledger(self) -> dict:
        return A.read_json(A.ledger_path(self.base)) or {}

    def plugin_up(self) -> bool:
        return time.time() - float(self.ledger().get("t") or 0) < 10

    def mutate(self, fn):
        now = time.time()
        with A.locked(self.base, self.sid):
            doc = A.read_json(self.path) or {}
            if doc.get("id") != self.sid:
                doc = {"v": 1, "id": self.sid, "created": now}
            r = fn(doc, now)
            doc["updated"], doc["agent"] = now, self.kind
            if self.a.title:
                doc["title"] = self.a.title[:80]
            if len(json.dumps(doc)) > A.QUOTA["doc_bytes"]:
                raise A.PadError(f"too big: your pad file would pass {A.QUOTA['doc_bytes']} bytes", 4)
            A.write_json(self.path, doc)
        return r

    def note(self, **kw):
        d = {"ok": True, "agent": self.sid}
        if not self.plugin_up():
            d["warning"] = "the pad's agents plugin is not running (daemon off, or no agents.* key in macros.yaml); saved, it shows once it runs"
        d.update(kw)
        return d


# ------------------------------------------------------------------ commands
def c_status(p, a):
    if a.clear:
        p.mutate(lambda d, now: d.pop("status", None))
        out(p.note(status=None))
    text = A.clean_text(" ".join(a.text), A.QUOTA["status_chars"], "status")
    ttl = A.clean_ttl(a.ttl)
    p.mutate(lambda d, now: d.__setitem__("status", {"text": text, "tone": a.tone, "until": now + ttl}))
    out(p.note(status=text, ttl=ttl))


def parse_fraction(s) -> float:
    s = str(s).strip()
    try:
        if s.endswith("%"):
            v = float(s[:-1]) / 100
        elif "/" in s:
            n, m = s.split("/", 1)
            v = float(n) / float(m)
        else:
            v = float(s)
            if v > 1:
                v /= 100
    except (ValueError, ZeroDivisionError):
        raise A.PadError("progress: 0.37, 37% or 37/100")
    if not (0 <= v <= 1.0 + 1e-9):
        raise A.PadError("progress: between 0 and 1 (or 0-100%)")
    return min(v, 1.0)


def c_progress(p, a):
    if a.clear:
        p.mutate(lambda d, now: d.pop("progress", None))
        out(p.note(progress=None))
    v = parse_fraction(a.value)
    ttl = A.clean_ttl(a.ttl)
    st = A.clean_text(a.status, A.QUOTA["status_chars"], "status") if a.status else None

    def f(d, now):
        d["progress"] = {"value": v, "ring": bool(a.ring), "until": now + ttl, "set": now}
        if st:
            d["status"] = {"text": st, "tone": a.tone, "until": now + ttl}
    p.mutate(f)
    used = A.ring_used(p.ledger(), p.sid, time.time())
    extra = {}
    if a.ring and used >= A.QUOTA["ring_s_per_hour"]:
        extra["ring_note"] = f"refused: you used the ring {int(used)} s this hour (cap {A.QUOTA['ring_s_per_hour']}); the bar on your key still shows"
    out(p.note(progress=round(v, 4), ring=bool(a.ring) and not extra, **extra))


def c_face(p, a):
    if a.clear:
        p.mutate(lambda d, now: d.pop("face", None))
        out(p.note(face=None))
    face = {}
    if a.file:
        txt = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
        try:
            face = json.loads(txt)
        except ValueError as e:
            raise A.PadError(f"face file: not JSON ({e})")
        if isinstance(face, list):
            face = {"rows": face}
    if a.rows:
        rows = a.rows[0].replace("/", "|").split("|") if len(a.rows) == 1 else a.rows
        face["rows"] = [r.strip() for r in rows if r.strip()]
    for kv in a.palette or []:
        k, _, v = kv.partition("=")
        face.setdefault("palette", {})[k.strip()] = v.strip()
    for k in ("label", "color", "fps"):
        if getattr(a, k) is not None:
            face[k] = getattr(a, k)
    clean = A.clean_face(face)
    stored = dict(clean)
    p.mutate(lambda d, now: d.__setitem__("face", stored))
    out(p.note(face={"frames": len(clean["frames"]), "label": clean.get("label"), "colors": len(clean["palette"])}))


def c_flash(p, a):
    now = time.time()
    why = A.flash_refusal(p.ledger(), p.sid, now)
    if why:
        out({"ok": False, "agent": p.sid, "refused": why}, 4)

    def f(d, now):
        n = int((d.get("flash") or {}).get("n") or 0) + 1
        d["flash"] = {"n": n, "at": now}
        return n
    n = p.mutate(f)
    if not p.plugin_up():
        out(p.note(flash="queued"))
    end = time.time() + 3
    while time.time() < end:
        r = (p.ledger().get("flash_results") or {}).get(p.sid)
        if r and r.get("n") == n:
            if r.get("ok"):
                out(p.note(flash="shown", attention_s=A.QUOTA["attention_s"]))
            out({"ok": False, "agent": p.sid, "refused": r.get("why")}, 4)
        time.sleep(0.1)
    out(p.note(flash="queued", warning="the plugin did not confirm within 3 s"))


def c_button(p, a):
    if a.op == "rm":
        bid = a.id

        def rm(d, now):
            n = len(d.get("buttons") or [])
            d["buttons"] = [b for b in d.get("buttons") or [] if b.get("id") != bid]
            return n != len(d["buttons"])
        out(p.note(removed=p.mutate(rm)), 0)
    now0 = time.time()
    b = A.clean_button({"id": a.id, "label": a.label, "color": a.color, "sticky": a.sticky, "ttl": a.ttl}, now0)

    def add(d, now):
        live = [x for x in d.get("buttons") or [] if isinstance(x, dict) and float(x.get("until") or 0) > now
                and x.get("id") != b["id"]]
        if len(live) >= A.QUOTA["buttons"]:
            raise A.PadError(f"quota: {A.QUOTA['buttons']} parked buttons per agent; `pad button rm <id>` one first", 4)
        old = next((x for x in d.get("buttons") or [] if x.get("id") == b["id"]), None)
        if old:
            b["created"] = old.get("created", b["created"])        # re-adding keeps its place
        d["buttons"] = live + [b]
    p.mutate(add)
    out(p.note(button=b["id"], label=b["label"], ttl=round(b["until"] - now0)))


def _take(p, match):
    """First inbox event that match(ev) accepts; it is removed. Events from a non-device press are dropped
    (with a note on stderr) unless --any-source: only the user's finger counts."""
    for path, ev in A.inbox_list(p.base, p.sid):
        if not match(ev):
            continue
        path.unlink(missing_ok=True)
        if ev.get("source") != "device" and not p.a.any_source:
            print(f"pad: ignored a {ev.get('type')} from source={ev.get('source')} (not a hardware press)", file=sys.stderr)
            continue
        return ev
    return None


def _wait_for(p, match, deadline, gone=None):
    """Block until an inbox event matches; the daemon's /events long-poll wakes us early."""
    seq = None
    while True:
        ev = _take(p, match)
        if ev:
            return ev
        if gone and gone():
            ev = _take(p, match)
            if ev:
                return ev
            out({"ok": False, "agent": p.sid, "gone": True, "why": "the button was removed or expired"}, 5)
        left = deadline - time.time()
        if left <= 0:
            return None
        try:
            if seq is None:
                seq = api("GET", "/events?since=0&timeout=0", timeout=5)[1].get("seq", 0)
                continue
            _, r = api("GET", f"/events?since={seq}&timeout={min(20.0, max(left, 0.1)):.1f}", timeout=30)
            seq = r.get("seq", seq) if not r.get("reset") else r.get("seq", 0)
        except (OSError, ValueError):
            time.sleep(min(1.0, max(left, 0.05)))


def c_wait(p, a):
    ids = set(a.ids)
    timeout = float(a.timeout)
    if not (0 < timeout <= A.QUOTA["ttl_max"]):
        raise A.PadError(f"--timeout: 1-{A.QUOTA['ttl_max']} s")
    doc = A.read_json(p.path) or {}
    mine = {b.get("id") for b in doc.get("buttons") or []}
    if ids and not ids & mine and not A.inbox_list(p.base, p.sid):
        out({"ok": False, "agent": p.sid, "gone": True, "why": f"no parked button {sorted(ids)}; `pad button add` first"}, 5)
    pid = str(os.getpid())
    deadline = time.time() + timeout
    p.mutate(lambda d, now: d.setdefault("waiters", {}).__setitem__(pid, deadline))

    def match(ev):
        if ev.get("type") == "agent_seen":
            return a.seen
        return ev.get("type") == "agent_button" and (not ids or ev.get("button") in ids)

    def gone():
        d = A.read_json(p.path) or {}
        now = time.time()
        have = {b.get("id") for b in d.get("buttons") or [] if float(b.get("until") or 0) > now}
        return bool(ids) and not ids & have and not a.seen
    try:
        ev = _wait_for(p, match, deadline, gone)
    finally:
        try:
            p.mutate(lambda d, now: (d.get("waiters") or {}).pop(pid, None))
        except A.PadError:
            pass
    if ev is None:
        out({"ok": False, "agent": p.sid, "timeout": True}, 3)
    out({"ok": True, "agent": p.sid, "event": ev})


def c_inbox(p, a):
    evs = []
    for path, ev in A.inbox_list(p.base, p.sid):
        if not a.keep:
            path.unlink(missing_ok=True)
        if ev.get("source") == "device" or a.any_source:
            evs.append(ev)
    hit = bool(a.check) and any(e.get("button") == a.check for e in evs)
    out({"ok": True, "agent": p.sid, "events": evs, **({"pressed": hit} if a.check else {})}, 10 if hit else 0)


def c_ask(p, a):
    acts = []
    for s in a.action or []:
        aid, _, lab = s.partition(":")
        acts.append({"id": aid.strip(), "label": (lab or aid).strip()})
    if not acts:
        raise A.PadError("ask: at least one --action id:LABEL")
    st, cards = api("GET", f"/cards?feed={A.feed_of(p.sid)}")
    if st != 200:
        raise A.PadError(f"daemon not reachable or refused (/cards {st}); a card needs the daemon", 2)
    if len(cards) >= A.QUOTA["cards"]:
        raise A.PadError(f"quota: {A.QUOTA['cards']} open cards per agent; answer or `pad clear` first", 4)
    ttl = A.clean_ttl(a.ttl)
    cid = f"ag:{p.sid}:{time.time_ns() // 1_000_000}"
    card = {"id": cid, "feed": A.feed_of(p.sid), "title": a.title, "lines": a.line or [], "actions": acts,
            "ttl_s": ttl, "priority": 1}
    if a.icon:
        card["icon"] = a.icon
    p.mutate(lambda d, now: d.__setitem__("asks", [x for x in d.get("asks") or [] if x.get("until", 0) > now]
                                          + [{"card": cid, "until": now + ttl}]))
    st, r = api("POST", "/cards", card)
    if st != 200:
        p.mutate(lambda d, now: d.__setitem__("asks", [x for x in d.get("asks") or [] if x.get("card") != cid]))
        raise A.PadError(f"card refused: {r.get('error')}", 2)
    if not a.wait:
        out(p.note(card=cid))
    ev = _wait_for(p, lambda e: e.get("type") == "card_action" and e.get("card") == cid,
                   time.time() + min(float(a.wait), ttl))
    p.mutate(lambda d, now: d.__setitem__("asks", [x for x in d.get("asks") or [] if x.get("card") != cid]))
    if ev is None:
        out({"ok": False, "agent": p.sid, "card": cid, "timeout": True, "note": "the card stays until its ttl"}, 3)
    out({"ok": True, "agent": p.sid, "card": cid, "action": ev.get("action"), "event": ev})


def c_clear(p, a):
    import shutil
    with A.locked(p.base, p.sid):
        p.path.unlink(missing_ok=True)
    shutil.rmtree(A.inbox_dir(p.base, p.sid), ignore_errors=True)
    removed = 0
    try:
        st, cards = api("GET", f"/cards?feed={A.feed_of(p.sid)}", timeout=5)
        for c in cards if st == 200 else []:
            if api("DELETE", f"/cards?id={urllib.request.quote(c['id'], safe='')}", timeout=5)[0] == 200:
                removed += 1
    except OSError:
        pass
    out({"ok": True, "agent": p.sid, "cleared": True, "cards_removed": removed})


def c_whoami(p, a):
    lg = p.ledger()
    shown = (lg.get("shown") or {}).get(p.sid)
    try:
        daemon = api("GET", "/status", timeout=3)[0] == 200
    except OSError:
        daemon = False
    doc = A.effective(A.read_json(p.path) or {}, time.time())
    where = None
    if shown and time.time() - float(lg.get("t") or 0) < 10:
        where = f"key {shown.get('key')} on page {shown.get('page')}" + (" (guest slot)" if shown.get("guest") else "")
    out({"ok": True, "agent": p.sid, "kind": p.kind, "id_from": p.src, "daemon": daemon,
         "plugin": p.plugin_up(), "shown": where or ("hidden (all keys taken: the user sees it in the deck)" if doc else None),
         "posted": {k: v for k, v in (doc or {}).items() if k in ("status", "progress", "buttons", "asks")} or None,
         "quota": {k: A.QUOTA[k] for k in ("buttons", "cards", "ring_s_per_hour", "flash_gap_s", "flash_per_hour")},
         "ring_used_s": round(A.ring_used(lg, p.sid, time.time()))})


# ------------------------------------------------------------------ main
def main(argv=None):
    global PORT
    ap = argparse.ArgumentParser(prog="pad", description=__doc__.split("\n\n")[0])
    ap.add_argument("--as", dest="as_", help="agent id (default: this session's id)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--title", help="a few words about you, shown when you are not a session on the pad")
    ap.add_argument("--any-source", action="store_true", help="accept presses made by software (tests only)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("status")
    s.add_argument("text", nargs="*")
    s.add_argument("--tone", choices=sorted(A.TONES), default="info")
    s.add_argument("--ttl", type=float)
    s.add_argument("--clear", action="store_true")
    s = sp.add_parser("progress")
    s.add_argument("value", nargs="?")
    s.add_argument("--ring", action="store_true")
    s.add_argument("--status")
    s.add_argument("--tone", choices=sorted(A.TONES), default="info")
    s.add_argument("--ttl", type=float)
    s.add_argument("--clear", action="store_true")
    s = sp.add_parser("face")
    s.add_argument("--file", help="JSON: {rows | frames, palette, label, color, fps} or a list of 16 rows; - = stdin")
    s.add_argument("--rows", nargs="+", help="16 rows, or one string with / between rows (| also works, but not through pad.cmd: cmd reads it as a pipe)")
    s.add_argument("--palette", nargs="*", help="1=#rrggbb ... (digits 1-6)")
    s.add_argument("--label")
    s.add_argument("--color", help="your colour (border, ring)")
    s.add_argument("--fps", type=float)
    s.add_argument("--clear", action="store_true")
    sp.add_parser("flash")
    s = sp.add_parser("button")
    s.add_argument("op", choices=["add", "rm"])
    s.add_argument("id")
    s.add_argument("--label")
    s.add_argument("--color")
    s.add_argument("--sticky", action="store_true", help="stays after a press (stop / skip buttons)")
    s.add_argument("--ttl", type=float)
    s = sp.add_parser("wait")
    s.add_argument("ids", nargs="*")
    s.add_argument("--timeout", default=3600)
    s.add_argument("--seen", action="store_true", help="also return when the user taps your flashing key")
    s = sp.add_parser("inbox")
    s.add_argument("--keep", action="store_true")
    s.add_argument("--check", help="exit 10 if this button was pressed")
    s = sp.add_parser("ask")
    s.add_argument("title")
    s.add_argument("--line", action="append")
    s.add_argument("--action", action="append", help="id:LABEL (up to 4)")
    s.add_argument("--icon")
    s.add_argument("--ttl", type=float)
    s.add_argument("--wait", type=float, default=0)
    sp.add_parser("clear")
    sp.add_parser("whoami")
    a = ap.parse_args(argv)
    if a.port:
        PORT = a.port
    if a.cmd == "status" and not a.clear and not a.text:
        ap.error("status: TEXT or --clear")
    if a.cmd == "progress" and not a.clear and a.value is None:
        ap.error("progress: VALUE or --clear")
    try:
        p = Pad(a)
        {"status": c_status, "progress": c_progress, "face": c_face, "flash": c_flash, "button": c_button,
         "wait": c_wait, "inbox": c_inbox, "ask": c_ask, "clear": c_clear, "whoami": c_whoami}[a.cmd](p, a)
    except A.PadError as e:
        out({"ok": False, "error": str(e)}, e.code)


if __name__ == "__main__":
    main()
