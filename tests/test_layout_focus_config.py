from pathlib import Path

from sober_console import config, focus, sober
from sober_console.layout import (Rect, ScreenInfo, anchor_for_rect, overlay_rect, pick_screen,
                                  reference_rect, screen_of)

LEFT = ScreenInfo("DP-1", Rect(0, 0, 1920, 1080), primary=True)
RIGHT = ScreenInfo("HDMI-A-1", Rect(1920, 0, 2560, 1440))
SCREENS = [LEFT, RIGHT]


# ---- screens ---------------------------------------------------------------------------
def test_screen_of_uses_window_centre():
    assert screen_of(SCREENS, Rect(100, 100, 800, 600)) is LEFT
    assert screen_of(SCREENS, Rect(2000, 100, 800, 600)) is RIGHT
    assert screen_of(SCREENS, None) is None


def test_pick_screen_modes():
    sober_on_right = Rect(2000, 50, 1200, 800)
    assert pick_screen(SCREENS, "same", sober_on_right) is RIGHT
    assert pick_screen(SCREENS, "other", sober_on_right) is LEFT
    assert pick_screen(SCREENS, "primary", sober_on_right) is LEFT
    assert pick_screen(SCREENS, "HDMI-A-1", None) is RIGHT
    assert pick_screen(SCREENS, "same", None) is LEFT                  # no window known -> primary
    assert pick_screen(SCREENS, "DP-9", sober_on_right) is RIGHT       # unplugged name -> same
    assert pick_screen([LEFT], "other", Rect(0, 0, 10, 10)) is LEFT    # nothing else to pick


def test_reference_rect_is_window_only_on_its_own_monitor():
    win = Rect(100, 50, 1200, 800)
    assert reference_rect(LEFT, win, SCREENS) == win
    assert reference_rect(RIGHT, win, SCREENS) == RIGHT.rect          # other monitor: whole screen
    assert reference_rect(LEFT, win, SCREENS, follow_window=False) == LEFT.rect
    assert reference_rect(LEFT, None, SCREENS) == LEFT.rect


def test_reference_rect_clips_partly_offscreen_windows():
    win = Rect(-200, -100, 1000, 700)
    assert reference_rect(LEFT, win, SCREENS) == Rect(0, 0, 800, 600)


# ---- overlay rectangle -----------------------------------------------------------------------
def test_overlay_rect_anchors():
    ref = Rect(100, 100, 1000, 800)
    b = Rect(0, 0, 3000, 2000)
    r = overlay_rect("top-left", 10, 20, 50, 50, ref, b)
    assert r == Rect(110, 120, 500, 400)
    r = overlay_rect("top-right", 10, 20, 50, 50, ref, b)
    assert (r.x, r.y) == (100 + 1000 - 500 - 10, 120)
    r = overlay_rect("bottom-left", 10, 20, 50, 50, ref, b)
    assert (r.x, r.y) == (110, 100 + 800 - 400 - 20)
    r = overlay_rect("center", 0, 0, 50, 50, ref, b)
    assert (r.x, r.y) == (350, 300)


def test_overlay_rect_is_clamped_onto_the_monitor_and_min_size():
    r = overlay_rect("top-left", -500, -500, 50, 50, Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1080))
    assert (r.x, r.y) == (0, 0)
    r = overlay_rect("top-right", -900, 0, 50, 50, Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1080))
    assert r.x + r.w <= 1920
    tiny = overlay_rect("top-left", 0, 0, 15, 10, Rect(0, 0, 800, 600), Rect(0, 0, 800, 600))
    assert tiny.w >= 360 and tiny.h >= 220


def test_anchor_for_rect_picks_the_nearest_corner_with_offsets_from_it():
    ref = Rect(1920, 0, 2560, 1440)
    assert anchor_for_rect(Rect(1940, 30, 500, 400), ref) == ("top-left", 20, 30)
    assert anchor_for_rect(Rect(4000, 30, 400, 300), ref) == ("top-right", 80, 30)
    assert anchor_for_rect(Rect(1930, 1000, 400, 300), ref) == ("bottom-left", 10, 140)
    assert anchor_for_rect(Rect(4000, 1000, 400, 300), ref) == ("bottom-right", 80, 140)


def test_dragged_position_round_trips_through_overlay_rect():
    ref = Rect(1920, 0, 2560, 1440)
    for dropped in (Rect(2100, 200, 1178, 792), Rect(3200, 600, 1178, 792), Rect(1920, 648, 1178, 792),
                    Rect(3302, 0, 1178, 792), Rect(2900, 300, 1178, 792)):
        anchor, ox, oy = anchor_for_rect(dropped, ref)
        # same size again (46% x 55% of ref), as the app does after a drag
        again = overlay_rect(anchor, ox, oy, 46, 55, ref, ref)
        assert (again.x, again.y) == (dropped.x, dropped.y), (dropped, anchor, ox, oy)


def test_dragged_position_follows_a_different_sized_reference():
    # dropped in the top-right corner of a 2560-wide Sober window; later Sober is 1280 wide:
    # it should still hug the top-right corner of the window
    anchor, ox, oy = anchor_for_rect(Rect(2560 - 1178 - 16, 16, 1178, 792), Rect(0, 0, 2560, 1440))
    small = overlay_rect(anchor, ox, oy, 46, 55, Rect(0, 0, 1280, 720), Rect(0, 0, 1280, 720))
    assert small.x + small.w == 1280 - 16 and small.y == 16


# ---- hide when Sober isn't the active window --------------------------------------------------------
def _hide(**kw):
    base = dict(enabled=True, sober_focused=False, overlay_screen="HDMI-A-1", sober_screen="HDMI-A-1",
                overlay_active=False)
    base.update(kw)
    return focus.should_auto_hide(**base)


def test_auto_hide_only_on_a_positive_sighting_of_another_window_on_sobers_monitor():
    assert _hide() is True
    assert _hide(sober_focused=True) is False
    assert _hide(sober_focused=None) is False                  # couldn't read the window: don't guess
    assert _hide(enabled=False) is False                       # the Settings toggle
    assert _hide(overlay_screen="DP-1") is False               # overlay on the other monitor stays up
    assert _hide(sober_screen=None) is False                   # don't know where Sober is
    assert _hide(overlay_active=True) is False                 # the overlay itself holds the keyboard


# ---- kdotool parsing (assumed output layout; see focus.py) ----------------------------------------
PROBE = """ID={4f5f6a}
CLASS=org.vinegarhq.Sober
NAME=Roblox
PID=4242
GEOM=Window {4f5f6a}   Position: 1920,30   Geometry: 1280x720
"""


def test_parse_probe_full():
    info = focus.parse_probe(PROBE)
    assert info.wclass == "org.vinegarhq.Sober" and info.pid == 4242
    assert info.geometry == (1920, 30, 1280, 720)


def test_parse_probe_tolerates_missing_fields():
    info = focus.parse_probe("ID={x}\nCLASS=firefox\nNAME=\nPID=\nGEOM=\n")
    assert info.pid is None and info.geometry is None and info.wclass == "firefox"
    assert focus.parse_probe("") is None
    assert focus.parse_probe("garbage") is None


def test_is_sober_window_by_pid_or_by_class():
    info = focus.parse_probe(PROBE)
    assert focus.is_sober_window(info, {4242}, "zzz")                    # pid in cgroup
    assert focus.is_sober_window(info, set(), "sober|roblox")            # class match
    other = focus.parse_probe("ID={x}\nCLASS=firefox\nNAME=Mozilla\nPID=1\nGEOM=\n")
    assert not focus.is_sober_window(other, {4242}, "sober|roblox")
    assert not focus.is_sober_window(None, {4242}, "sober")
    assert focus.is_sober_window(info, set(), "(unclosed")               # bad regex -> default pattern


# ---- sober presence ------------------------------------------------------------------------
def test_scope_glob_pattern(monkeypatch, tmp_path):
    monkeypatch.setattr(sober.glob, "glob", lambda p: ["/x/app-flatpak-org.vinegarhq.Sober-1.scope"])
    assert sober.running(1000)
    monkeypatch.setattr(sober.glob, "glob", lambda p: [])
    assert not sober.running(1000)


def test_default_log_dir():
    assert str(sober.default_log_dir()).endswith(".var/app/org.vinegarhq.Sober/data/sober/sober_logs")


# ---- config ------------------------------------------------------------------------------
def test_hide_when_unfocused_defaults_on_and_is_a_bool_setting(tmp_path):
    assert config.Config().hide_when_unfocused is True
    f = tmp_path / "c.toml"
    f.write_text("hide_when_unfocused = false\n")
    assert config.load(f).hide_when_unfocused is False
    f.write_text('hide_when_unfocused = "no"\n')
    assert config.load(f).hide_when_unfocused is True           # wrong type -> default


def test_config_roundtrip_and_defaults(tmp_path):
    p = tmp_path / "c.toml"
    assert config.load(p) == config.Config().sanitize()               # missing file -> defaults
    cfg = config.Config(monitor="DP-2", anchor="bottom-right", width_pct=60, muted_groups=["http"], query="foo")
    config.save(cfg, p)
    back = config.load(p)
    assert (back.monitor, back.anchor, back.width_pct, back.muted_groups, back.query) == \
        ("DP-2", "bottom-right", 60, ["http"], "foo")


def test_config_bad_values_are_repaired(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('width_pct = 5000\nanchor = "nowhere"\nopacity_pct = "high"\ntoggle_key = "F9"\n'
                 'levels = ["bogus"]\nunknown_key = 1\nkeyboard = "sometimes"\n')
    cfg = config.load(p)
    assert cfg.width_pct == 100 and cfg.anchor == "top-left"
    assert cfg.opacity_pct == config.Config().opacity_pct
    assert cfg.toggle_key == "KEY_F9" and cfg.keyboard == "on_demand"
    assert set(cfg.levels) == {"output", "info", "warning", "error"}


def test_config_garbage_file_falls_back(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("this is [not toml")
    assert config.load(p).anchor == "top-left"
