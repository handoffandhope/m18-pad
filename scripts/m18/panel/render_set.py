"""Validate a macros.yaml-shaped file and render pictures of its pages, without touching any daemon.

    python render_set.py --home <M18_HOME> --yaml <file> [--out-dir D] [--pages home|all|a,b] [--validate-only]

Prints one JSON object: {ok, errors, pages, home, files: {page: png}}.
Validation is the daemon's own loader (m18lib.config.validate) with <home> as the base folder, so a set
is judged as if it were <home>/macros.yaml. Rendering builds a daemon around a mock device (like
`m18ctl preview --offline`) and never starts it. Live plugins that only read local data
(sysstats, codex face) run; the others get a stand-in face so a thumbnail never talks to Discord,
spawns Haiku calls or grabs the LED ring.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REAL_LIVE = {"sysstats.tile", "ring.codex_face"}
STAND_IN = {
    "sessions.slot": {"icon": "mdi:robot-outline", "label": "Agent"},
    "ring.status": {"icon": "mdi:chart-donut", "label": "Agents"},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--yaml", required=True)
    ap.add_argument("--out-dir")
    ap.add_argument("--pages", default="home")
    ap.add_argument("--validate-only", action="store_true")
    a = ap.parse_args()
    home = Path(a.home).resolve()
    sys.path.insert(0, str(home))
    os.environ.setdefault("M18_SESSIONS_DRY", "1")
    import yaml
    from m18lib import config as C

    try:
        raw = yaml.safe_load(Path(a.yaml).read_text(encoding="utf-8"))
        cfg = C.validate(raw, home)
    except yaml.YAMLError as e:
        return out({"ok": False, "errors": [f"YAML syntax: {e}"]})
    except C.ConfigError as e:
        return out({"ok": False, "errors": list(e.errors)})
    except Exception as e:  # noqa: BLE001 - a broken file must come back as an error, not a crash
        return out({"ok": False, "errors": [f"{type(e).__name__}: {e}"]})
    res = {"ok": True, "errors": [], "pages": list(cfg["pages"]), "home": cfg["settings"]["home"], "files": {}}
    if a.validate_only:
        return out(res)

    from m18d import Daemon
    from m18lib.device import MockDevice

    d = Daemon(home / "macros.yaml", MockDevice(), 0)
    d.log = lambda *x: None
    d.cfg = cfg
    d.page = cfg["settings"]["home"]
    real = d.plugin_fn

    def plugin_fn(name):
        if name in REAL_LIVE:
            return real(name)
        face = STAND_IN.get(name, {})
        return lambda ctx, **_: dict(face)
    d.plugin_fn = plugin_fn

    want = res["pages"] if a.pages == "all" else [res["home"]] if a.pages == "home" else a.pages.split(",")
    od = Path(a.out_dir or ".")
    od.mkdir(parents=True, exist_ok=True)
    for p in want:
        if p not in cfg["pages"]:
            continue
        sys.stdout = sys.stderr                     # plugins may print; keep stdout for the JSON
        try:
            res["files"][p] = str(d.preview(p, od / f"{p}.png"))
        finally:
            sys.stdout = sys.__stdout__
    return out(res)


def out(obj) -> int:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()
    os._exit(0 if obj.get("ok") else 1)            # don't wait on plugin threads


if __name__ == "__main__":
    main()
