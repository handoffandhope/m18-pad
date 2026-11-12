# Plugins: live keys and Python actions

A plugin is a plain `.py` file in `$M18/plugins/`. It reloads automatically when saved.

```yaml
7: {live: {plugin: weather.face, interval: 600, args: {city: Lisbon}},
    action: {plugin: weather.open_forecast, args: {city: Lisbon}}}
```

## Contract

```python
def face(ctx, **args) -> dict:
    # Return face fields, e.g. {"icon": "mdi:...", "label": "72°", "bg": "#123"},
    # or {"image": PIL.Image} (any size; square; drawn at 192px looks sharp) for custom graphics.
    # or {"frames": [PIL.Image, ...], "fps": 24, "loop": True} for animation: the daemon plays
    # the frames itself (~30 fps cap), so build them once, cache in ctx.state, use a long interval.
    # See plugins/anim.py (orbit) for a template.
    ...

def open_forecast(ctx, **args) -> None:
    ...
```

`ctx` has:
- `ctx.key`, `ctx.page`: which key and page triggered the call
- `ctx.run(action_dict_or_list)`: run normal actions (`{"hotkey": "ctrl+c"}`, `{"open": url}`)
- `ctx.goto(page)`, `ctx.flash(face, seconds)`: change pages, briefly show a face
- `ctx.source`: who caused this call: `"device"` or `"api"` for a press, `"daemon"` for a live tick,
  `"preview"` for a one-off render of a page the pad is not on (`ctx.preview` is then True and ring, modal,
  goto, run and cards calls do nothing; skip starting threads there).
  Check it before doing something only a human should trigger (or use `physical_only` on the key).
- `ctx.modal(faces, on_key=fn(ctx, key, down, t), timeout=120)`: take the pad over; `faces` is
  `{key: face}`, other keys go blank, every press goes to `on_key` (not to actions). Ends on `timeout`
  (max 120 s), `ctx.end_modal()`, or a physical b1 press. Raises if dark mode is on or another owner's
  modal is up.
- `ctx.rgb(color, brightness=None, ttl=None)`: set the ring; with `ttl` the page's colour returns after.
- `ctx.leds(colors, ttl=None, brightness=None, owner=None, priority=1)`: per-LED frame (24 `"#rrggbb"`/None).
  One owner at a time, latest wins, lower priority never replaces higher; see api.md. `ctx.leds_release(owner)`
  gives the ring back. `m18lib.ringmap` has the position map and frame helpers (`meter_frame`, `arc_sizes`).
- `ctx.modal(...)` returns a token; `ctx.modal_update(token, faces=None, timeout=None)` redraws or re-arms that
  modal (use it from `on_key`: a self-closing menu re-arms its timeout on every press).
- `ctx.pressed_at` (when this key went down) and `ctx.wait_release(timeout)` (block until it is up; True/False)
  let a plugin ACTION on a key without `hold:` (it runs at key down) follow a hold as it happens.
- `ctx.cards`: post / remove / list cards for the card key (`post(card, handler="mod.fn")`); see cards.md.
- `ctx.state`: a dict per key that survives reloads (cache tokens, counters, last values here)
- `ctx.log(...)`: writes to `m18d.log`; `m18ctl status` shows the tail
- `ctx.theme`: the active theme's PALETTE (`{}` without one). `ctx.theme_fn("meters")`: a drawing helper
  the theme offers, or None (`meters(rows, key=ctx.key)` draws `[(name, percent), ...]` as an RGBA
  tile in the theme's style; see `plugins/sysstats.py`). Return RGBA images so the theme shows through.
- `ctx.config_dir`, `ctx.daemon` (escape hatch; `ctx.daemon.live_next.clear()` forces an
  immediate refresh of all live keys)

Rules of thumb:
- **Never hang.** Each live key runs in its own worker (8 total) and skips ticks while its last
  call is still running, so a slow key only slows itself. Still use timeouts on network calls
  (≤ 2 s) and cache slow results in `ctx.state`. `interval` can go down to 0.03 s.
- **Fail soft.** If an exception escapes, the key shows "plugin err" and retries in 10 s.
  Better to return a gray "offline" face yourself.
- **Secrets** go in `plugins/<name>.json` or environment variables, not in macros.yaml.
- Extra pip packages: `$M18\.venv\Scripts\python -m pip install <pkg>`.
- Test without the pad: `m18ctl preview` calls each live function once and draws the result.
  `m18ctl press N` against a `--mock` daemon runs plugin actions.
- Use `from m18lib.render import font` for the same fonts the daemon uses.

## Bundled plugins

| plugin | what it does |
|---|---|
| `sysstats.tile` | CPU / RAM / GPU bars on one key (NVIDIA via NVML or nvidia-smi). `args: {show_vram: true}` adds VRAM |
| `ring.status` | live; starts one background thread that draws agent sessions on the ring from `state/sessions-summary.json` `{working, waiting, finished}`: three arcs sized by share (green/amber/aqua in the theme's good/warn/aqua; the waiting arc breathes at 0.4 Hz only while waiting > 0; strips show the most urgent state; never red). Absent or all zero = ring released. Ambient priority. Put it on any key: `live: {plugin: ring.status, interval: 5}` |
| `ring.meter` | action/helper: `args: {fraction, color, stop_at, ttl}` fills the ring clockwise from the top-left, optional marker LED |
| `ring.hold_progress` | action for a key without `hold:`: ring sweeps 0 to full over `args.seconds` (default 1) while held; early release cancels, completion flashes and runs `args.then`. Use with `physical_only: true` for "hold to confirm" |
| `ring.codex_meter` / `ring.codex_face` | ring arc / key face for the Codex weekly limit window, read from the newest `~/.codex/sessions` rollout (`rate_limits.primary.used_percent`; no model call). Marker at 90 %. The value is as of Codex's last turn |
| `mixer.open` | action for the Vol keys: modal volume mixer (needs `pycaw comtypes` in the venv); layout follows the apps making sound, closes 2.5 s after the last press, b1 closes at once. Details in the file header |
| `cards.key` / `cards.open` | the card key: live face = top card (count badge at 2+), press = takeover with the card's words and action keys. Store, API and `ctx.cards`: references/cards.md |
| `clip.status` / `clip.save` / `clip.pick` | clip the last 30 s of the screen with OBS. The pad owns an OBS profile + scene collection "Pad Clips" (built by `scripts/m18/obs_pad_clips.py`: replay buffer 30 s, NVENC mp4 to `%USERPROFILE%\Videos\PadClips`, one Display Capture scene per monitor): no OBS setup. Tap with OBS off = arm (starts OBS minimised; the key shows ARMED, then FILL 12s while the buffer fills; a clip exists only after it has run); tap while armed = save and post a card (frame, duration, size; Keep / Delete / Copy path); OBS running another profile = face OTHER, nothing touched. Hold = takeover, one key per monitor (number + resolution, recorded one green) that switches the program scene. Websocket password read from OBS's own config. File header |
| `clipboard.face` / `clipboard.open` | live clipboard key: kind, size, thumbnail or first word; password-manager clips show HIDDEN and are never stored; press = last 10 clips (memory only) to restore + paste as plain text |
| `layout.face` / `layout.spread` / `layout.open` | live map of the monitors with the agent windows (Claude, ChatGPT/Codex) on each, green or amber; tap = spread them over the side monitors (least movement, halves/thirds); hold = per-monitor takeover with Even / By app, monitor on-off, Undo, Go. references/layout.md |
| `agents.slot` / `.press` / `.hold` / `.deck` / `.tick` | drop-in for the sessions keys that lets agents post on their own key through `pad.cmd`: status, progress (+ ring arc), 16x16 face, attention flash, temporary buttons whose press only reaches that agent. Quotas and rate limits in code. `.deck` (an action, e.g. hold on key 10) shows every agent with posts. references/agents.md |
| `sessions.slot` / `.press` / `.nudge` | agent session keys (zone of slots); a working session silent 11 min turns violet with its age; hold (`.nudge`) = "still going?" on the clipboard + the session opened; `args: {send: true}` types and sends it into the Claude or Codex app when that is safe (falls back to the clipboard otherwise). references/sessions.md |
| `claude_limit.face` / `.ring` / `.resume` | Claude plan limits: 5-hour and weekly fills (estimate from the app's own usage samples, grey when over 1 h old), countdown after a limit hit (from the transcript), green RESET after it; press = meter on the ring, hold after a reset = "It has reset now. Please continue." on the clipboard + the stopped session opened (`args: {send: true}` sends it, as sessions.nudge). `13: {live: {plugin: claude_limit.face, interval: 5}, action: {plugin: claude_limit.ring}, hold: {plugin: claude_limit.resume}}`. references/claude-limit.md |
| `nightshift.face` / `.tap` / `.hold` | away key: tap = away (`state/away.json`, PC kept awake), moon + one count per frame (finished, waiting, silent since then); tap again = back, tally under a sun for 10 min; hold = same, plus monitors off when going away. `14: {live: {plugin: nightshift.face, interval: 2}, action: {plugin: nightshift.tap}, hold: {plugin: nightshift.hold}}`. references/nightshift.md |
| `fishbowl.tile` / `.tap` / `.hold` | page mode: the tank is a normal page (`sets-tank.yaml`), every key `live: fishbowl.tile`; fixture keys keep their own action (`args: {fixture: lamp\|buoy\|gauge\|chest}` = lights, Scrawl, CPU/RAM/GPU, cards). Agents' status, progress, buttons and faces show on their creature; sleeps off-page, in dark mode and under a modal. references/fishbowl.md |
| `fishbowl.open` / `.face` / `.close` | modal mode: the pad becomes one aquarium; each pad session is a creature built from its sprite (state = behaviour, model = species, subagents = fry, last tool = what it does, crash = belly-up; water level and ice = Claude limit, reef = commits, night = away mode). Tap = name plate, tap again = open, hold = nudge, empty water = food; b1 leaves; ring = waterline with gold toward whoever needs you. `face` with `idle_min: N` opens it after N idle minutes. references/fishbowl.md |
| `scrawl.record` | action: presses Scrawl's record shortcut (Alt+Shift+S; the same press stops a capture). Starts scrawl.exe first when it is not running, since launching the exe alone never records. `args: {exe, hotkey}` |
| `discord_voice.status` / `.toggle_mute` / `.toggle_deaf` | real Discord mute/deafen state + control over local RPC. Needs the one-time `discord.json` setup in the file header |

## Animating whole keys yourself

A plugin that draws its own frames (the tank) writes them with `ctx.daemon.blit(page, {key: (image, digest)}, owner)`:
64x64 PIL images go straight to the pad, skipping the face pipeline, and only while the key really shows that
page's own face (not dark, no modal, no override, no pushed face). Keep `daemon.live_faces[(page, key)]` current
too, so a normal redraw shows the same picture. Measured on the real pad: 235 key writes/s at 5 % of one core.

## Recipes

**Discord mute.** Two options; offer the first:
1. `discord_voice` plugin (accurate state, toggles directly, no keybind). The user creates a
   Discord developer app once and puts client_id/secret in `plugins/discord.json` (steps in
   the plugin header).
2. Keybind fallback, no setup: Discord's default *Toggle Mute* is `ctrl+shift+m` (check
   Discord → Settings → Keybinds). Use `states:` for a red/green face, and tell the user it
   can drift if they mute with the mouse.

**OBS (scenes, record, stream).** OBS 28+ has obs-websocket built in (Tools → WebSocket Server
Settings, port 4455, password). `pip install obsws-python`, then:
```python
import obsws_python as obs
def _c(ctx):
    if "cl" not in ctx.state:
        ctx.state["cl"] = obs.ReqClient(host="localhost", port=4455, password=PASSWORD, timeout=2)
    return ctx.state["cl"]
def scene_face(ctx, scene, **_):
    cur = _c(ctx).get_current_program_scene().scene_name
    return {"label": scene, "icon": "mdi:monitor", "bg": "#a01818" if cur == scene else "#222"}
def set_scene(ctx, scene, **_): _c(ctx).set_current_program_scene(scene)
def toggle_record(ctx, **_): _c(ctx).toggle_record()
```

**Now playing / album art.** Windows' media session API (`pip install winsdk`):
`GlobalSystemMediaTransportControlsSessionManager.request_async()` → current session →
`try_get_media_properties_async()` gives title, artist and a thumbnail stream. Render the
thumbnail as `{"image": ...}`. It's async (asyncio), so cache the result and refresh it every 2-3 s.

**HTTP status / home lab.** `urllib.request.urlopen(url, timeout=2)`. Green if 200, red otherwise.
Good for checking that a Tailscale site or local service is up.

**Per-app pages without plugins.** Use `follow: [app.exe]` on a page. Get the exe name from Task
Manager → Details.
