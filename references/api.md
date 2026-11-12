# Control API (127.0.0.1:8718)

Use it to make the pad a two-way surface: push faces, read presses, take over the pad, set the ring.
`m18ctl` wraps each call; the Python below is the same thing without it.

## The guard
Any web page can send requests to 127.0.0.1, so every method on every path is refused with 403 unless
all of these hold: no `Origin` header, `Host` is `127.0.0.1:<port>` or `localhost:<port>`, and the
header `X-M18: 1` is present. m18ctl sends it. Helper for the examples:

```python
import json, urllib.request
def m18(method, path, body=None, port=8718):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-M18": "1", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=40))
    except urllib.error.HTTPError as e:
        return json.load(e)
```

## Press sources
Every press has a source: `device` (the hardware) or `api` (`POST /press`, `m18ctl press`). A key with
`physical_only: true` runs its action and hold only for `device`; an api press returns 403
`refused: physical_only`, logs it, and runs nothing. Plugins see `ctx.source` (`device`, `api`, or
`daemon` for live ticks, `preview` for a one-off render in `/preview`). `/press?key=K&hold=S&wait=S`: hold and
wait are numbers 0-10 (capped), else 400. `&taps=1-3` (needs the default phase=both) presses that many times
`&gap=S` apart (default 0.08) to fire `double:`/`triple:`; `m18ctl press K --taps 2`. API presses use logical
key numbers (key_map is for hardware codes only).

## Preview: `GET /preview?page=<name>`
PNG of any page (default: the one the pad shows), without switching the pad or writing to it: `status()`
`faces_sent` stays the same. Live keys on another page use their last face, else one call of the live
function with a sandboxed ctx (`ctx.preview` True, source `preview`: run, goto, flash, modal, rgb, leds and
cards do nothing, `ctx.daemon.plugin_state` is a separate dict), cached for the key's interval. Unknown page: 400.

## Push a face: `POST /face`, `DELETE /face?key=`
Body `{key: 1-18 | "all" | [list], face: {icon,label,glyph,bg,fg,border,...}, image_b64?, ttl?}`.
`image_b64` is a PNG/JPEG (opaque; it replaces the drawn face; for `"all"` a 320x192 image is cut into
the 15 tiles, row-major). `ttl` is seconds, or null to stay until deleted. A pushed face sits above live
results and below countdown and error flashes, survives `goto`, and is never drawn on a `physical_only`
key (so a consent key can't be faked). Keys 16-18 have no screen and are reported under `no_screen`.

```python
m18("POST", "/face", {"key": 4, "face": {"icon": "mdi:alert", "label": "CI red", "bg": "#8b1a1a"}, "ttl": 60})
m18("DELETE", "/face?key=4")          # or key=all, or key=4,5
```
CLI: `m18ctl face 4 --icon mdi:alert --label "CI red" --bg "#8b1a1a" --ttl 60`, `m18ctl face --clear 4`.

## Read presses: `GET /events?since=<seq>&timeout=<sec, max 25>`
Long-poll. Returns `{seq, events: [{seq, t, key, down, held_ms (on release), page, source,
handled_by: "action"|"modal"|"ignored", note?}]}` from a ring of the last 500. Pass the returned `seq` as
the next `since`; if `since` is ahead of the daemon (it restarted) you get `reset: true`.

```python
seq = m18("GET", "/events")["seq"]
r = m18("GET", f"/events?since={seq}&timeout=20")   # blocks until a press or 20 s
```
CLI: `m18ctl events --since N --timeout 20`.

## Take over the pad: `POST /modal`, `DELETE /modal`
Body `{faces: {"1": face, ...}, image_b64?, timeout, owner}`. Keys not named show blank (an image fills
all 15 first). While it is up, presses do not run actions: they appear in `/events` with
`handled_by: "modal"`. The page underneath is untouched and redrawn at the end. It ends on `timeout`
(default and max 120 s), `DELETE /modal`, or a physical press on b1 (Home), which always works. A
different owner gets 409 while one is up, unless the one that is up was opened by a plugin with
`ctx.modal(..., yields=True)` (an ambient screen such as the fishbowl), which the new one replaces. Any
request gets 409 while dark mode is on (dark mode also ends
a modal that is up).

```python
m18("POST", "/modal", {"faces": {"7": {"label": "Allow?", "bg": "#1d6b2a"}, "9": {"label": "Deny", "bg": "#8b1a1a"}},
                       "timeout": 30, "owner": "my-agent"})
ev = m18("GET", f"/events?since={seq}&timeout=25")["events"]   # which key did the human press?
m18("DELETE", "/modal")
```
CLI: `m18ctl modal --faces '{"7":{"label":"Allow?"}}' --timeout 30`, `m18ctl modal --end`.
`/events` marks each press with its `source`: trust only `device` as a human answer.
For a consent that software must not be able to press, put it on a `physical_only` key.

## Cards: `POST /cards`, `GET /cards`, `DELETE /cards?id=`
| route | does |
|---|---|
| `POST /cards {id, feed, title, lines, image_png_b64?, icon?, actions: [{id, label, color?}], priority?, ttl_s?}` | upsert a card for the card key; an action press removes it and publishes `{type: "card_action", card, feed, action, source}` on `/events`. Details: cards.md |

## Ring: `POST /rgb` (one colour) and `POST /leds` (per LED)
`/rgb`: `{color: "#rrggbb"|"off", brightness: 0-100|null, ttl: seconds|null}`; after ttl the page's ring colour
returns. CLI: `m18ctl rgb "#ff0000" --brightness 60 --ttl 5`.

`/leds`: `{colors: [24 x "#rrggbb"|null], ttl?, brightness?, owner?: "api", priority?: 1}`; null = off.
`GET /leds` shows the owner, the wanted and the last-sent frame, and counters; `DELETE /leds[?owner=x]` releases
(the page colour returns). LED indices: 0-21 run round the case, 22-23 are the two front strips; positions are
in the ring map (below). Dark mode refuses frames (409), turns the ring off and drops any owner.

**Owner rule (one owner at a time).** The latest frame wins: a frame from a different owner replaces the live
one, and the old owner's frames are not mixed in. Exception: `priority` (0 ambient, 1 default, 2 foreground):
a frame with a lower priority than the live owner's is refused (409; `ctx.leds` returns False), so a background
status thread (ring.status is 0) never fights a foreground one (hold progress, ringwalk are 2) and takes the ring
back when that owner releases or its `ttl` ends. `POST /rgb`, `ctx.rgb` and the `rgb:` action also take the
ring (they drop the owner). A page's own `rgb:` (page change, config reload) never touches the ring while an
owner holds it; the page colour, read fresh, returns on release. Unchanged frames are not resent (colour first,
then brightness only if it changed), frames are capped at 20 fps (the newest one is kept), brightness defaults
to the page's, else 60. Always give a `ttl` to anything that might crash.

**Ring map.** `state/ring-map.json` (written by `m18ctl ringwalk`; the default map is used until then):
per LED the nearest key, `x`,`y` on a unit rectangle over the case (y down) and `s` (perimeter position),
plus `order` (LEDs 0-21 round the perimeter) and `clockwise` (from the top-left corner). `m18ctl ringshow
sweep|halves|meter 0.35` draws through the map so the owner can judge it by eye.

**`m18ctl ringwalk`** (`POST /ringwalk {idle_timeout?}`, `GET` progress, `DELETE` abort): modal; one LED glows
white and the owner presses the key nearest to it (b1-b3 for the bottom/front). Hold any key 1 s = back one LED.
Three b1 presses within 0.8 s, or no press for 60 s, abort and save nothing. (b1 does not close this modal.)

In plugins: `ctx.modal(faces, on_key=fn(ctx, key, down, t), timeout=)` (returns a token),
`ctx.modal_update(token, faces=None, timeout=None)`, `ctx.end_modal()`, `ctx.rgb(...)`, `ctx.leds(colors,
ttl=None, brightness=None, owner=None, priority=1)`, `ctx.leds_release(owner=None)`, `ctx.wait_release(timeout)`,
`ctx.pressed_at`, `ctx.source` (see plugins.md).
