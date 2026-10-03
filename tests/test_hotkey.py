"""
KeyListener logic with the real evdev constants but fake devices.  Reading actual /dev/input
devices is NOT covered (no input devices where this was written).
"""
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

evdev = pytest.importorskip("evdev")
pytest.importorskip("PySide6")
from evdev import ecodes
from PySide6.QtWidgets import QApplication

from sober_console import hotkey
from sober_console.hotkey import KeyListener


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def listener(qapp, monkeypatch):
    monkeypatch.setattr(evdev, "list_devices", lambda: [])
    kl = KeyListener("KEY_F9")
    kl.start()
    yield kl
    kl.stop()


def collect(signal):
    got = []
    signal.connect(got.append) if signal is not None else None
    return got


def test_toggle_key_press_is_debounced(listener):
    pressed = []
    listener.pressed.connect(lambda: pressed.append(1))
    listener._handle_key(ecodes.KEY_F9)
    listener._handle_key(ecodes.KEY_F9)                 # an echo from input-remapper / a second device
    assert len(pressed) == 1
    time.sleep(hotkey.DEBOUNCE_S + 0.05)
    listener._handle_key(ecodes.KEY_F9)
    assert len(pressed) == 2


def test_other_keys_are_ignored(listener):
    pressed = []
    listener.pressed.connect(lambda: pressed.append(1))
    listener._handle_key(ecodes.KEY_F10)
    listener._handle_key(ecodes.KEY_A)
    assert pressed == []


def test_rebind_captures_the_next_key_and_swallows_it(listener):
    pressed, captured = [], []
    listener.pressed.connect(lambda: pressed.append(1))
    listener.captured.connect(captured.append)
    listener.capture_next()
    listener._handle_key(ecodes.KEY_F10)
    assert captured == ["KEY_F10"] and pressed == []
    listener.set_key("KEY_F10")
    listener._handle_key(ecodes.KEY_F10)                # normal operation resumes, on the new key
    assert len(pressed) == 1


def test_capture_ignores_keys_without_a_KEY_name(listener):
    captured = []
    listener.captured.connect(captured.append)
    listener.capture_next()
    listener._handle_key(0x110)                         # BTN_LEFT: a mouse button, not a key
    assert captured == []
    listener._handle_key(ecodes.KEY_GRAVE)              # still waiting: the next real key is captured
    assert captured == ["KEY_GRAVE"]


def test_unknown_key_name_falls_back_to_f9_with_a_warning(listener):
    status = []
    listener.status.connect(status.append)
    listener.set_key("KEY_DOES_NOT_EXIST")
    assert any("unknown key" in s for s in status)
    pressed = []
    listener.pressed.connect(lambda: pressed.append(1))
    listener._handle_key(ecodes.KEY_F9)
    assert pressed == [1]


def test_missing_input_permission_is_reported(qapp, monkeypatch):
    monkeypatch.setattr(evdev, "list_devices", lambda: ["/dev/input/event0", "/dev/input/event1"])

    def deny(path):
        raise PermissionError(path)

    monkeypatch.setattr(evdev, "InputDevice", deny)
    kl = KeyListener("KEY_F9")
    status = []
    kl.status.connect(status.append)
    kl.start()
    kl.stop()
    assert status and "input' group" in status[-1]


class FakeNotifier:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, v):
        self.enabled = v

    def deleteLater(self):
        pass


class FakeDevice:
    def __init__(self, events=(), error=None):
        self._events, self._error, self.closed = events, error, False

    def read(self):
        if self._error:
            raise self._error
        yield from self._events

    def close(self):
        self.closed = True


def ev(code, value=1, type_=ecodes.EV_KEY):
    return SimpleNamespace(type=type_, code=code, value=value)


def test_on_readable_only_reacts_to_key_down(listener):
    pressed = []
    listener.pressed.connect(lambda: pressed.append(1))
    listener._devices["/dev/x"] = (FakeDevice([ev(ecodes.KEY_F9, 0), ev(ecodes.KEY_F9, 2),     # up, autorepeat
                                               ev(ecodes.KEY_F9, 1, ecodes.EV_SYN),             # wrong type
                                               ev(ecodes.KEY_F9, 1)]), FakeNotifier())
    listener._on_readable("/dev/x")
    assert pressed == [1]


def test_on_readable_survives_would_block_and_drops_unplugged_devices(listener):
    listener._devices["/dev/a"] = (FakeDevice(error=BlockingIOError()), FakeNotifier())
    listener._on_readable("/dev/a")
    assert "/dev/a" in listener._devices

    gone = FakeDevice(error=OSError(19, "No such device"))
    listener._devices["/dev/b"] = (gone, FakeNotifier())
    listener._on_readable("/dev/b")
    assert "/dev/b" not in listener._devices and gone.closed
