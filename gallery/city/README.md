# City

Built by Claude Haiku 5.5 in one run of about two and a half minutes. It read one page,
[references/scene-card.md](../../references/scene-card.md), and nothing else. `city.py` is its
scene file, unedited.

To try it, copy `city.py` into `scenes/`, add `city: {scene: city}` under `pages:` and open the page.
(The skill also ships its own `scenes/city.py`; this one replaces it.)

## The prompt

> Build a scene called `city` that turns all 15 keys into one living night skyline, drawn as a
> single picture across the keys (mind the physical gaps between keys). It shows what the PC is
> doing: more windows light up as CPU load rises, a moon moves across the top row through the day,
> traffic along the bottom row gets busier with network activity, and it rains when memory use is
> above 80%. Tapping a building key shows that key's reading as text for a few seconds.

## What it cost

Measured on one run each, same picture, same model. "Task tokens" is the peak context minus the
62k the harness loaded before the task began.

| How Haiku was briefed | Tool calls | Code written | Task tokens |
|---|---|---|---|
| Whole skill, write a plugin | 23 | 13,300 characters | 77k |
| One-page scene card, write a scene | 16 | 5,600 characters | 36k |
