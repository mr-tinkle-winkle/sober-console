"""
The toggle key (F9 by default), read straight from the keyboards via evdev.

Wayland has no global key listening for ordinary apps, and a KDE global shortcut would
*swallow* F9 instead of letting Sober see it.  Reading /dev/input/event* is passive -- the key
still reaches the game -- and needs membership of the ``input`` group (the NixOS module adds
it; afterglow and puppetry do the same).

Every keyboard-like device gets a QSocketNotifier, so there are no threads.  Devices are
re-scanned every few seconds to pick up hot-plugged keyboards.  ``capture_next()`` makes the
next key press (any key) come back through ``captured`` instead -- used by the Settings
page's "rebind" button.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QSocketNotifier, QTimer, Signal

logger = logging.getLogger("sober_console.hotkey")

DEBOUNCE_S = 0.2


class KeyListener(QObject):
    pressed = Signal()
    captured = Signal(str)       # an evdev key name, e.g. "KEY_F10"
    status = Signal(str)         # "" = fine, otherwise a human-readable problem

    def __init__(self, key_name: str = "KEY_F9", parent=None):
        super().__init__(parent)
        self._evdev = None
        self._ecodes = None
        self._code = None
        self._devices: dict = {}                 # path -> (InputDevice, QSocketNotifier)
        self._capture = False
        self._last = 0.0
        self._key_name = key_name
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.rescan)

    # -- lifecycle ---------------------------------------------------------------------
    def start(self) -> None:
        try:
            import evdev
            from evdev import ecodes
        except ImportError as e:
            self.status.emit(f"evdev is not available ({e}); toggle with `sober-console toggle`")
            return
        self._evdev, self._ecodes = evdev, ecodes
        self.set_key(self._key_name)
        self.rescan()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        for path in list(self._devices):
            self._drop(path)

    def set_key(self, name: str) -> None:
        self._key_name = name
        if self._ecodes is None:
            return
        code = self._ecodes.ecodes.get(name)
        if code is None:
            self.status.emit(f"unknown key {name!r}; using KEY_F9")
            self._key_name = "KEY_F9"
            code = self._ecodes.KEY_F9
        self._code = code

    def capture_next(self) -> None:
        self._capture = True

    # -- devices ---------------------------------------------------------------------
    def rescan(self) -> None:
        if self._evdev is None:
            return
        try:
            paths = set(self._evdev.list_devices())
        except OSError:
            paths = set()
        for gone in set(self._devices) - paths:
            self._drop(gone)
        denied = 0
        for path in sorted(paths - set(self._devices)):
            try:
                dev = self._evdev.InputDevice(path)
                keys = dev.capabilities().get(self._ecodes.EV_KEY, [])
                if self._ecodes.KEY_A not in keys and (self._code is None or self._code not in keys):
                    dev.close()
                    continue                     # not a keyboard
            except PermissionError:
                denied += 1
                continue
            except OSError:
                continue
            notifier = QSocketNotifier(dev.fd, QSocketNotifier.Type.Read, self)
            notifier.activated.connect(lambda *_a, p=path: self._on_readable(p))
            self._devices[path] = (dev, notifier)
        if not self._devices and denied:
            self.status.emit("cannot read /dev/input (add your user to the 'input' group); "
                             "toggle with `sober-console toggle` meanwhile")
        elif self._devices:
            self.status.emit("")

    def _drop(self, path: str) -> None:
        dev, notifier = self._devices.pop(path, (None, None))
        if notifier is not None:
            notifier.setEnabled(False)
            notifier.deleteLater()
        if dev is not None:
            try:
                dev.close()
            except OSError:
                pass

    # -- events --------------------------------------------------------------------------
    def _on_readable(self, path: str) -> None:
        entry = self._devices.get(path)
        if entry is None:
            return
        dev = entry[0]
        try:
            for ev in dev.read():
                if ev.type == self._ecodes.EV_KEY and ev.value == 1:      # key down
                    self._handle_key(ev.code)
        except BlockingIOError:
            pass
        except OSError:                                                    # unplugged
            self._drop(path)

    def key_name(self, code: int) -> "str | None":
        name = self._ecodes.KEY.get(code)
        if isinstance(name, (list, tuple)):
            name = name[0] if name else None
        return name if isinstance(name, str) and name.startswith("KEY_") else None

    def _handle_key(self, code: int) -> None:
        if self._capture:
            name = self.key_name(code)
            if name:
                self._capture = False
                self.captured.emit(name)
            return
        if code == self._code:
            now = time.monotonic()
            if now - self._last >= DEBOUNCE_S:     # input-remapper etc. can echo a key twice
                self._last = now
                self.pressed.emit()
