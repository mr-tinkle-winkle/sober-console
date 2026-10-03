"""
The UI process: owns the overlay, the log tailer, the toggle key and a tiny control socket.

It is started by ``sober-console watch`` only while Sober is running, and also exits by itself
once Sober has been gone for a few seconds (``run --standalone`` skips that, for trying it out).
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from . import config as config_mod
from . import focus, ipc, layershell, sober
from .filters import from_config
from .hotkey import KeyListener
from .layout import (Rect, ScreenInfo, anchor_for_rect, overlay_rect, pick_screen, reference_rect,
                     screen_of)
from .model import ConsoleModel
from .tailer import LogTailer
from .ui import ConsoleOverlay

logger = logging.getLogger("sober_console.app")

FILTER_FIELDS = {"levels", "query", "dedupe", "script_only", "auto_mute_chatty",
                 "muted_groups", "muted_tags", "exclude"}
PLACEMENT_FIELDS = {"monitor", "anchor", "offset_x", "offset_y", "width_pct", "height_pct", "follow_window"}
LOOK_FIELDS = {"opacity_pct", "font_size", "show_tags", "auto_copy"}
TRACK_INTERVAL_MS = 1000
GEOMETRY_REFRESH_S = 5.0       # how often to re-read Sober's window position while the overlay is up
SHOW_FALLBACK_MS = 1500        # show anyway if the first focus probe never answers
REMAP_DELAY_MS = 120


class FocusProbe(QObject):
    """Runs the (slow, subprocess-based) active-window query off the UI thread."""

    done = Signal(object, bool)          # (WindowInfo | None, was it a full probe?)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="focus")
        self._busy = False

    def request(self, full: bool = True) -> bool:
        """``full``: class/pid/geometry too (~0.7s).  Otherwise only the active window's id."""
        if self._busy:
            return False
        self._busy = True
        self._pool.submit(self._run, full)
        return True

    def _run(self, full: bool) -> None:
        try:
            info = focus.query_active() if full else focus.query_active_id()
        except Exception:  # noqa: BLE001 -- never let the worker die silently with _busy stuck
            logger.exception("focus probe failed")
            info = None
        self._busy = False
        self.done.emit(info, full)


class ConsoleApp(QObject):
    def __init__(self, cfg: config_mod.Config, *, standalone: bool = False, use_layer: bool = False):
        super().__init__()
        self.cfg = cfg
        self.standalone = standalone
        self.layer = use_layer
        self.sober_rect: "Rect | None" = None
        self._applied: "tuple | None" = None
        self._pending_toggle = False
        self._wanted = False                # the user has the overlay "open" (it may be auto-hidden)
        self._auto_hidden = False           # hidden only because Sober isn't the active window
        self._show_after_probe = False      # a show request is waiting for the first focus probe
        self._remapping = False
        self._origin = (0, 0)               # top-left of the monitor the layer surface covers
        self._verdicts: "dict[str, bool]" = {}   # window id -> "is Sober's window"
        self._last_full = 0.0
        self._misses = 0
        self._dirty = False                 # a setting changed since the config was last written
        self._notes: "dict[str, str]" = {}
        self._kdotool_ok = focus.kdotool_available()

        log_dir = Path(cfg.log_dir).expanduser() if cfg.log_dir else sober.default_log_dir()
        self.log_dir = log_dir
        self.tailer = LogTailer(log_dir)
        self.model = ConsoleModel(from_config(cfg), max_lines=cfg.max_lines, display_max=cfg.display_max)
        self.overlay = ConsoleOverlay(cfg, self.model)
        if not self.layer:
            flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            if cfg.keyboard == "never":
                flags |= Qt.WindowType.WindowDoesNotAcceptFocus
                self.overlay.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.overlay.setWindowFlags(flags)

        self.overlay.closeRequested.connect(self.hide_overlay)
        self.overlay.clearRequested.connect(self._clear)
        self.overlay.settingChanged.connect(self._on_setting)
        self.overlay.rebindRequested.connect(self._start_rebind)
        self.overlay.dragFinished.connect(self._on_drag_finished)

        self.probe = FocusProbe(self)
        self.probe.done.connect(self._on_probe)

        self.keys = KeyListener(cfg.toggle_key, self)
        self.keys.pressed.connect(self._on_key)
        self.keys.captured.connect(self._on_captured)
        self.keys.status.connect(lambda text: self._note("keys", text))

        self._tail_timer = QTimer(self)
        self._tail_timer.setInterval(250)
        self._tail_timer.timeout.connect(self._tick_tail)
        self._presence_timer = QTimer(self)
        self._presence_timer.setInterval(2000)
        self._presence_timer.timeout.connect(self._tick_presence)
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(TRACK_INTERVAL_MS)
        self._track_timer.timeout.connect(self._tick_track)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)

        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)

    # -- lifecycle ---------------------------------------------------------------------------
    def start(self) -> bool:
        QLocalServer.removeServer(ipc.socket_path())      # stale socket from a crash
        if not self.server.listen(ipc.socket_path()):
            logger.error("cannot listen on %s: %s", ipc.socket_path(), self.server.errorString())
            return False
        if self.cfg.focus_check == "kdotool" and not self._kdotool_ok:
            self._note("focus", "kdotool not found: the toggle key works regardless of the focused window")
        self.keys.start()
        self._refresh_key_label()
        self._tail_timer.start()
        if not self.standalone:
            self._presence_timer.start()
        self._tick_tail()
        if self._kdotool_ok:
            self.probe.request(full=True)     # learn where Sober's window is before anything is shown
        return True

    def stop(self) -> None:
        self._save()
        self.keys.stop()
        self.server.close()
        self.overlay.hide()

    # -- notices -----------------------------------------------------------------------------
    def _note(self, key: str, text: str) -> None:
        if text:
            self._notes[key] = text
        else:
            self._notes.pop(key, None)
        self.overlay.set_notice("  ·  ".join(self._notes.values()))

    # -- timers --------------------------------------------------------------------------------
    @Slot()
    def _tick_tail(self) -> None:
        res = self.tailer.poll()
        if res.reset:
            self.model.clear()
            if self.overlay.isVisible():
                self.overlay.clear_view()
        if res.lines:
            ops = self.model.ingest(res.lines, backlog=res.backlog)
            if self.overlay.isVisible():
                self.overlay.apply_ops(ops)
        if res.path is None:
            self._note("log", f"no log file in {self.log_dir}")
        else:
            self._note("log", res.path.name)

    @Slot()
    def _tick_presence(self) -> None:
        if sober.running():
            self._misses = 0
            return
        self._misses += 1
        if self._misses >= 2:
            logger.info("Sober is no longer running; exiting")
            QApplication.quit()

    @Slot()
    def _tick_track(self) -> None:
        """While the overlay is wanted: keep an eye on the active window.  Mostly a cheap id-only
        query; a full one now and then (or when Sober's position is unknown) for its geometry."""
        if not self._wanted or self._pending_toggle or not self._kdotool_ok:
            return
        c = self.cfg
        need_geometry = self.sober_rect is None or (
            c.follow_window and time.monotonic() - self._last_full >= GEOMETRY_REFRESH_S)
        if need_geometry or c.hide_when_unfocused:
            self.probe.request(full=need_geometry)

    # -- toggle key -----------------------------------------------------------------------------
    @Slot()
    def _on_key(self) -> None:
        if self.overlay.isVisible() and self.overlay.isActiveWindow():
            self.hide_overlay()          # the overlay itself has the keyboard: just close it
            return
        if self.cfg.focus_check == "off" or not self._kdotool_ok:
            self.toggle()
            return
        # If a (tracking) probe is already in flight, its result will pick this up -- the
        # key press must never be dropped just because the worker happened to be busy.
        self._pending_toggle = True
        self.probe.request(full=True)

    def _sober_screen_name(self) -> "str | None":
        screens, _ = self._screens()
        s = screen_of(screens, self.sober_rect)
        return s.name if s else None

    @Slot(object, bool)
    def _on_probe(self, info, full: bool) -> None:
        if info is None:
            focused = None
            self._note("focus", "could not read the focused window (run `sober-console focus`)")
        else:
            self._note("focus", "")
            if full:
                focused = focus.is_sober_window(info, sober.pids(), self.cfg.window_match)
                self._verdicts[info.wid] = focused
                self._last_full = time.monotonic()
                if focused and info.geometry:
                    self.sober_rect = Rect(*info.geometry)
            else:
                focused = self._verdicts.get(info.wid)
                if focused is None:                   # a window we haven't identified yet
                    if self.probe.request(full=True):
                        return
        if self._pending_toggle and (full or focused is not None or info is None):
            self._pending_toggle = False
            if focused:
                self.toggle()
            elif info is not None:
                logger.info("toggle key ignored: focused window is %s, not Sober",
                            f"class={info.wclass!r} name={info.name!r} pid={info.pid}" if full else f"id={info.wid}")
        if self._show_after_probe:
            self._show_after_probe = False
            self._show_now()
            return
        self._apply_focus_policy(focused)
        if full and focused and self.overlay.isVisible():
            self._place()                             # tracking: follow the window if it moved

    def _apply_focus_policy(self, focused: "bool | None") -> None:
        if not self._wanted or focused is None:
            return
        if self._auto_hidden and focused:
            logger.info("Sober is focused again: showing the overlay")
            self._auto_hidden = False
            self._show_now()
        elif self.overlay.isVisible() and focus.should_auto_hide(
                enabled=self.cfg.hide_when_unfocused, sober_focused=focused,
                overlay_screen=self._applied[0] if self._applied else None,
                sober_screen=self._sober_screen_name(),
                overlay_active=self.overlay.isActiveWindow() or self.overlay.dragging):
            logger.info("another window is active: hiding the overlay until Sober is focused again")
            self._auto_hidden = True
            self.overlay.hide()

    # -- show / hide ------------------------------------------------------------------------------
    def _shown(self) -> bool:
        """Logically open: visible, or hidden only temporarily (auto-hide / re-mapping)."""
        return self._wanted

    def toggle(self) -> None:
        self.hide_overlay() if self._shown() else self.request_show()

    def request_show(self) -> None:
        """Show the overlay.  If Sober's position isn't known yet (first use), ask where it is
        first: placing on a guess and then moving to another monitor is the flash-and-vanish."""
        if self._shown():
            if not self.overlay.isVisible() and not self._remapping:
                self._auto_hidden = False
                self._show_now()
            return
        self._wanted = True
        self._auto_hidden = False
        if self.sober_rect is None and self._kdotool_ok:
            self._show_after_probe = True
            self.probe.request(full=True)
            QTimer.singleShot(SHOW_FALLBACK_MS, self._show_fallback)
            self._track_timer.start()
            return
        self._show_now()

    @Slot()
    def _show_fallback(self) -> None:
        if self._show_after_probe:
            logger.info("focus probe did not answer in time; showing anyway")
            self._show_after_probe = False
            self._show_now()

    def show_overlay(self) -> None:
        self.request_show()

    def _show_now(self) -> None:
        if not self._wanted or self.overlay.isVisible():
            return
        self._place(initial=True)
        self.overlay.show()
        self.overlay.raise_()
        self.overlay.rebuild_view()
        self.overlay.scroll_to_end()
        self._track_timer.start()
        logger.info("overlay shown on %s", self._applied[0] if self._applied else "?")

    def hide_overlay(self) -> None:
        self._wanted = False
        self._auto_hidden = False
        self._show_after_probe = False
        self._track_timer.stop()
        self.overlay.hide()

    def _remap(self) -> None:
        """A layer surface can't change monitor while mapped: unmap now, map again a moment later."""
        logger.info("moving the overlay to another monitor")
        self._remapping = True
        self.overlay.hide()
        QTimer.singleShot(REMAP_DELAY_MS, self._finish_remap)

    @Slot()
    def _finish_remap(self) -> None:
        self._remapping = False
        if self._wanted and not self._auto_hidden:
            self._show_now()

    # -- placement ----------------------------------------------------------------------------------
    def _screens(self) -> "tuple[list[ScreenInfo], dict]":
        primary = QGuiApplication.primaryScreen()
        infos, by_name = [], {}
        for s in QGuiApplication.screens():
            g = s.geometry()
            infos.append(ScreenInfo(s.name(), Rect(g.x(), g.y(), g.width(), g.height()), s is primary))
            by_name[s.name()] = s
        return infos, by_name

    def _place(self, initial: bool = False) -> None:
        screens, by_name = self._screens()
        if not screens:
            return
        c = self.cfg
        screen = pick_screen(screens, c.monitor, self.sober_rect)
        ref = reference_rect(screen, self.sober_rect, screens, c.follow_window)
        rect = overlay_rect(c.anchor, c.offset_x, c.offset_y, c.width_pct, c.height_pct, ref, screen.rect)
        key = (screen.name, rect, c.keyboard)
        if not initial and key == self._applied:
            return
        ov = self.overlay
        visible = ov.isVisible()
        if self.layer and visible and self._applied is not None and self._applied[0] != screen.name:
            self._remap()                  # (re-places itself when it maps again)
            return
        ov.winId()                         # create the native window (layer config needs it)
        handle = ov.windowHandle()
        if handle is not None:
            handle.setScreen(by_name[screen.name])
        mode = layershell.KEYBOARD_ON_DEMAND if c.keyboard == "on_demand" else layershell.KEYBOARD_NEVER
        if self.layer and handle is not None:
            # The surface covers the whole monitor; the panel is a widget inside it.
            self._origin = (screen.rect.x, screen.rect.y)
            ov.set_layer_mode(True)
            ov.resize(screen.rect.w, screen.rect.h)
            ov.set_panel_rect(rect.x - screen.rect.x, rect.y - screen.rect.y, rect.w, rect.h)
            if visible:
                layershell.set_keyboard(handle, mode)
                ov.update()
            else:
                layershell.configure(handle, mode)
        else:
            ov.set_layer_mode(False)
            ov.resize(rect.w, rect.h)
            ov.move(rect.x, rect.y)
        self._applied = key
        self.overlay.settings_page.set_monitors([s.name for s in screens])

    @Slot()
    def _on_drag_finished(self) -> None:
        """Remember where the panel was dropped: nearest corner of the reference area + offsets."""
        screens, _ = self._screens()
        x, y, w, h = self.overlay.panel_geometry()
        if self.layer:
            rect = Rect(self._origin[0] + x, self._origin[1] + y, w, h)
        else:
            rect = Rect(x, y, w, h)
        screen = screen_of(screens, rect)
        if screen is None:
            return
        c = self.cfg
        ref = reference_rect(screen, self.sober_rect, screens, c.follow_window)
        c.anchor, c.offset_x, c.offset_y = anchor_for_rect(rect, ref)
        c.sanitize()
        self._applied = (screen.name, rect, c.keyboard)
        self.overlay.settings_page.load()
        self._dirty = True
        self._save_timer.start()
        logger.info("overlay moved: anchor=%s offset=(%s, %s)", c.anchor, c.offset_x, c.offset_y)

    # -- settings ------------------------------------------------------------------------------------
    @Slot(str)
    def _on_setting(self, name: str) -> None:
        if name in FILTER_FIELDS:
            self.model.filters = from_config(self.cfg)
            self.overlay.rebuild_view()
        elif name in PLACEMENT_FIELDS:
            self._place()
        elif name in LOOK_FIELDS:
            self.overlay.apply_look()
            if name == "show_tags":
                self.overlay.rebuild_view()
        elif name == "keyboard":
            self._place()
        elif name == "hide_when_unfocused":
            if not self.cfg.hide_when_unfocused and self._auto_hidden:
                self._auto_hidden = False
                self._show_now()
        self._dirty = True
        self._save_timer.start()

    @Slot()
    def _clear(self) -> None:
        self.model.clear()
        self.overlay.clear_view()

    def _save(self) -> None:
        """Write the config -- only if something was changed from inside the overlay, so a
        hand-edited config.toml (comments and all) is left alone otherwise."""
        if not self._dirty:
            return
        try:
            config_mod.save(self.cfg)
            self._dirty = False
        except OSError as e:
            logger.warning("could not save config: %s", e)

    def reload_config(self) -> None:
        new = config_mod.load()
        for f in fields(config_mod.Config):
            setattr(self.cfg, f.name, getattr(new, f.name))
        self.keys.set_key(self.cfg.toggle_key)
        self._refresh_key_label()
        self.model.filters = from_config(self.cfg)
        self.overlay.apply_look()
        self.overlay.sync_filter_widgets()
        self.overlay.settings_page.load()
        self.overlay.filters_page.load()
        if self.overlay.isVisible():
            self._place()
            self.overlay.rebuild_view()

    # -- rebinding the toggle key -------------------------------------------------------------------------
    def _refresh_key_label(self) -> None:
        self.overlay.settings_page.set_toggle_key_label(
            f"{self.cfg.toggle_key.removeprefix('KEY_')}   (click to rebind)")

    @Slot()
    def _start_rebind(self) -> None:
        self.overlay.settings_page.set_toggle_key_label("Press any key…")
        self.keys.capture_next()

    @Slot(str)
    def _on_captured(self, name: str) -> None:
        self.cfg.toggle_key = name
        self.keys.set_key(name)
        self._refresh_key_label()
        self._dirty = True
        self._save_timer.start()

    # -- control socket ---------------------------------------------------------------------------------
    @Slot()
    def _on_connection(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is None:
            return
        sock.readyRead.connect(lambda s=sock: self._handle_ipc(s))

    def _handle_ipc(self, sock) -> None:
        cmd = bytes(sock.readAll()).decode(errors="replace").strip().lower()
        if cmd == "toggle":
            self.toggle()
            reply = "visible" if self._shown() else "hidden"
        elif cmd == "show":
            self.request_show()
            reply = "visible"
        elif cmd == "hide":
            self.hide_overlay()
            reply = "hidden"
        elif cmd == "reload":
            self.reload_config()
            reply = "reloaded"
        elif cmd == "status":
            reply = ("visible" if self.overlay.isVisible() else "auto-hidden" if self._auto_hidden else "hidden") + f" log={self.tailer.path or 'none'}"
        elif cmd == "quit":
            reply = "bye"
            QTimer.singleShot(0, QApplication.quit)
        else:
            reply = f"unknown command: {cmd}"
        sock.write((reply + "\n").encode())
        sock.flush()
        sock.disconnectFromServer()


def run(*, standalone: bool = False) -> int:
    """Entry point of the UI process."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = config_mod.load()

    # Must happen before the QApplication exists: makes every window of this process a
    # layer-shell surface (the process has exactly one).  Only on a Wayland session.
    wants_layer = bool(os.environ.get("WAYLAND_DISPLAY")) and layershell.enable_in_this_process()

    app = QApplication([])
    app.setApplicationName("sober-console")
    app.setQuitOnLastWindowClosed(False)
    use_layer = wants_layer and QGuiApplication.platformName().startswith("wayland")
    if os.environ.get("WAYLAND_DISPLAY") and not use_layer:
        logger.warning("layer-shell unavailable: the overlay will be a normal window and cannot be "
                       "positioned over Sober on Wayland")

    if ipc.send_command("status") is not None:
        logger.info("another sober-console UI is already running")
        return 0

    console = ConsoleApp(cfg, standalone=standalone, use_layer=use_layer)
    if not console.start():
        return 1
    code = app.exec()
    console.stop()
    return code
