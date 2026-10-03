"""
Follow Sober's newest log file (pure Python, no Qt).

``LogTailer.poll()`` is cheap enough to call a few times a second: it stats the current file,
reads whatever was appended since last time, and only rescans the directory every couple of
seconds to notice a new session's file.  Partial last lines are held back until their newline
arrives.  When a different file takes over (a new Sober launch), or the current one shrinks,
the result is flagged ``reset`` so the UI can clear itself and read the new file from its start.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PollResult:
    lines: list = field(default_factory=list)
    reset: bool = False          # a new file took over: clear the console first
    backlog: bool = False        # these lines are history from before we attached
    path: "Path | None" = None


class LogTailer:
    def __init__(self, log_dir: "str | Path", *, backlog_bytes: int = 512 * 1024, rescan_every: float = 2.0):
        self.log_dir = Path(log_dir)
        self.backlog_bytes = backlog_bytes
        self.rescan_every = rescan_every
        self.path: "Path | None" = None
        self._ident: "tuple | None" = None
        self._pos = 0
        self._partial = b""
        self._last_scan = 0.0
        self._attached = False

    # -- file selection ---------------------------------------------------------------
    def _newest(self) -> "Path | None":
        best, best_key = None, None
        try:
            with os.scandir(self.log_dir) as it:
                for e in it:
                    if not e.is_file() or not e.name.endswith((".log", ".txt")):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    key = (st.st_mtime_ns, e.name == "latest.log")
                    if best_key is None or key > best_key:
                        best, best_key = Path(e.path), key
        except OSError:
            return None
        return best

    def _switch(self, path: Path) -> bool:
        try:
            st = path.stat()
        except OSError:
            return False
        ident = (st.st_dev, st.st_ino)
        if ident == self._ident:
            self.path = path
            return False
        self.path, self._ident, self._partial = path, ident, b""
        return True

    # -- polling ----------------------------------------------------------------------
    def poll(self) -> PollResult:
        res = PollResult()
        now = time.monotonic()
        if self.path is None or now - self._last_scan >= self.rescan_every:
            self._last_scan = now
            newest = self._newest()
            if newest is not None and self._switch(newest):
                res.reset = self._attached          # first attach is not a "reset"
                self._pos = 0
                first_attach = not self._attached
                self._attached = True
                res.backlog = first_attach
                return self._read(res, from_start=True, tail_only=first_attach)

        if self.path is None:
            return res
        return self._read(res, from_start=False, tail_only=False)

    def _read(self, res: PollResult, *, from_start: bool, tail_only: bool) -> PollResult:
        res.path = self.path
        try:
            st = self.path.stat()
        except OSError:
            return res
        if not from_start and st.st_size < self._pos:        # truncated in place
            self._pos, self._partial = 0, b""
            res.reset = True
        if from_start and tail_only and st.st_size > self.backlog_bytes:
            self._pos = st.st_size - self.backlog_bytes
            skip_first = True                                  # we landed mid-line
        else:
            skip_first = False
        if st.st_size <= self._pos:
            return res
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._pos)
                chunk = fh.read(st.st_size - self._pos)
        except OSError:
            return res
        self._pos += len(chunk)
        data = self._partial + chunk
        parts = data.split(b"\n")
        self._partial = parts.pop()                            # incomplete tail (maybe empty)
        if skip_first and parts:
            parts.pop(0)
        res.lines = [p.decode("utf-8", "replace").rstrip("\r") for p in parts]
        return res
