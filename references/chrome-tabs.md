# Chrome tabs feed (stale tabs as cards on the pad)

A Manifest V3 extension notices tabs you have not activated for a while and offers one at a time to the pad as a card: **Keep / Close / Later / Skip**. Code: `scripts/m18/chrome-tabs/`.

```
Chrome extension  <-- native messaging (stdin/stdout, 4-byte LE length + JSON) -->  m18_tabs_host.py  -->  m18d 127.0.0.1:8718
 (stale.js logic)       Chrome starts the host only for the extension id; no port opened         (X-M18: 1, no Origin)
```

## What it does
- Tracks per tab: first seen, last activated (seeded from `tab.lastAccessed`), url, title, pinned, audible. Reads nothing from pages (no host permissions, no content scripts). Only `http(s)` tabs are tracked; incognito is never touched (`"incognito": "not_allowed"` plus a `tab.incognito` skip).
- Stale = not activated for 3 days (`cfg.staleDays` in `chrome.storage.local` key `state.cfg`). Pinned and audible tabs are never stale. Max 5 new cards a day, oldest first, one open card at a time. Card ttl 12 h; an unanswered card counts as Skip. Activating the tab withdraws its card.
- Card: `id tabs-<tabId>`, `feed "tabs"`, title = site (`youtube.com`), lines = [page title cut to 22, "9 days"], favicon scaled to 64x64 as `image_png_b64`, `priority 1`, `ttl_s 43200`.
- Keep: no asking for 14 days. Close: closes, then shows a 60 s card `tabs-undo` (Undo / OK; Undo uses `chrome.sessions.restore`). Later: `chrome.readingList.addEntry`, then closes (falls back to a "Later" bookmark folder only if the optional `bookmarks` permission was granted; if neither works the tab stays and is asked again tomorrow). Skip: asked again in 24 h.
- Daemon contract used (matches what `test_stub.py` implements): `POST /cards`, `DELETE /cards?id=`, `GET /events?since=&timeout=` for `{type: "card_action", card, feed: "tabs", action}`. Extra card field `default_action` (the id to highlight, from Decisions) is sent when known; a daemon may ignore it.

## Decisions sorting (host side, on only if the grant check passes)
The host runs `node decide.mjs --check` from the skill folder; if it prints `Granted`, each new card costs one call (about 510 input tokens, `--max-input-tokens 20000`, caching off via explicit mode, at most 200 calls a day). Input: tab title, host+path (never the query string), days unused, and the labels in `state/sessions.json` (`sessions-summary.json` holds only counts, no titles). Questions: predicate `related` (at least 0.7: no card, push the tab out 7 days) and choice `kind` (video, article, docs, shopping, tool, other; confidence at least 0.6: video/article highlight Later, tool/docs highlight Keep). Switch off with env `M18_TABS_DECISIONS=0`. Sample answers (2026-11-11, 8 real calls): video, article, docs, shopping, tool all classified correctly at 0.96 to 1.0; "related" fired on a Minecraft wiki page against a "MINECRAFT WIKI" label but not on a Chrome native-messaging page against "M18 PAD TABS". Real session labels are 10-character truncations, so `related` will fire rarely.

## Install (the user does this; nothing here touches Chrome or HKCU by itself)
1. Register the host: `powershell -ExecutionPolicy Bypass -File %USERPROFILE%\.agents\skills\m18-macropad\scripts\m18\chrome-tabs\install.ps1` (add `-DryRun` to preview). Extension id is fixed: `aiifhcajkkocipiofhgdccolcoemjdpb` (from the `key` in `manifest.json`).
2. Chrome: open `chrome://extensions`, switch on Developer mode, Load unpacked, pick `%USERPROFILE%\.agents\skills\m18-macropad\scripts\m18\chrome-tabs\extension`. Check the id shown matches. Leave "Allow in Incognito" off.
3. The daemon must be running. To see a card sooner, set `staleDays` small in the service worker console (`chrome://extensions` > "service worker"): `chrome.storage.local.get('state').then(r => chrome.storage.local.set({state: {...r.state, cfg: {staleDays: 0.0001}}}))`, wait a minute, then reset it with `cfg: {}`.
4. Uninstall: `uninstall.ps1` in the same folder, then remove the extension at `chrome://extensions`.

Host log: `%LOCALAPPDATA%\m18-tabs\host.log` (errors only).

## Tests (run from the chrome-tabs folder, no Chrome or pad needed)
- `node test_e2e.mjs` runs `background.js` and `stale.js` under node with a fake `chrome`, wired through real pipes to the real host and `test_stub.py` (port 8732): oldest-first, one open card, 5 a day, Keep/Close/Undo/Later/Skip, withdraw on activate.
- `python test_framing.py`: split writes, unicode, 300 KB message, reply length prefix, exit on EOF.
- `python test_decisions.py`: real Decisions calls on 8 sample tabs (about $0.0004).

## Not verified in real Chrome yet
`tab.lastAccessed` seeding, the `_favicon` endpoint and `chrome.readingList` behaviour, the `.cmd` launcher being accepted by Chrome, service-worker keep-alive through the native port, and the alarm cadence.
