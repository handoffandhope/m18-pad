"""Native messaging host between the M18 Stale Tabs extension and the M18 daemon's control API.

Chrome starts this process (stdin/stdout, 4-byte little-endian length + UTF-8 JSON) only for the
extension id in the host manifest, so no port is opened. It talks to m18d at 127.0.0.1:8718 with
the `X-M18: 1` header and no Origin header.

Extension -> host:  {type: offer, req, card_id, ttl_s, tab: {id, title, url, age_days, favicon_b64}}
                    {type: post_card, card}   {type: delete_card, id}   {type: hello}
Host -> extension:  {reply_to: req, result: posted|deferred|failed, days?, kind?, error?}
                    {type: card_action, card, action}   {type: status, daemon, decisions}

Env: M18_API_PORT (default 8718), M18_TABS_DECISIONS=0 to switch sorting off.
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PORT = int(os.environ.get("M18_API_PORT", "8718"))
SKILL_DIR = os.environ.get("M18_SKILL_DIR", os.path.expanduser(r"~\.agents\skills\m18-macropad"))
DECIDE = os.environ.get("M18_DECIDE", os.path.expanduser("~/.agents/skills/decisions-api/scripts/decide.mjs"))
SESSIONS = os.environ.get("M18_SESSIONS_JSON", os.path.expanduser(r"~\tools\m18\state\sessions.json"))
MAX_IN = 8 * 1024 * 1024
RELATED_AT = 0.7      # probability above which a tab counts as related to work in progress
DEFER_DAYS = 7
LOG = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "m18-tabs", "host.log")

_out_lock = threading.Lock()


def log(*a):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 200_000:
            os.replace(LOG, LOG + ".old")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except OSError:
        pass


# ---- native messaging framing ---------------------------------------------------------------------
def read_exact(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def read_msg(stream):
    head = read_exact(stream, 4)
    if head is None:
        return None
    (n,) = struct.unpack("<I", head)
    if n > MAX_IN:
        raise ValueError(f"message too large: {n}")
    body = read_exact(stream, n)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def write_msg(stream, obj):
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    if len(data) > 1024 * 1024:           # Chrome refuses host -> browser messages over 1 MB
        raise ValueError("outgoing message over 1 MB")
    with _out_lock:
        stream.write(struct.pack("<I", len(data)) + data)
        stream.flush()


# ---- daemon API -----------------------------------------------------------------------------------
def api(method, path, body=None, timeout=10):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-M18": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return {"error": e.code, **json.load(e)}
        except Exception:
            return {"error": e.code}


# ---- decisions sorting (optional) -----------------------------------------------------------------
KINDS = [
    ("video", "A video or stream page (YouTube, Twitch, Vimeo, a talk or lecture recording)."),
    ("article", "A readable article, blog post, news story, paper or forum thread, read once."),
    ("docs", "Reference documentation, a wiki, an API reference or a how-to the user looks things up in."),
    ("shopping", "A product page, store, cart, listing or price comparison."),
    ("tool", "A web app or tool used to do work: dashboard, editor, console, mail, chat, admin page."),
    ("other", "Anything that fits none of the above."),
]
DEFAULT_ACTION = {"video": "later", "article": "later", "tool": "keep", "docs": "keep"}
_dec = {"checked": 0.0, "ok": False, "n": 0, "day": ""}


def decisions_enabled():
    if os.environ.get("M18_TABS_DECISIONS") == "0":
        return False
    if time.time() - _dec["checked"] > 600:
        _dec["checked"] = time.time()
        try:
            r = subprocess.run([node(), DECIDE, "--check"], cwd=SKILL_DIR, capture_output=True, text=True,
                               timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _dec["ok"] = r.returncode == 0 and r.stdout.startswith("Granted")
        except Exception as e:
            log("grant check failed", e)
            _dec["ok"] = False
    return _dec["ok"]


def node():
    return shutil.which("node") or r"C:\Program Files\nodejs\node.exe"


def session_labels():
    try:
        with open(SESSIONS, encoding="utf-8") as f:
            return [v for v in json.load(f).get("labels", {}).values() if isinstance(v, str)]
    except Exception:
        return []


def url_host_path(url):
    u = urllib.parse.urlsplit(url)           # no query string, no fragment
    return (u.hostname or "") + (u.path or "")[:120]


def build_request(tab):
    ctx = {"tab": {"title": (tab.get("title") or "")[:160], "site": url_host_path(tab.get("url", "")),
                   "days_unused": tab.get("age_days", 0)},
           "current_agent_sessions": session_labels()}
    return {
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text":
                "The user has a browser tab he has not looked at for days. current_agent_sessions are short labels of the "
                "coding-agent sessions he is working in right now."}]},
            {"role": "user", "content": [{"type": "input_text", "text": "The tab and his current sessions:"},
                                         {"type": "input_json", "data": ctx}]}],
        "prompt_cache_options": {"mode": "explicit"},          # nothing here is reused: caching off
        "questions": [
            {"type": "predicate", "name": "related",
             "instructions": "Is this tab about the same project or subject as one of the current agent sessions, so the user "
                             "may still need it for work in progress? Answer no when the labels are too short or generic "
                             "to connect to the tab's title and site."},
            {"type": "choice", "name": "kind", "instructions": "What is this page?",
             "choices": [{"value": v, "description": d} for v, d in KINDS]},
        ]}


def classify(tab):
    """Returns {related: p, kind: str, confidence: c} or None. One Decisions call, through decide.mjs only."""
    day = time.strftime("%Y-%m-%d")
    if _dec["day"] != day:
        _dec["day"], _dec["n"] = day, 0
    if _dec["n"] >= 200:
        return None
    _dec["n"] += 1
    tmp = tempfile.mkdtemp(prefix="m18tabs-")
    try:
        rq, ans = os.path.join(tmp, "rq.json"), os.path.join(tmp, "ans.json")
        with open(rq, "w", encoding="utf-8") as f:
            json.dump(build_request(tab), f)
        r = subprocess.run([node(), DECIDE, rq, "--out", ans, "--max-input-tokens", "20000"], cwd=SKILL_DIR,
                           capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not os.path.exists(ans):
            log("decide failed", r.returncode, r.stderr[:300])
            return None
        with open(ans, encoding="utf-8") as f:
            resp = json.load(f)
        by = {a.get("name"): a for a in resp.get("answers", [])}
        rel, kind = by.get("related", {}), by.get("kind", {})
        valid = {v for v, _ in KINDS}
        return {"related": rel.get("probability"),
                "kind": kind.get("choice") if kind.get("choice") in valid else None,
                "confidence": kind.get("confidence"),
                "usage": resp.get("usage", {}).get("input_tokens")}
    except Exception as e:
        log("classify error", repr(e))
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- cards ----------------------------------------------------------------------------------------
def site_name(url):
    h = (urllib.parse.urlsplit(url).hostname or "tab")
    p = h.split(".")
    if len(p) > 2 and not h.replace(".", "").isdigit():      # docs.python.org -> python.org, bbc.co.uk stays whole
        p = p[-3:] if (len(p[-1]) == 2 and p[-2] in ("co", "com", "org", "ac", "gov", "net")) else p[-2:]
    return ".".join(p)


def short(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def age_text(d):
    return f"{d} {'day' if d == 1 else 'days'}"


def build_card(offer, default=None):
    tab = offer["tab"]
    card = {"id": offer.get("card_id") or f"tabs-{tab['id']}", "feed": "tabs",
            "title": short(site_name(tab.get("url", "")), 16),
            "lines": [short(tab.get("title") or tab.get("url", ""), 22), age_text(int(tab.get("age_days", 0)))],
            "actions": [{"id": "keep", "label": "Keep"}, {"id": "close", "label": "Close"},
                        {"id": "later", "label": "Later"}, {"id": "skip", "label": "Skip"}],
            "priority": 1, "ttl_s": int(offer.get("ttl_s", 43200))}
    if tab.get("favicon_b64"):
        card["image_png_b64"] = tab["favicon_b64"]
    if default:
        card["default_action"] = default    # extra field: the action to highlight; a daemon that does not know it ignores it
    return card


def handle_offer(msg, out):
    reply = {"reply_to": msg.get("req"), "type": "offer_result"}
    try:
        tab, default, verdict = msg["tab"], None, None
        if decisions_enabled():
            verdict = classify(tab)
            if verdict:
                reply["kind"] = verdict["kind"]
                if (verdict["related"] or 0) >= RELATED_AT:
                    reply.update(result="deferred", days=DEFER_DAYS, related=verdict["related"])
                    log("deferred (related)", tab.get("title"), verdict)
                    return write_msg(out, reply)
                if verdict["kind"] in DEFAULT_ACTION and (verdict["confidence"] or 0) >= 0.6:
                    default = DEFAULT_ACTION[verdict["kind"]]
        r = api("POST", "/cards", build_card(msg, default))
        if r.get("error"):
            reply.update(result="failed", error=f"daemon refused: {r}")
        else:
            reply.update(result="posted", default_action=default)
    except Exception as e:
        log("offer failed", repr(e))
        reply.update(result="failed", error=repr(e)[:200])
    write_msg(out, reply)


def handle(msg, out):
    t = msg.get("type")
    if t == "offer":
        threading.Thread(target=handle_offer, args=(msg, out), daemon=True).start()
    elif t == "post_card":
        card = dict(msg.get("card") or {})
        if str(card.get("id", "")).startswith("tabs-"):
            card["feed"] = "tabs"
            api("POST", "/cards", card)
    elif t == "delete_card":
        cid = str(msg.get("id", ""))
        if cid.startswith("tabs-"):
            api("DELETE", "/cards?id=" + urllib.parse.quote(cid))
    elif t == "hello":
        write_msg(out, {"type": "status", "decisions": decisions_enabled()})


def event_loop(out, stop):
    """Long-poll the daemon for card_action events on feed 'tabs' and forward them."""
    since, backoff, up = None, 1.0, None
    while not stop.is_set():
        try:
            if since is None:
                since = api("GET", "/events", timeout=5).get("seq", 0)
            r = api("GET", f"/events?since={since}&timeout=20", timeout=40)
            if r.get("error"):
                raise RuntimeError(f"events error {r}")
            if up is not True:
                up = True
                write_msg(out, {"type": "status", "daemon": True})
            backoff = 1.0
            if r.get("reset"):
                since = r.get("seq", 0)
                continue
            since = r.get("seq", since)
            for ev in r.get("events", []):
                if ev.get("type") == "card_action" and ev.get("feed") == "tabs":
                    write_msg(out, {"type": "card_action", "card": ev.get("card"), "action": ev.get("action")})
        except Exception as e:
            if up is not False:
                up = False
                try:
                    write_msg(out, {"type": "status", "daemon": False})
                except Exception:
                    return
            log("events loop", repr(e))
            since = None
            stop.wait(backoff)
            backoff = min(backoff * 2, 15.0)


def main():
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    stop = threading.Event()
    threading.Thread(target=event_loop, args=(stdout, stop), daemon=True).start()
    try:
        while True:
            msg = read_msg(stdin)
            if msg is None:
                break
            handle(msg, stdout)
    except Exception as e:
        log("main loop", repr(e))
    finally:
        stop.set()


if __name__ == "__main__":
    main()
