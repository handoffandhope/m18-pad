"""3D for scenes: a small ray tracer. Balls on a floor under a sky, with a sun, soft shadows,
reflections and glow. Needs numpy.

    from m18lib import ray

    def draw(im, d, s, t, state):
        balls = [ray.ball((0, 1, 0), 1, "#e63946", gloss=0.6),
                 ray.ball((2, 0.5, 1), 0.5, "#ffd166", glow=2.0)]
        im.paste(ray.render(balls, ray.orbit(t * 0.2)))

Units: y is up, the floor is y = 0, a ball of radius r resting on the floor has y = r.
Colours are "#rrggbb" or (r, g, b), so `mix` and `ramp` results work. `ray.project(cam, pos)` gives the canvas pixel of a 3D point (for taps and labels).
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from .scene import H, W

F = np.float32


def _rgb(c) -> np.ndarray:
    """'#rrggbb' or (r, g, b) 0-255 -> linear-light float RGB."""
    if isinstance(c, str):
        c = c.lstrip("#")
        c = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    return (np.array(c[:3], F) / 255) ** F(2.2)


def _norm(v):
    v = np.asarray(v, F)
    return v / max(1e-6, float(np.linalg.norm(v)))


def ball(pos, r=1.0, color="#ffffff", gloss=0.3, glow=0.0) -> dict:
    """A ball. gloss 0..1 = how mirror-like; glow > 0 makes it a lamp that lights what is near it."""
    return {"pos": tuple(pos), "r": float(r), "color": color, "gloss": float(gloss), "glow": float(glow)}


def camera(eye, look=(0, 1, 0), fov=40) -> dict:
    """A camera at `eye` looking at `look`; fov is the vertical angle in degrees."""
    return {"eye": tuple(float(x) for x in eye), "look": tuple(float(x) for x in look), "fov": float(fov)}


def orbit(angle, height=4.0, dist=11.0, look=(0, 0.8, 0), fov=40) -> dict:
    """A camera circling the centre: `angle` in radians, `height` above the floor, `dist` away."""
    return camera((look[0] + dist * math.sin(angle), height, look[2] - dist * math.cos(angle)), look, fov)


def _basis(cam):
    eye, f = np.array(cam["eye"], F), _norm(np.array(cam["look"], F) - np.array(cam["eye"], F))
    right = _norm(np.cross(np.array((0, 1, 0), F), f))
    up = np.cross(f, right)
    return eye, f, right, up, math.tan(math.radians(cam["fov"]) / 2)


def project(cam, pos, size=(W, H)):
    """Canvas (x, y) of a 3D point, or None when it is behind the camera."""
    eye, f, right, up, th = _basis(cam)
    v = np.array(pos, F) - eye
    z = float(v @ f)
    if z <= 0.05:
        return None
    return (size[0] / 2 + float(v @ right) / (z * th) * size[1] / 2, size[1] / 2 - float(v @ up) / (z * th) * size[1] / 2)


def _hit(o, d, C, R):
    """Nearest hit of rays (o, d) with the balls and the floor: (distance, index); index len(C) = floor, -1 = sky."""
    n = len(C)
    t = np.full(len(d), np.inf, F)
    idx = np.full(len(d), -1, np.int16)
    if n:
        b = np.sum(o * d, -1, keepdims=True) - d @ C.T
        c = np.sum(o * o, -1, keepdims=True) - 2 * (o @ C.T) + np.sum(C * C, -1) - R * R
        h = b * b - c
        ts = np.where(h > 0, -b - np.sqrt(np.maximum(h, 0)), np.inf)
        ts[ts < 1e-3] = np.inf
        idx = np.argmin(ts, -1).astype(np.int16)
        t = np.take_along_axis(ts, idx[:, None].astype(np.int64), -1)[:, 0]
        idx[~np.isfinite(t)] = -1
    with np.errstate(divide="ignore", invalid="ignore"):
        tf = np.where(d[:, 1] < -1e-5, -o[:, 1] / d[:, 1], np.inf).astype(F)
    tf[tf < 1e-3] = np.inf
    fl = tf < t
    t = np.where(fl, tf, t)
    idx[fl] = n
    return t, idx


class _World:
    def __init__(self, balls, sun, sun_color, sky, floor, floor2, tile, gloss, fog, stars):
        self.C = np.array([b["pos"] for b in balls], F).reshape(-1, 3)
        self.R = np.array([b["r"] for b in balls], F)
        self.col = np.array([_rgb(b["color"]) for b in balls], F).reshape(-1, 3)
        self.gloss = np.array([b["gloss"] for b in balls], F)
        self.glow = np.array([b["glow"] for b in balls], F)
        self.sun, self.sun_color = _norm(sun), _rgb(sun_color)
        self.horizon, self.zenith = _rgb(sky[0]), _rgb(sky[1])
        self.floor, self.floor2 = _rgb(floor), _rgb(floor2 if floor2 else floor)
        self.tile, self.fgloss, self.fog, self.stars = F(tile), F(gloss), F(fog), F(stars)

    def sky(self, d, stars=True):
        y = np.clip(d[:, 1], 0, 1)[:, None] ** F(0.45)
        c = self.horizon * (1 - y) + self.zenith * y
        sd = np.clip(d @ self.sun, 0, 1)[:, None]
        c = c + self.sun_color * (sd ** 600 * 6 + sd ** 12 * F(0.3))
        if self.stars > 0 and stars:
            q = np.floor(d * 160)
            h = np.modf(np.sin(q @ np.array((12.9898, 78.233, 37.719), F)) * F(43758.5453))[0]
            c = c + (self.stars * (np.abs(h) > 0.996) * (d[:, 1] > 0.02))[:, None] * F(1.6)
        return c

    def surface(self, o, d, t, idx):
        """Position, normal, colour, gloss and own glow at the hits."""
        n_b = len(self.C)
        p = o + d * t[:, None]
        fl = idx == n_b
        i = np.clip(idx, 0, max(0, n_b - 1))
        if n_b:
            n = (p - self.C[i]) / self.R[i][:, None]
            alb, gl, em = self.col[i].copy(), self.gloss[i].copy(), self.glow[i].copy()
        else:
            n, alb, gl, em = np.zeros_like(p), np.zeros_like(p), np.zeros(len(p), F), np.zeros(len(p), F)
        n[fl] = (0, 1, 0)
        chk = ((np.floor(p[fl, 0] / self.tile) + np.floor(p[fl, 2] / self.tile)) % 2)[:, None]
        alb[fl] = self.floor * (1 - chk) + self.floor2 * chk
        gl[fl], em[fl] = self.fgloss, 0
        return p, n, alb, gl, em

    def light(self, p, n, v, alb, gl, em):
        """Sun with soft shadows, sky light with contact shadows, lamp balls, a highlight."""
        ndl = np.clip(n @ self.sun, 0, 1)
        sh = np.ones(len(p), F)
        occ = np.zeros(len(p), F)
        lamp = np.zeros_like(p)
        for k in range(len(self.C)):
            oc = p - self.C[k]
            r = self.R[k]
            l2 = np.sum(oc * oc, -1)
            b = oc @ self.sun                                   # soft shadow of ball k (after I. Quilez)
            h = b * b - (l2 - r * r)
            dd = np.sqrt(np.maximum(0, r * r - h)) - r
            tt = -b - np.sqrt(np.maximum(h, 0))
            s = np.clip(8 * dd / np.maximum(tt, 1e-4), 0, 1)
            sh *= np.where(tt < 0, 1, s * s * (3 - 2 * s))
            ln = np.sqrt(l2)
            ndr = np.sum(n * -oc, -1)
            occ += np.clip(ndr, 0, None) * (r * r) / np.maximum(ln ** 3, 1e-4)
            if self.glow[k] > 0:
                fall = self.glow[k] * r * r / np.maximum(l2 - r * r * F(0.6), F(0.05))
                lamp += self.col[k] * (np.clip(ndr / np.maximum(ln, 1e-4), 0, 1) * fall)[:, None]
        up = (0.5 + 0.5 * n[:, 1])[:, None]
        amb = (self.zenith * up + self.horizon * (1 - up) * F(0.6)) * np.clip(1 - occ, 0.05, 1)[:, None]
        hv = self.sun - v
        hv = hv / np.maximum(np.linalg.norm(hv, axis=-1, keepdims=True), 1e-6)
        spec = (np.clip(np.sum(n * hv, -1), 0, 1) ** (24 + 300 * gl) * gl * sh * 3)[:, None]
        direct = self.sun_color * (ndl * sh)[:, None]
        return alb * (direct + amb * F(0.9) + lamp) + self.sun_color * spec + alb * (em * (F(0.7) + F(0.6) * np.clip(-np.sum(n * v, -1), 0, 1) ** 2))[:, None]

    def trace(self, o, d, bounce=True):
        t, idx = _hit(o, d, self.C, self.R)
        out = self.sky(d, bounce)
        m = idx >= 0
        if not m.any():
            return out
        om, dm, tm = o[m], d[m], t[m]
        p, n, alb, gl, em = self.surface(om, dm, tm, idx[m])
        c = self.light(p, n, dm, alb, gl, em)
        if bounce:
            g = gl > 0.02
            if g.any():
                dn = np.sum(dm[g] * n[g], -1, keepdims=True)
                rd = dm[g] - 2 * dn * n[g]
                f = (gl[g] * (F(0.3) + F(0.7) * (1 - np.clip(-dn[:, 0], 0, 1)) ** 3))[:, None]
                c[g] = c[g] * (1 - f) + self.trace(p[g] + n[g] * F(2e-3), rd, False) * f
        if self.fog > 0:
            k = (1 - np.exp(-self.fog * tm))[:, None]
            c = c * (1 - k) + self.horizon * k
        out[m] = c
        return out


def render(balls, cam, sun=(0.5, 0.7, -0.4), sun_color="#fff1d6", sky=("#f2a65a", "#1b2a5c"), floor="#2b2f3a",
           floor2=None, tile=2.0, gloss=0.35, fog=0.02, stars=0.0, bloom=True, size=(W, H), quality=1.0) -> Image.Image:
    """Draw the balls as seen from `cam`; returns an RGB image of `size` (the whole pad by default).

    sun: direction to the sun (below the horizon = night; raise `stars` then).   sky: (horizon, zenith).
    floor2: a second colour makes a checkerboard of `tile`-sized squares.        gloss: the floor's.
    fog: 0 = none, 0.05 = thick.    quality: 0.5 is 4x faster and softer, 2 is sharper and 4x slower.
    """
    w, h = max(8, int(size[0] * quality)), max(8, int(size[1] * quality))
    eye, f, right, up, th = _basis(cam)
    u = (np.arange(w, dtype=F) + F(0.5)) / w * 2 - 1
    v = 1 - (np.arange(h, dtype=F) + F(0.5)) / h * 2
    uu, vv = np.meshgrid(u * F(th * w / h), v * F(th))
    d = f + uu.reshape(-1, 1) * right + vv.reshape(-1, 1) * up
    d = (d / np.linalg.norm(d, axis=-1, keepdims=True)).astype(F)
    world = _World(balls, sun, sun_color, sky, floor, floor2, tile, gloss, fog, stars)
    if world.sun[1] < 0:                                       # night: the sun lights nothing
        world.sun_color = world.sun_color * F(max(0.0, 1 + world.sun[1] * 6))
    c = world.trace(np.broadcast_to(eye, d.shape).copy(), d)
    hot = np.clip(c - 1.1, 0, 4)
    c = c / (1 + c * F(0.55))                                  # roll the highlights off
    img = Image.fromarray((np.clip(c, 0, 1) ** F(1 / 2.2) * 255).astype(np.uint8).reshape(h, w, 3), "RGB")
    if bloom and hot.max() > 0:
        halo = Image.fromarray((np.clip(hot * F(0.5), 0, 1) ** F(1 / 2.2) * 255).astype(np.uint8).reshape(h, w, 3), "RGB")
        img = ImageChops.add(img, halo.filter(ImageFilter.GaussianBlur(max(2, h // 28))))
    return img if (w, h) == tuple(size) else img.resize(size, Image.LANCZOS)


def unproject(cam, x, y, height=0.0, size=(W, H)):
    """The 3D point under canvas pixel (x, y) on the level plane y = `height`, or None when the pixel looks at the sky.
    With `key_box` this puts a thing exactly in a key: `ray.unproject(cam, x0 + 32, y0 + 32, r)`."""
    eye, f, right, up, th = _basis(cam)
    d = f + (x - size[0] / 2) / (size[1] / 2) * th * right + (size[1] / 2 - y) / (size[1] / 2) * th * up
    if d[1] > -1e-5:
        return None
    k = (height - eye[1]) / d[1]
    return tuple(float(v) for v in eye + d * k)
