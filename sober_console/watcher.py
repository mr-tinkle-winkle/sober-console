"""
``sober-console watch``: the always-on part, kept deliberately tiny.

A few MB of plain Python (no Qt imported) that checks every couple of seconds whether Sober
is running.  While it is, the real UI process (``sober-console run``) is kept alive; when
Sober closes, the UI is stopped.  So nothing Qt-related, no input-device reads and no log
tailing exist while Sober is closed.

Respawn rules: if the UI crashes while Sober is still running it is restarted after a short
delay; if it exits cleanly (exit code 0 -- e.g. ``sober-console quit``) it is left alone until
Sober itself restarts.
"""
from __future__ import annotations

import fcntl
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import sober

logger = logging.getLogger("sober_console.watcher")

RESPAWN_DELAY = 5.0


def ui_command() -> "list[str]":
    exe = os.environ.get("SOBER_CONSOLE_BIN")      # set by the flake's wrapper
    if exe:
        return [exe, "run"]
    return [sys.executable, "-m", "sober_console", "run"]


def _lock_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / f"sober-console-watch-{os.getuid()}.lock"


def is_running() -> bool:
    """Is a watcher (``sober-console watch``) currently running for this user?"""
    try:
        fh = open(_lock_path(), "a")
    except OSError:
        return False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True                        # someone else holds the lock
    fcntl.flock(fh, fcntl.LOCK_UN)
    return False


def acquire_lock():
    """Only one watcher per user.  Returns the open lock file, or None if one is running."""
    fh = open(_lock_path(), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


class Watcher:
    def __init__(self, *, running=sober.running, spawn=None, clock=time.monotonic):
        self._running = running
        self._spawn = spawn or (lambda: subprocess.Popen(ui_command()))
        self._clock = clock
        self.child = None
        self._done = False            # UI exited cleanly this Sober session: leave it be
        self._next_spawn = 0.0

    def step(self) -> None:
        up = self._running()
        now = self._clock()
        alive = self.child is not None and self.child.poll() is None
        if self.child is not None and not alive:
            code = self.child.returncode
            self.child = None
            if code == 0:
                self._done = True
            else:
                logger.warning("UI exited with code %s; restarting in %.0fs", code, RESPAWN_DELAY)
                self._next_spawn = now + RESPAWN_DELAY
        if not up:
            self._done = False
            if alive:
                self.stop_child()
            return
        if self.child is None and not self._done and now >= self._next_spawn:
            logger.info("Sober is running: starting the UI")
            self.child = self._spawn()

    def stop_child(self) -> None:
        child, self.child = self.child, None
        if child is None or child.poll() is not None:
            return
        logger.info("Sober closed: stopping the UI")
        child.terminate()
        try:
            child.wait(5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def watch(poll: float = 2.0) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    lock = acquire_lock()
    if lock is None:
        logger.error("another sober-console watcher is already running")
        return 1
    w = Watcher()
    stop = {"now": False}

    def _bye(*_):
        stop["now"] = True

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    logger.info("watching for Sober (every %.0fs)", poll)
    while not stop["now"]:
        w.step()
        time.sleep(poll)
    w.stop_child()
    return 0
