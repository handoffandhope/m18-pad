# Claude limit key (plugins/claude_limit.py)

```yaml
13: {live: {plugin: claude_limit.face, interval: 5}, action: {plugin: claude_limit.ring},
     hold: {plugin: claude_limit.resume}}
```

## Faces

- normal: two bars, "5H" and "WK", 10 pips each (green under 70 %, amber from 70 %), plate CLAUDE. Grey pips = the
  reading is over 1 h old; "?" = no reading for that window.
- hit: which limit ("5H" or "WK" in amber), the time to its reset (`h:mm`, or days as "3D"), plate LIMIT.
- reset: green slot with a check, plate RESET, for 4 h after the reset or until held.

Press = the meter on the LED ring for 6 s (5-hour fill, or the time left after a hit). Hold after a reset = "It has
reset now. Please continue." on the clipboard and the stopped session opened in the desktop app; the green face
clears. The user pastes. With `hold: {plugin: claude_limit.resume, args: {send: true}}` the text is typed and sent into
that session in the Claude app instead, with the guards and fallback in sessions.md "Nudging a session" (green SENT,
or amber PASTE and the clipboard route). Hold in any other state = press.

## Sources (all local files, read only; no web call, no token)

- Limit hits, exact (observed 2026-11-11 in the user's transcripts): when Claude Code hits a limit it writes an assistant
  record to `~/.claude/projects/<slug>/<id>.jsonl` with `apiError: "usage_limit_reached"` and `quotaLimits`
  `{status: "rejected", rateLimitType: "five_hour" | "seven_day", resetsAt: <epoch s>}` (the same object is under
  `apiErrorParams.rate_limit_info`). The plugin reads the last 1 MB of each transcript changed in the last 8 days once,
  then only appended bytes, every 30 s (cold scan of 143 files 135 ms, warm 58 ms). 8 hits found over the last month.
  Only rejections are written: transcripts hold no usage percentages.
- Fills, an estimate: `plan-usage-history.json` that the Claude desktop app keeps in its data folders
  (`%LOCALAPPDATA%\Claude-Data\<profile>\` and `~/.claude-instances/<id>\`): samples `{t ms, u: {fh: 5-hour %, sd:
  7-day %}}`. Observed: samples are hours to days apart and stop when the app is closed, so the value is "as of" the
  newest sample, not live. The plugin shows the 5-hour fill only if that sample is under 5 h old and no 5-hour reset
  came after it (same rule for the week); over 1 h old it is drawn grey. What the app samples from, and when, is not
  documented (inferred: the app's own usage call).
- Session to open: the desktop app's `claude-code-sessions/*/*/local_*.json` records map the transcript's session id
  (`cliSessionId`) to the app session (`claude://claude.ai/epitaxy/<sessionId>`).

Not used: the account usage endpoint (it needs the user's login token), statusLine (none configured).

## Files and switches

`state/claude-limit.json`: `acked` (reset times already held) and `last` (the current reading: state, type,
resets_at, fh, sd, as_of, stale) for other tools. Test switches: `M18_LIMIT_PROJECTS`, `M18_LIMIT_HISTORY`
(os.pathsep list), `M18_LIMIT_APP`, `M18_HOME`, `M18_LIMIT_DRY=1` (no clipboard, `state/opened.log` instead of opening).

## Check

`python scripts/m18/claude_limit_check.py`: fixture states (fresh, stale and old samples, 5-hour and weekly hits,
reset, a reset older than 4 h, a sample from before a reset, an appended hit), resume in dry mode, and a mock daemon
(port 8736: render, press = ring owner `claude_limit:<key>`, hold after a reset). Previews to `%TEMP%\m18keys\preview`.
Not verified: a real pad, how often the app writes new samples while it runs, a hit seen live.
