"""Load and validate macros.yaml.

The config is the single source of truth for what the pad does. Validation is
strict and the error messages are written for an agent to read and fix: every
error names the exact path (pages.main.keys.4.action[2]) and what was expected.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

LCD_KEYS = list(range(1, 16))          # 15 LCD keys, row-major 5x3 (see layout)
BUTTON_KEYS = [16, 17, 18]             # 3 physical buttons under the screen
ALL_KEYS = LCD_KEYS + BUTTON_KEYS
KEY_ALIASES = {"b1": 16, "b2": 17, "b3": 18}

ACTION_TYPES = {
    "hotkey":  "str  e.g. 'ctrl+shift+m', 'win+shift+s', 'alt+f4', 'f13'",
    "key":     "str  single key tap, e.g. 'enter', 'f24', 'volume_mute'",
    "type":    "str  text typed as unicode keystrokes",
    "paste":   "str  text put on clipboard then ctrl+v (fast, for long text)",
    "run":     "str|list  command line, started detached (no window wait)",
    "shell":   "str  command run through cmd /c (pipes, &&, env vars)",
    "open":    "str  file, folder, URL, app path, or URI like discord:// or ms-settings:",
    "media":   "str  play_pause|next|prev|stop|vol_up|vol_down|mute",
    "page":    "str  page name, or 'back' / 'home'",
    "delay":   "int  milliseconds to wait (inside a sequence)",
    "plugin":  "str  'module.function' in plugins/, called as fn(ctx, **args)",
    "brightness": "int|str  0-100 or '+10' / '-10'",
    "rgb":     "str|dict  '#ff0066' or {color, brightness} or 'off'",
    "notify":  "str  log line + Windows toast if available",
    "dark":    "str  on|off|toggle  blank every other key, ring off, ignore other presses; the key that pressed it stays faintly lit and turns it back off",
    "countdown": "int  seconds; shows 3..2..1 on the pressed key, then continues the sequence",
}
# keys allowed alongside the action key inside one action dict
ACTION_EXTRAS = {"args", "repeat", "hold_ms"}

FACE_FIELDS = {"label", "icon", "emoji", "glyph", "bg", "fg", "label_color",
               "font_size", "label_pos", "border"}
KEY_FIELDS = FACE_FIELDS | {"action", "hold", "double", "triple", "states", "live", "comment", "physical_only"}
TAP_FIELDS = ("hold", "double", "triple")   # the gesture actions a key may add next to `action`
PAGE_FIELDS = {"keys", "rgb", "follow", "brightness", "inherit", "comment", "scene"}
SETTINGS_FIELDS = {"brightness", "rgb", "home", "hold_ms", "double_ms", "follow_focus",
                   "font", "emoji_font", "default_bg", "default_fg",
                   "key_map", "api_port", "theme", "log_raw"}

HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def norm_key(k: Any) -> int | None:
    if isinstance(k, int):
        return k if k in ALL_KEYS else None
    s = str(k).strip().lower()
    if s in KEY_ALIASES:
        return KEY_ALIASES[s]
    if s.isdigit() and int(s) in ALL_KEYS:
        return int(s)
    return None


def _check_color(v, path, errs):
    if not isinstance(v, str) or not HEX.match(v):
        errs.append(f"{path}: expected hex color like '#1e1e2e', got {v!r}")


def _check_action(a, path, errs, pages: set[str]):
    if isinstance(a, list):
        if not a:
            errs.append(f"{path}: empty action list")
        for i, step in enumerate(a):
            _check_action(step, f"{path}[{i}]", errs, pages)
        return
    if isinstance(a, str):
        errs.append(f"{path}: bare string {a!r} is ambiguous; write e.g. "
                    f"{{hotkey: {a!r}}} or {{type: {a!r}}}")
        return
    if not isinstance(a, dict):
        errs.append(f"{path}: action must be a dict or list of dicts")
        return
    kinds = [k for k in a if k in ACTION_TYPES]
    unknown = [k for k in a if k not in ACTION_TYPES and k not in ACTION_EXTRAS]
    if unknown:
        errs.append(f"{path}: unknown field(s) {unknown}. Action types: "
                    f"{sorted(ACTION_TYPES)}")
    if len(kinds) != 1:
        errs.append(f"{path}: exactly one action type per step (got {kinds or 'none'}); "
                    f"use a list for sequences")
        return
    kind = kinds[0]
    v = a[kind]
    if kind == "delay" and not (isinstance(v, int) and 0 <= v <= 60000):
        errs.append(f"{path}.delay: int ms 0-60000")
    elif kind == "media" and v not in {"play_pause", "next", "prev", "stop",
                                       "vol_up", "vol_down", "mute"}:
        errs.append(f"{path}.media: unknown {v!r}")
    elif kind == "page" and v not in pages | {"back", "home"}:
        errs.append(f"{path}.page: no page named {v!r} (pages: {sorted(pages)})")
    elif kind in ("hotkey", "key"):
        from .keys import parse_combo  # local import keeps config import-light
        try:
            parse_combo(str(v))
        except ValueError as e:
            errs.append(f"{path}.{kind}: {e}")
    elif kind == "dark" and v not in ("on", "off", "toggle"):
        errs.append(f"{path}.dark: on, off or toggle")
    elif kind == "countdown" and not (isinstance(v, int) and 1 <= v <= 30):
        errs.append(f"{path}.countdown: int seconds 1-30")
    elif kind == "plugin" and (not isinstance(v, str) or "." not in v):
        errs.append(f"{path}.plugin: expected 'module.function'")
    if "repeat" in a and not (isinstance(a["repeat"], int) and 1 <= a["repeat"] <= 100):
        errs.append(f"{path}.repeat: int 1-100")


def _check_face(d, path, errs):
    ic = d.get("icon")
    if isinstance(ic, str) and ic.startswith("mdi:"):
        from .render import mdi_codepoint, mdi_names
        if mdi_codepoint(ic[4:]) is None:
            import difflib
            near = difflib.get_close_matches(ic[4:].lower(), list(mdi_names()), n=6, cutoff=0.6)
            errs.append(f"{path}.icon: no MDI icon {ic!r}; similar: {near}")
    for c in ("bg", "fg", "label_color", "border"):
        if c in d:
            _check_color(d[c], f"{path}.{c}", errs)
    if "label_pos" in d and d["label_pos"] not in ("bottom", "top", "center", "none"):
        errs.append(f"{path}.label_pos: bottom|top|center|none")


def validate(cfg: Any, base_dir: Path | None = None) -> dict:
    """Return a normalized config or raise ConfigError with every problem found."""
    errs: list[str] = []
    if not isinstance(cfg, dict):
        raise ConfigError(["top level must be a mapping with 'settings' and 'pages'"])
    for k in cfg:
        if k not in ("settings", "pages"):
            errs.append(f"top-level: unknown section {k!r} (allowed: settings, pages)")
    settings = cfg.get("settings") or {}
    pages = cfg.get("pages") or {}
    if not isinstance(pages, dict) or not pages:
        raise ConfigError(errs + ["pages: need at least one page"])
    page_names = set(map(str, pages))

    for k in settings:
        if k not in SETTINGS_FIELDS:
            errs.append(f"settings.{k}: unknown (allowed: {sorted(SETTINGS_FIELDS)})")
    home = str(settings.get("home", next(iter(pages))))
    if home not in page_names:
        errs.append(f"settings.home: no page {home!r}")
    if "brightness" in settings and not (0 <= int(settings["brightness"]) <= 100):
        errs.append("settings.brightness: 0-100")
    for f, lo, hi in (("hold_ms", 100, 5000), ("double_ms", 80, 1000)):
        if f in settings and not (isinstance(settings[f], int) and not isinstance(settings[f], bool)
                                  and lo <= settings[f] <= hi):
            errs.append(f"settings.{f}: int ms {lo}-{hi}, got {settings[f]!r}")
    if "log_raw" in settings and not isinstance(settings["log_raw"], bool):
        errs.append("settings.log_raw: true or false")

    norm_pages: dict[str, dict] = {}
    for pname, page in pages.items():
        pname = str(pname)
        ppath = f"pages.{pname}"
        page = page or {}
        if not isinstance(page, dict):
            errs.append(f"{ppath}: must be a mapping")
            continue
        for k in page:
            if k not in PAGE_FIELDS:
                errs.append(f"{ppath}.{k}: unknown (allowed: {sorted(PAGE_FIELDS)})")
        if "inherit" in page and str(page["inherit"]) not in page_names:
            errs.append(f"{ppath}.inherit: no page {page['inherit']!r}")
        if "follow" in page:
            f = page["follow"]
            if isinstance(f, str):
                page["follow"] = [f]
            elif not (isinstance(f, list) and all(isinstance(x, str) for x in f)):
                errs.append(f"{ppath}.follow: exe name or list, e.g. ['obs64.exe']")
        keys_out: dict[int, dict] = {}
        for raw_k, key in (page.get("keys") or {}).items():
            kpath = f"{ppath}.keys.{raw_k}"
            k = norm_key(raw_k)
            if k is None:
                errs.append(f"{kpath}: key must be 1-15 (LCD) or 16-18 / b1-b3 (buttons)")
                continue
            if k in keys_out:
                errs.append(f"{kpath}: key {k} defined twice on this page")
            if key is None:            # explicit blank (useful to mask inherited key)
                keys_out[k] = {"blank": True}
                continue
            if not isinstance(key, dict):
                errs.append(f"{kpath}: must be a mapping")
                continue
            for f in key:
                if f not in KEY_FIELDS:
                    errs.append(f"{kpath}.{f}: unknown (allowed: {sorted(KEY_FIELDS)})")
            _check_face(key, kpath, errs)
            if "physical_only" in key and not isinstance(key["physical_only"], bool):
                errs.append(f"{kpath}.physical_only: true or false")
            if "action" in key:
                _check_action(key["action"], f"{kpath}.action", errs, page_names)
            for g in TAP_FIELDS:
                if g in key:
                    _check_action(key[g], f"{kpath}.{g}", errs, page_names)
            if "states" in key:
                st = key["states"]
                if not (isinstance(st, list) and len(st) >= 2):
                    errs.append(f"{kpath}.states: list of >=2 state dicts")
                else:
                    for i, s in enumerate(st):
                        if not isinstance(s, dict):
                            errs.append(f"{kpath}.states[{i}]: mapping"); continue
                        _check_face(s, f"{kpath}.states[{i}]", errs)
                        if "action" in s:
                            _check_action(s["action"], f"{kpath}.states[{i}].action",
                                          errs, page_names)
            if "live" in key:
                lv = key["live"]
                if not (isinstance(lv, dict) and isinstance(lv.get("plugin"), str)
                        and "." in lv["plugin"]):
                    errs.append(f"{kpath}.live: {{plugin: 'module.function', interval: 2}}")
            if k in BUTTON_KEYS and any(f in key for f in ("icon", "emoji", "glyph")):
                pass  # harmless: buttons have no screen, face fields only show in preview
            keys_out[k] = key
        if "scene" in page:            # shorthand: one scene file drawn across the 15 LCD keys
            sc = page["scene"]
            so = dict(sc) if isinstance(sc, dict) else {"name": sc}
            sc, hold = so.pop("name", None), so.pop("hold", False)
            if not (isinstance(sc, str) and re.fullmatch(r"[A-Za-z0-9_]+", sc)):
                errs.append(f"{ppath}.scene: a scene file name, e.g. city (scenes/city.py), or {{name: city, ...}}")
            else:
                live = {"plugin": "scene.tile", "interval": 1, "args": {"name": sc, "opts": so}}
                for k in range(1, 16):
                    kd = keys_out.get(k)
                    if kd is None:                 # a free key: the scene's picture, tap and (if asked) hold
                        kd = keys_out[k] = {"live": live, "action": {"plugin": "scene.tap", "args": {"name": sc}}}
                        if hold:
                            kd["hold"] = {"plugin": "scene.hold", "args": {"name": sc}}
                    elif not kd.get("blank") and "live" not in kd and not (set(kd) & FACE_FIELDS):
                        kd["live"] = live          # a key with only actions keeps them and shows the scene
        page = dict(page)
        page["keys"] = keys_out
        norm_pages[pname] = page

    if errs:
        raise ConfigError(errs)
    return {"settings": {**settings, "home": home}, "pages": norm_pages}


def load(path: Path) -> dict:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError([f"YAML syntax: {e}"])
    return validate(raw, Path(path).parent)


def resolved_keys(cfg: dict, page: str) -> dict[int, dict]:
    """Keys for a page after applying `inherit` chains (child wins)."""
    seen, chain = set(), []
    p = page
    while p and p not in seen:
        seen.add(p)
        chain.append(p)
        p = cfg["pages"][p].get("inherit")
    out: dict[int, dict] = {}
    for name in reversed(chain):
        out.update(cfg["pages"][name]["keys"])
    return {k: v for k, v in out.items() if not v.get("blank")}
