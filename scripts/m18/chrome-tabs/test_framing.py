"""Pipe test for the host's native-messaging framing (4-byte little-endian length + UTF-8 JSON).
  python test_framing.py        (uses this Python; talks to the stub on 8732, started here)
Checks: a message split across writes still parses, non-ASCII survives, a ~300 KB message (favicon-sized
payloads and up) is accepted, the host's reply has a correct length prefix, and EOF on stdin ends the host.
"""
import json, os, struct, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_stub

srv = test_stub.serve(8732)
threading.Thread(target=srv.serve_forever, daemon=True).start()
env = dict(os.environ, M18_API_PORT="8732", M18_TABS_DECISIONS="0")
p = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "host", "m18_tabs_host.py")], env=env,
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def frame(o):
    b = json.dumps(o, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(b)) + b


def read_reply(want):
    while True:
        head = p.stdout.read(4)
        assert len(head) == 4, "host closed stdout"
        (n,) = struct.unpack("<I", head)
        body = p.stdout.read(n)
        assert len(body) == n, "short body: length prefix wrong"
        m = json.loads(body.decode("utf-8"))
        if m.get("reply_to") == want:
            return m


def offer(req, title, fav=None):
    return {"type": "offer", "req": req, "card_id": f"tabs-{req}", "ttl_s": 60,
            "tab": {"id": req, "title": title, "url": "https://www.example.com/a?b=c", "age_days": 4, "favicon_b64": fav}}


# 1. split write, non-ASCII title
data = frame(offer(1, "Café 日本語 — test"))
p.stdin.write(data[:3]); p.stdin.flush(); time.sleep(0.2)
p.stdin.write(data[3:9]); p.stdin.flush(); time.sleep(0.2)
p.stdin.write(data[9:]); p.stdin.flush()
assert read_reply(1)["result"] == "posted"
assert test_stub.CARDS["tabs-1"]["lines"][0].startswith("Café 日本語")
# 2. big message
big = "A" * 300_000
p.stdin.write(frame(offer(2, "big", big))); p.stdin.flush()
assert read_reply(2)["result"] == "posted" and len(test_stub.CARDS["tabs-2"]["image_png_b64"]) == 300_000
# 3. EOF ends the host
p.stdin.close()
assert p.wait(timeout=10) == 0
print("framing OK: split write, unicode, 300 KB payload, reply prefix, clean exit on EOF")
