"""
Where the overlay goes (pure geometry, no Qt).

Everything is in the compositor's global logical coordinates, which is what both KWin (via
kdotool) and Qt's ``QScreen.geometry()`` use.

  * ``pick_screen``      which monitor: the one Sober is on, "the other one", the primary, or one
                         named by connector (DP-1, HDMI-A-1 ...)
  * ``reference_rect``   what the overlay is placed relative to: Sober's window when the overlay is
                         on Sober's monitor, otherwise the whole monitor
  * ``overlay_rect``     size + anchor + offsets -> the overlay's rectangle, kept on screen
"""
from __future__ import annotations

from dataclasses import dataclass

ANCHORS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")
MIN_W, MIN_H = 360, 220


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def intersect(self, other: "Rect") -> "Rect":
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2 = min(self.x + self.w, other.x + other.w)
        y2 = min(self.y + self.h, other.y + other.h)
        return Rect(x1, y1, max(0, x2 - x1), max(0, y2 - y1))


@dataclass(frozen=True)
class ScreenInfo:
    name: str
    rect: Rect
    primary: bool = False


def screen_of(screens: "list[ScreenInfo]", rect: "Rect | None") -> "ScreenInfo | None":
    """The screen holding the centre of ``rect`` (falling back to the largest overlap)."""
    if rect is None or not screens:
        return None
    for s in screens:
        if s.rect.contains(rect.cx, rect.cy):
            return s
    best = max(screens, key=lambda s: s.rect.intersect(rect).w * s.rect.intersect(rect).h)
    return best if best.rect.intersect(rect).w > 0 else None


def pick_screen(screens: "list[ScreenInfo]", mode: str, sober_rect: "Rect | None") -> "ScreenInfo":
    """``mode``: "same" | "other" | "primary" | a connector name.  Always returns a screen."""
    if not screens:
        raise ValueError("no screens")
    primary = next((s for s in screens if s.primary), screens[0])
    sober_screen = screen_of(screens, sober_rect) or primary
    mode = (mode or "same").strip()
    if mode == "same":
        return sober_screen
    if mode == "primary":
        return primary
    if mode == "other":
        others = [s for s in screens if s is not sober_screen]
        return others[0] if others else sober_screen
    for s in screens:
        if s.name == mode:
            return s
    return sober_screen            # unknown / unplugged monitor name


def reference_rect(screen: ScreenInfo, sober_rect: "Rect | None", screens: "list[ScreenInfo]",
                   follow_window: bool = True) -> Rect:
    if follow_window and sober_rect is not None and screen_of(screens, sober_rect) is screen:
        clipped = sober_rect.intersect(screen.rect)
        if clipped.w > 0 and clipped.h > 0:
            return clipped
    return screen.rect


def overlay_rect(anchor: str, off_x: int, off_y: int, width_pct: int, height_pct: int,
                 ref: Rect, bounds: Rect) -> Rect:
    w = max(MIN_W, round(ref.w * width_pct / 100))
    h = max(MIN_H, round(ref.h * height_pct / 100))
    w, h = min(w, bounds.w), min(h, bounds.h)

    anchor = anchor if anchor in ANCHORS else "top-left"
    if anchor.endswith("left"):
        x = ref.x + off_x
    elif anchor.endswith("right"):
        x = ref.x + ref.w - w - off_x
    else:
        x = ref.x + (ref.w - w) // 2 + off_x
    if anchor.startswith("top"):
        y = ref.y + off_y
    elif anchor.startswith("bottom"):
        y = ref.y + ref.h - h - off_y
    else:
        y = ref.y + (ref.h - h) // 2 + off_y

    x = min(max(x, bounds.x), bounds.x + bounds.w - w)     # never leave the monitor
    y = min(max(y, bounds.y), bounds.y + bounds.h - h)
    return Rect(int(x), int(y), int(w), int(h))


def layer_margins(rect: Rect, screen: Rect) -> "tuple[int, int]":
    """(top, left) margins of a top|left-anchored layer surface placing ``rect`` on ``screen``."""
    return rect.y - screen.y, rect.x - screen.x
