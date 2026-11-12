# Using the M18 to its full potential

Ideas for an agent building on the pad, grouped by what the hardware offers. Each names its
mechanism so you can build it with what m18d already has. Check limits.md for ceilings. Propose
these to the user when the pad has free keys or a request hints at one; don't fill keys unasked.

## Treat the 15 keys as one 320x192 screen
The keys sit in a 5x3 grid, so one image cut into 64 px tiles makes a single display (ignore the
bezel gaps or draw around them).
- **Album art wall:** a now-playing plugin draws the cover across all 15 keys on a `music` page.
  The middle row keeps prev/play/next drawn over the art.
- **Pad-wide graphs:** CPU/GPU/network history as one long line chart across a row of 5 keys.
- **Splash and transitions:** a page switch wipes or slides between layouts using `frames`.
- **Mechanism:** one plugin renders the big image, caches it in a module-level dict, and 15 live
  keys each return their own `{"image": tile}` (pass `args: {tile: N}`).

## Animation (GIF icons, plugin `frames`, ~30 fps cap)
- **Status that moves:** a spinner while a build, render or agent run is going; still when done.
- **Progress fills:** a key that fills like a bar for a download, a render, or a Pomodoro timer.
- **Attention without noise:** a slow pulse on the one key that needs a press (meeting starts,
  CI failed), static everywhere else. Motion reads at a glance; color alone doesn't.
- **Press feedback:** `ctx.flash` with a short frame burst (ripple, check mark) confirms a press.
- Keep animation rare: one moving key draws the eye, five are noise, and each costs CPU.

## Live data keys (each in its own worker)
- Meters and sparklines drawn with PIL: CPU, GPU temperature, RAM, ping, battery of a headset.
- Counters: unread mail, open PRs, CI status, queue length, stock of a tracked item.
- Clocks: time in other zones, countdown to the next calendar event, a stopwatch key.
- Agent dashboards: one key per running Claude or Codex session showing working, waiting for
  you, or done, from a local status file or API; press to focus that window.
- Real state over guessed state: read mute or recording state from the app's API (Discord RPC,
  OBS websocket) instead of `states:` toggles that drift.

## The pad as a two-way surface
Software can push to the pad and listen to it (`references/api.md`).
- **Push:** `POST /face` puts an alert, count or image on any key with a ttl; it survives page changes.
- **Events:** `GET /events` long-polls every press and release with `held_ms` and `source`.
- **Modal:** `POST /modal` or `ctx.modal` takes the whole pad for a question ("Allow this command?"),
  reports the answer through `/events`, and gives the page back.
- **Human-only key:** mark a key `physical_only: true` so only a real finger runs it; an agent can ask
  for consent on that key and no software press can answer for the human.

## Context-aware layouts
- `follow:` pages swap the whole pad when an app gets focus: editor keys in VS Code, scene keys in
  OBS, tool keys in Blender or Photoshop, transport keys in a DAW.
- `inherit:` keeps shared keys (media, home) on every page while the rest changes.
- Time-of-day or presence: a plugin calls `ctx.goto` to switch to a work page in the morning or a
  meeting page when the camera turns on.

## Input tricks (press and release only, so build vocabulary in timing)
- Tap vs `hold:` gives every key two actions; show the hold action in small text on the face.
- Double and triple tap are built in: `double:` and `triple:` next to `action:` (config.md "Gestures"). They
  delay that key's single tap by `double_ms`, so keep them off keys that must feel instant.
- Press duration: `hold:` gives two lengths; a plugin can read `/events` `held_ms` for more (short,
  medium, long). Chords are not possible: the stock firmware loses a second key pressed during a hold.
- Modal layers: a button (b1-b3) jumps to a page, and the page's keys jump back after use.
- Number pad or launcher grids: a page of 15 keys that types digits, emoji or snippets.
- Confirm-to-act: destructive keys turn red on first press and only fire on a second press within
  2 s (two `states:`, or a plugin with a timer).

## The RGB ring (one color for the whole ring)
- Ambient status: ring color per page, or driven by a live plugin with `ctx.run({"rgb": ...})`
  (green idle, amber something running, red needs you). Its real update rate is unmeasured, so
  test before animating it.
- Brightness as a second channel: dim at night, full when an alert fires.

## Games and toys (keep them small)
- Memory or whack-a-key games on a `play` page: plugins own the game state, keys flash targets.
- A pixel-art editor: each key is one pixel or one 8x8 cell; the result renders back as a tile.

## Integrations worth wiring
- OBS (scenes, recording state), Discord (mute and deafen state), Spotify or Windows media session
  (art, track), Home Assistant (lights, scenes), GitHub (PR and CI status), the Claude/Codex
  tooling on this PC (session status).
- Web APIs need a timeout of 2 s or less, and the result cached in `ctx.state`.

## Pad-wide aesthetics
A theme (`aesthetics/`) can draw one scene across all keys and restyle every icon. Reach for a
new aesthetic when the user asks for a vibe; see aesthetics/README.md.

## Design rules at 64 px
- One idea per key: an icon or a number, a short label. Text under ~10 characters.
- High contrast; JPEG compression smears thin colored lines and smooth gradients.
- Color has meaning: keep red for destructive or recording and green for go across all pages.
- Preview every change with `m18ctl preview` and look at the image before reporting.
