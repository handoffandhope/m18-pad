"""Fish bowl: one tank seen through 15 windows; every agent session is a creature (`agents` signal).
The water level falls with Claude usage and freezes on a hit limit, lights go out at night, and the
filter gauge reads cpu / ram / gpu. Tap a creature for its name, tap again to open its session,
hold to nudge it; tap empty water to feed.

    pages:
      tank:
        scene: {name: fishbowl, hold: true, fixtures: {1: lamp, 5: buoy, 11: gauge, 15: chest}}
        keys:
          1: {action: {dark: toggle}}          # a fixture key keeps its own action and shows the fixture

The tank itself (simulation and art) is the engine in plugins/fishbowl.py; this file only feeds it
signals. Not here yet, still in the plugin's own page mode: agents' posts on their creatures, the
card count on the chest, and the away-mode night.
"""
import importlib.util
import sys
import time
from pathlib import Path

from PIL import Image

from m18lib.scene import key_box

SIGNALS = ["agents", "claude_usage", "time_of_day", "cpu", "ram", "gpu"]
FPS = 5
SCAN_S = 2.0


def _engine():
    """plugins/fishbowl.py as a module (the user's copy first, then the shipped one)."""
    mod = sys.modules.get("m18scene_fishbowl_engine")
    if mod is None:
        here = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location("m18scene_fishbowl_engine", here / "plugins" / "fishbowl.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    return mod


def setup(state):
    fb = _engine()
    tank = fb.Tank(fb.Geo(), seed=int(time.time()), themed=False)
    fx = {int(k): v for k, v in (state["opts"].get("fixtures") or {}).items()}
    tank.set_layout(None, fx)
    store = state.get("dir")
    state.update(fb=fb, tank=tank, scan=-99.0, last=None, first=True, env=None,
                 reef_path=(Path(store) / "fishbowl-reef.json") if store else None, reef=None,
                 pen=fb.make_pen(fb._PenHost({"settings": {}}, Path(store).parent if store else Path("."))))


def _refresh(s, t, state):
    """Roster, environment and reef, every SCAN_S (the plugin's refresh(), fed by signals)."""
    fb, tank = state["fb"], state["tank"]
    stamp = _stamp(state)
    if state["reef"] is None or stamp != state.get("reef_stamp"):     # first run, or another page saved the reef
        state["reef"] = fb.reef_load(state["reef_path"]) if state["reef_path"] else {"blocks": [], "keeps": [], "acked": {}}
        state["reef_stamp"] = stamp
        tank.set_reef(state["reef"])
    reef, rows = state["reef"], [dict(r) for r in s.agents.items or []]
    for r in rows:
        r["acked"] = bool(r.get("crashed")) and reef["acked"].get(r["id"]) == r.get("err_key")
    hour = s.time_of_day.value * 24
    night = state["opts"].get("night", "auto")
    use = s.claude_usage
    env = {"fh": None if use.value is None else use.value * 100, "frozen": use.data.get("frozen"),
           "resets_at": use.data.get("resets_at"), "night": night == "on" or (night == "auto" and (hour >= 21 or hour < 6))}
    if env != state["env"]:                              # a new env object redraws the background: only on change
        state["env"] = env
        tank.set_env(env)
    tank.sync(rows, t, first=state["first"] and bool(rows))
    state["first"] = state["first"] and not rows
    ended, tank.ended = tank.ended, []
    if fb.reef_update(reef, rows, time.time(), ended):
        tank.set_reef(reef)
        _save(s, state)


def _stamp(state):
    try:
        return state["reef_path"].stat().st_mtime_ns if state["reef_path"] else None
    except OSError:
        return None


def _save(s, state):
    if state["reef_path"] and s.agents.live:             # simulated sessions never reach the real reef file
        try:
            state["fb"].reef_save(state["reef_path"], state["reef"])
            state["reef_stamp"] = _stamp(state)
        except OSError:
            pass


def draw(im, d, s, t, state):
    fb, tank = state["fb"], state["tank"]
    if t - state["scan"] >= SCAN_S or t < state["scan"]:
        state["scan"] = t
        _refresh(s, t, state)
    tank.set_fixture_data(sys_=(float(s.cpu) * 100, float(s.ram) * 100, None if s.cpu.live and not s.gpu.live else float(s.gpu) * 100))
    tank.step(max(0.0, min(1.0, t - state["last"])) if state["last"] is not None else 1 / FPS, t)
    state["last"] = t
    world = tank.render(t).convert("RGB")
    im.paste(world)
    for k, name in tank.fixtures.items():                # fixture art over its own window of the water
        x0, y0, x1, y1 = key_box(k)
        im.paste(tank.fixture_tile(world.crop((x0, y0, x1 + 1, y1 + 1)), k, name, t), (x0, y0))
    p = tank.plate
    if p and p["key"] not in tank.blocked and p["id"] in tank.fish:
        x0, y0, x1, y1 = key_box(p["key"])
        tile = fb.plate_tile(world.crop((x0, y0, x1 + 1, y1 + 1)), tank.fish[p["id"]], state["pen"], p.get("word"))
        im.paste(tile.resize((64, 64), Image.LANCZOS) if tile.size != (64, 64) else tile, (x0, y0))


def tap(key, x, y, s, t, state):
    tank = state["tank"]
    what, fid = tank.tap(key, tank.t)
    if what == "open" and not fid.startswith("sim-"):
        return {"plugin": "fishbowl._open_session", "args": {"sid": fid}}
    if what == "ack":                                    # a crashed creature was acknowledged: remember which error
        f = tank.fish.get(fid)
        if f is not None and f.row.get("err_key"):
            state["reef"]["acked"][fid] = f.row["err_key"]
            _save(s, state)
    return None


def hold(key, x, y, s, t, state):
    tank = state["tank"]
    fid = tank.hit(key)
    if not fid:
        tank.feed(key, tank.t)
        return None
    tank.tap(key, tank.t, held=True)
    return None if fid.startswith("sim-") else {"plugin": "fishbowl._nudge", "args": {"sid": fid}}


def ring(s, t, state, rm):
    return state["tank"].ring_frame(rm, t)
