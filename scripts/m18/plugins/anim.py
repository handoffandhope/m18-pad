"""Animation helpers and demos for live keys.

    10: {live: {plugin: anim.orbit, interval: 60}, label: ...}

A live function may return {"frames": [PIL.Image, ...], "fps": 24, "loop": True}.
The daemon plays the frames at ~30 fps max without calling the plugin again, so
build frames once and cache them (ctx.state), and use a long `interval`.
"""
import math

from PIL import Image, ImageDraw

S = 64


def orbit(ctx, color="#7c3aed", n=36, fps=24):
    """A dot orbiting the key: smooth-animation demo and a template for spinners."""
    cache = ctx.state.setdefault("orbit", {})
    k = (color, n)
    if k not in cache:
        frames = []
        for i in range(n):
            img = Image.new("RGB", (S * 3, S * 3), "#0e0e16")
            d = ImageDraw.Draw(img)
            for j in range(6):                      # fading tail
                a = 2 * math.pi * (i - j * 0.6) / n
                x, y = S * 1.5 + 60 * math.cos(a), S * 1.5 + 60 * math.sin(a)
                r = 16 - j * 2
                shade = 1 - j / 6
                c = tuple(int(int(color[p:p + 2], 16) * shade) for p in (1, 3, 5))
                d.ellipse((x - r, y - r, x + r, y + r), fill=c)
            frames.append(img.resize((S, S), Image.LANCZOS))
        cache[k] = frames
    return {"frames": cache[k], "fps": fps}
