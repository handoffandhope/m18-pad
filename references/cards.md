# Cards (the card key)

Feeds produce cards; one pad key shows the top card; pressing it takes over the whole pad to show
that card and its action keys. Store and API live in the daemon (`m18lib/cards.py`, routes in
`m18d.py`); the key and the takeover are `plugins/cards.py`.

```yaml
5: {live: {plugin: cards.key, interval: 2}, action: {plugin: cards.open}}
```

## Card
`{id, feed, title, lines: [str], image_png_b64?, icon?, actions: [{id, label, color?}], priority?, ttl_s?}`
- `id` (≤120 chars) upserts: posting the same id replaces the card but keeps its place in line.
- `priority` 0-2 (default 1). Order: priority high to low, then oldest first.
- `actions`: up to 4 (keys 11-14). `color` `#rrggbb`; without it the colour follows the label/id
  (keep/ok/open/... green, delete/remove/dismiss/... red, anything else blue). Short labels read best:
  5 characters fit large on a key, 6-10 drop to the small font.
- `image_png_b64`: PNG or JPEG up to 4096 px; shown in a 3x2 block (keys 1-3 + 6-8), letterboxed.
- `icon`: an mdi name (`mdi:alert` or `alert`); unknown names are refused.
- `ttl_s`: the card drops after this many seconds. Cards persist in `<config dir>/state/cards.json`
  across daemon restarts; up to 60 cards (the least important, oldest go first).

## API (same guard as every route: `X-M18: 1`, no `Origin`, Host 127.0.0.1/localhost)
- `POST /cards` body = a card → `{ok, card, created}`; 400 with the reason on a bad card.
- `GET /cards[?feed=x]` → JSON array of cards in display order (with `created`, `updated`, `expires`).
- `DELETE /cards?id=x` → `{ok, removed}`.
- An action key press removes the card and publishes on `/events`:
  `{seq, t, type: "card_action", card, feed, action, source, page}` (`source` = device or api; trust device
  for a human answer). Press events now carry `type: "press"`. NEXT, CLOSE and b1 leave cards alone.

## Plugins: `ctx.cards`
`post(card, handler="module.fn")`, `remove(id)`, `list(feed=None)`, `get(id)`, `act(id, action_id)`,
`on_action(feed, fn)`. A handler is called as `fn(ctx, card, action_id)` in an action worker after the
card is removed. `handler` is stored with the card, so it survives restarts; `on_action` is in memory
and wins over a stored handler. Example: `plugins/clip.py` (`post_clip_card`, `on_card`).

## What the pad shows
- Key: nothing with no cards (theme backdrop); else the top card's image or icon with the title's first
  word, a red count badge at 2+ cards, a red frame for priority 2.
- Takeover (60 s, re-armed per press; physical b1 always ends it): rows 1-2 the card, one word or number
  per key (title gold, lines white, numbers aqua; a number keeps its unit: "48 MB" → `48MB`); row 3 the
  actions, key 15 NEXT (n/m) or CLOSE. After an action the takeover shows the next card or closes.
  Ten content keys at most (four beside an image): write titles of 1-3 short words and short lines.

## Feeds that ship
- `clip` (`plugins/clip.py`): OBS replay buffer in the pad's own "Pad Clips" profile and scene collection (nothing to
  set up in OBS; `scripts/m18/obs_pad_clips.py` builds them) → a card with a frame, duration, size; Keep / Delete
  (Recycle Bin) / Copy path. Hold the key to pick which monitor is recorded.
- The clipboard key (`plugins/clipboard.py`) is a live key with its own takeover, not a card feed.
