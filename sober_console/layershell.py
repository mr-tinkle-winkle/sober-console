"""
Layer-shell for the overlay window (Wayland).

A normal Qt window can't place itself on Wayland and won't sit above a fullscreen game.  The
overlay has to be a *layer-shell surface* on the ``overlay`` layer.  KDE's implementation is
LayerShellQt, which has a C++ API only, so ``native/sober_console_layershell.cpp`` is a small
shim (built in the flake) loaded here with ctypes.  This follows afterglow's indicator.

Rules (same as afterglow's):
  * the shim and PySide6 must use the SAME Qt -- the flake builds both from one nixpkgs;
  * ``configure`` must run after the QWindow exists (``widget.winId()``) but before ``show()``;
  * ``QT_WAYLAND_SHELL_INTEGRATION=layer-shell`` makes EVERY window of a process a layer
    surface, so it is only set in the UI process, which has exactly one window.

The library is found via ``SOBER_CONSOLE_LAYERSHELL_LIB`` (set by the flake's wrapper) or next
to the package (a developer build).  No library -> ``available()`` is False and the overlay
falls back to a plain always-on-top window (fine on X11; on Wayland it cannot be positioned).
"""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

logger = logging.getLogger("sober_console.layershell")

ANCHOR_TOP, ANCHOR_BOTTOM, ANCHOR_LEFT, ANCHOR_RIGHT = 1, 2, 4, 8   # LayerShellQt::Window::Anchor
KEYBOARD_NEVER, KEYBOARD_ON_DEMAND = 0, 1
LIB_NAME = "libsober_console_layershell.so"

_lib = None
_tried = False


def _candidates() -> "list[Path]":
    out = []
    env = os.environ.get("SOBER_CONSOLE_LAYERSHELL_LIB")
    if env:
        out.append(Path(env))
    here = Path(__file__).resolve().parent
    out += [here / LIB_NAME, here.parent / "native" / "build" / LIB_NAME, here.parent / "native" / LIB_NAME]
    return out


def _load():
    global _lib, _tried
    if _tried:
        return _lib
    _tried = True
    for path in _candidates():
        if not path.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            lib.sober_console_configure.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 6
            lib.sober_console_configure.restype = ctypes.c_int
            lib.sober_console_set_keyboard.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.sober_console_set_keyboard.restype = ctypes.c_int
            _lib = lib
            return _lib
        except (OSError, AttributeError) as e:
            logger.warning("layer-shell shim %s could not be loaded: %s", path, e)
    return None


def available() -> bool:
    return _load() is not None


def enable_in_this_process() -> bool:
    """Make Qt's Wayland platform plugin use the layer-shell integration.  UI process only,
    before the QApplication is created."""
    if not available():
        return False
    os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"
    return True


def _ptr(window):
    try:
        import shiboken6
        return ctypes.c_void_p(shiboken6.getCppPointer(window)[0])
    except Exception as e:  # noqa: BLE001
        logger.warning("layer-shell: no native pointer for the window: %s", e)
        return None


def configure(window, keyboard: int) -> bool:
    """Make the (created, not yet shown) QWindow an overlay-layer surface that covers its whole
    output (all four edges anchored, no margins, exclusive zone -1 so panels don't shrink it).
    The visible panel is drawn inside it and the rest is click-through (see ui.ConsoleOverlay), so
    dragging only moves a widget -- never a layer-shell margin."""
    lib = _load()
    ptr = _ptr(window) if lib is not None and window is not None else None
    if ptr is None:
        return False
    anchors = ANCHOR_TOP | ANCHOR_BOTTOM | ANCHOR_LEFT | ANCHOR_RIGHT
    rc = lib.sober_console_configure(ptr, anchors, 0, 0, 0, 0, int(keyboard))
    if rc != 0:
        logger.warning("layer-shell configure failed (rc=%s)", rc)
    return rc == 0


def set_keyboard(window, keyboard: int) -> bool:
    lib = _load()
    ptr = _ptr(window) if lib is not None and window is not None else None
    return ptr is not None and lib.sober_console_set_keyboard(ptr, int(keyboard)) == 0
