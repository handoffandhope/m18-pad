"""Stub of the m18d card contract, for developing the tabs feed without the real daemon.

  python test_stub.py [port]        serves on 127.0.0.1:8732 (default)

Contract implemented (and nothing else):
  POST   /cards {id, feed, title, lines, image_png_b64?, actions, priority, ttl_s}  upsert
  DELETE /cards?id=...
  GET    /events?since=N&timeout=S   long-poll (max 25 s) -> {seq, events: [...]}; no `since` returns the
                                     current seq at once; `since` ahead of the stub -> reset: true
Guard like m18d: 403 unless there is no Origin header, Host is 127.0.0.1:<port>/localhost:<port>, and X-M18: 1.
Test controls (not part of the contract), under /_test/:
  GET  /_test/cards                      -> {cards: {...}}
  POST /_test/action {card, action}      -> queues {type: card_action, card, feed, action}; drops the card
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

LOCK = threading.Condition()
CARDS: dict = {}
EVENTS: list = []      # {seq, ...}
SEQ = [0]
CALLS: list = []       # (method, path) log, for assertions


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _guard(self):
        port = self.server.server_address[1]
        ok = ("Origin" not in self.headers and self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")
              and self.headers.get("X-M18") == "1")
        if not ok:
            self._send(403, {"error": "forbidden"})
        return ok

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        if not self._guard():
            return
        u = urlsplit(self.path)
        with LOCK:
            CALLS.append(("POST", u.path))
            if u.path == "/cards":
                c = self._body()
                bad = [k for k in ("id", "feed", "title", "lines", "actions", "priority", "ttl_s") if k not in c]
                if bad:
                    return self._send(400, {"error": f"missing {bad}"})
                CARDS[c["id"]] = c
                return self._send(200, {"ok": True, "id": c["id"]})
            if u.path == "/_test/action":
                a = self._body()
                c = CARDS.pop(a["card"], None)
                SEQ[0] += 1
                EVENTS.append({"seq": SEQ[0], "t": time.time(), "type": "card_action", "card": a["card"],
                               "feed": (c or {}).get("feed", "tabs"), "action": a["action"]})
                LOCK.notify_all()
                return self._send(200, {"ok": True})
        self._send(404, {"error": "no such path"})

    def do_DELETE(self):
        if not self._guard():
            return
        u = urlsplit(self.path)
        with LOCK:
            CALLS.append(("DELETE", u.path))
            if u.path == "/cards":
                cid = parse_qs(u.query).get("id", [""])[0]
                return self._send(200, {"ok": True, "deleted": CARDS.pop(cid, None) is not None})
        self._send(404, {"error": "no such path"})

    def do_GET(self):
        if not self._guard():
            return
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/_test/cards":
            with LOCK:
                return self._send(200, {"cards": CARDS, "calls": CALLS})
        if u.path == "/events":
            with LOCK:
                if "since" not in q:
                    return self._send(200, {"seq": SEQ[0], "events": []})
                since = int(q["since"][0])
                if since > SEQ[0]:
                    return self._send(200, {"seq": SEQ[0], "events": [], "reset": True})
                end = time.time() + min(float(q.get("timeout", ["0"])[0]), 25)
                while not any(e["seq"] > since for e in EVENTS) and time.time() < end:
                    LOCK.wait(end - time.time())
                return self._send(200, {"seq": SEQ[0], "events": [e for e in EVENTS if e["seq"] > since]})
        self._send(404, {"error": "no such path"})


def serve(port=8732):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    return srv


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8732
    print(f"stub on 127.0.0.1:{p}", flush=True)
    serve(p).serve_forever()
