"""
Settings, stored as TOML in ``$XDG_CONFIG_HOME/sober-console/config.toml`` (usually
``~/.config/sober-console/config.toml``).  Unknown keys are ignored, bad values fall back to
the default, and numbers are clamped -- a hand-edited config can't make the overlay unusable.
The in-overlay Filters and Settings pages write to the same file.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path

from .layout import ANCHORS
from .logparse import LEVELS


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "sober-console" / "config.toml"


@dataclass
class Config:
    # --- input --------------------------------------------------------------------------
    toggle_key: str = "KEY_F9"        # any evdev key name, e.g. KEY_F9, KEY_GRAVE
    focus_check: str = "kdotool"      # "kdotool": only toggle while Sober is focused; "off": always
    window_match: str = "sober|roblox"  # regex on the focused window's class / title
    keyboard: str = "on_demand"       # "on_demand": clicking the overlay lets you type + Ctrl+C
                                      #   (takes keyboard focus from Sober while you do);
                                      # "never": never takes focus (copy with the button)
    auto_copy: bool = False           # copy selected text to the clipboard on mouse release

    # --- placement ----------------------------------------------------------------------
    monitor: str = "same"             # "same" (as Sober) | "other" | "primary" | connector name
    anchor: str = "top-left"
    offset_x: int = 16                # px from the anchored edge of the reference area
    offset_y: int = 16
    width_pct: int = 46               # size as a % of the reference area
    height_pct: int = 55
    follow_window: bool = True        # place relative to Sober's window (else the whole monitor)

    # --- look ---------------------------------------------------------------------------
    opacity_pct: int = 94
    font_size: int = 10
    show_tags: bool = False           # show each line's source tag (handy while tuning filters)

    # --- log ----------------------------------------------------------------------------
    log_dir: str = ""                 # empty = Sober's default log folder
    max_lines: int = 20000
    display_max: int = 5000

    # --- filters (also edited from the Filters page) --------------------------------------
    levels: list = field(default_factory=lambda: list(LEVELS))
    query: str = ""
    dedupe: bool = True
    script_only: bool = False
    auto_mute_chatty: bool = False
    muted_groups: list = field(default_factory=list)
    muted_tags: list = field(default_factory=list)
    exclude: list = field(default_factory=list)

    def sanitize(self) -> "Config":
        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

        self.width_pct = clamp(self.width_pct, 15, 100)
        self.height_pct = clamp(self.height_pct, 10, 100)
        self.offset_x = clamp(self.offset_x, -4000, 4000)
        self.offset_y = clamp(self.offset_y, -4000, 4000)
        self.opacity_pct = clamp(self.opacity_pct, 30, 100)
        self.font_size = clamp(self.font_size, 7, 32)
        self.max_lines = clamp(self.max_lines, 500, 200000)
        self.display_max = clamp(self.display_max, 200, 50000)
        if self.anchor not in ANCHORS:
            self.anchor = "top-left"
        if self.keyboard not in ("on_demand", "never"):
            self.keyboard = "on_demand"
        if self.focus_check not in ("kdotool", "off"):
            self.focus_check = "kdotool"
        self.levels = [lv for lv in self.levels if lv in LEVELS] or list(LEVELS)
        if not self.toggle_key.startswith("KEY_"):
            self.toggle_key = "KEY_F9"
        return self


def _coerce(value, default):
    """Accept ``value`` only if it has the same type as ``default`` (ints may widen to bool-free ints)."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        return [str(v) for v in value] if isinstance(value, list) else default
    return default


def load(path: "Path | None" = None) -> Config:
    path = path or config_path()
    cfg = Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return cfg.sanitize()
    for f in fields(Config):
        if f.name in data:
            default = f.default if f.default is not MISSING else f.default_factory()
            setattr(cfg, f.name, _coerce(data[f.name], default))
    return cfg.sanitize()


def save(cfg: Config, path: "Path | None" = None) -> None:
    import tomli_w

    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {f.name: getattr(cfg, f.name) for f in fields(Config)}
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(tomli_w.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
