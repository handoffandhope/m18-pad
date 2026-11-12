# Night shift key (plugins/nightshift.py)

```yaml
14: {live: {plugin: nightshift.face, interval: 2}, action: {plugin: nightshift.tap},
     hold: {plugin: nightshift.hold}}
```

**For agents:** the user is away from the PC while `%USERPROFILE%\tools\m18\state\away.json` exists; it holds `since`
(epoch seconds) and `by` ("pad"), so don't wait on him for answers then and leave a clear summary for when he is back.

- Tap = away: writes `away.json`, keeps the PC from sleeping (`SetThreadExecutionState(ES_CONTINUOUS |
  ES_SYSTEM_REQUIRED)` from the plugin's own thread; no power-plan change; released when he comes back or the daemon
  exits), and tracks the session keys from `state/sessions-summary.json`.
- While away the key shows a moon over one count at a time, 2 s each, zeros skipped: finished since `since` (green
  check), waiting now (yellow ?), silent now (violet z). Nothing yet = a dim check 0.
- Tap again = back: deletes `away.json`, releases the wake lock, shows the final tally under a sun for 10 minutes,
  then the idle face (moon icon, "Away").
- Hold = the same, plus monitors off (SC_MONITORPOWER 2) when going away. Hold while away = back, monitors untouched.

A session counts as finished when the plugin sees it turn finished after `since`, or first sees it already finished;
it stays counted after it leaves the pad. Works with no sessions at all.

## State

`state/away.json` (the flag) and `state/nightshift.json` (`since`, `done` ids, last state per id, `wake` {held, at},
then `back_at` and `final` {done, waiting, silent}). Both survive a daemon restart; the next live tick re-arms the
wake lock. `M18_NIGHTSHIFT_DRY=1` writes `state/nightshift-monitor.log` instead of turning monitors off.

## Check

`python scripts/m18/nightshift_check.py`: the tally rules, then a mock daemon (port 8736): away on, wake lock taken
(the call's return value), a transition counted, restart keeps away and the tally, back writes the tally, hold in dry
mode, zero sessions. Previews to `%TEMP%\m18keys\preview`.
Not verified: monitors really turning off, the PC really staying awake overnight (`powercfg /requests`), a real pad.
