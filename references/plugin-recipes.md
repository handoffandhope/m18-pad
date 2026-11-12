# Plugin recipes: what you need without reading the source

Read this before writing a plugin. It answers what agents otherwise dig out of `m18d.py`,
`m18lib/` and the bundled plugins. `plugins.md` has the full `ctx` list.

## Imports and helpers

| Need | Use |
|---|---|
| Fonts | `from m18lib.render import font`; `font("regular" \| "bold", size)` returns a PIL font |
| Key image size | return `{"image": im}`; 64x64 is the LCD's real size, 192x192 is downsampled smoothly |
| Colour maths | `from m18lib import tiles as T`; `T.rgb("#ff8800")`, `T.mix(a, b, t)` |
| One picture over several keys | `T.block_size(cols, rows)` gives the canvas size with the bars included (5x3 keys = 440x252); `T.split(im, cols, rows)` returns `{(col, row): 64px tile}` with the pixels behind the bars dropped. Key number = `row * 5 + col + 1` |
| Ring as a meter | `from m18lib import ringmap as RM`; `ctx.leds(RM.meter_frame(RM.load(ctx.config_dir), fraction, "#ff7043"), ttl=2.0, owner="myplugin")` |
| Ring, raw | `ctx.leds([24 colours or None], ttl=2.0, owner="myplugin")`; resend before `ttl` runs out; `ctx.leds_release("myplugin")` gives it back |
| State that survives reloads | `ctx.state` (per key); `ctx.daemon.plugin_state.setdefault("myplugin", {})` (shared by all keys) |
| Redraw now | `ctx.daemon.live_next.clear()` after an action changes what a live key shows |
| Text on a key for a moment | `ctx.flash({"image": im}, 3.0)` |
| Preview renders | when `ctx.preview` is True: return a still image, start no threads, skip ring calls |

## Recipe: one live key with state (timer, counter, toggle)

```python
import time
from PIL import Image, ImageDraw
from m18lib.render import font
from m18lib import ringmap as RM

def face(ctx, **_):                      # live: {plugin: mykey.face, interval: 0.5}
    st = ctx.state.setdefault("s", {"end": 0.0})
    left = max(0.0, st["end"] - time.time())      # store wall-clock times, never tick counts
    if left and not ctx.preview:
        ctx.leds(RM.meter_frame(RM.load(ctx.config_dir), left / 1500, "#ff7043"), ttl=2.0, owner="mykey")
    im = Image.new("RGB", (192, 192), "#1c1c26")
    ImageDraw.Draw(im).text((96, 96), f"{int(left) // 60:02d}:{int(left) % 60:02d}", font=font("bold", 58), fill="#fff", anchor="mm")
    return {"image": im}

def tap(ctx, **_):                       # action: {plugin: mykey.tap}
    ctx.state.setdefault("s", {})["end"] = time.time() + 1500
    ctx.daemon.live_next.clear()
```

## Recipe: an animated scene across the whole page

One engine thread draws the whole picture and writes only the keys that changed. Every key on the
page uses the same live function, which starts the engine once and returns that key's last tile.

```python
import threading, time
from PIL import Image
from m18lib import tiles as T

PAGE, COLS, ROWS, FPS = "scene", 5, 3, 12
_LOCK = threading.Lock()

def _loop(d, st, stop):
    while not stop.is_set():
        t0 = time.time()
        if d.page == PAGE:                                # draw only while the page is showing
            frame = _render(t0)                           # your picture, size T.block_size(COLS, ROWS)
            changed = {}
            for (c, r), im in T.split(frame, COLS, ROWS).items():
                k, dg = r * COLS + c + 1, hash(im.tobytes())
                if st["dig"].get(k) != dg:
                    st["dig"][k] = dg
                    d.live_faces[(PAGE, k)] = st["faces"][k] = {"image": im, "_fid": ("scene", PAGE, k, dg)}
                    changed[k] = (im, dg)
            if changed:
                d.blit(PAGE, changed, "scene")            # fast path: no flicker, skips unchanged keys
        time.sleep(max(0.0, 1 / FPS - (time.time() - t0)))

def tile(ctx, **_):                      # live on every key: {plugin: scene.tile, interval: 1}
    if ctx.preview:
        return {"image": T.split(_render(time.time()), COLS, ROWS)[((ctx.key - 1) % COLS, (ctx.key - 1) // COLS)]}
    st = ctx.daemon.plugin_state.setdefault("scene", {"faces": {}, "dig": {}})
    with _LOCK:
        if not (st.get("thread") and st["thread"].is_alive()):
            st["stop"] = threading.Event()
            st["thread"] = threading.Thread(target=_loop, args=(ctx.daemon, st, st["stop"]), daemon=True)
            st["thread"].start()
    return st["faces"].get(ctx.key) or {"image": Image.new("RGB", (64, 64), "#000")}
```

Keep `_render` cheap (a few ms): draw static layers once and cache them. Catch exceptions inside
the loop and `d.log` them, or one error ends the animation. `plugins/fishbowl.py` is the full-size
example; you should not need to read it.

## Testing without a pad

| Command | Needs a daemon | What it does |
|---|---|---|
| `m18ctl validate` | no | checks `macros.yaml` |
| `m18ctl icons WORD` | no | searches icon names |
| `m18ctl preview --offline [--page P]` | no | PNG of a page; calls each live function once with `ctx.preview` True |
| `m18ctl dryrun N [--hold S]` | no | presses key N on a throwaway mock and prints what would be sent |
| `m18ctl start --mock` | starts one | whole daemon without USB; keystrokes and launches are logged, not sent |
| `m18ctl page P`, `m18ctl press N`, `m18ctl status` | mock | switch page, press, read the log tail for plugin errors |
| `m18ctl preview --page P` | mock | PNG of what the running page shows now, animation included |
| `m18ctl stop` | mock | stop it when done |

A threaded scene only runs in a started daemon, so test it with `start --mock`, `page P`, wait a
few seconds, `status` (no errors in the log, `faces_sent` rising), then `preview --page P`.
