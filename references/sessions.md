# Session keys (plugins/sessions.py)

Each running Claude Code (desktop or CLI) or Codex session gets a key. The key shows the state, a
Haiku-made face and label; pressing it opens the session. One plugin file, no helper modules (the
daemon loads plugins by path).

## Config

Give a zone of keys; each key is one slot of that zone. The last slot becomes "+N" when more
sessions are on the pad than slots. Pressing "+N" opens a paged modal (14 sessions per page, key 15 = NEXT).

```yaml
7:  {live: {plugin: sessions.slot, interval: 1, args: {zone: [6, 7, 8, 9, 10], slot: 1}},
     action: {plugin: sessions.press, args: {zone: [6, 7, 8, 9, 10], slot: 1}}}
```

Repeat for slot 0..N-1 over the same `zone`. No sessions: `{}` is returned and the theme backdrop shows.
`args.silent_min` on `sessions.slot` changes the silence threshold (default 11, see "Silence").

Hold = "still going?" on the clipboard and the session opened (see "Nudging a session"):

```yaml
7:  {live: ..., action: ..., hold: {plugin: sessions.nudge, args: {zone: [6, 7, 8, 9, 10], slot: 1}}}
```

## What is on the pad

- working and waiting: always.
- finished: until pressed once, or 30 min.
- stale: never. Slots are stable: a session keeps its slot; a new one takes the lowest free slot.

Look (64x64 inventory slot, hillside theme or plain): working = navy with a "..." badge, static;
waiting = gold with a pulse (the only pulsing state); finished = grey, check badge; silent (working, but no
transcript output for 11 min) = violet with its silence age ("14M", "2H", capped at "9H") on a lavender plate, static.
Red is for errors only.
The waiting badge says what kind of waiting it is: "?" question (needs an answer or a choice), check mark
approval (wants a go-ahead for an action), "!" blocked (cannot continue until the user does something). Question
and approval pulse at 3 fps, blocked at 5 fps. Without Decisions the kind comes from the rules: pending ask or
a rule hit = "?", a registry "waiting" with nothing asked = check mark.

## Where state comes from

- Claude: `~/.claude/sessions/<pid>.json` (status busy/idle/waiting, authority), Claude desktop app records
  (title, archived), and the last 64 KB of `~/.claude/projects/<slug>/<id>.jsonl` as fallback
  (pending AskUserQuestion or a wrap_up with an ask = waiting). Subagent and headless (sdk-cli) runs are skipped.
- Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` located by the UUIDv7 start day, plus
  session_index.jsonl and thread-writer-locks. Subagents and `codex exec` runs are skipped.
- Only files modified in the last 2 h are read, scan cache 2 s, a 40-session cold scan is about 55-105 ms.
- Link: `codex://threads/<id>` for Codex; Claude opens the app session or focuses the CLI window by pid.
- `classify(last_text)` decides finished vs waiting from the final assistant text when no registry exists. It stays
  the primary call; Decisions can only escalate it and name the kind (see "Decisions").

## Files (%USERPROFILE%\tools\m18\state\)

- `sessions.json`: persisted slots. `sessions-summary.json`: counts per state (`working`, `waiting`, `finished`,
  `total`), `kinds` (waiting sessions per kind) and `sessions`: one row per pad session with `id`, `agent`
  (claude|codex), `title`, `state`, `kind` (question|approval|blocked|null), `has_recommendation` (bool, null when
  only the rules spoke), `urgency` (0-3, rules give 3 for waiting and 0 otherwise), `kind_src` (decisions|rules), `silent_s` (seconds since
the last output, rounded down to the minute; null unless working) and `silent` (bool). The top-level `silent` counts
silent sessions; they stay counted under `working` too, so `total` is unchanged.
  The LED ring and other tools read this file.
- `session-decisions.json`: Decisions answers cached per session id + last-message hash, and the day's token usage.
- `session-faces/<id>.json`: label + 16x16 icon. `_calls.json` rate limit (20 per hour), `_busy.lock` one call at a time.

## Silence

A working session is silent when nothing was written for `silent_min` (11) minutes. Last output = the newest of the
transcript's mtime, its last event timestamp and (Claude) any file under `<id>/subagents/`. A foreground Bash or
PowerShell call that is still running with a `timeout` extends the limit to that timeout + 60 s, so a 10-minute
build is not called silent at minute 11.

Why 11 (study 2026-11-11, read only, tails): gaps between transcript events inside a turn, in the 15 busiest Claude
sessions of the last 10 days (21.9 working hours) and 5 Codex sessions (1.3 h). Claude p50 0.6 s, p90 6.4 s, p99
75 s. After removing waits that are not work (AskUserQuestion open, compaction continuations of 136 and 384 min):
25 gaps over 5 min, 16 over 8, 4 over 10, 1 over 15. 10 of the 16 gaps over 8 min (9.6-10.0 min) were foreground
tool calls with a 10-minute timeout, which the timeout rule covers; the 4 over 10 min were 10.3, 12.2, 14.9 and 22.0,
so at 11 min the key would have gone violet 3 times in 21.9 working hours. Codex never went past 2.4 min (small sample). Limit: one person's sessions, in-sample, real hangs too rare to measure
recall.

## Nudging a session

Default (`sessions.nudge` without `send`): hold puts "still going?" on the clipboard and opens the session; the user
pastes. With `args: {send: true}` (also `text:` for other words) the hold types and sends it, through
`m18lib/sendtext.py`:

1. Refuse unless `ctx.source == "device"` (an API or `m18ctl` press never sends) and nobody touched the keyboard or
   mouse in the last 1.5 s (GetLastInputInfo).
2. Open the session (`claude://` link or `codex://threads/<id>?prompt=<text>`), then wait up to 8 s (Codex 12 s) for
   the app's own exe (WindowsApps Claude.exe / Codex.exe) in front with an editable element focused, stable for
   0.35 s. Claude: if the focus is elsewhere, UIA SetFocus on the composer ("Prompt", tiptap ProseMirror).
3. Claude: check the window's document name is "<session title> - Claude Code", so a wrong session is never typed
   into. The composer must be empty (a draft is never touched). Codex: the composer must hold exactly the
   prefilled text.
4. Claude: save the whole clipboard (every format), put the text on it marked private (kept out of clipboard
   history and cloud sync), Ctrl+V, wait until the composer holds exactly the text, restore the clipboard.
5. Enter, then confirm the composer emptied. Before each step: same window in front, same element focused, no new
   input. Any failure stops there.

Result on the key: green SENT, or amber PASTE with the text left on the clipboard and the session opened (amber NO
SEND for a non-hardware press). A failure after the paste leaves the text unsent in the composer. A Claude CLI
session (a terminal) is never typed into: nothing can check which session a terminal window shows, so it keeps the
clipboard route.

Claude app focus (dry opens 2026-11-11, app 5.1231.2.0, nothing pasted): a recent session put the focus on the
composer directly; a 9-day-old one put it on the document and showed no composer at all (that send aborts with the
clipboard fallback). The full pipeline up to the paste ran on a real session in 0.75 s (opened, composer focused,
title verified); a wrong title was refused.

What was checked earlier on 2026-11-11, from the installed bundles and `--help`, for a route that needs no focus:

- Claude CLI 2.1.293: no send command; `--bg --resume <id>` starts a copy of a running session, `claude agents` and
  `attach` cover background sessions only.
- Claude desktop app 5.1231.2.0: the `claude://` links open a session or prefill a new one; none sends to an existing
  one. No localhost listener (one LAN-IP port, not touched). The session-management `send_message` tool exists only
  inside Claude sessions, not for the daemon (inferred).
- Codex app 26.1103.7773.0: `codex://threads/<id>?prompt=` opens the thread with the text prefilled in the composer
  (it does not send, and could replace a draft there, inferred); `codex://threads/wake/<id>` carries no text. The
  named pipe `\\.\pipe\codex-ipc` is private and undocumented, so it is not used.

None found, hence the focused send above.

## Haiku faces

A background thread runs `claude -p --model claude-haiku-5-5 --safe-mode --strict-mcp-config --tools ""
--no-session-persistence --output-format json`. The token comes from Credential Manager target
`Claude/M18/OAuth` and goes only into the child env. No token or no CLI: silently use the fallback
(deterministic sprite plus a word from the title). The label must not share a word with another session's
title and must differ from other labels; the icon must be 16 rows of 16 legal letters. One retry carries the reason.

## Decisions (OpenAI Decisions API, gpt-6-luna)

The user's grant for this project: event-triggered only, 1M input tokens a day (`node decide.mjs --check` from the
skill folder). The plugin runs `node ~/.agents/skills/decisions-api/scripts/decide.mjs - --max-input-tokens 4000`
with the skill folder as cwd and the request on stdin. The key is read only by that script; the plugin never sees
it. Caching is off (`prompt_cache_options.mode = explicit`, no breakpoints) because every input differs. A shared
budget in `session-decisions.json` stops at 950k input tokens a day and 90 calls an hour; a request estimated over
4000 tokens is never sent. All calls run in background threads (the kind in `sessions-decisions`, the label check
inside the face thread); `slot()` only reads the cache. Node missing, script missing, no key, over cap, bad reply:
silently the rules, retried after a backoff (3 tries per message).

**1. Waiting kind.** One read when a pad session's last message is new (hash of its last 2,000 chars plus any pending
ask), never on a timer: `state` choice (finished / question / approval / blocked / other), predicate
`has_recommendation`, score `urgency` 0-3 (nothing waits / optional / needed soon / stopped). The rules stay
primary for finished vs waiting. Decisions turns a rule's "finished" into "waiting" only for question, approval or
blocked at probability 0.7 or more (`DEC_ESCALATE`), never talks a rule's "waiting" down, and names the kind at 0.5
or more (`DEC_MIN_CONF`). Registry "waiting" and pending AskUserQuestion states are not re-read for their state,
only for the kind.

Measured 2026-11-11 on 30 real final messages: 26 drawn from transcripts of the last 40 days (every message the rules
called waiting, the rest random) plus 4 found by keyword to include blocked and approval cases. Claude labelled them
from the message tail before any Decisions answer was seen; 4 more ambiguous messages were dropped from the draw:

- Finished vs waiting: rules 22 of 30, Decisions alone at any confidence 24, Decisions alone at 0.5 or more 26, the
  shipped policy (rules primary, escalate at 0.7) 26. On the 26 random messages alone it is a tie (Decisions 22,
  rules 21). The gain is blocked and approval, which the rules cannot see: Decisions found 5 of 5 true blocked or
  approval at 0.56 or more, the rules 0 of 5.
- What fails: Decisions called three finished messages "blocked" (0.41, 0.56, 0.63; it read a mention of limits or
  errors as a stop) and answered "finished" at 0.82-0.89 for two real questions buried in the last paragraph. It
  also corrected two rule false positives (soft offers such as "tell me what felt wrong") but a third answer in
  that direction was wrong, so that direction is not used. Hence "escalate only", at 0.7.
- `urgency` tracks the labels (Spearman 0.66; mean 0.7 / 1.0 / 1.9 / 2.8 for labelled 0 / 1 / 2 / 3).
  `has_recommendation` had one labelled yes (0.95) and no labelled no above 0.26: too few to call.
- Call cost: about 1,000 input tokens (about $0.10 per thousand calls), 0.44 s median.
- Limits: 30 messages, one labeller, cuts picked on the same 30 (in sample); real blocked/approval cases are rare in
  the transcripts, so the extras were found by a keyword search.

**2. Label clash check.** After Haiku returns a label that passes the rules, one Decisions call with two predicates
(`distinct`: would this label let the user tell this session apart from the others listed; `on_topic`: does it name the
session's actual topic). Input: the label, this session's title and first request (500 chars), the other sessions'
titles and labels (12 at most). Below 0.5 on `distinct` or 0.3 on `on_topic` = rejected: the existing single retry
carries that as the reason; still bad = title-word fallback label and no more Haiku tries for that session.
Decisions down = the label is accepted as before.

Cut picked 2026-11-11 from 63 labelled labels on 18 real recent sessions (titles from the desktop app records, first
requests from the transcripts, read only): per session a good label, a wrong-topic label, a generic one ("Chat") and,
for 9, a duplicate of a related session's label. AUC `distinct` 0.97, `on_topic` 0.82. At (0.5, 0.3) 3 of 18 good
labels were rejected and 44 of 45 bad ones caught (the miss: "Podcast" 0.61 for a ChatGPT Pro session). Held out, 24 new
labels (12 good, 6 plausible wrong, 6 generic) written after the cut was chosen: 22 right; one good label rejected
("Plan merge" 0.11 topic) and one wrong accepted ("Pixel art" 0.53 for a block-game session). Not measured: real Haiku
output. Short cryptic good labels ("Exo", "Verify") score low on topic, so 0.3 is deliberately low.

## Env switches (tests)

`M18_SESSIONS_ROOT` (fixture home), `M18_HOME` (state dir), `M18_SESSIONS_DRY=1` (no window focus or launch,
no send; the nudge text goes to `state/clipboard.log` instead of the clipboard),
`M18_SESSIONS_CLAUDE` (CLI path or stub), `M18_SESSIONS_TOKEN_TARGET`. Decisions: `M18_SESSIONS_DECISIONS=off`
(never call), `M18_SESSIONS_DECISIONS_CMD` (a stand-in for `node decide.mjs`: same stdin request, JSON response on
stdout; sessions_check.py uses one), `M18_DECISIONS_JS` and `M18_DECISIONS_CWD` (script and grant folder).
sessions_check.py turns Decisions off for every check except `check_decisions`, which uses the stub, and writes its
previews to `M18_PREVIEW_DIR` (default `%TEMP%\m18sess\preview`).

## Check

`python scripts/m18/sessions_check.py` (fixtures from real tail shapes, stub CLI, writes previews);
`--real` also lists the real sessions. Mock daemon: `m18d.py --mock --port 8721 --config <yaml>`.
`python scripts/m18/send_check.py`: real keystrokes into two throwaway "M18 stand-in" windows (never a chat app),
about 30 s, hands off the keyboard: happy path with clipboard restore (text and a custom format), refused API
source, recent input, input mid-send, focus moved, foreground changed after the paste, wrong app, a draft, failed
target check, prefilled (Codex) mode.

## Verified vs not

Verified: states from fixtures and the real registry, slot stability and restart, overflow modal and paging,
40 sessions under 300 ms, label rules, stub-CLI cache, 3 real Haiku calls, mock-daemon press and modal.
Decisions: stubbed fixtures (event trigger, hash cache across restart, 2,000-char cap, caching off, escalate-only,
low-confidence, failure backoff, missing script, daily cap, slot() not waiting on a call, label retry and
fallback), one real end-to-end run (a blocked and a finished fixture read through the real script, 32 ms scan),
badge renders inspected at 3x. Silence: fixtures (quiet 14 min, 5 min, a 10-minute Bash at 10.5 and 12 min, a
fresh subagent file, Codex, summary fields, default 11, nudge in dry mode), the silent tile inspected at 3x in both looks.
Send: every guard on the stand-in (send_check.py 13/13), the Claude app pipeline up to the paste on a real session
(dry, nothing pasted) and the wrong-title refusal. Not verified: a real paste and Enter into the Claude app, anything
in the Codex app (not running during the work: its prefill, focus and UIA tree are inferred from the link handler),
whether a pad press itself counts as input for the 1.5 s rule (if it does, the hold release lands before the 1.5 s
window and the send would always fall back: check on the pad), the send started from the daemon process (the stand-in
check ran it from a script).
Not verified: the nudge on a real press (clipboard + focus), the silent look on the real LCD, a real pad, window focus on a real press, Claude CLI-window focus by pid, long-run memory,
the production token target, Haiku label quality over many sessions (one of 3 labels was wrong: "Login done"),
the clash check on real Haiku output, Decisions kinds on a live pad over days, the badges on the real LCD.
