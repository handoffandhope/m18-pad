"""Card store: feeds post cards, one pad key shows the top card, pressing it takes the pad over.

A card is {id, feed, title, lines: [str], image_png_b64?, icon?, actions: [{id, label, color?}],
priority: 0-2 (default 1), ttl_s?}. Upsert by id keeps the card's place in line (its `created`).
Display order: priority high to low, then oldest first. Cards persist in <config dir>/state/cards.json;
a card whose ttl ran out is dropped. Resolving a card (an action key) removes it, publishes
{type: "card_action", card, feed, action, source} on /events and calls the feed's handler, if any.
"""
from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
from pathlib import Path

MAX_CARDS = 60
MAX_ACTIONS = 4               # the bottom row has 5 keys; key 15 is the pad's own Next / Close
MAX_LINES = 12
MAX_IMAGE_B64 = 8 * 1024 * 1024


class CardError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _s(v, what, n, required=True):
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > n:
        raise CardError(f"{what}: non-empty string up to {n} chars")
    return v


def validate(body: dict, hex_re, mdi_ok=None) -> dict:
    """A clean card from a POST body (or a plugin's dict). Raises CardError."""
    if not isinstance(body, dict):
        raise CardError("card must be a JSON object")
    known = {"id", "feed", "title", "lines", "image_png_b64", "icon", "actions", "priority", "ttl_s"}
    bad = sorted(set(body) - known)
    if bad:
        raise CardError(f"unknown field(s) {bad}; allowed {sorted(known)}")
    c = {"id": _s(body.get("id"), "id", 120), "feed": _s(body.get("feed"), "feed", 40),
         "title": _s(body.get("title"), "title", 200)}
    lines = body.get("lines", [])
    if not isinstance(lines, list) or len(lines) > MAX_LINES or \
            not all(isinstance(x, str) and len(x) <= 300 for x in lines):
        raise CardError(f"lines: list of up to {MAX_LINES} strings (300 chars each)")
    c["lines"] = [x for x in lines]
    img = body.get("image_png_b64")
    if img is not None:
        if not isinstance(img, str) or len(img) > MAX_IMAGE_B64:
            raise CardError("image_png_b64: base64 PNG/JPEG string up to 8 MB")
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(base64.b64decode(img, validate=True)))
            if im.format not in ("PNG", "JPEG") or im.width > 4096 or im.height > 4096:
                raise ValueError("need a PNG or JPEG up to 4096x4096")
            im.load()
        except Exception as e:                               # noqa: BLE001
            raise CardError(f"image_png_b64: {e}")
        c["image_png_b64"] = img
    icon = body.get("icon")
    if icon is not None:
        _s(icon, "icon", 80)
        name = icon[4:] if icon.startswith("mdi:") else icon
        if mdi_ok and not mdi_ok(name):
            raise CardError(f"icon: unknown mdi name {name!r} (see `m18ctl icons <word>`)")
        c["icon"] = "mdi:" + name
    acts = body.get("actions", [])
    if not isinstance(acts, list) or len(acts) > MAX_ACTIONS:
        raise CardError(f"actions: list of up to {MAX_ACTIONS} {{id, label, color?}}")
    seen, out = set(), []
    for i, a in enumerate(acts):
        if not isinstance(a, dict) or set(a) - {"id", "label", "color"}:
            raise CardError(f"actions[{i}]: object {{id, label, color?}}")
        aid = _s(a.get("id"), f"actions[{i}].id", 60)
        if aid in seen:
            raise CardError(f"actions[{i}].id: duplicate {aid!r}")
        seen.add(aid)
        act = {"id": aid, "label": _s(a.get("label"), f"actions[{i}].label", 24)}
        col = a.get("color")
        if col is not None:
            if not (isinstance(col, str) and hex_re.match(col)):
                raise CardError(f"actions[{i}].color: '#rrggbb'")
            act["color"] = col
        out.append(act)
    c["actions"] = out
    pr = body.get("priority", 1)
    if isinstance(pr, bool) or not isinstance(pr, int) or not 0 <= pr <= 2:
        raise CardError("priority: 0, 1 (default) or 2")
    c["priority"] = pr
    ttl = body.get("ttl_s")
    if ttl is not None:
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl != ttl or ttl <= 0 \
                or ttl == float("inf"):
            raise CardError("ttl_s: positive number of seconds")
        c["ttl_s"] = min(float(ttl), 30 * 86400.0)
    return c


class CardStore:
    """Thread-safe, persisted. `publish(event_dict)` and `on_change()` come from the daemon."""

    def __init__(self, state_dir: Path, publish, on_change, run_handler, log):
        self.path = Path(state_dir) / "cards.json"
        self.publish, self.on_change, self.run_handler, self.log = publish, on_change, run_handler, log
        self.lock = threading.RLock()
        self.cards: dict[str, dict] = {}
        self.callbacks: dict[str, object] = {}       # feed -> fn(ctx, card, action_id) (in memory)
        self.listeners: list = []                     # fn() after any change (the open takeover)
        self.version = 0
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            now = time.time()
            for c in raw.get("cards", []):
                if isinstance(c, dict) and c.get("id") and not (c.get("expires") and c["expires"] <= now):
                    self.cards[c["id"]] = c
        except FileNotFoundError:
            pass
        except Exception as e:                                # noqa: BLE001
            self.log(f"cards: {self.path} unreadable ({e}); starting empty")

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cards": list(self.cards.values())}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            self.log(f"cards: save failed: {e}")

    def _changed(self):
        """Under the lock: bump the version and save. Callers run _notify() after releasing it."""
        self.version += 1
        self._save()

    def _notify(self):
        try:
            self.on_change()
        except Exception as e:                                # noqa: BLE001
            self.log(f"cards: on_change: {e}")
        for fn in list(self.listeners):
            try:
                fn()
            except Exception as e:                            # noqa: BLE001
                self.log(f"cards: listener: {e}")

    # ------------------------------------------------------------ operations
    def post(self, card: dict, handler: str | None = None) -> dict:
        """Upsert a validated card. `handler` ("module.fn", plugins only) is called on its actions."""
        now = time.time()
        with self.lock:
            old = self.cards.get(card["id"])
            c = dict(card)
            c["created"] = old["created"] if old else now
            c["updated"] = now
            if c.get("ttl_s"):
                c["expires"] = now + c["ttl_s"]
            if handler:
                c["handler"] = handler
            elif old and old.get("handler") and old.get("feed") == c["feed"]:
                c["handler"] = old["handler"]
            self.cards[c["id"]] = c
            if len(self.cards) > MAX_CARDS:                   # drop the least important, oldest first
                for x in self.ordered()[MAX_CARDS:]:
                    self.cards.pop(x["id"], None)
            self._changed()
        self._notify()
        return {"ok": True, "card": c["id"], "created": old is None}

    def remove(self, cid: str) -> bool:
        with self.lock:
            gone = self.cards.pop(cid, None) is not None
            if gone:
                self._changed()
        if gone:
            self._notify()
        return gone

    def get(self, cid):
        with self.lock:
            return self.cards.get(cid)

    def ordered(self, feed: str | None = None) -> list[dict]:
        now = time.time()
        with self.lock:
            cs = [c for c in self.cards.values() if not (c.get("expires") and c["expires"] <= now)
                  and (feed is None or c["feed"] == feed)]
        return sorted(cs, key=lambda c: (-int(c.get("priority", 1)), c["created"]))

    def public(self, feed=None) -> list[dict]:
        return [{k: v for k, v in c.items() if k != "handler"} for c in self.ordered(feed)]

    def expire(self, now=None) -> int:
        now = now or time.time()
        with self.lock:
            dead = [k for k, c in self.cards.items() if c.get("expires") and c["expires"] <= now]
            for k in dead:
                del self.cards[k]
            if dead:
                self._changed()
        if dead:
            self._notify()
        return len(dead)

    def act(self, cid: str, action: str, source: str = "device") -> dict | None:
        """Resolve a card: remove it, publish the event, run the feed's handler. None if unknown."""
        with self.lock:
            c = self.cards.get(cid)
            if c is None or action not in [a["id"] for a in c.get("actions", [])]:
                return None
            del self.cards[cid]
            self._changed()
        self._notify()
        ev = {"type": "card_action", "card": cid, "feed": c["feed"], "action": action, "source": source}
        self.publish(ev)
        self.log(f"card {cid} ({c['feed']}): {action} [{source}]")
        fn = self.callbacks.get(c["feed"]) or c.get("handler")
        try:
            if fn is not None:
                self.run_handler(fn, c, action, source)        # fn(ctx, card, action) in a worker
        except Exception as e:                                # noqa: BLE001
            self.log(f"card {cid}: handler failed: {e}")
        return ev
