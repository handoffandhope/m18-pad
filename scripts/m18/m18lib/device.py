"""Device backends: the real M18 via the VSDinside Python SDK, or a mock.

Both expose the same tiny interface used by the daemon:
    start(on_press(key:int, down:bool, t:float=None, state:int=None)), set_face(key, PIL.Image), flush(),
    set_brightness(pct), set_rgb(hex|None, brightness), close(), connected
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path


class MockDevice:
    name = "mock"

    def __init__(self):
        self.faces = {}
        self.brightness = None
        self.rgb = None
        self.connected = True
        self._cb = None
        self.leds = None
        self.led_bri = None

    @property
    def ring(self):
        return self.rgb

    def set_led_brightness(self, pct):
        self.led_bri = pct                  # like the real device: the page colour in .rgb is untouched

    def set_leds(self, colors):
        """Per-LED frame (mock records it so the ringtest path can be checked)."""
        self.leds = [tuple(c) for c in colors]
        return 0

    def start(self, on_press):
        self._cb = on_press

    def press(self, key: int, hold_s: float = 0.0):
        """Simulate a press (used by the HTTP API / tests)."""
        if self._cb:
            self._cb(key, True)
            if hold_s:
                time.sleep(hold_s)
            self._cb(key, False)

    def cached(self, h) -> bool:
        return False

    def set_face(self, key, img=None, h=None):
        self.faces[key] = img

    def flush(self):
        pass

    @property
    def current_brightness(self):
        return self.brightness

    def set_brightness(self, pct):
        self.brightness = pct

    def set_rgb(self, color, brightness=None):
        self.rgb = (color, brightness)

    def close(self):
        self.connected = False


class M18Device:
    """Wraps StreamDock.Devices.StreamDockM18 from the VSDinside SDK.

    The SDK writes temp JPEGs into the CWD and owns a reader thread that calls
    our callback. Hotplug is handled by DeviceManager.listen(); on re-attach we
    re-push every face so the pad never comes back blank.
    """
    name = "m18"

    def __init__(self, sdk_path: str | None = None):
        sdk = sdk_path or os.environ.get("M18_SDK")
        if sdk and sdk not in sys.path:
            sys.path.insert(0, sdk)
        from StreamDock.DeviceManager import DeviceManager  # noqa
        from StreamDock.InputTypes import EventType, ButtonKey  # noqa
        self._BK = ButtonKey
        self._jpeg: dict = {}
        self._DM, self._ET = DeviceManager, EventType
        self.mgr = DeviceManager()
        self.dev = None
        self.connected = False
        self.faces: dict[int, bytes] = {}
        self._dirty: set[int] = set()
        self._lock = threading.RLock()
        self._cb = None
        self._brightness = 70
        self._rgb = (None, None)
        self._tmp = Path(tempfile.mkdtemp(prefix="m18faces_"))
        os.chdir(self._tmp)   # SDK drops temp files in CWD; keep them out of the config dir

    # -- lifecycle -------------------------------------------------------
    def start(self, on_press):
        self._cb = on_press
        devs = [d for d in self.mgr.enumerate() if type(d).__name__ == "StreamDockM18"]
        if devs:
            self._attach(devs[0])
        else:
            print("[m18] no M18 found yet; waiting for hotplug", flush=True)
        t = threading.Thread(target=self.mgr.listen, kwargs=dict(
            on_device_added=self._on_added, on_device_removed=self._on_removed,
            auto_open=False), daemon=True)
        t.start()

    def _attach(self, d):
        with self._lock:
            if not d.open():
                raise RuntimeError("could not open M18 (is VSD Craft running? quit it first)")
            d.init()
            self.dev = d
            self.connected = True
            d.set_key_callback(self._on_event)
            d.raw_read_callback = self._on_raw
            print(f"[m18] connected fw={getattr(d, 'firmware_version', '?')} "
                  f"sn={getattr(d, 'serial_number', '?')}", flush=True)
            d.set_brightness(self._brightness)
            if self._rgb[0]:
                self.set_rgb(*self._rgb)
            self._dirty = set(self.faces)
            self.flush()

    def _on_added(self, d):
        if type(d).__name__ == "StreamDockM18" and not self.connected:
            try:
                self._attach(d)
            except Exception as e:
                print(f"[m18] attach failed: {e}", flush=True)

    def _on_removed(self, d):
        if d is self.dev:
            self.connected = False
            self.dev = None
            print("[m18] disconnected", flush=True)

    # Packet sniffer for protocol questions (chords, unknown reports). Off unless the file raw-packets.on
    # exists beside m18d.py; writes every non-ACK packet's first 32 bytes to raw-packets.log (1 MB cap).
    _RAW_DIR = Path(__file__).resolve().parent.parent

    def _on_raw(self, device, data):
        try:
            if not (self._RAW_DIR / "raw-packets.on").exists():
                return
            b = bytes(data[:32])
            if len(b) > 9 and b[9] == 0xFF:
                return
            p = self._RAW_DIR / "raw-packets.log"
            if p.exists() and p.stat().st_size > 1_000_000:
                return
            with open(p, "a", encoding="ascii") as f:
                f.write(f"{int(time.time() * 1000)} {len(data)} {b.hex(' ')}\n")
        except Exception:
            pass

    def _on_event(self, device, event):
        t = time.time()                     # stamp first: everything after may take time
        try:
            if event.event_type == self._ET.BUTTON and self._cb:
                self._cb(int(event.key.value), event.state == 1, t, int(event.state))
        except Exception as e:
            print(f"[m18] callback error: {e}", flush=True)

    # -- output ----------------------------------------------------------
    # Faces are encoded once to native JPEG bytes (64x64, no rotation/flip on the M18),
    # kept in memory by face hash, and streamed straight to the transport: no temp files,
    # and a repeating animation costs one USB write per frame after its first loop.
    _JPEG_CACHE_MAX = 512

    def cached(self, h) -> bool:
        return h is not None and h in self._jpeg

    def set_face(self, key, img=None, h=None):
        if key > 15:
            return                      # physical buttons have no screen
        data = self._jpeg.get(h) if h is not None else None
        if data is None:
            buf = io.BytesIO()
            if img.mode != "RGB":
                img = img.convert("RGB")
            if img.size != (64, 64):
                img = img.resize((64, 64))
            img.save(buf, "JPEG", quality=90)
            data = buf.getvalue()
            if h is not None:
                if len(self._jpeg) >= self._JPEG_CACHE_MAX:
                    self._jpeg.pop(next(iter(self._jpeg)))
                self._jpeg[h] = data
        with self._lock:
            if self.faces.get(key) is not data:
                self.faces[key] = data
                self._dirty.add(key)

    def flush(self):
        with self._lock:
            if not (self.dev and self.connected and self._dirty):
                return
            for k in sorted(self._dirty):
                hw = self.dev.get_image_key(self._BK(k))
                self.dev.transport.set_key_image_stream(self.faces[k], hw)
            self.dev.refresh()
            self._dirty.clear()

    @property
    def current_brightness(self):
        return self._brightness

    def set_brightness(self, pct):
        self._brightness = int(pct)
        if self.dev:
            self.dev.set_brightness(self._brightness)

    def set_rgb(self, color, brightness=None):
        self._rgb = (color, brightness)
        if not self.dev:
            return
        if color in (None, "off"):
            self.dev.set_led_brightness(0)
            self._led_off = True
            return
        c = color.lstrip("#")
        if len(c) == 3:
            c = "".join(x * 2 for x in c)
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        self.dev.set_led_color(r, g, b)
        if brightness is None and getattr(self, "_led_off", False):
            brightness = 60                     # coming back from "off": don't stay dark
        if brightness is not None:
            self.dev.set_led_brightness(int(brightness))
        self._led_off = False

    @property
    def ring(self):
        return self._rgb

    def set_led_brightness(self, pct):
        if self.dev:
            self.dev.set_led_brightness(int(pct))

    def set_leds(self, colors):
        """One 24-LED frame through the SDK transport's per-LED call (what the vendor SDK
        exposes for N4 Pro). The M18 device class gates this off via a feature flag, so we call
        the transport object directly; whether the M18 firmware honours it is what `m18ctl
        ringtest` finds out. No raw HID writes here. Returns the SDK call's return value."""
        if not self.dev:
            raise RuntimeError("device not connected")
        with self._lock:
            return self.dev.transport.set_single_led_color([tuple(int(v) for v in c) for c in colors])

    def close(self):
        if self.dev:
            try:
                self.dev.clearAllIcon()
                self.dev.close()
            except Exception:
                pass
        self.connected = False
