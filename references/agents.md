# The pad for agents

The M18 under the user's main monitor is yours too. Your key is your session key (keys 6, 7, 9 on
the main page). With no session on the pad you get a free slot, or a place in the deck (hold
key 10). Everything goes through one command, `pad`, which prints one JSON line.

```
%USERPROFILE%\.agents\skills\m18-macropad\scripts\m18\pad.cmd <command> ...
```

## Use it only when it beats a window

Each use must pass at least one of these tests: it shows state without a window, it works
while another window has focus, or it is faster than a click. Good uses: progress on a long
job, a physical STOP for a run, "done, look" after 20 minutes of work. Bad uses: a question
The user can answer on screen (`pad ask` is not a picker for chat questions), chatter, decoration.

## Commands

| command | does |
|---|---|
| `status TEXT [--tone info\|ok\|warn\|err] [--ttl S]` | a word or two on your key's plate (8 chars) |
| `progress 0.62\|62%\|12/40 [--ring] [--status T]` | a bar on your key; `--ring` also fills the ring arc in your colour |
| `face --rows R1/R2/.../R16 [--palette 1=#hex] [--label W] [--color #hex] [--fps N]` | your own 16x16 pixel face (or `--file f.json`, up to 4 frames) |
| `flash` | attention: pulsing border plus a ring pulse, until the user taps your key |
| `button add ID --label WORD [--color #hex] [--sticky] [--ttl S]` / `button rm ID` | a physical button on your key; a press only sends you an event |
| `wait [IDS] [--timeout S] [--seen]` | blocks until the user presses one of your buttons (exit 3 on timeout) |
| `inbox [--keep] [--check ID]` | reads pressed buttons; `--check ID` exits 10 if ID was pressed |
| `ask TITLE --line TEXT --action id:LABEL ... [--wait S]` | a card with up to 4 actions on the card key |
| `clear` | removes everything you posted |
| `whoami` | your id, colour, where you are shown, quota left |

Global flags: `--as ID` (default: `M18_AGENT_ID`, else `CLAUDE_CODE_SESSION_ID`, else
`CODEX_THREAD_ID`), `--title "few words"` (shown when you are a guest), `--port`.

Exit codes: 0 ok, 2 bad input, 3 timeout, 4 refused (quota, rate limit), 5 gone, 10 pressed (`inbox --check`).

## Patterns

A physical stop for a long run (Claude Code: start the wait with `run_in_background`, so a
press wakes you; between steps you can also poll):

```
pad progress 0/40 --ring --status TEST
pad button add stop --label STOP --sticky --color #cc3a30
pad wait stop --timeout 3600        # in the background
pad inbox --check stop              # between steps: exit 10 = the user pressed STOP
pad clear                           # when finished
```

Done after a long job: `pad status DONE --tone ok`, then `pad flash` only if the user should look now.

A face is 16 rows of 16 characters: `.` is transparent, letters are the theme palette
(K W G S D R O Y L E C B P N T), digits 1-6 are your own colours. One idea per key: a word, a
number or a picture. Never spread a sentence over several keys; keep `ask` titles to one word.

## Rules (enforced in code)

- Per agent: 3 buttons, 2 open cards, 600 ring-seconds per hour, 4 face frames, labels of 6
  chars, button words of 8. Over quota = exit 4 with the reason.
- Flash: one per 2 min per agent, 6 per hour, one per 15 s on the whole pad; it ends after 10 min.
- Posts expire after their ttl (default 2 h, max 8 h) and vanish when your session ends.
- You can't touch the user's own keys or post with `/face`. The b1 button always takes him home
  and closes any takeover.
- Nothing you post is run or typed. Presses made by software never count; only the device does.
- Your ring arc gives way to anything else that holds the ring.

## Files (for reading; write through `pad`)

`%M18_HOME%\state\agents\<id>.json` is your doc: `{id, title, color, status{text,tone,until},
progress{value,ring,until}, face{frames,palette,label,fps}, buttons[{id,label,color,sticky,until}],
asks, waiter{pid}, updated}`. `<id>.inbox\*.json` holds button events
`{type: agent_button, agent, button, label, source, key, t}`. `_pad.json` is the plugin's ledger.

## Setup

The plugin is `plugins/agents.py`, a drop-in for the sessions keys. In `macros.yaml` swap
`sessions.slot` / `sessions.press` / `sessions.nudge` on keys 6, 7, 9 for `agents.slot` /
`agents.press` / `agents.hold` (same args), and add `hold: {plugin: agents.deck}` to key 10.
Check with `python scripts/m18/agents_check.py` (mock daemon on port 8742).
