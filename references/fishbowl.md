# Fishbowl

The whole pad becomes one aquarium. Every agent session on the pad lives in it as a creature coloured from its own
16x16 session sprite. The plugin is `plugins/fishbowl.py`, and the creature and fixture art is in `m18lib/fishart.py`.
Checks and previews are in `fishbowl_check.py`.

It runs two ways: as the home page itself (page mode, below), or as a takeover opened from one key (modal mode).

## Page mode: the tank is the home page

`scripts/m18/sets-tank.yaml` is a full config with the tank on `main`. Every LCD key is
`live: {plugin: fishbowl.tile, interval: 1}`. Four keys also carry a fixture: the art sits on the water, and the key
keeps its own `action`.

| key | fixture | shows | tap |
|---|---|---|---|
| 1 | lamp | warm light cone into the tank; faint bulb in dark mode | `{dark: toggle}` (Lights) |
| 5 | buoy | red record float bobbing on the surface ("REC"), frozen in ice | `scrawl.record`: Scrawl's record shortcut (Alt+Shift+S, start and stop); starts the app first when it is not running |
| 11 | gauge (filter) | CPU / RAM / GPU bars with numbers, orange at 75, red at 90; bubbles pour from its nozzle at 75+ | `hotkey ctrl+shift+esc` |
| 15 | chest | shut at zero cards, open with the count (red gem at priority 2+) | `cards.open` |

```yaml
1: {live: {plugin: fishbowl.tile, interval: 1, args: {fixture: lamp}}, action: {dark: toggle}}
2: {live: {plugin: fishbowl.tile, interval: 1}, action: {plugin: fishbowl.tap}, hold: {plugin: fishbowl.hold}}
```

- Water keys: tap a creature = name plate, tap again = open the session; tap a creature whose agent has parked
  buttons = the agents takeover for it (`agents._takeover`). Tap empty water = food.
- Hold a creature = nudge. Hold empty water = `agents.deck` if any agent has posted, else food.
- Live args (first key that sets one wins): `fps` (default 30, max 60), `source`, `night`, `gap`; on a fixture key `fixture`.
- One engine thread (`fishbowl.page`) steps the world at `fps` and writes only the keys whose picture changed
  straight to the pad with `daemon.blit` (no face pipeline per frame). It never renders the whole tank: the paint
  code runs against a recorder (`_Rec`), each key's list of draw calls is compared with the last frame's, and
  only a key whose list, ambient window or fixture art differs is composed (`Tank.dirty_faces`;
  `fishbowl_check.py --only displaylist` proves it pixel-equal to the full render on every frame). Bubbles move
  on a 15 Hz clock. The engine also keeps `live_faces` current, so any normal redraw shows the same picture. The
  daemon's 1 s live poll only keeps it alive. The ring gets 8 frames/s.
- It sleeps (no frames, ring released) when `main` is not showing, in dark mode, or while any modal is up, and
  resumes on return. CPU/RAM/GPU are sampled every 1.5 s (psutil, `sysstats._gpu`) only while the gauge shows.
- Creatures never park, wait, rest or speak on a fixture key, and the fixture's readable box
  (`fishart.READABLE`) is drawn on top of anything swimming through.
- Agents (`plugins/agents.py` docs, read through plugin state, never edited): `status` = a speech bubble in the
  tone colour, `progress` = a bar under the creature, a parked button = a gold bubble with its label, `flash` /
  attention = the creature comes to the glass, the agent's 16x16 face = the flag on a resting fish (or a small
  marking while it swims).
- Ring: the waterline while the tank shows. It yields when an agent ring (`progress.ring`, a flash) or any other
  non-ambient owner holds it.
- `tools` (b2) does not inherit `main`: it holds everything that moved off the home page.

Measured 2026-11-11 on the real pad with the user's own sessions, whole daemon, % of one core (first three rows:
process CPU time over 15 to 20 s; last row: thread cycle counters over 30 s; a reading moves about half a point
with what the creatures are doing):

| fps | renderer | % of one core | key writes/s |
|---|---|---|---|
| 5 | face pipeline, full render | 5.5 | 55 |
| 30 | blit, full render | 5.2 | 235 |
| 60 | blit, full render | 10.6 | 554 |
| 60 | blit, display list, 15 Hz bubbles | 5.3 to 5.8 | 160 to 185 |

At 60 fps the engine thread takes 2.9 to 3.5, the vendor DLL's own USB thread 0.8, the agents thread 0.45. Mock
(`python fishbowl_check.py --only page --secs 30`, 12 sessions, 30 fps, checker in the same process): 5.7 %. The
same check covers the pause rules, taps, the chest count, the card takeover and the ring yield. The cost
grows with the number of creatures and with what else the PC is doing: a later reading with a fuller tank on a
busy machine was 7 to 8 (engine 5 to 5.6), so compare versions with the fixed scenes below, not live readings.

Render work alone, fixed scenes, 60 fps, run flat out (`Tank.step` + `Tank.dirty_faces`, no JPEG, no USB):
4 sessions 0.9 % of one core, 12 sessions 1.9 % (two creatures with fry in each). Waking 60 times a second costs
about 1.5 times that (cold caches). One 64x64 JPEG costs 37 us to encode, so 200 writes/s is another 0.7 %.

What a person at the pad found (`python panel_probe.py`, firmware V3.VSDM18_HXJDF.02.020, 2026-11-11):
- The SDK's panel-frame call (`set_background_frame_stream`, any rectangle of the 480x272 panel) draws nothing
  on this firmware, on any layer. Per-key JPEGs are the only way to draw.
- Key writes do not back up. After 3 s of 180, 480 or 840 key writes a second, a signal sent down the same queue
  was answered as fast as after silence (median press 0.31, 0.30, 0.25 s against 0.33 s calm). The tank's 160 to
  250 writes a second are far inside that.

## Modal mode

```yaml
1: {live: {plugin: fishbowl.face, interval: 5, args: {idle_min: 0}}, action: {plugin: fishbowl.open}}
```

- `fishbowl.open` (action) takes the pad over. Physical b1 leaves.
  Args: `fps` (default 5, see Cost), `source` (`auto` | `sessions` | `summary`), `gap` (px, default `tiles.GAP`),
  `night` (`auto` | `on` | `off`).
- `fishbowl.face` (live) draws a small bowl with one dot per session, in gold when a session needs the user.
  While the bowl is closed, it also grows the reef once a minute.
  With `idle_min: N` it opens the bowl after N minutes without a key press (default 0 means off).
- `fishbowl.close` ends the bowl.

The bowl re-arms its modal every frame for 20 s. The daemon's 120 s cap applies to each arm, so the bowl stays up
until b1, dark mode, or a crash (then it ends at most 20 s later). It opens its modal with `yields=True`, so
any other owner's takeover (a card, an agent's buttons, an API prompt) replaces it and the bowl stops within a frame.

## Layout: one tank, 15 windows

The world is `tiles.block_size(5, 3)` px, bars included (30 px measured, so about 45 % of the tank is behind bars).
Each screen is a habitat. The bars are for crossing and hiding.

| where | who |
|---|---|
| top row | the glass: a waiting session comes here, faces the user, at 1.5x size with a pulsing gold outline and a question or approval badge. A crashed session floats belly-up here. Keys 3, 2, 4, 1, 5 fill first |
| middle row | open-water swim lanes (horizontal, steady speed through the bars). Kelp in each screen holds a blocked session, tangled and jittering |
| bottom row | sand: a finished session rests with a green flag, one per screen. Readers nibble along the sand. The reef grows at the screen edges |
| vertical bars | a silent working session hides in a bar, pale, with only its eye peeking 5 px into the screen on the right, and a "z" rising above it |

Resting and waiting creatures, plates and badges always sit fully inside one screen.

## What each visual means, and where it comes from

| visual | fact | source |
|---|---|---|
| swim (working) / glass, facing out, gold (waiting) / tangled in kelp (blocked) / resting on the sand with a flag (finished) / behind a bar, one eye showing (silent) / belly-up (crashed) | session state | `sessions.scan` (same as the session keys), falling back to `state/sessions-summary.json` |
| hatching egg, swimming off the side | session appeared / ended | roster diff |
| species, size and speed | model: Opus and Fable a large round fish, slow; Sonnet a mid fish; Haiku a tiny darting tetra; unknown a goldfish | last assistant `message.model` in the Claude transcript |
| moon jelly, mola | Codex model: `luna`, `sol` (another Codex model is a goldfish) | `turn_context.model` in the rollout (tail, then the head) |
| body and pattern colours | identity: the session sprite's two most used colours, else a hue from the id; pattern kind from the id (stable per id) | `state/session-faces/<id>.json` |
| fry schooling behind the parent | one per subagent transcript written in the last 60 s (max 6) | `<session>/subagents/*.jsonl` mtimes (Claude only) |
| nibbling the sand / carrying a block / bubble bursts / out through the surface and back | last tool call within 90 s: read (Read, Grep, Glob), edit (Edit, Write), run (Bash, PowerShell, Codex `exec`), web (WebFetch, WebSearch, browser tools) | transcript tail |
| belly-up, red x | the last record is an API error other than `usage_limit_reached`; a tap acknowledges it (saved) | transcript tail |
| water level and murk | 5-hour Claude usage (`fh`): the surface drops up to 22 px, staying inside the top row | `state/claude-limit.json` (ignored when over 6 h old) |
| ice | a limit hit until `resets_at`: thickness = share of the window still to wait, cracks as it thaws; all motion stops | `state/claude-limit.json` |
| reef blocks | one per successful `git commit` a session ran, in the session's colour; mossy with age, gone after 7 days, cap 120 | Claude: `tool_use` + a non-error `tool_result`. Codex: any `exec` call containing `git commit` (success not checked) |
| keepsakes | an ended session's 16x16 sprite, 1 px per cell, on the sand for 3 days (max 5) | roster diff while the bowl or the bowl key runs |
| night | `state/away.json` exists, or 21:00-06:00 local: water at 28 %, creatures get a soft glow in their own colour | file + clock |

Not shown, because there is no honest source: subagents of Codex sessions, and Codex crashes beyond `error` /
`stream_error` events (that event shape is unverified on real rollouts).

State: `state/fishbowl-reef.json` (blocks, keepsakes, acknowledged crashes). Facts come from the last 256 KB of
each transcript, cached on mtime and size. A commit is caught only while it is in that tail, so the reef is complete
only when `fishbowl.face` is on a key (it scans every 60 s while the bowl is closed).

## Keys and ring

Taps on keys 1-15 act on release.

- A tap on a creature (on its key or half behind the bar next to it) shows its name plate for 3 s: label plus what
  it is doing (WORKING, READING, EDITING, WAITING, BLOCKED, DONE, QUIET, CRASHED).
- A second tap while the plate shows opens the session (`sessions.open_session`).
- A tap on a belly-up creature acknowledges the crash.
- A hold (>= `settings.hold_ms`) on a creature nudges it, using the same calls `sessions.nudge` makes:
  `sessions.put_clipboard("still going?")` plus `sessions.open_session`. `sessions.nudge` itself picks by slot, so
  the bowl calls its two parts by name.
- A tap on empty water makes a ripple and drops food; swimmers chase it.

The ring is the waterline: a slow blue shimmer (icy when frozen, dim at night). It breathes gold on the LEDs
nearest each creature that needs the user, and the front strips turn gold while any does. It uses priority 1 and yields
explicitly: the bowl draws only while the ring is free, its own, or held by the ambient `ring.status`.

## Cost (modal mode)

Measured 2026-11-11 with `python fishbowl_check.py --only cost --secs 130`. The repo daemon runs in-process on a mock
device that JPEG-encodes every face like the real one (USB time itself is not included). Setup: 12 sessions,
hillside theme, GAP 30.

| fps | CPU, % of one core | face writes/s | ring frames/s |
|---|---|---|---|
| 5 (default) | 8.2 (draw 1.0, daemon redraw + JPEG 6.3, ring 0.1) | 64 (4.3 per key) | 5 |
| 6 | 10.1 | 71 | 6 |
| 8 | 12.4-15.3 | 95 | 8 |

With no bowl up, the daemon idles at 0.3 %. The table was measured while the daemon re-resolved the
theme path for every key on every redraw (about two thirds of the redraw cost, profiled). `render.theme_path` now
caches that for a second; the table has not been re-measured since, so 8 fps under 10 % is a prediction.

The bowl outlived the 120 s modal cap (130 s run), and physical b1 ended it and released the ring.

Checks: `python fishbowl_check.py` runs mapping (states to postures, identity), bar geometry, tap/hold hit tests,
transcript facts, the home-page layout (fixtures never covered, no parking on them), environment and reef, and
writes previews to `%TEMP%\m18tank\preview`. `--only tank` writes the page-mode previews and the species sheet,
`--only page` the daemon run above, `--only cost` the modal table. Previews are judged by eye, not by the script.
