# What the M18 and m18d can and can't do

Read this before promising a feature (animation, effects, new input types) or an update rate.

## Hardware (from the SDK's StreamDockM18.py, 2026-11-09)
- Input: 15 LCD keys + 3 buttons, press/release only. No knobs, touch strip or press pressure.
  Taps, holds, double and triple taps are timing logic in the daemon (`hold:`, `double:`, `triple:`,
  config.md "Gestures"). No chords (see below).
- Screens: 64x64 per key, sent as JPEG (gradients band, thin text blurs). One brightness for all.
  A black bar separates neighbouring screens (width not measured; `tiles.GAP` = 14 key pixels is an
  estimate), so text never continues across keys and multi-key pictures skip the strip behind each bar.
  The 3 buttons have no screen. Never call the SDK's 480x272 `set_touchscreen_image` or any
  boot-logo or LOG write: it is the boot logo and the SDK says the write goes to device ROM.
- Ring: 24 LEDs, individually addressable on the user's unit (verified by eye 2026-11-11 with
  `m18ctl ringtest`), although the SDK's M18 class flags `support_single_led_color` False. m18d
  reaches them through the transport call, not the device class. Config `rgb:` still sets one color.
  Usable now (2026-11-11): `/leds`, `ctx.leds`, `m18ctl ringwalk` (measures where each LED sits),
  `state/ring-map.json`, one owner at a time (api.md). Real LED update rate is unmeasured: m18d caps frames at
  20 fps and never resends an identical one. Not yet confirmed on the pad: that `ringwalk`'s nearest-key answers
  match what the owner sees (the default map is a guess built from the half-split facts below).
- One owner: VSD Craft and m18d can't both hold the device.
- The user's unit: the driver's press-code table is rows bottom-up, so `settings.key_map` swaps
  rows 1 and 3 (setup.md).

## Community reverse-engineering (github.com/bidoofgoo/streamdock-m18, firmware 02.020)
Each item is reported by that repo unless marked verified here.
- Per-LED ring: reports 24 addressable LEDs, 0-21 around the ring and 22-23 on the front, a 24x RGB frame
  ("SETLB": colour first, then brightness, don't resend unchanged brightness). The vendor SDK flags it off
  for the M18 (`support_single_led_color` False; only the N4 Pro sets it). Verified here 2026-11-11:
  `m18ctl ringtest` (LEDs 0-11 red, 12-23 blue, through the SDK transport's `set_single_led_color`, no
  raw HID) showed red on the bottom half and blue on the top half, and the two short front strips blue
  (so 22-23 are those strips). Geometry as the user saw it: the red/blue boundary on each side sits level
  with the middle key row, which is above the case's true middle, because the case extends below the
  keys for the strips and the 3 buttons. So "half the indices" is not "half the height". The exact
  position of each index is not mapped yet; light one LED at a time before drawing arcs or meters.
- Touchscreen image: the 480x272 one is the boot logo, written to ROM. Reported by the repo and by the SDK
  text; never call it.
- Input: stock firmware reports one key at a time, so a second key pressed during a hold is lost
  (untested on this unit); hence no chords. Press and release are both reported.
- Key ids: image-vs-event key ids are reversed for the top and bottom rows on this model, which is why
  `key_map` swaps rows 1 and 3 (setup.md). Reported by the repo as a model trait; the swap itself is
  verified on this unit.
- Idle revert: the screen reverts to the stock screen after ~8-10 s without traffic. Verified here by
  reading the SDK: `StreamDock.open()` starts a heartbeat thread that calls `transport.heartbeat()` every
  10 s, and m18d opens the device through it, so the SDK heartbeat is the intended cover. Whether 10 s is
  enough for an 8-10 s timeout has not been tested on the pad.

## Measured speed (the user's pad, 2026-11-09, `%TEMP%\m18bench\bench.py`, daemon stopped)
These time the SDK calls returning. Writes may be buffered in transport.dll, so the rate the
screens actually show has not been checked by eye.
- One key image + refresh: ~3 ms (~370/s).
- All 15 keys + refresh: ~31 ms (~32 full frames/s), ~2 ms per key.
- Ring color call: returns in under 0.1 ms (likely async; real LED rate unmeasured).

## Daemon limits (m18d.py)
- Live keys (updated 2026-11-09): each key's plugin runs in its own worker (8 workers), the
  scheduler ticks every 20 ms, `interval` floor 0.03 s. A slow plugin only delays its own key.
- Unchanged faces aren't resent (hash check), so many live keys are cheap if they rarely change.
- Animation: an animated GIF `icon` plays at its own frame timing; a live plugin can return
  `{"frames": [...], "fps": N}` (fps capped at 30). The anim thread sleeps until the next frame
  change and redraws only the due keys. Each distinct frame is rendered once and kept as JPEG
  bytes (512-entry cache) that stream straight to the transport, so a looping animation costs one
  USB write per frame. Measured 2026-11-09: a 24 fps plugin key plus a 12.5 fps GIF key cost
  ~1.25% of one core (was ~11% before the cache). Smoothness confirmed by eye on the pad, before and after the cache.
- Faces are sent as quality-90 JPEG (the SDK's own path used its default, ~75).
- Actions run in a pool of 6 threads; a long `delay`/`countdown` sequence holds one slot.
- Synthetic keystrokes: ignored by elevated windows (UIPI), go to the focused window, and can't
  type into the secure desktop (UAC, Ctrl+Alt+Del, lock screen).
- Sending text into an agent session (`m18lib/sendtext.py`, sessions.md "Nudging a session"): only the Claude
  desktop app and the Codex app, only for a hardware press, only with nobody typing (no input in the last 1.5 s or
  during the send), and only into an empty composer. A Claude CLI terminal is never typed into: nothing can check
  which session a terminal shows. Anything that changes focus mid-send aborts it, which leaves the clipboard text.
- `states:` toggles track presses only and drift; real state needs an API plugin.
- Daemon code changes need stop + start; config and plugins hot-reload.

## Buildable on top
Anything Python can reach, at up to ~30 fps animation per key and a per-LED ring (20 fps cap, one owner at a time).
See creative.md for ideas.
