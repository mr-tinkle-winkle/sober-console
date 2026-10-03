"""
Turn raw Sober log lines into structured ``LogLine`` records.

The exact line format of Sober's log file could not be confirmed while writing this (the
format of Roblox's own engine lines inside it in particular), so the parser is deliberately
tolerant: it recognises several known shapes, and anything it does not recognise still shows
up as a plain "info" line instead of being dropped.  ``sober-console inspect`` prints how
your real log is being parsed, so the patterns below can be tuned against it.

Shapes understood, in the order they are tried:

  1. Roblox client log      2024-05-01T12:00:00.123Z,12.345678,00001e8c,6 [FLog::Output] hello
  2. Sober's own log        info: app: lifecycle: app_start delta=0.0001ms
  3. Android logcat         W/RobloxApp( 1234): something
  4. Anything with a leading ISO timestamp (the rest is parsed again as 2-4)
  5. Continuation lines     indented lines / "Stack Begin" blocks inherit the previous line's level
  6. Everything else        level guessed from keywords, otherwise "info"

``level`` is one of LEVELS, mirroring the four message types of Roblox's own developer
console: output ("Messages"), info, warning, error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LEVELS = ("output", "info", "warning", "error")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_ROBLOX = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z?,"
    r"(?P<uptime>[\d.]+),(?P<thread>[0-9a-fA-F]+),(?P<lvl>\d+)\s*"
    r"\[(?P<tag>[^\]]+)\]\s?(?P<msg>.*)$"
)
_ISO_PREFIX = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)Z?[\s,:\-]*(?P<rest>.*)$"
)
_SOBER = re.compile(
    r"^(?P<lvl>info|warn|warning|error|err|debug|trace|fatal)\s*:\s*(?P<rest>.*)$", re.IGNORECASE
)
_SOBER_TAG2 = re.compile(r"^(?P<a>[A-Za-z_][\w.\-]*):\s+(?P<b>[A-Za-z_][\w.\-]*):\s+(?P<m>.*)$")
_SOBER_TAG1 = re.compile(r"^(?P<a>[A-Za-z_][\w.\-]*):\s+(?P<m>.*)$")
_LOGCAT = re.compile(r"^(?P<lvl>[VDIWEF])/(?P<tag>[^:(\s]+)\s*(?:\(\s*\d+\))?:\s?(?P<msg>.*)$")
_CONTINUATION = re.compile(r"^(\s+\S|Stack (Begin|End)|Script '|\s*at\s)")

_ERROR_WORDS = re.compile(r"\b(error|exception|fatal|traceback|segfault|crash(ed)?)\b", re.IGNORECASE)
_WARN_WORDS = re.compile(r"\bwarn(ing)?\b", re.IGNORECASE)

# Tags that carry what a game's scripts print (the dev console's "Messages").
_OUTPUT_TAGS = {"flog::output", "output", "print", "script", "lua", "flog::scriptoutput"}


@dataclass(frozen=True, slots=True)
class LogLine:
    raw: str
    time: str      # "HH:MM:SS.mmm" when the line carried a timestamp, else ""
    level: str     # one of LEVELS
    tag: str       # "FLog::Output", "app: lifecycle", "W/RobloxApp" ... or ""
    message: str

    @property
    def key(self) -> tuple:
        """What "identical" means for collapsing repeats."""
        return (self.level, self.tag, self.message)


def is_output_tag(tag: str) -> bool:
    t = tag.lower()
    return t in _OUTPUT_TAGS or t.endswith("::output")


def _guess_level(text: str) -> str:
    if _ERROR_WORDS.search(text):
        return "error"
    if _WARN_WORDS.search(text):
        return "warning"
    return "info"


def _level_from_tag(tag: str, message: str) -> str:
    low = tag.lower()
    if is_output_tag(tag):
        return "output"          # never guess from a script's own text
    if "error" in low:
        return "error"
    if "warn" in low:
        return "warning"
    return _guess_level(message)


def _short_time(t: str) -> str:
    t = t.replace(",", ".")
    if "." in t:
        head, frac = t.split(".", 1)
        return f"{head}.{(frac + '000')[:3]}"
    return t


_SOBER_LEVELS = {
    "info": "info", "debug": "info", "trace": "info",
    "warn": "warning", "warning": "warning",
    "error": "error", "err": "error", "fatal": "error",
}
_LOGCAT_LEVELS = {"V": "info", "D": "info", "I": "info", "W": "warning", "E": "error", "F": "error"}


def _parse_body(text: str, time: str) -> "LogLine | None":
    """Shapes 1-3 (no timestamp prefix handling).  Returns None when nothing matches."""
    m = _ROBLOX.match(text)
    if m:
        tag, msg = m["tag"], m["msg"]
        return LogLine(text, _short_time(m["time"]), _level_from_tag(tag, msg), tag, msg)

    m = _SOBER.match(text)
    if m:
        level = _SOBER_LEVELS[m["lvl"].lower()]
        rest = m["rest"]
        t2 = _SOBER_TAG2.match(rest)
        if t2:
            tag, msg = f"{t2['a']}: {t2['b']}", t2["m"]
        else:
            t1 = _SOBER_TAG1.match(rest)
            tag, msg = (t1["a"], t1["m"]) if t1 else ("", rest)
        if level == "info":
            level = _level_from_tag(tag, msg)    # an "info:" line that says "error" is shown as one
        return LogLine(text, time, level, tag, msg)

    m = _LOGCAT.match(text)
    if m:
        level = _LOGCAT_LEVELS[m["lvl"]]
        return LogLine(text, time, level, f"{m['lvl']}/{m['tag']}", m["msg"])
    return None


def parse_line(raw: str, prev: "LogLine | None" = None) -> LogLine:
    """Parse one raw log line.  ``prev`` (the previous parsed line) lets continuation lines
    such as stack traces inherit its level and tag."""
    text = _ANSI.sub("", raw.rstrip("\r\n"))

    hit = _parse_body(text, "")
    if hit:
        return hit

    m = _ISO_PREFIX.match(text)
    if m:
        rest, time = m["rest"], _short_time(m["time"])
        hit = _parse_body(rest, time)
        if hit:
            return LogLine(text, hit.time or time, hit.level, hit.tag, hit.message)
        return LogLine(text, time, _guess_level(rest), "", rest)

    if prev is not None and _CONTINUATION.match(text):
        return LogLine(text, prev.time, prev.level, prev.tag, text.strip())

    return LogLine(text, "", _guess_level(text), "", text)
