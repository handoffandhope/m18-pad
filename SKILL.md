---
name: m18-macropad
description: Program the user's VSDinside VSD M18 macro pad (15 LCD keys + 3 buttons + RGB ring) by editing its macros.yaml and driving the m18d daemon with m18ctl. Use this whenever the user wants a new keybind, macro, shortcut, button, key icon, live-status key, page/layer, or LED color on their macro pad, stream deck, StreamDock, M18, or "the pad", even if they just say "make a key that..." or "add a button for...". Also use it for setting up, troubleshooting, or previewing the pad.
---

# M18 macro pad

The user's VSD M18 is driven by **m18d**, a small Python daemon built on VSDinside's official
device SDK. It **replaces VSD Craft**: one YAML file decides what every key shows and does, and
the daemon reloads it on save. Your job is to turn a plain-language request ("a key that mutes
Discord") into a working, tested key with a good icon, without breaking the existing keys.

## Where things live

| Thing | Path (Windows) |
|---|---|
| Install dir (`$M18`) | `%USERPROFILE%\tools\m18` (or `$env:M18_HOME`) |
| Config, single source of truth | `$M18\macros.yaml` |
| CLI | `$M18\m18ctl.cmd <command>` (or `$M18\.venv\Scripts\python $M18\m18ctl.py`) |
| Custom Python for live keys and actions | `$M18\plugins\*.py` |
| Image icons | `$M18\icons\` |
| Log | `$M18\m18d.log` |

If `$M18` doesn't exist, the daemon isn't installed yet. Read `references/setup.md` and run
`scripts/setup.ps1` from this skill.

## Physical layout

```
 [ 1][ 2][ 3][ 4][ 5]      15 LCD keys (64x64 px each), row-major
 [ 6][ 7][ 8][ 9][10]
 [11][12][13][14][15]
  (b1)   (b2)   (b3)       3 physical buttons, no screen; config keys b1/b2/b3 = 16/17/18
       RGB ring around the edge
```
Row-major is the SDK's logical order. If the user says a key lit up somewhere unexpected, run
`m18ctl identify` (shows each number on the pad) and fix with `settings.key_map`.

The keys are 15 separate small screens with a black bar between each pair; the preview PNG hides this.
So every key must read on its own: one idea per key (an icon, a number, or at most two short words),
never a sentence or a word continued onto the next key. A picture spread over several keys must drop the
strip behind each bar (`m18lib.tiles.block_size` / `split`), or lines look broken. User feedback, 2026-11-12:
agents forget the bars and it hurts readability and visual continuity.

## Workflow for "add a key / macro"

1. **Look before you write.** Run `m18ctl status` (daemon up? which page?) and `m18ctl keys --all`
   (what's taken). Put the new key on a free slot unless the user named one. Never silently
   overwrite an existing key; if the pad is full, ask or suggest a new page.
2. **Pick the most robust way to do the thing.** Roughly in order of preference:
   - the app's own **URI or CLI** (`open: "ms-settings:sound"`, `run: "code ."`): no focus
     problems, works from anywhere;
   - the app's **global hotkey** (Discord, OBS, ShareX, Snipping Tool have them). Look up the
     real default, and tell the user if it must be enabled in the app's settings;
   - **keystrokes into the focused window** (`hotkey`, `type`, `paste`) for in-app shortcuts;
   - a **plugin** (Python in `plugins/`) when you need logic, an API (OBS websocket, Discord RPC,
     HTTP), or live state on the key.
   When you're unsure of an app's shortcut or launch command, find it: search the web, check the
   Start Menu shortcuts (`%APPDATA%\Microsoft\Windows\Start Menu\Programs`,
   `%ProgramData%\Microsoft\Windows\Start Menu\Programs`), check the app's settings and docs, or
   `Get-Command`. Don't invent shortcuts.
3. **Make the face clear at 64 px.** Use `icon: mdi:<name>` from the bundled Material Design Icons
   (find names with `m18ctl icons <word>`) plus a short `label` (≤ 10 chars reads well). Use
   `glyph` for text like `3s` or `F13`, and `bg` for a color code (red = destructive or recording,
   green = go). Prefer mdi icons over `emoji`, which depends on the system emoji font.
4. **Edit `macros.yaml` surgically.** Change only the lines you need and keep the user's comments.
   Flow style (`12: {icon: ..., label: ..., action: ...}`) keeps one key per line.
5. **Validate:** `m18ctl validate`. It prints every error with its exact path. Fix and re-run
   until it says OK. (The daemon also rejects a bad save and keeps running the last good config,
   so a typo never bricks the pad, but the new key won't appear until it validates.)
6. **Look at it:** `m18ctl preview` writes a PNG of the whole pad. Open the image and check the
   icon reads well and nothing else moved.
7. **Test the action.** `m18ctl press <key>` simulates a press through the daemon and returns what
   was sent. Against the real daemon this really sends the keys or launches the app, so only
   press keys whose effect is harmless (open a URL, show a toast). For anything disruptive
   (closes windows, types into the focused app, mutes the call the user is on), use
   `m18ctl dryrun N [--page P] [--hold S]`. It copies the config without any `live:` keys to a temp
   folder, runs a throwaway mock daemon on a free port, presses the key and prints `sent` (the exact
   keystrokes, launches and page changes, none performed; plugin actions are only logged), then
   cleans up. The real daemon is never stopped or touched.
8. **Report in a line or two:** which key, what it shows, what it does, plus any one-time thing
   the user must do (enable a hotkey in Discord, set a Discord app ID). Mention when you couldn't
   verify something without the hardware.

## Config at a glance

```yaml
settings:
  home: main              # page shown on start; `page: home` goes here
  brightness: 70
  rgb: {color: "#7c3aed", brightness: 60}
  hold_ms: 450            # long-press threshold for `hold:`
  double_ms: 250          # how long a key with `double:`/`triple:` waits for the next tap
pages:
  main:
    keys:
      1: {icon: mdi:content-cut, label: Snip, action: [{countdown: 3}, {hotkey: win+shift+s}]}
      2: {live: {plugin: sysstats.tile, interval: 1.5}, action: {hotkey: ctrl+shift+esc}}
      3: {icon: mdi:web, label: Projects, action: {open: "https://example.com"}}
      4: {icon: mdi:microphone, label: Mic, states: [{bg: "#1d6b2a", action: {hotkey: ctrl+shift+m}},
                                                     {bg: "#8b1a1a", icon: mdi:microphone-off, action: {hotkey: ctrl+shift+m}}]}
      5: {icon: mdi:folder, label: OBS, action: {page: obs}, hold: {run: "C:/Program Files/obs-studio/bin/64bit/obs64.exe"}}
      b1: {label: Home, action: {page: home}}
  obs:
    follow: [obs64.exe]   # auto-switch to this page while OBS is focused
    inherit: main         # start from main's keys, override some
    rgb: "#ff2d55"
    keys: {...}
```

Action types: `hotkey`, `key`, `type`, `paste`, `run`, `shell`, `open`, `media`, `page`,
`delay`, `countdown`, `plugin`, `brightness`, `rgb`, `dark`, `notify`. A list of actions runs as a
sequence. Any step can add `repeat: N`, and `hotkey`/`key` can add `hold_ms`. Key fields:
the face (`icon` `emoji` `glyph` `label` `bg` `fg` `label_color` `border` `font_size`
`label_pos`), plus `action`, `hold`, `double`, `triple`, `states`, `live`, and `physical_only: true`
(the key's actions run only for a real hardware press, never for `m18ctl press` or the API; use it
for a human-consent key).

Gestures: a key with only `action` fires on key-down, never delayed. `hold` fires at `hold_ms` while
the key is still down (that press is not also a tap). A key with `double` or `triple` waits
`double_ms` after a release for another tap; `triple` fires at the third key-down, `double` at the
second when the key has no `triple`. Test with `m18ctl press <key> --taps 2`.

**Read `references/config.md`** for every field, all key names (F13-F24 work well as
"invisible" hotkeys for apps), and edge cases.

## Aesthetics (themes)

`settings.theme` gives the whole pad one designed look, and every key added later follows it.
The looks live in this skill's `aesthetics/` folder: **read `aesthetics/README.md`**, then the active
theme's `BRIEF.md` before designing keys, icons or plugin tiles. Built themes: **hillside**, vaporjazz, wabisabi.
To build a new aesthetic, follow the README's steps (brief, theme.py, true-size preview,
fresh-eyes judge).

## Scenes: a live picture across the page

When the user wants the whole page to be one animated picture driven by live numbers (a skyline,
weather, an aquarium), write a **scene**, not a plugin: one file with a `draw` function, fed by
named signals (`cpu`, `net`, `time_of_day`, ...). **Read `references/scene-card.md`**; it is the
whole manual, about one page, and it is all a small model needs. A page uses a scene with one line,
`pages: {city: {scene: city}}`. A number the PC does not offer yet becomes a signal file in
`$M18/signals/` (see the top of `m18lib/signals.py`): that is setup work for this PC, done once, and
every scene can then use it by name. Beyond the card (long press, a scene's own ring frame, page
options, a tap that returns a daemon action, list signals such as `agents`): the top of
`m18lib/scene.py`, with `scenes/fishbowl.py` as the worked example.

## Live keys and plugins

A `live` key calls `plugins/<module>.py:<function>(ctx, **args)` every `interval` seconds. The
function returns a face dict, either fields like `{label, bg, icon}` or `{"image": PIL.Image}`
for custom graphics. `action: {plugin: mod.fn, args: {...}}` runs Python on press. Plugins
reload on save. **Read `references/plugin-recipes.md`** before writing one: it has the helper
calls, two working skeletons (a stateful key, an animated scene across the page) and the commands
that test a plugin without a pad, so you don't need to read the daemon's source or the bundled
plugins. `references/plugins.md` has the full `ctx` API
and recipes for system stats, Discord mute state (local RPC), OBS (websocket), now-playing,
and HTTP checks.

When live state isn't available (no API), use `states:` to make a toggle that tracks presses.
Tell the user it can drift if they change the state another way, and offer the real
integration if one exists.

## Control API (push faces, events, takeover)

The daemon's localhost API also lets software draw on the pad (`/face`), read presses
(`/events`), take the whole pad over (`/modal`) and set the ring (`/rgb`). Every request needs the
header `X-M18: 1` (m18ctl sends it; browsers can't). **Read `references/api.md`** before using it.

## If you are an agent with something to show

The pad is yours to use for your own state: `scripts/m18/pad.cmd status|progress|face|flash|button|wait|ask|clear`
puts it on your session key, gives the user physical buttons that only send you an event, and fills
the ring with your progress. Quotas, rate limits and expiry are enforced; nothing you post is run.
**Read `references/agents.md`** (one minute) first. Use it for state the user should see without a
window or a physical go/stop, never for questions he can answer on screen.

## Things that bite

- **VSD Craft must not be running**, because it claims the device. `m18ctl doctor` checks this.
- **Elevated windows ignore synthetic keystrokes** from a non-admin daemon (Windows UIPI).
  System hotkeys like `ctrl+shift+esc` and `win+...` still work.
- **Config and plugin edits hot-reload; daemon code edits don't.** After changing `m18d.py` or
  `m18lib/`, run `m18ctl stop` and then `m18ctl start`. `python scripts/m18/e2e_check.py` checks the
  daemon on its own mock.
- **Paths in YAML:** use forward slashes or single quotes (`'C:\Program Files\...'`). In
  double-quoted YAML strings a backslash starts an escape.
- The 3 physical buttons have no screen, so their `label` only shows in previews and `keys` output.
- `m18ctl status` → `config_errors` shows why the last save was rejected, and `log` shows
  recent presses and plugin errors. Check it when the user says "the key does nothing".

`references/limits.md` lists hardware, speed and daemon limits; read it before promising
animation, LED effects or fast live keys. `references/creative.md` lists ways to use the pad to
its full potential (pad-wide images, animation, live dashboards, context pages); read it when
designing a layout or when the user asks what the pad could do. `references/setup.md` covers install, first-run calibration, autostart, and going back to VSD Craft.
