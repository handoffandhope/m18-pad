# Pad aesthetics

Each folder here is one complete look for the whole pad: a design brief for agents (`BRIEF.md`),
the renderer hooks that apply it (`theme.py`), and a true-size picture of it (`preview.png`).
Pick one with a single config line, and every key, including ones added later, follows it.

| Aesthetic | Look | Status |
|---|---|---|
| [vaporjazz](vaporjazz/BRIEF.md) | "Late Night Mall, 1994": dusk sunset scene across all keys, early-90s jazz/Memphis confetti, chrome sticker icons, black italic tags | built 2026-11-09 |
| [wabisabi](wabisabi/BRIEF.md) | "The mended tea bowl": lip, ash glaze and clay foot across the rows, brushed sumi icons each slightly off true, a kintsugi seam, an enso power key, hanko status seals, a seasonal accent | built 2026-11-09 |
| [hillside](hillside/BRIEF.md) | "Cross-section of a hillside" (textures drawn from scratch): sky, grass blocks, stone with ores; pixel-art item icons, tooltip labels, inventory-slot status | built 2026-11-09, on the user's pad |
| (none) | the plain dark look: flat `#16161e` keys, white icons | built in |

## Using one
```yaml
settings:
  theme: {name: vaporjazz}          # options per BRIEF.md, e.g. {name: vaporjazz, scanlines: false}
  rgb: {color: "#ff4fa0", brightness: 60}   # use the theme's PALETTE["rgb"] for the ring
```
`setup.ps1` installs every `aesthetics/<name>/theme.py` to `$M18/themes/<name>/theme.py`. The daemon
hot-reloads both the config line and edits to `theme.py`.

**When adding keys under a theme, read that theme's BRIEF.md first.** Its rules say which icons,
labels, colors and plugin drawing fit the look.

## How a theme plugs in (contract for `theme.py`)
All hooks are optional, and all drawing happens at S = 192 px (3x supersampled, shown at 64).
- `PALETTE` (dict): `fg`, `label_color`, `ink`, accents, `good`/`warn`/`bad`, `track`, `font`,
  `rgb`. Plugins read it as `ctx.theme` to draw their own tiles in the theme's colors.
- `FONTS`: `{"label": path, "glyph": path}` for labels and big text.
- `backdrop(key, face, S, opts) -> Image`: the key's background. `key` is 1-15 in row-major order,
  so a theme can draw one pad-wide scene and slice it (see `_geom` in vaporjazz). Empty keys get
  `face = {}` and show the backdrop alone.
- `ink(img, mask, role, fill, face, key, opts)`: paints an icon, glyph or label from its mask
  (role is `"icon"`, `"glyph"` or `"label"`). This is where outlines, shadows and gradient fills go.
- `finish(img, face, key, opts) -> Image`: last pass (scanlines, grain).
- `meters(rows, key=None, opts=None) -> RGBA Image`: draws `[(name, percent|None), ...]` (CPU/RAM/GPU
  and similar) in the theme's own style. Plugins reach it with `ctx.theme_fn("meters")`. Add more
  helpers the same way; any module-level function is reachable by name.
- `RESAMPLE = Image.BOX`: set it when everything is drawn on the 64 px grid (pixel art), so the
  3x downscale stays crisp. The default is LANCZOS.
- Live plugin images in RGBA mode are composited over `backdrop` and get `finish`; RGB images stay
  as drawn. Faces marked `_plain` (dark mode) skip the theme.
- `ink` is called once per wrapped label line. To draw the whole label your own way (pixel font,
  lowercase), draw it on the first call from `face["label"]` and set a flag in `img.info`, as
  hillside and wabisabi do.

## Making a new aesthetic
1. Write `BRIEF.md` first: the era or mood in one line, palette with hex values, motifs, icon,
   label and status-color treatment, and do/don't rules. Commit to one direction.
2. Copy `vaporjazz/theme.py` as a starting point, and keep the scene cached (`lru_cache`), because
   it renders once and every key slices it.
3. Iterate with the true-size preview, which sends nothing to the pad:
   `$M18\.venv\Scripts\python $M18\theme_preview.py <name> out.png` (copy theme.py into
   `$M18\themes\<name>\` first). Judge at that size, not from the smooth `m18ctl preview`.
4. Have a fresh-context agent judge the render against the brief (legibility at 64 px, fit,
   cohesion). The builder doesn't grade its own look.
5. Save `preview.png` from the final render and add a row to the table above.

Lessons from building vaporjazz (2026-11-09):
- At 64 px, decoration must be fat (strokes ≥ 5% of the key) or it disappears.
- Decoration lives in key corners and never crosses the icon zone. The key under the most-pressed
  icon (center) stays clear.
- Every label needs its own dark tag, or it fails over the brightest part of the scene.
- Status colors work as a neon rim drawn under the ink, not as a flat fill.

Lessons from wabisabi and hillside (2026-11-09):
- Each look shows status in its own way: a neon rim (vaporjazz), a hanko seal (wabisabi), an
  inventory slot (hillside). Pick the thing in that world that means "this one is different".
- When the ground changes a lot across the pad, choose ink per key from the ground's brightness
  under the icon (wabisabi `_ink_for`) instead of one fixed colour.
- Icons with letters inside (`record-rec`) never survive pixelation or a rough brush. Swap them for
  a plain shape inside the theme (`DISC_ICONS`).
- Small text in plugin tiles needs at least 12 LCD px of height in a serif font. Pixel fonts read
  at 7 px.
- A pad-wide line (a crack or a seam) breaks at every bezel. Keep it on one column of key margins
  so the pieces line up.
