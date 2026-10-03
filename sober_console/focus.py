"""
Which window is focused, and is it Sober?  (KDE Plasma, via kdotool.)

Wayland clients cannot see other windows, so this asks KWin through ``kdotool`` -- the same
route afterglow uses.  One short shell script asks for the active window's class, title, pid
and geometry, each on a labelled line, so parsing doesn't depend on kdotool's per-command
output layout.

NOT verified against a live KWin session: the ``Position: x,y`` / ``Geometry: WxH`` layout of
``kdotool getwindowgeometry`` follows kdotool's README (and afterglow's reading of it), and
whether ``getwindowpid`` exists depends on the kdotool version.  Each field is optional: a Sober
window is recognised by its pid being inside Sober's cgroup OR its class/title matching
``window_match``.  ``sober-console focus`` prints exactly what this module sees, for debugging.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

_SCRIPT = r'''
id=$(kdotool getactivewindow) || exit 1
echo "ID=$id"
echo "CLASS=$(kdotool getwindowclassname "$id" 2>/dev/null)"
echo "NAME=$(kdotool getwindowname "$id" 2>/dev/null)"
echo "PID=$(kdotool getwindowpid "$id" 2>/dev/null)"
echo "GEOM=$(kdotool getwindowgeometry "$id" 2>/dev/null | tr '\n' ' ')"
'''


@dataclass
class WindowInfo:
    wid: str = ""
    wclass: str = ""
    name: str = ""
    pid: "int | None" = None
    geometry: "tuple | None" = None        # (x, y, w, h) in logical px
    raw: str = field(default="", repr=False)


def parse_geometry(text: str) -> "tuple | None":
    pos = re.search(r"Position:\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", text or "")
    geo = re.search(r"Geometry:\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)", text or "")
    if not pos or not geo:
        return None
    return (int(float(pos[1])), int(float(pos[2])), int(float(geo[1])), int(float(geo[2])))


def parse_probe(text: str) -> "WindowInfo | None":
    """Parse the labelled output of ``_SCRIPT``.  None when there was no active window."""
    fields = {}
    for line in (text or "").splitlines():
        key, sep, val = line.partition("=")
        if sep and key in ("ID", "CLASS", "NAME", "PID", "GEOM"):
            fields[key] = val.strip()
    if not fields.get("ID"):
        return None
    pid = fields.get("PID", "")
    return WindowInfo(
        wid=fields["ID"],
        wclass=fields.get("CLASS", ""),
        name=fields.get("NAME", ""),
        pid=int(pid) if pid.isdigit() else None,
        geometry=parse_geometry(fields.get("GEOM", "")),
        raw=text,
    )


def kdotool_available() -> bool:
    return shutil.which("kdotool") is not None


def query_active(timeout: float = 3.0) -> "WindowInfo | None":
    """Synchronous probe of the active window (run it off the UI thread)."""
    if not kdotool_available():
        return None
    try:
        out = subprocess.run(["sh", "-c", _SCRIPT], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return parse_probe(out.stdout)


def query_active_id(timeout: float = 2.0) -> "WindowInfo | None":
    """Cheap probe: only the active window's id (one kdotool call, ~1/5 of ``query_active``).
    Callers map the id to an earlier full probe's verdict."""
    if not kdotool_available():
        return None
    try:
        out = subprocess.run(["kdotool", "getactivewindow"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    wid = out.stdout.strip()
    if out.returncode != 0 or not wid:
        return None
    return WindowInfo(wid=wid, raw=out.stdout)


def should_auto_hide(*, enabled: bool, sober_focused: "bool | None", overlay_screen: "str | None",
                     sober_screen: "str | None", overlay_active: bool) -> bool:
    """Hide the overlay because Sober isn't the active window?  Only on a *positive* sighting of
    another window (``sober_focused is False``; None = couldn't tell), only when the overlay is
    on the same monitor as Sober (on another monitor it stays up), and never while the overlay
    itself holds the keyboard."""
    if not enabled or sober_focused is not False or overlay_active:
        return False
    return bool(overlay_screen) and overlay_screen == sober_screen


def is_sober_window(info: "WindowInfo | None", sober_pids: "set[int]", window_match: str) -> bool:
    if info is None:
        return False
    if info.pid is not None and info.pid in sober_pids:
        return True
    try:
        rx = re.compile(window_match, re.IGNORECASE)
    except re.error:
        rx = re.compile(r"sober|roblox", re.IGNORECASE)
    return bool(rx.search(info.wclass or "") or rx.search(info.name or ""))
