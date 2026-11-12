# macros.yaml reference

Contents: [settings](#settings) · [pages](#pages) · [keys](#keys-and-faces) ·
[actions](#actions) · [key names](#key-names) · [patterns](#patterns)

## settings

| field | type | meaning |
|---|---|---|
| `home` | page name | start page; target of `page: home`. Defaults to the first page |
| `brightness` | 0-100 | LCD brightness |
| `rgb` | `"#hex"` / `{color, brightness}` / `off` | ring LED default; yields to a plugin/API per-LED owner and returns when it releases (api.md) |
| `hold_ms` | int 100-5000 | long-press threshold (default 450) |
| `double_ms` | int 80-1000 | how long a key with `double`/`triple` waits after a release for the next tap (default 250) |
| `follow_focus` | bool | enable `follow:` auto page switching (default true) |
| `default_bg`, `default_fg` | hex | theme for every key |
| `theme` | name / `{name, ...opts}` | pad-wide look from `$M18/themes/<name>/theme.py`; see `aesthetics/README.md` |
| `font`, `emoji_font` | path | override the TTF used for labels and emoji |
| `key_map` | `{raw: logical}` | remap physical key codes if `m18ctl identify` shows a mismatch (hardware presses only) |
| `log_raw` | bool | append every device event to `raw-events.log` beside `m18d.log` as `epoch_ms hwcode statebyte logical_key` (2 MB cap, one rotation). Default false. Use it to see whether the hardware sends duplicate press packets |

## pages

```yaml
pages:
  <name>:
    keys: {<key>: <keydef>, ...}
    inherit: <page>        # copy another page's keys first; this page overrides them
    follow: [app.exe, ...] # show this page while one of these exes is focused (lowercase name)
    rgb: "#hex"            # ring color while on this page
    brightness: 0-100
```
Key ids are `1`-`15` (LCD, row-major from top-left) and `b1` `b2` `b3` (or `16` `17` `18`) for
the physical buttons. A key set to `null` (`7: ~`) blanks an inherited key.

When a `follow` app loses focus, the pad returns to the page it was on before.
Navigation: `page: <name>` pushes onto a back stack, `page: back` pops it, and `page: home` goes to settings.home.

A common layout uses `b1` = home, `b2` = back, and `b3` for a per-page extra. Pages can also
work like folders: a key with `action: {page: obs}` opens a sub-page, and that page's `b2` goes back.

## keys and faces

```yaml
4:
  icon: mdi:microphone      # bundled Material Design Icon (m18ctl icons <word>), or an image path
  emoji: "🎙"               # alternative to icon; needs a system emoji font
  glyph: "3s"               # big text instead of an icon (auto-shrinks)
  label: Mic                # small text; wraps to 2 lines; keep ≤ ~10 chars
  label_pos: bottom         # bottom|top|center|none (center when there's no icon/glyph)
  bg: "#1f7a3a"             # background
  fg: "#ffffff"             # icon/glyph color (and label, unless label_color)
  label_color: "#b8b8cc"
  border: "#ffd400"         # outline ring, e.g. to show "armed"/"active"
  font_size: 13             # label size in native px
  action: <action>          # single tap (see "Gestures" below for when it fires)
  hold: <action>            # long press: fires at hold_ms while the key is still down
  double: <action>          # two taps within double_ms of each other
  triple: <action>          # three taps
  states: [<state>, ...]    # cycling toggle; each state = face fields + optional action
  live: {plugin: mod.fn, interval: 2, args: {...}}   # face from Python, refreshed
  physical_only: true       # action and hold run only for a real hardware press; api presses are refused
  comment: "free text, ignored"
```
### Gestures

- Only `action`: fires on key-down, exactly as before; nothing waits.
- `action` + `hold`: the tap fires on release before `hold_ms`; at `hold_ms` the hold fires while the key is
  still down, and that release does nothing more. Hold only counts on the first press of a sequence.
- `double` or `triple` present: after each release the key waits `double_ms` for another tap. `triple` fires at
  the third key-down; `double` fires at the second key-down when there is no `triple`, otherwise once the
  `double_ms` window after the second release ends. A sequence that stops early fires what it reached (one tap =
  `action`; two taps on a key with `triple` but no `double` = `action` twice).
- Taps further apart than `double_ms` are separate single taps. Each tap adds `double_ms` of delay to `action`
  on such a key, so give `double`/`triple` only to keys where that is acceptable.
- A sequence counts as a hardware press (`physical_only`, sends to sessions) only if every tap came from the pad.
  A page or config change drops a key that is down or mid-sequence.
- Chords (two keys at once) are not possible: the firmware reports one key at a time (limits.md).

Face priority: base fields < current `states` entry < `live` result < pushed face (`POST /face`) <
temporary overrides (countdown or error flash). A modal covers all of it while up (api.md).

Image icons: PNG/JPG, or an animated GIF, which plays at its own frame timing (up to ~30 fps). A relative path resolves against the config folder, so
keep them in `icons/`. They're scaled to fit, so square transparent PNGs look best.

## actions

An action is one dict, or a **list** run in order (a sequence/macro).

| action | value | notes |
|---|---|---|
| `hotkey` | `"ctrl+shift+m"` | press together, release in reverse. Optional `hold_ms` |
| `key` | `"enter"` | single key tap (same engine as hotkey) |
| `type` | `"text"` | typed as unicode keystrokes into the focused window; `\n` = Enter |
| `paste` | `"text"` | clipboard + ctrl+v; use for long text. Overwrites the clipboard |
| `run` | `"C:/path/app.exe --flag"` or list | starts detached; `%VARS%` and `~` expanded |
| `shell` | `"cmd line"` | via `cmd /c`, hidden window; pipes, `&&`, `start "" ...` |
| `open` | path / URL / URI | `os.startfile`: files, folders, `.lnk`, `https://`, `ms-settings:`, `discord://`, `steam://run/<id>` |
| `media` | `play_pause` `next` `prev` `stop` `vol_up` `vol_down` `mute` | system media keys |
| `page` | name / `back` / `home` | |
| `delay` | ms | wait inside a sequence |
| `countdown` | seconds | shows N..1 on the pressed key, then continues the sequence |
| `plugin` | `"module.function"` + `args: {}` | Python in `plugins/`; see plugins.md |
| `brightness` | `40` or `"+10"` / `"-10"` | |
| `rgb` | `"#hex"` / `{color, brightness}` / `off` | |
| `notify` | `"text"` | writes to the log |
| `dark` | `toggle` / `on` / `off` | "lights out": every other key goes black, ring off, all other keys and buttons ignored; the key that pressed it stays as a faint icon and wakes the pad |

Any step may add `repeat: N` (1-100).

Launching an app the robust way: `open:` its Start Menu `.lnk`, or `run:` the exe. Store/UWP
apps: `open: "shell:AppsFolder\\<AppUserModelID>"`. Find the ID with PowerShell
`Get-StartApps | ? Name -like '*name*'`.

## key names

Modifiers: `ctrl` `shift` `alt` `win` (also `lctrl` `rctrl` `lshift` `rshift` `lalt` `ralt` `rwin`)
Letters/digits: `a`-`z`, `0`-`9`. Function keys: `f1`-`f24`.
Navigation: `enter` `tab` `esc` `space` `backspace` `delete` `insert` `home` `end` `pageup` `pagedown`
`up` `down` `left` `right` `printscreen` `pause` `capslock` `apps`.
Media: `volume_mute` `volume_up` `volume_down` `media_play_pause` `media_next` `media_prev` `media_stop`,
`browser_back` `browser_forward` `browser_refresh`, `launch_calc`.
Punctuation: `; = , - . / ` [ \ ] '` or the names `semicolon` `equals` `plus` `comma` `minus`
`period` `slash` `backtick` `backslash` `quote`. Numpad: `num0`-`num9` `num+` `num-` `num*` `num/` `num.`.

**F13-F24** exist on no physical keyboard, so they never collide. When an app lets you bind
a custom global hotkey (OBS, Discord, ShareX, AutoHotkey, games), binding it to e.g. `ctrl+f13` is the
cleanest way to drive it from the pad.

## patterns

```yaml
# delayed rectangle snip, with a visible 3-2-1 on the key
1: {icon: mdi:content-cut, label: Snip 3s, action: [{countdown: 3}, {hotkey: win+shift+s}]}

# text expander
2: {icon: mdi:email-outline, label: Sig, action: {paste: "Alex\nExample Co\n(555) 010-0100"}}

# multi-step: open Run, launch something
3: {icon: mdi:console, label: Term, action: [{hotkey: win+r}, {delay: 250}, {type: "wt"}, {key: enter}]}

# folder-style sub-page
4: {icon: mdi:folder-outline, label: Stream, action: {page: stream}}

# toggle that tracks presses (can drift if changed elsewhere; prefer a live plugin when an API exists)
5: {label: Cam, states: [{icon: mdi:video, bg: "#1f7a3a", action: {hotkey: ctrl+f14}},
                        {icon: mdi:video-off, bg: "#8b1a1a", action: {hotkey: ctrl+f14}}]}

# tap vs hold
6: {icon: mdi:volume-plus, label: Vol, action: {media: vol_up}, hold: {media: mute}}
```
