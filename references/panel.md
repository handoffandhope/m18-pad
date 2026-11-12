# M18 panel (local web app)

A small page in front of m18d: see the pad, tap a key to read what it does, switch button sets and
themes in one click, and type a request ("make key 4 open my bank's site") that a headless Claude Code
agent carries out. Code: `scripts/m18/panel/` (Python stdlib + the daemon's venv; no npm, no CDN).

**Start:** `scripts\m18\panel\start-panel.cmd` (hidden pythonw, detached), then open
<http://127.0.0.1:8725>. A second start exits by itself (the port is taken). Log and state:
`$M18\state\panel\` (`panel.log`, `undo.json`, `requests\<id>\` with before/after yaml and pictures).

## What it does
- **Pad:** the daemon's own `GET /preview?page=<name>` for any page the daemon knows, refreshed on every
  press event (it long-polls `/events`) and every 10 s for live tiles. The preview never switches or writes to
  the pad (api.md "Preview"). A page the daemon does not know, or a stopped daemon, is rendered offline from
  the file (`render_set.py`).
- **Sets:** `$M18\sets\<name>.yaml` (a whole macros.yaml) + `<name>.png` thumbnail. First start seeds
  `everyday` (a copy of the macros.yaml it found) and the starters `focus` and `media` from
  `panel/starters/` (marker `sets\.seeded`). Apply = validate with the daemon's loader
  (`m18lib.config.validate`, base `$M18`, what `m18ctl validate` runs), copy the current file to
  `sets\_previous.yaml` (shown as the "Previous" card), write macros.yaml atomically; the daemon hot-reloads.
  "Save current as…" copies macros.yaml into a new set.
- **Themes:** hillside / vaporjazz / wabisabi / plain. Edits only the `settings.theme` and `settings.rgb`
  lines (comments kept, rgb brightness kept; plain removes the theme line).
- **Undo** (header button, and on each request result): one level, survives restarts, restores the file
  from before the last change. It refuses when macros.yaml changed since (apply Previous instead).
  A request's plugin edits are listed but not undone.
- **Thumbnails and before/after pictures** are offline renders. Live keys there show stand-ins
  (sessions slot = "Agent", ring.status = "Agents"; others show their base face) except `sysstats.tile`
  and `ring.codex_face`, so a thumbnail never touches Discord, Haiku or the LED ring.

## Request box
One request at a time; Stop kills the child (`taskkill /t /f`). Same pattern as Shelf Life's
`site-agent-runner.mjs`:
`claude -p --model claude-sonnet-5-5 --effort high --output-format stream-json --verbose
--include-partial-messages --strict-mcp-config --permission-mode dontAsk --permission-prompts none
--no-session-persistence --add-dir <this skill> --allowedTools Read Write Edit MultiEdit Glob Grep Bash
PowerShell TodoWrite WebSearch WebFetch --append-system-prompt <panel rules>`, cwd `$M18`, prompt on stdin.
Env: API-key and parent-session variables removed; `CLAUDE_CODE_OAUTH_TOKEN` read from Credential Manager
target `Claude/M18/OAuth` (the same one the session keys use) and set only in the child env; also
`M18_HOME`, `M18_CONFIG`, `M18_PORT`, so `m18ctl.cmd` hits the right file and daemon. The system prompt
limits edits to macros.yaml, plugins/ and sets/ and forbids stopping the daemon; these are instructions,
not enforced by the tool allowlist (Bash/PowerShell are allowed, as in the runner). After the run the
panel validates the new file (puts the old one back if it fails) and renders before/after for every page
whose keys changed, with the changed keys outlined.

No token yet, or it expired: run `scripts\m18\panel\panel-login.ps1` once on the PC (`claude setup-token`,
paste; it checks the token works). Without a token the child falls back to the CLI's own login.

## Security
Binds 127.0.0.1 only. Every request needs `Host` = `127.0.0.1:8725` or `localhost:8725` (DNS rebinding).
Every POST needs `Origin` = that same origin **and** `X-Panel-Token` = a random token made at start and
embedded in the served page; OPTIONS is always 403 (no CORS). The page has a strict CSP and
`frame-ancestors 'none'`.

## Start at login (not registered)
Per the always-on-local-app skill: a Startup-folder launcher with a hidden window, written from a
WMI-created process (Claude desktop is MSIX and redirects `%APPDATA%` writes). `panel/start-panel-hidden.vbs`
runs start-panel.cmd with no window; put a shortcut to it in the Startup folder (command not yet run or tested):

```powershell
$cmd = "powershell -NoProfile -Command `"`$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\M18 Panel.lnk'); `$s.TargetPath='wscript.exe'; `$s.Arguments='`"`"$env:USERPROFILE\.agents\skills\m18-macropad\scripts\m18\panel\start-panel-hidden.vbs`"`"'; `$s.Save()`""; Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
```
Check: stop the panel, run `wscript start-panel-hidden.vbs`, then `curl http://127.0.0.1:8725/api/state`
and count `server.py` processes (one venv launcher + its child interpreter).

## Env (tests)
`M18_HOME` (folder with macros.yaml, sets, themes, plugins), `M18_PORT` (daemon, default 8718),
`M18_PANEL_PORT` (default 8725), `M18_PANEL_TOKEN_TARGET`, `M18_PANEL_CLAUDE`, `M18_PANEL_MODEL`.
Test against a copy: `python m18d.py --mock --port 8734 --config <copy>\macros.yaml` with `M18_HOME=<copy>`,
then start the panel with `M18_HOME=<copy>` and `M18_PORT=8734`.
