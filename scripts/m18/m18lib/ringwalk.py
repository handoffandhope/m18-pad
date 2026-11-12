"""ringwalk: map each ring LED to a place on the pad, using the pad itself as the input.

The daemon takes the pad over (a modal), lights one LED white with the rest off, and the owner
presses the LCD key nearest to it (or b1/b2/b3 for LEDs along the bottom/front, where the buttons
are closer than any key). Hold any key 1 s to go back one LED; press b1 three times quickly, or
leave it 60 s without a press, to abort (nothing is saved). The result is state/ring-map.json.
"""
from __future__ import annotations

import threading
import time

from . import ringmap as RM

HOLD_BACK_S = 1.0           # hold any key this long to go back one LED
ABORT_TAPS = 3              # this many b1 taps ...
ABORT_WITHIN_S = 0.8        # ... within this many seconds abort (answering b1 for three LEDs in a row takes longer)
IDLE_TIMEOUT_S = 60.0
OWNER = "ringwalk"


class RingWalk:
    def __init__(self, daemon, idle_timeout: float = IDLE_TIMEOUT_S):
        self.d = daemon
        self.idle = float(idle_timeout)
        self.state = "running"              # running | done | aborted
        self.reason = None
        self.step = 0
        self.answers: list[int | None] = [None] * RM.N_LEDS
        self.path: str | None = None
        self.tok = None
        self.last = time.time()
        self.down: dict[int, float] = {}
        self.taps: list[float] = []
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        self.tok = self.d.modal_start(self._faces(), on_key=self._on_key, owner=OWNER,
                                      timeout=120.0, home_exit=False)
        self._light()
        threading.Thread(target=self._watch, daemon=True, name="ringwalk").start()

    def info(self) -> dict:
        with self.lock:
            return {"state": self.state, "reason": self.reason, "step": self.step, "total": RM.N_LEDS,
                    "answers": list(self.answers), "path": self.path, "idle_timeout": self.idle}

    def abort(self, reason="aborted"):
        with self.lock:
            if self.state != "running":
                return
            self.state, self.reason = "aborted", reason
        self._cleanup()
        self.d.log(f"ringwalk aborted: {reason}")

    def _finish(self):
        rm = RM.build_from_walk([int(a) for a in self.answers])
        p = RM.save(self.d.base, rm)
        with self.lock:
            self.state, self.path = "done", str(p)
        self._cleanup()
        self.d.log(f"ringwalk done: {p}")

    def _cleanup(self):
        self.d.leds_release(OWNER)
        self.d.modal_end("ringwalk", tok=self.tok)

    def _watch(self):
        while True:
            time.sleep(0.05)
            with self.lock:
                if self.state != "running":
                    return
                idle = time.time() - self.last
            md = self.d.modal
            if md is None or md["tok"] != self.tok:
                self.abort("pad taken over or dark mode")
            elif idle > self.idle:
                self.abort(f"no press for {self.idle:g} s")
            else:
                try:                                    # keep the lit LED (ttl) and the modal alive
                    self.d.modal_update(self.tok, timeout=120.0)
                    if self.d.leds_owner is None:
                        self._light()
                except Exception as e:
                    self.abort(f"error: {e}")

    # ------------------------------------------------------------------ input
    def _on_key(self, ctx, k, down, t):
        try:
            with self.lock:
                if self.state != "running":
                    return
                self.last = time.time()
                if down:
                    self.down[k] = t
                    return
                t0 = self.down.pop(k, None)
                held = (t - t0) if t0 is not None else 0.0
                if held >= HOLD_BACK_S:
                    if self.step > 0:
                        self.step -= 1
                        self.answers[self.step] = None
                    abort = False
                elif k == 16 and self._tap(t):
                    abort = True
                else:
                    abort = False
                    if 1 <= k <= 18:
                        self.answers[self.step] = k
                        self.step += 1
                done = self.step >= RM.N_LEDS
            if abort:
                self.abort(f"{ABORT_TAPS} quick presses of b1")
            elif done:
                self._finish()
            else:
                self.d.modal_update(self.tok, self._faces(), timeout=120.0)
                self._light()
        except Exception as e:
            self.abort(f"error: {e}")

    def _tap(self, t) -> bool:
        self.taps = [x for x in self.taps if t - x <= ABORT_WITHIN_S] + [t]
        return len(self.taps) >= ABORT_TAPS

    # ------------------------------------------------------------------ output
    def _light(self):
        with self.lock:
            i = min(self.step, RM.N_LEDS - 1)
        frame = [None] * RM.N_LEDS
        frame[i] = "#ffffff"
        self.d.leds_set(frame, ttl=min(self.idle + 5.0, 70.0), owner=OWNER, priority=2)

    def _faces(self) -> dict:
        n = min(self.step + 1, RM.N_LEDS)
        # Each key reads on its own: black bars sit between the screens, so a sentence never runs across keys.
        return {8: {"glyph": f"{n}/{RM.N_LEDS}", "label": "TAP NEAR", "bg": "#0e5a2a", "font_size": 16},
                3: {"icon": "mdi:led-on", "label": "1 LED lit", "bg": "#123a5e"},
                13: {"icon": "mdi:undo", "label": "Hold=back", "bg": "#3a3a3a"},
                # the 3 screenless buttons are valid answers for LEDs along the bottom edge (the user missed this once)
                12: {"icon": "mdi:arrow-down-bold", "label": "b1-3 too", "bg": "#123a5e"}}
