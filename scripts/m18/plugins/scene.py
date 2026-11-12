"""Runs a scene file (`scenes/<name>.py`) on a page. Normally reached through the page shorthand

    pages:
      city: {scene: city}

which puts `live: {plugin: scene.tile}` and `action: {plugin: scene.tap}` on the 15 LCD keys.
The scene contract is in m18lib/scene.py.
"""
import os
import threading
import time

from m18lib import config as C
from m18lib import ringmap as RM
from m18lib import scene as S
from m18lib import signals as SG

_LOCK = threading.RLock()


def hub(d):
    """The daemon's one signal hub. `M18_SIGNALS=sim` forces simulated values (demos, recordings)."""
    h = getattr(d, "_signal_hub", None)
    if h is None:
        with _LOCK:
            h = getattr(d, "_signal_hub", None)
            if h is None:
                h = SG.Hub(d.base, sim=os.environ.get("M18_SIGNALS") == "sim", log=d.log, plugin_fn=d.plugin_fn)
                d._signal_hub = h
    return h


def _loop(d, page, st):
    pl, dig = st["player"], st["dig"]
    ring_on, ring_t, keys_t, mine = False, 0.0, 0.0, set()
    while not st["stop"].is_set():
        t0 = time.time()
        if d.page != page:                                    # hidden: no drawing, give the ring back
            if ring_on:
                d.leds_release("scene"); ring_on = False
            time.sleep(0.2)
            continue
        if t0 - keys_t > 1.0:                                 # which keys are still this scene's, per the config
            keys_t = t0
            mine = _own_keys(d, page, pl.name)
            if not mine:                                      # the page lost its scene: stop for good
                st["stop"].set()
                break
        frame = pl.frame()
        changed = {}
        for (c, r), im in S.T.split(frame, S.COLS, S.ROWS).items():
            k, dg = r * S.COLS + c + 1, hash(im.tobytes())
            if k in mine and dig.get(k) != dg:
                dig[k] = dg
                d.live_faces[(page, k)] = st["faces"][k] = {"image": im, "_fid": ("scene", page, k, dg)}
                changed[k] = (im, dg)
        if changed:
            d.blit(page, changed, "scene")
        if getattr(d, "dark_key", None) is not None:          # dark mode owns the ring: nothing to set or release
            ring_on = False
        elif t0 - ring_t > 0.25:                              # ring: a meter of one signal, ambient priority
            ring_t = t0
            try:
                rm = RM.load(d.base)
                own, rg = pl.ring_frame(rm), None
                if own is None:
                    rg = pl.ring()
                if own or rg:
                    ring_on = d.leds_set(own or RM.meter_frame(rm, rg[0], rg[1]), ttl=3, owner="scene", priority=0) or ring_on
                elif ring_on:
                    d.leds_release("scene"); ring_on = False
            except Exception as e:                            # noqa: BLE001  ring trouble never stops the picture
                if not st.get("ring_err"):
                    st["ring_err"] = True
                    d.log(f"scene {pl.name} ring: {e}")
        time.sleep(max(0.0, 1 / pl.fps - (time.time() - t0)))
    if ring_on:
        d.leds_release("scene")


def _own_keys(d, page, name) -> set:
    """Keys of `page` that the config gives to this scene (a key the user set himself is left alone)."""
    try:
        keys = C.resolved_keys(d.cfg, page) if d.cfg and page in d.cfg["pages"] else {}
    except Exception:                                         # noqa: BLE001
        return set()
    return {k for k, kd in keys.items()
            if (kd.get("live") or {}).get("plugin") == "scene.tile"
            and ((kd["live"].get("args") or {}).get("name") or page) == name}


def _state(d, page, name, opts=None):
    """The page's player and engine thread; restarted when the page's scene name or options change."""
    store = d.plugin_state.setdefault("scene", {})
    st = store.get(page)
    if st and (st["player"].name != name or (opts is not None and st["player"].opts != opts)):
        st["stop"].set()
        st = None
    if st is None or not st["thread"].is_alive():
        with _LOCK:
            st = store.get(page)
            if st is None or st["stop"].is_set() or not st["thread"].is_alive():
                st = {"player": S.Player(d.base, name, hub(d), d.log, opts, d.base / "state"),
                      "faces": {}, "dig": {}, "stop": threading.Event()}
                st["thread"] = threading.Thread(target=_loop, args=(d, page, st), daemon=True, name=f"m18.scene.{page}")
                store[page] = st
                st["thread"].start()
    return st


def tile(ctx, name=None, opts=None, **_):
    name, opts = name or ctx.page, dict(opts or {})
    if ctx.preview:                                           # one still frame, no thread
        d = ctx.daemon
        live = getattr(d, "_d", d).plugin_state.get("scene", {}).get(ctx.page)
        if live and live["player"].name == name and live["thread"].is_alive() and ctx.key in live["faces"] \
                and getattr(d, "page", None) == ctx.page:
            return live["faces"][ctx.key]                     # the page is running: show what the pad shows
        cache = d.plugin_state.setdefault("scene_preview", {})
        name_o, name = name, (name, repr(sorted(opts.items(), key=str)))      # cache per scene and options
        hit = cache.get((ctx.page, name))
        if not hit or time.time() - hit[0] > 0.5:
            pl = cache.get(("player", ctx.page, name)) or S.Player(d.base, name_o, hub(d._d if hasattr(d, "_d") else d), d.log, opts)
            cache[("player", ctx.page, name)] = pl
            hit = cache[(ctx.page, name)] = (time.time(), S.T.split(pl.frame(), S.COLS, S.ROWS))
        return {"image": hit[1][((ctx.key - 1) % S.COLS, (ctx.key - 1) // S.COLS)]}
    st = _state(ctx.daemon, ctx.page, name, opts)
    return st["faces"].get(ctx.key) or {"image": S.Image.new("RGB", (64, 64), "#000")}


def tap(ctx, name=None, gesture="tap", **_):
    if ctx.key and 1 <= ctx.key <= 15:
        act = _state(ctx.daemon, ctx.page, name or ctx.page)["player"].tap(ctx.key, gesture)
        if act:                                               # the scene asked the daemon to do something
            ctx.log(f"scene {name or ctx.page}: {gesture} {ctx.key} -> {act}")
            ctx.run(act)


def hold(ctx, name=None, **_):
    tap(ctx, name, "hold")
