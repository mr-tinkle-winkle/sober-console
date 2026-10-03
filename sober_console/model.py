"""
The console's data model (pure Python, no Qt): a ring buffer of parsed lines plus the
currently-visible, filtered, optionally de-duplicated view of it.

The UI never filters anything itself.  It calls ``ingest`` for new lines and gets back a list
of small operations to apply to its text widget:

    ("append", entry)   a new visible entry
    ("bump", entry)     the last visible entry's repeat count went up

and calls ``rebuild`` after any filter change to get the whole visible list again.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime

from .filters import FilterState, chatty_tags
from .logparse import LogLine, parse_line


@dataclass
class Entry:
    line: LogLine
    count: int = 1


class ConsoleModel:
    def __init__(self, filters: "FilterState | None" = None, *, max_lines: int = 20000, display_max: int = 5000):
        self.filters = filters or FilterState()
        self.max_lines = max_lines
        self.display_max = display_max
        self.lines: "deque[LogLine]" = deque()
        self.tag_counts: Counter = Counter()
        self.view: "deque[Entry]" = deque(maxlen=display_max)
        self.shown = 0       # lines displayed / hidden since the last rebuild (approximate
        self.hidden = 0      # once old lines have been evicted from the buffer)
        self._prev: "LogLine | None" = None
        self._since_chatty = 0

    # -- ingest -----------------------------------------------------------------------
    def ingest(self, raws, *, backlog: bool = False, now: "datetime | None" = None) -> list:
        """Parse and add raw lines; returns the ops the UI should apply (see module doc)."""
        ops: list = []
        stamp = None if backlog else (now or datetime.now()).strftime("%H:%M:%S.%f")[:-3]
        for raw in raws:
            if not raw.strip():
                continue
            ln = parse_line(raw, self._prev)
            if stamp and not ln.time:
                ln = LogLine(ln.raw, stamp, ln.level, ln.tag, ln.message)
            self._prev = ln
            self._store(ln)
            op = self._view_add(ln)
            if op:
                ops.append(op)
        self._since_chatty += len(raws)
        if self.filters.auto_mute and self._since_chatty >= 1000:
            self._refresh_chatty()
        return ops

    def _store(self, ln: LogLine) -> None:
        self.lines.append(ln)
        self.tag_counts[ln.tag] += 1
        if len(self.lines) > self.max_lines:
            old = self.lines.popleft()
            self.tag_counts[old.tag] -= 1
            if self.tag_counts[old.tag] <= 0:
                del self.tag_counts[old.tag]

    def _view_add(self, ln: LogLine):
        if not self.filters.matches(ln):
            self.hidden += 1
            return None
        self.shown += 1
        if self.filters.dedupe and self.view and self.view[-1].line.key == ln.key:
            self.view[-1].count += 1
            return ("bump", self.view[-1])
        entry = Entry(ln)
        self.view.append(entry)
        return ("append", entry)

    # -- filter changes / maintenance ------------------------------------------------
    def _refresh_chatty(self) -> None:
        self.filters.auto_muted = chatty_tags(self.tag_counts, len(self.lines))
        self._since_chatty = 0

    def rebuild(self) -> list:
        """Recompute the whole visible view from the buffer (after a filter change)."""
        if self.filters.auto_mute:
            self._refresh_chatty()
        else:
            self.filters.auto_muted = frozenset()
        self.view = deque(maxlen=self.display_max)
        self.shown = self.hidden = 0
        for ln in self.lines:
            self._view_add(ln)
        return list(self.view)

    def clear(self) -> None:
        self.lines.clear()
        self.tag_counts.clear()
        self.view.clear()
        self.shown = self.hidden = 0
        self._prev = None

    # -- introspection (for the Filters page and the status line) --------------------
    def top_tags(self, limit: int = 40) -> list:
        return [(t, n) for t, n in self.tag_counts.most_common(limit + 1) if t][:limit]

    def visible_text(self) -> str:
        out = []
        for e in self.view:
            suffix = f"  x{e.count}" if e.count > 1 else ""
            out.append(f"{e.line.time + '  ' if e.line.time else ''}{e.line.message}{suffix}")
        return "\n".join(out)
