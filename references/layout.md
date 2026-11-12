# Layout key: agent windows onto the side monitors

`plugins/layout.py`. One key that shows where the agent windows sit and puts them back on the side monitors.

```yaml
9: {live: {plugin: layout.face, interval: 2}, action: {plugin: layout.spread}, hold: {plugin: layout.open}}
```

## The desk it was built on (an example)
| monitor | where | DPI | role |
|---|---|---|---|
| main | 0,0 2560x1440 | 96 | primary = main monitor |
| side 1 | left of main, 1920x1080 | 120 | side |
| laptop | left of side 1, a virtual screen streamed to a laptop | 120 | side |
| tablet | above the laptop, a virtual screen streamed to a tablet | 168 | side |

Monitors show as "Mon N" until you name them in `plugins/layout.json`:
`{"names_by_id": {"ABC1234": "Left"}, "names_by_parent": {"ROOT\\DISPLAY\\0001": "Tablet"}}` (hardware id from
Device Manager; `names_by_parent` is for virtual screens). Names follow the device, so DISPLAYn renumbering is fine.

## What counts as an agent window
The `AGENTS` table at the top of the plugin: (group, process regex, title regex), first match wins.
Observed: the Claude desktop app is one top-level window (`claude.exe`, title "Claude"; its sessions are
inside it), and ChatGPT.exe (MSIX `OpenAI.Codex`) is one window for ChatGPT and Codex. Browser tabs
(chatgpt.com, claude.ai) and terminals (Claude Code, Codex CLI) match by the window title, which is the
active tab's only. Owned windows, tool windows, cloaked windows (other virtual desktops) and windows with no
maximise box are never touched.

## Face, tap, hold
- **Face**: the monitors on a small grid in their desk arrangement (the tablet above the laptop, then side 1, then main
  with a white stand = main). Each agent window is a strip: orange Claude, teal ChatGPT/Codex. Green outlines
  = nothing to do. Amber marks the monitors involved when an agent window is on the main monitor or on a
  switched-off monitor, or when a spread would move one. Grey = no agent windows.
- **Tap**: spread with the saved mode. Never moves the window that has focus on the main monitor.
- **Hold**: takeover. One key per monitor, placed like the desk (keys 2, 7, 8, 9 here), drawn as it will
  look AFTER Go (amber outline = changes). Row 3: **Even** (11), **By app** (12, Claude left, ChatGPT
  right), **Undo** (14), **Go** (15). A monitor key switches that monitor off or on for spreading (a
  laptop's virtual screen stays on the desktop when the laptop is shut). Go also moves a focused agent window
  off the main monitor. Closes 15 s after the last press; b1 closes at once. Mode and switched-off monitors
  persist in `state/layout-prefs.json`.

## Placement rules
Even shares over the live side monitors (counts differ by at most one; the larger shares go to monitors
already holding the most), windows stay on their monitor while it is within its share, the rest fill left to
right. 1 window = maximised, 2 = halves, 3 = thirds (columns on landscape screens, rows on portrait). Cap 3 per
monitor; more windows than 3 x side monitors stay where they are. By app picks the monitor split with the
least overflow, then closest to the window ratio, then fewest moves.

## Moves and undo
- Old placements go to `state/layout-last.json` before every move; Undo restores them (minimised and
  maximised states included) and renames the file to `layout-last.undone.json`, so a second Undo does nothing.
- The plugin runs its thread per-monitor-DPI-aware v2 (physical pixels) and uses SetWindowPlacement for
  every step. Gotcha found while testing: SetWindowPos on a DPI-unaware window whose outer rect (with the
  invisible ~9 px borders) crossed into a neighbouring monitor grew it to ~1.8x; such windows get their outer
  rect clamped to the monitor, so their visible frame sits a border width inside the tile.
- Maximising activates the window; focus is given back to the window that had it.
- Elevated windows are skipped (a normal process cannot move them).
- A `--mock` daemon or `M18_LAYOUT_DRY=1` moves nothing and logs the plan. `M18_LAYOUT_SNAPSHOT=<json>` feeds
  a recorded snapshot instead of the live windows.

## CLI (read-only)
```
python plugins/layout.py plan [--mode even|app] [--snapshot f.json]
python plugins/layout.py snapshot out.json
```

## Not handled
- A laptop that is switched off is not detected (its virtual screen stays); switch it off in the takeover.
- Claude desktop sessions are not separate windows, so with one Claude and one ChatGPT window there is
  little to spread; browser tabs and terminal sessions are what multiply.
