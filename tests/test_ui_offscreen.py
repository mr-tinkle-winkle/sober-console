"""
UI tests on Qt's offscreen platform: widget behaviour, filtering wiring, the control socket and
placement fallback.  They cannot exercise anything compositor-specific (layer-shell, kdotool,
real input devices) -- see the README's "what is not verified" section.
"""
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QPushButton

from sober_console import config, focus, ipc
from sober_console.app import ConsoleApp
from sober_console.filters import from_config
from sober_console.layout import Rect
from sober_console.model import ConsoleModel
from sober_console.ui import ConsoleOverlay, CycleButton, Stepper

SAMPLE = [
    "2024-05-01T12:00:00.100Z,1.0,0001,6 [FLog::Output] hello from a script",
    "2024-05-01T12:00:00.200Z,1.0,0001,6 [FLog::Network] replicator tick",
    "2024-05-01T12:00:00.300Z,1.0,0001,6 [FLog::Network] replicator tick",
    "2024-05-01T12:00:00.400Z,1.0,0001,6 [FLog::Error] something broke",
    "info: app: lifecycle: app_start delta=0.1ms",
]


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(qapp):
    cfg = config.Config().sanitize()
    model = ConsoleModel(from_config(cfg))
    ov = ConsoleOverlay(cfg, model)
    ov.resize(760, 460)
    ov.show()
    yield ov
    ov.close()


def pump(ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        QApplication.processEvents()


def test_ingest_renders_and_dedupe_bumps_last_line(overlay):
    ops = overlay.model.ingest(SAMPLE)
    overlay.apply_ops(ops)
    text = overlay.view.toPlainText()
    assert "hello from a script" in text and "something broke" in text
    assert text.count("replicator tick") == 1 and "×2" in text        # collapsed, with a count
    assert overlay.view.document().blockCount() == 4


def test_level_checkbox_updates_config_and_emits(overlay):
    seen = []
    overlay.settingChanged.connect(seen.append)
    overlay.level_boxes["info"].setChecked(False)
    assert "info" not in overlay.cfg.levels and seen == ["levels"]


def test_filter_change_rebuilds_view(overlay):
    overlay.apply_ops(overlay.model.ingest(SAMPLE))
    overlay.cfg.levels = ["error"]
    overlay.model.filters = from_config(overlay.cfg)
    overlay.rebuild_view()
    assert overlay.view.toPlainText().strip().endswith("something broke")
    assert "hello" not in overlay.view.toPlainText()
    assert "hidden" in overlay.status.text()


def test_search_box_debounces_and_sets_query(overlay):
    seen = []
    overlay.settingChanged.connect(seen.append)
    overlay.search.setText("broke")
    pump(300)
    assert overlay.cfg.query == "broke" and seen == ["query"]


def test_copy_prefers_selection_then_everything(overlay, qapp):
    overlay.apply_ops(overlay.model.ingest(SAMPLE))
    overlay.copy()
    assert "hello from a script" in QGuiApplication.clipboard().text()
    cur = overlay.view.textCursor()
    cur.setPosition(0)
    cur.movePosition(cur.MoveOperation.EndOfBlock, cur.MoveMode.KeepAnchor)
    overlay.view.setTextCursor(cur)
    overlay.copy()
    copied = QGuiApplication.clipboard().text()
    assert "hello from a script" in copied and "something broke" not in copied


def test_scrolling_up_pauses_following_and_jump_resumes(overlay):
    overlay.apply_ops(overlay.model.ingest([f"info: net: line {i}" for i in range(400)]))
    pump()
    sb = overlay.view.verticalScrollBar()
    assert sb.maximum() > 0 and overlay._follow
    sb.setValue(0)
    assert not overlay._follow and "paused" in overlay.status.text()
    overlay.scroll_to_end()
    assert overlay._follow and sb.value() == sb.maximum()


def test_cycle_button_steps_and_keeps_unknown_values(qapp):
    b = CycleButton([("a", "A"), ("b", "B")])
    seen = []
    b.changed.connect(seen.append)
    b.click()
    assert b.value() == "b" and seen == ["b"]
    b.click()
    assert b.value() == "a"
    b.set_value("DP-3")                      # a monitor that isn't in the list yet
    assert b.value() == "DP-3"


def test_stepper_clamps_emits_and_disables_at_the_ends(qapp):
    st = Stepper(0, 20, 10, " px")
    seen = []
    st.valueChanged.connect(seen.append)
    st.plus.click()
    st.plus.click()
    st.plus.click()                                   # already at the maximum
    assert st.value() == 20 and seen == [10, 20] and not st.plus.isEnabled()
    st.minus.click()
    assert st.value() == 10 and st.label.text() == "10 px"
    st.setValue(10)                                   # unchanged: no signal
    assert seen == [10, 20, 10]


def test_ampersands_in_labels_are_escaped_not_turned_into_mnemonics(overlay):
    http = overlay.filters_page.groups["http"]
    assert http.text() == "HTTP requests && responses"
    assert "&" in http.text().replace("&&", "&")      # i.e. shown as a literal ampersand
    b = CycleButton([("x", "A & B")])
    assert b.text().startswith("A && B")


def test_filters_page_preset_and_group_wiring(overlay):
    page = overlay.filters_page
    seen = []
    overlay.settingChanged.connect(seen.append)
    page.preset.set_value("all")
    page.preset.click()                      # all -> quiet
    assert page.preset.value() == "quiet"
    assert "http" in overlay.cfg.muted_groups and "muted_groups" in seen
    page.groups["http"].setChecked(False)
    assert "http" not in overlay.cfg.muted_groups and page.preset.value() == "custom"


def test_filters_page_lists_seen_tags_and_mutes_them(overlay):
    overlay.model.ingest(SAMPLE)
    page = overlay.filters_page
    page.refresh_tags()
    boxes = [page.tag_box.itemAt(i).widget() for i in range(page.tag_box.count())]
    network = next(b for b in boxes if "FLog::Network" in b.text())
    network.setChecked(False)
    assert "FLog::Network" in overlay.cfg.muted_tags


def test_exclude_patterns_are_saved_from_the_textbox(overlay):
    seen = []
    overlay.settingChanged.connect(seen.append)
    overlay.filters_page.patterns.setPlainText("foo\n\n  bar  ")
    pump(600)
    assert overlay.cfg.exclude == ["foo", "bar"] and "exclude" in seen


def test_settings_page_edits_write_through(overlay):
    page = overlay.settings_page
    seen = []
    overlay.settingChanged.connect(seen.append)
    page.width_pct.plus.click()                       # 46 -> 51
    page.width_pct.setValue(60)
    page.anchor.click()
    page.auto_copy.setChecked(True)
    assert overlay.cfg.width_pct == 60 and overlay.cfg.anchor == "top-right" and overlay.cfg.auto_copy
    assert seen == ["width_pct", "width_pct", "anchor", "auto_copy"]


def test_settings_load_does_not_emit_changes(overlay):
    seen = []
    overlay.settingChanged.connect(seen.append)
    overlay.settings_page.load()
    overlay.filters_page.load()
    assert seen == []


def test_escape_and_buttons_request_close_and_clear(overlay):
    got = []
    overlay.closeRequested.connect(lambda: got.append("close"))
    overlay.clearRequested.connect(lambda: got.append("clear"))
    for b in overlay.findChildren(QPushButton):
        if b.text() == "Clear":
            b.click()
        if b.objectName() == "close":
            b.click()
    assert got == ["clear", "close"]


# ---- the app around it ------------------------------------------------------------------------
@pytest.fixture
def console(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    logdir = tmp_path / "logs"
    logdir.mkdir()
    (logdir / "latest.log").write_text("\n".join(SAMPLE) + "\n")
    cfg = config.Config(log_dir=str(logdir)).sanitize()
    app = ConsoleApp(cfg, standalone=True, use_layer=False)
    assert app.start()
    yield app, logdir
    app.stop()


def test_app_tails_the_log_and_shows_history_when_opened(console):
    app, logdir = console
    pump(100)
    assert len(app.model.lines) == 5                      # backlog was ingested while hidden
    app.show_overlay()
    assert app.overlay.isVisible() and "hello from a script" in app.overlay.view.toPlainText()
    with open(logdir / "latest.log", "a") as fh:
        fh.write("2024-05-01T12:00:01.000Z,2.0,0001,6 [FLog::Output] a brand new line\n")
    pump(600)
    assert "a brand new line" in app.overlay.view.toPlainText()                  # live-appended while visible


def test_app_clear_button_only_drops_what_was_there(console):
    app, logdir = console
    app.show_overlay()
    app._clear()
    assert app.overlay.view.toPlainText() == "" and len(app.model.lines) == 0
    pump(600)
    assert len(app.model.lines) == 0                      # old lines are not re-read


def test_app_toggle_and_fallback_placement_uses_config(console):
    app, _ = console
    app.cfg.anchor, app.cfg.offset_x, app.cfg.offset_y = "top-left", 5, 7
    app.toggle()
    assert app.overlay.isVisible()
    screen = QGuiApplication.primaryScreen().geometry()
    assert app.overlay.width() <= screen.width() and app.overlay.width() >= 360
    assert (app.overlay.x(), app.overlay.y()) == (screen.x() + 5, screen.y() + 7)
    app.toggle()
    assert not app.overlay.isVisible()


def test_setting_change_triggers_rebuild_and_debounced_save(console, tmp_path):
    app, _ = console
    app.show_overlay()
    app.overlay.level_boxes["output"].setChecked(False)
    assert "hello from a script" not in app.overlay.view.toPlainText()
    pump(900)
    saved = config.load(config.config_path())
    assert "output" not in saved.levels


def test_control_socket_commands(console):
    app, _ = console
    results = {}

    def client():
        for cmd in ("status", "toggle", "toggle", "show", "hide", "bogus"):
            results[cmd + str(len(results))] = ipc.send_command(cmd, timeout=3)

    t = threading.Thread(target=client)
    t.start()
    while t.is_alive():
        QApplication.processEvents()
        time.sleep(0.005)
    t.join()
    replies = list(results.values())
    assert replies[0].startswith("hidden")
    assert replies[1:5] == ["visible", "hidden", "visible", "hidden"]
    assert replies[5].startswith("unknown command")


def test_second_ui_process_would_see_the_first(console):
    result = {}
    t = threading.Thread(target=lambda: result.update(r=ipc.send_command("status", timeout=3)))
    t.start()
    while t.is_alive():
        QApplication.processEvents()
        time.sleep(0.005)
    assert result["r"] is not None


def test_send_command_without_a_server_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert ipc.send_command("status") is None


def test_config_file_is_only_rewritten_when_a_setting_changed(console, tmp_path):
    app, _ = console
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# my hand-written notes\nmonitor = \"DP-1\"\n")
    app.show_overlay()
    app.hide_overlay()
    app._save()                                         # what quitting does
    assert path.read_text().startswith("# my hand-written notes")       # untouched, comment intact
    app.overlay.level_boxes["info"].setChecked(False)   # a real change from the overlay
    app._save()
    assert "my hand-written notes" not in path.read_text()
    assert "info" not in config.load(path).levels


def test_toggle_key_during_a_busy_probe_is_not_dropped(console, monkeypatch):
    app, _ = console
    app.cfg.focus_check = "kdotool"
    app._kdotool_ok = True
    monkeypatch.setattr(app.probe, "request", lambda: False)             # worker busy with a tracking probe
    app._on_key()
    assert app._pending_toggle                                           # remembered, not dropped
    sober_win = focus.WindowInfo(wid="{1}", wclass="org.vinegarhq.Sober", pid=None, geometry=(10, 20, 800, 600))
    app._on_probe(sober_win)                                             # the in-flight probe lands
    assert app.overlay.isVisible() and not app._pending_toggle
    assert app.sober_rect == Rect(10, 20, 800, 600)


def test_toggle_key_is_ignored_when_another_window_is_focused(console):
    app, _ = console
    app._kdotool_ok = True
    app._pending_toggle = True
    app._on_probe(focus.WindowInfo(wid="{2}", wclass="firefox", name="Mozilla Firefox", geometry=(0, 0, 100, 100)))
    assert not app.overlay.isVisible()
    assert app.sober_rect is None                    # a non-Sober window must not move the overlay


def test_failed_focus_detection_is_surfaced_in_the_overlay(console):
    app, _ = console
    app._pending_toggle = True
    app._on_probe(None)
    assert not app.overlay.isVisible()
    assert "sober-console focus" in app.overlay.notice.text()


def test_overlay_open_with_keyboard_focus_closes_on_toggle_key(console, monkeypatch):
    app, _ = console
    app.show_overlay()
    monkeypatch.setattr(type(app.overlay), "isActiveWindow", lambda self: True)
    app._on_key()
    assert not app.overlay.isVisible()
