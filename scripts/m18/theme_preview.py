"""True-size pad preview for judging a theme: every key rendered at its real 64 px, pasted with
bezel gaps, then upscaled 3x with hard pixels so each LCD pixel shows. Nothing is sent to the pad.
    python theme_preview.py <theme-name> <out.png> [json-options]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m18d
from m18lib import render as R
from m18lib.device import MockDevice
from PIL import Image

name, out = sys.argv[1], Path(sys.argv[2])
opts = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
d = m18d.Daemon(HERE / "macros.yaml", MockDevice(), 0)
d.load_config(initial=True)
d.cfg["settings"]["theme"] = dict(name=name, **opts)
for k, kd in m18d.C.resolved_keys(d.cfg, d.page).items():
    if kd.get("live"):
        d._run_live((d.page, k), k, d.page, kd["live"])
gap = 18
pad = Image.new("RGB", (5 * 64 + 4 * gap + 2 * gap, 3 * 64 + 2 * gap + 2 * gap), "#08080c")
for k in range(1, 16):
    face = d.face_for(k) or {}
    tile = R.render_face(face, d.base, d.cfg["settings"], key=k)
    r, c = divmod(k - 1, 5)
    pad.paste(tile, (gap + c * (64 + gap), gap + r * (64 + gap)))
pad.resize((pad.width * 3, pad.height * 3), Image.NEAREST).save(out)
print(out)
