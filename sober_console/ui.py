"""
The overlay widget: styled after Roblox's in-game developer console (dark panel, "Developer
Console" header, colour-coded Messages / Info / Warnings / Errors), with Filters and Settings
pages built in.

Design constraints that come from being a layer-shell surface:

  * No popups.  Combo boxes, context menus and tooltips are separate popup surfaces, which are
    unreliable when their parent is a layer-shell surface -- so choices are cycle buttons
    (click to step through the options) and the right-click menu is disabled.
  * No keyboard unless asked.  A layer surface only receives keystrokes if it was given
    keyboard interactivity (config ``keyboard = "on_demand"``).  Selecting and copying also work
    without it: the Copy button copies the selection, or everything shown if nothing is selected.
  * The widget never filters or parses anything itself; the ConsoleModel does.  This module only
    draws ``Entry`` objects and reports setting changes via ``settingChanged``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from .layout import ANCHORS
from .logparse import LEVELS
from .noise import NOISE_GROUPS, PRESETS

# Roblox-console-like palette
BG = (27, 29, 33)
HEADER_BG = "#24272c"
BORDER = "#3a3e45"
TEXT = "#e6e6e6"
DIM = "#8a919b"
ACCENT = "#3d8bff"
LEVEL_COLOR = {"output": "#e6e6e6", "info": "#6cb6ff", "warning": "#ffcf5c", "error": "#ff6b68"}
LEVEL_LABEL = {"output": "Messages", "info": "Info", "warning": "Warnings", "error": "Errors"}


def _qss(alpha: int, font_pt: int) -> str:
    r, g, b = BG
    return f"""
    QFrame#frame {{ background: rgba({r},{g},{b},{alpha}); border: 1px solid {BORDER}; border-radius: 6px; }}
    QWidget#header {{ background: {HEADER_BG}; border-top-left-radius: 6px; border-top-right-radius: 6px;
                      border-bottom: 1px solid {BORDER}; }}
    QLabel {{ color: {TEXT}; background: transparent; }}
    QLabel#title {{ font-weight: 600; font-size: {font_pt + 1}pt; padding-left: 6px; }}
    QLabel#dim {{ color: {DIM}; }}
    QLabel#section {{ color: {DIM}; font-weight: 600; padding-top: 8px; }}
    QLabel#value {{ color: {TEXT}; background: #202226; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 6px; }}
    QPushButton:disabled {{ color: #5b616b; }}
    QPushButton {{ color: {TEXT}; background: #2f333a; border: 1px solid {BORDER}; border-radius: 4px;
                   padding: 3px 10px; }}
    QPushButton:hover {{ background: #394049; }}
    QPushButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
    QPushButton#close {{ padding: 3px 8px; }}
    QPushButton#close:hover {{ background: #c0392b; border-color: #c0392b; }}
    QPushButton#jump {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
    QCheckBox {{ color: {TEXT}; spacing: 6px; background: transparent; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #6b7078; border-radius: 3px;
                            background: #202226; }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
    QLineEdit, QPlainTextEdit#patterns {{ color: {TEXT}; background: #202226; border: 1px solid {BORDER};
                                                    border-radius: 4px; padding: 2px 6px;
                                                    selection-background-color: {ACCENT}; }}
    QPlainTextEdit#log {{ color: {TEXT}; background: transparent; border: none; selection-background-color: {ACCENT};
                          selection-color: white; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: #4a4f58; border-radius: 4px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: #5d636e; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


class CycleButton(QPushButton):
    """A button that steps through options when clicked -- a popup-free combo box."""

    changed = Signal(str)

    def __init__(self, options: "list[tuple[str, str]]", parent=None):
        super().__init__(parent)
        self._options = list(options)
        self._index = 0
        self.clicked.connect(self._advance)
        self._refresh()

    def set_options(self, options: "list[tuple[str, str]]") -> None:
        current = self.value()
        self._options = list(options)
        self.set_value(current)

    def set_value(self, value: str) -> None:
        for i, (v, _label) in enumerate(self._options):
            if v == value:
                self._index = i
                break
        else:
            self._options = self._options + [(value, value)]
            self._index = len(self._options) - 1
        self._refresh()

    def value(self) -> str:
        return self._options[self._index][0] if self._options else ""

    def _advance(self) -> None:
        if self._options:
            self._index = (self._index + 1) % len(self._options)
            self._refresh()
            self.changed.emit(self.value())

    def _refresh(self) -> None:
        label = self._options[self._index][1] if self._options else ""
        self.setText(label.replace("&", "&&") + "  ▸")        # a bare "&" would be a mnemonic


class Stepper(QWidget):
    """``[\u2212]  16 px  [+]`` -- a spin box without its tiny arrows.  Large click targets, holds to
    repeat, needs no keyboard and no popup."""

    valueChanged = Signal(int)

    def __init__(self, lo: int, hi: int, step: int, suffix: str = "", parent=None):
        super().__init__(parent)
        self._lo, self._hi, self._step, self._suffix = lo, hi, step, suffix
        self._value = lo
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.minus, self.plus = QPushButton("\u2212"), QPushButton("+")
        self.label = QLabel()
        self.label.setObjectName("value")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumWidth(78)
        for b, sign in ((self.minus, -1), (self.plus, 1)):
            b.setFixedWidth(30)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(350)
            b.setAutoRepeatInterval(60)
            b.clicked.connect(lambda _=False, sg=sign: self.setValue(self._value + sg * self._step))
        row.addWidget(self.minus)
        row.addWidget(self.label)
        row.addWidget(self.plus)
        self._refresh()

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:
        v = max(self._lo, min(self._hi, int(v)))
        if v == self._value:
            return
        self._value = v
        self._refresh()
        self.valueChanged.emit(v)

    def _refresh(self) -> None:
        self.label.setText(f"{self._value}{self._suffix}")
        self.minus.setEnabled(self._value > self._lo)
        self.plus.setEnabled(self._value < self._hi)


class LogView(QPlainTextEdit):
    """Read-only, selectable log text.  Optionally copies the selection on mouse release."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("log")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)     # menus are popups
        self.auto_copy = False

    def selected_text(self) -> str:
        return self.textCursor().selectedText().replace(" ", "\n")

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.auto_copy:
            text = self.selected_text()
            if text:
                QGuiApplication.clipboard().setText(text)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("section")
    return label


def _scrolled(inner: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(inner)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


class FiltersPage(QWidget):
    changed = Signal(str)       # name of the Config field that changed

    def __init__(self, cfg, model, parent=None):
        super().__init__(parent)
        self.cfg, self.model = cfg, model
        self._building = False

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(12, 4, 12, 12)
        col.setSpacing(6)

        col.addWidget(_section("PRESET"))
        row = QHBoxLayout()
        self.preset = CycleButton([(p[0], p[1]) for p in PRESETS] + [("custom", "Custom")])
        self.preset.changed.connect(self._preset_chosen)
        row.addWidget(self.preset)
        row.addStretch()
        col.addLayout(row)

        col.addWidget(_section("HIDE ENGINE NOISE  (best-guess patterns; warnings and errors are never hidden by these)"))
        self.groups: "dict[str, QCheckBox]" = {}
        for g in NOISE_GROUPS:
            cb = QCheckBox(g.label.replace("&", "&&"))
            cb.toggled.connect(self._groups_edited)
            self.groups[g.id] = cb
            col.addWidget(cb)

        col.addWidget(_section("BEHAVIOUR"))
        self.dedupe = QCheckBox("Collapse repeated lines  (shows ×N)")
        self.script_only = QCheckBox("Script output only  (plus warnings and errors from anywhere)")
        self.auto_mute = QCheckBox("Auto-mute chatty sources  (any one source making up >20% of the log)")
        for name, cb in (("dedupe", self.dedupe), ("script_only", self.script_only), ("auto_mute_chatty", self.auto_mute)):
            cb.toggled.connect(lambda checked, n=name: self._flag(n, checked))
            col.addWidget(cb)

        col.addWidget(_section("ALWAYS HIDE LINES MATCHING  (one text or regex per line)"))
        self.patterns = QPlainTextEdit()
        self.patterns.setObjectName("patterns")
        self.patterns.setFixedHeight(70)
        self.patterns.setPlaceholderText("e.g.  HttpResponse status:200")
        self._pattern_timer = QTimer(self)
        self._pattern_timer.setSingleShot(True)
        self._pattern_timer.setInterval(400)
        self._pattern_timer.timeout.connect(self._patterns_edited)
        self.patterns.textChanged.connect(self._pattern_timer.start)
        col.addWidget(self.patterns)

        head = QHBoxLayout()
        head.addWidget(_section("SOURCES SEEN IN THE LOG  (untick to hide a source)"))
        head.addStretch()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_tags)
        head.addWidget(refresh)
        col.addLayout(head)
        self.tag_box = QVBoxLayout()
        self.tag_box.setSpacing(2)
        col.addLayout(self.tag_box)
        col.addStretch()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrolled(inner))

    # -- sync ---------------------------------------------------------------------------
    def load(self) -> None:
        self._building = True
        for gid, cb in self.groups.items():
            cb.setChecked(gid in self.cfg.muted_groups)
        self.dedupe.setChecked(self.cfg.dedupe)
        self.script_only.setChecked(self.cfg.script_only)
        self.auto_mute.setChecked(self.cfg.auto_mute_chatty)
        text = "\n".join(self.cfg.exclude)
        if self.patterns.toPlainText() != text:
            self.patterns.setPlainText(text)
        self._building = False
        self._sync_preset()
        self.refresh_tags()

    def refresh_tags(self) -> None:
        while self.tag_box.count():
            item = self.tag_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        tags = self.model.top_tags(40)
        if not tags:
            note = QLabel("No tagged sources seen yet.")
            note.setObjectName("dim")
            self.tag_box.addWidget(note)
            return
        for tag, n in tags:
            cb = QCheckBox(f"{tag}   ({n:,})".replace("&", "&&"))
            cb.setChecked(tag not in self.cfg.muted_tags)
            cb.toggled.connect(lambda shown, t=tag: self._tag_toggled(t, shown))
            self.tag_box.addWidget(cb)

    def _sync_preset(self) -> None:
        current = "custom"
        for pid, _label, groups, script in PRESETS:
            if set(groups) == set(self.cfg.muted_groups) and script == self.cfg.script_only:
                current = pid
                break
        self.preset.set_value(current)

    # -- edits --------------------------------------------------------------------------------
    def _preset_chosen(self, pid: str) -> None:
        for p, _label, groups, script in PRESETS:
            if p == pid:
                self.cfg.muted_groups = list(groups)
                self.cfg.script_only = script
                self.load()
                self.changed.emit("muted_groups")
                return
        # "custom" is only a label for hand-picked combinations; nothing to apply

    def _groups_edited(self, _checked=None) -> None:
        if self._building:
            return
        self.cfg.muted_groups = [gid for gid, cb in self.groups.items() if cb.isChecked()]
        self._sync_preset()
        self.changed.emit("muted_groups")

    def _flag(self, name: str, checked: bool) -> None:
        if self._building:
            return
        setattr(self.cfg, name, checked)
        self._sync_preset()
        self.changed.emit(name)

    def _patterns_edited(self) -> None:
        if self._building:
            return
        self.cfg.exclude = [ln.strip() for ln in self.patterns.toPlainText().splitlines() if ln.strip()]
        self.changed.emit("exclude")

    def _tag_toggled(self, tag: str, shown: bool) -> None:
        muted = set(self.cfg.muted_tags)
        (muted.discard if shown else muted.add)(tag)
        self.cfg.muted_tags = sorted(muted)
        self.changed.emit("muted_tags")


class SettingsPage(QWidget):
    changed = Signal(str)
    rebindRequested = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._building = False

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(12, 4, 12, 12)
        col.setSpacing(6)

        def row(label: str, widget: QWidget) -> None:
            r = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(150)
            r.addWidget(lab)
            r.addWidget(widget)
            r.addStretch()
            col.addLayout(r)

        def spin(name: str, lo: int, hi: int, step: int, suffix: str = "") -> Stepper:
            s = Stepper(lo, hi, step, suffix)
            s.valueChanged.connect(lambda v, n=name: self._set(n, v))
            return s

        col.addWidget(_section("POSITION"))
        self.monitor = CycleButton([("same", "Same monitor as Sober"), ("other", "Other monitor"), ("primary", "Primary monitor")])
        self.monitor.changed.connect(lambda v: self._set("monitor", v))
        row("Monitor", self.monitor)
        self.anchor = CycleButton([(a, a.replace("-", " ").title()) for a in ANCHORS])
        self.anchor.changed.connect(lambda v: self._set("anchor", v))
        row("Anchor", self.anchor)
        self.offset_x = spin("offset_x", -4000, 4000, 10, " px")
        self.offset_y = spin("offset_y", -4000, 4000, 10, " px")
        row("Offset X", self.offset_x)
        row("Offset Y", self.offset_y)
        self.width_pct = spin("width_pct", 15, 100, 5, " %")
        self.height_pct = spin("height_pct", 10, 100, 5, " %")
        row("Width", self.width_pct)
        row("Height", self.height_pct)
        self.follow = QCheckBox("Place relative to Sober's window  (off: the whole monitor)")
        self.follow.toggled.connect(lambda c: self._set("follow_window", c))
        col.addWidget(self.follow)

        col.addWidget(_section("LOOK"))
        self.opacity_pct = spin("opacity_pct", 30, 100, 5, " %")
        self.font_size = spin("font_size", 7, 32, 1, " pt")
        row("Opacity", self.opacity_pct)
        row("Font size", self.font_size)
        self.show_tags = QCheckBox("Show each line's source tag  (useful while tuning filters)")
        self.show_tags.toggled.connect(lambda c: self._set("show_tags", c))
        col.addWidget(self.show_tags)

        col.addWidget(_section("INPUT"))
        self.key_button = QPushButton()
        self.key_button.clicked.connect(self.rebindRequested.emit)
        row("Toggle key", self.key_button)
        self.keyboard = CycleButton([("on_demand", "Click to type / Ctrl+C"), ("never", "Never take focus")])
        self.keyboard.changed.connect(lambda v: self._set("keyboard", v))
        row("Keyboard", self.keyboard)
        note = QLabel("“Click to type” lets you use the search box and Ctrl+C, but clicking the overlay "
                      "takes keyboard focus from Sober until you click back into the game. “Never take focus” "
                      "keeps the game focused; select text and use the Copy button instead.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        col.addWidget(note)
        self.auto_copy = QCheckBox("Copy selected text to the clipboard automatically")
        self.auto_copy.toggled.connect(lambda c: self._set("auto_copy", c))
        col.addWidget(self.auto_copy)
        col.addStretch()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrolled(inner))

    def _set(self, name: str, value) -> None:
        if self._building:
            return
        setattr(self.cfg, name, value)
        self.changed.emit(name)

    def set_monitors(self, names: "list[str]") -> None:
        base = [("same", "Same monitor as Sober"), ("other", "Other monitor"), ("primary", "Primary monitor")]
        self.monitor.set_options(base + [(n, n) for n in names])

    def set_toggle_key_label(self, text: str) -> None:
        self.key_button.setText(text)

    def load(self) -> None:
        self._building = True
        c = self.cfg
        self.monitor.set_value(c.monitor)
        self.anchor.set_value(c.anchor)
        for name in ("offset_x", "offset_y", "width_pct", "height_pct", "opacity_pct", "font_size"):
            getattr(self, name).setValue(getattr(c, name))
        self.follow.setChecked(c.follow_window)
        self.show_tags.setChecked(c.show_tags)
        self.keyboard.set_value(c.keyboard)
        self.auto_copy.setChecked(c.auto_copy)
        self._building = False


class ConsoleOverlay(QWidget):
    """The whole panel.  ``cfg`` is edited in place; every edit announces itself through
    ``settingChanged(name)`` and the app decides what needs redoing."""

    closeRequested = Signal()
    clearRequested = Signal()
    settingChanged = Signal(str)
    rebindRequested = Signal()

    def __init__(self, cfg, model, parent=None):
        super().__init__(parent)
        self.cfg, self.model = cfg, model
        self._follow = True
        self.setObjectName("overlay")
        self.setWindowTitle("sober-console")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._f_time = QTextCharFormat()
        self._f_time.setForeground(QColor(DIM))
        self._f_tag = QTextCharFormat()
        self._f_tag.setForeground(QColor("#9d8fd0"))
        self._f_count = QTextCharFormat()
        self._f_count.setForeground(QColor(LEVEL_COLOR["warning"]))
        self._f_level = {lv: self._fmt(LEVEL_COLOR[lv]) for lv in LEVELS}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setObjectName("frame")
        outer.addWidget(self.frame)
        body = QVBoxLayout(self.frame)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        body.addWidget(self._build_header())
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)

        self.pages.addWidget(self._build_log_page())
        self.filters_page = FiltersPage(cfg, model)
        self.settings_page = SettingsPage(cfg)
        self.pages.addWidget(self.filters_page)
        self.pages.addWidget(self.settings_page)
        self.filters_page.changed.connect(self.settingChanged)
        self.settings_page.changed.connect(self.settingChanged)
        self.settings_page.rebindRequested.connect(self.rebindRequested)

        self.apply_look()
        self.filters_page.load()
        self.settings_page.load()

    # -- construction ------------------------------------------------------------------------
    @staticmethod
    def _fmt(color: str) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        return f

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("header")
        header.setFixedHeight(38)
        row = QHBoxLayout(header)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        title = QLabel("Developer Console")
        title.setObjectName("title")
        row.addWidget(title)
        row.addSpacing(10)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        for i, name in enumerate(("Client Log", "Filters", "Settings")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.tab_group.addButton(b, i)
            row.addWidget(b)
        self.tab_group.idClicked.connect(self._tab_changed)
        row.addStretch()

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(self.copy)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clearRequested)
        close = QPushButton("✕")
        close.setObjectName("close")
        close.clicked.connect(self.closeRequested)
        for b in (self.copy_btn, clear, close):
            row.addWidget(b)
        return header

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(8, 6, 8, 4)
        col.setSpacing(4)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.level_boxes: "dict[str, QCheckBox]" = {}
        for lv in LEVELS:
            cb = QCheckBox(LEVEL_LABEL[lv])
            cb.setStyleSheet(f"QCheckBox {{ color: {LEVEL_COLOR[lv]}; }}")
            cb.setChecked(lv in self.cfg.levels)
            cb.toggled.connect(self._levels_edited)
            self.level_boxes[lv] = cb
            bar.addWidget(cb)
        bar.addSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter   (words, -exclude, "phrase", /regex/)')
        self.search.setClearButtonEnabled(True)
        self.search.setText(self.cfg.query)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._query_edited)
        self.search.textChanged.connect(self._search_timer.start)
        bar.addWidget(self.search, 1)
        col.addLayout(bar)

        self.view = LogView()
        col.addWidget(self.view, 1)
        sb = self.view.verticalScrollBar()
        sb.valueChanged.connect(self._scrolled)

        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("dim")
        foot.addWidget(self.status, 1)
        self.notice = QLabel("")
        self.notice.setObjectName("dim")
        foot.addWidget(self.notice)
        self.jump = QPushButton("Jump to latest ↓")
        self.jump.setObjectName("jump")
        self.jump.clicked.connect(self.scroll_to_end)
        self.jump.hide()
        foot.addWidget(self.jump)
        col.addLayout(foot)
        return page

    # -- look ----------------------------------------------------------------------------------
    def apply_look(self) -> None:
        alpha = round(255 * self.cfg.opacity_pct / 100)
        self.setStyleSheet(_qss(alpha, self.cfg.font_size))
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(self.cfg.font_size)
        self.view.setFont(font)
        self.view.setMaximumBlockCount(self.cfg.display_max)
        self.view.auto_copy = self.cfg.auto_copy

    # -- rendering ---------------------------------------------------------------------------
    def _write_entry(self, cur: QTextCursor, e) -> None:
        ln = e.line
        if ln.time:
            cur.insertText(ln.time + "  ", self._f_time)
        if self.cfg.show_tags and ln.tag:
            cur.insertText(f"[{ln.tag}] ", self._f_tag)
        cur.insertText(ln.message, self._f_level.get(ln.level, self._f_level["info"]))
        if e.count > 1:
            cur.insertText(f"   ×{e.count}", self._f_count)

    def _end_cursor(self) -> QTextCursor:
        cur = QTextCursor(self.view.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        return cur

    def rebuild_view(self) -> None:
        """Redraw everything from the model's current view (after any filter change)."""
        entries = self.model.rebuild()
        self.view.setUpdatesEnabled(False)
        self.view.clear()
        cur = self._end_cursor()
        cur.beginEditBlock()
        for i, e in enumerate(entries):
            if i:
                cur.insertBlock()
            self._write_entry(cur, e)
        cur.endEditBlock()
        self.view.setUpdatesEnabled(True)
        if self._follow:
            self.scroll_to_end()
        self.update_status()

    def apply_ops(self, ops: list) -> None:
        if not ops:
            return
        follow = self._follow
        cur = self._end_cursor()
        cur.beginEditBlock()
        for op, entry in ops:
            if op == "append":
                if not self.view.document().isEmpty():
                    cur.insertBlock()
                self._write_entry(cur, entry)
            else:                                             # "bump": rewrite the last block
                cur.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cur.removeSelectedText()
                self._write_entry(cur, entry)
        cur.endEditBlock()
        if follow:
            self.scroll_to_end()
        self.update_status()

    def clear_view(self) -> None:
        self.view.clear()
        self.update_status()

    def update_status(self) -> None:
        m = self.model
        state = "following" if self._follow else "paused (scrolled up)"
        self.status.setText(f"{m.shown:,} shown · {m.hidden:,} hidden · {state}")

    def set_notice(self, text: str) -> None:
        self.notice.setText(text)

    # -- scrolling ---------------------------------------------------------------------------------
    def scroll_to_end(self) -> None:
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._follow = True
        self.jump.hide()
        self.update_status()

    def _scrolled(self, value: int) -> None:
        sb = self.view.verticalScrollBar()
        follow = value >= sb.maximum() - 3
        if follow != self._follow:
            self._follow = follow
            self.jump.setVisible(not follow)
            self.update_status()

    # -- user actions --------------------------------------------------------------------------------
    def copy(self) -> None:
        text = self.view.selected_text() or self.view.toPlainText()
        QGuiApplication.clipboard().setText(text)
        self.copy_btn.setText("Copied ✓")
        QTimer.singleShot(1000, lambda: self.copy_btn.setText("Copy"))

    def _tab_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 1:
            self.filters_page.load()
        elif index == 2:
            self.settings_page.load()

    def _levels_edited(self, _checked=None) -> None:
        self.cfg.levels = [lv for lv in LEVELS if self.level_boxes[lv].isChecked()]
        self.settingChanged.emit("levels")

    def _query_edited(self) -> None:
        self.cfg.query = self.search.text()
        self.settingChanged.emit("query")

    def sync_filter_widgets(self) -> None:
        """Push cfg -> widgets (after a preset or config reload)."""
        for lv, cb in self.level_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(lv in self.cfg.levels)
            cb.blockSignals(False)
        if self.search.text() != self.cfg.query:
            self.search.blockSignals(True)
            self.search.setText(self.cfg.query)
            self.search.blockSignals(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)
