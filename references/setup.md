# Setup, first run, troubleshooting

## Install (Windows)
```powershell
powershell -ExecutionPolicy Bypass -File <skill>\scripts\setup.ps1
```
This copies the daemon to `%USERPROFILE%\tools\m18`, creates a venv (pillow, pyyaml, psutil, numpy,
nvidia-ml-py, wmi, pywin32), clones VSDinside's official SDK into `sdk\`, writes `m18ctl.cmd`,
and adds a Startup shortcut so the daemon runs hidden at login (`-NoStartup` skips that).
Re-running it updates the code and keeps `macros.yaml` and the user's plugins.

## First run with the pad
1. Quit VSD Craft (tray → Exit) and disable its autostart. Only one program can own the device.
2. `m18ctl doctor`: every line should say ok and it should find `StreamDockM18`.
3. `m18ctl start`, then `m18ctl identify`. Each LCD key shows its number for 8 s. If 1 isn't
   top-left or the order is off, add `settings.key_map: {<number shown>: <number wanted>}`.
   Seen on the user's pad (2026-11-09): faces draw correctly but presses come back rows bottom-up
   (tapping 1 runs 11). Fix: `key_map: {1: 11, 2: 12, 3: 13, 4: 14, 5: 15, 11: 1, 12: 2, 13: 3, 14: 4, 15: 5}`.
   Test presses after identify; identify only checks faces.
4. Press each physical button and check `m18ctl status` → log shows `key 16/17/18`.

## Without the pad
`m18ctl start --mock` runs everything except USB. Presses come from `m18ctl press`, and
keystrokes and launches are logged instead of sent (`--live-actions` sends them for real).
`m18ctl preview --offline` renders without any daemon.

## Troubleshooting
| symptom | check |
|---|---|
| keys blank, `connected: false` | VSD Craft running? cable/port? `m18ctl doctor` |
| new key doesn't appear | `m18ctl status` → `config_errors` (bad save was rejected) |
| key does nothing in one app | that app is elevated (UIPI) or uses its own focus. Try the app's global hotkey or URI |
| key fires twice | key has both a tap action and an app hotkey bound to the same combo |
| plugin key says "plugin err" | `m18ctl status` log tail; fix the plugin and save, and it reloads |
| changed m18d.py, nothing happened | code changes need `m18ctl stop` + `m18ctl start` |

## Going back to VSD Craft
`m18ctl stop`, delete the Startup shortcut `M18 Macro Pad.lnk`, and start VSD Craft.
