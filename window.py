"""
window.py — Main application window.

Layout
------
  Left  : vertical toolbar (icons + labels)
  Center: QScrollArea → PDFCanvas
  Bottom: status bar

Keyboard shortcuts
------------------
  Ctrl+O   open PDF
  Ctrl+S   save PDF
  V        select tool
  L        LaTeX text tool
  D        draw tool
  E        erase tool
  +/-      zoom in / out
  Ctrl+Z   undo draw stroke
"""

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QSize, QRect, QPoint
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QPolygon
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QApplication, QColorDialog,
    QFileDialog, QLabel, QMainWindow, QMessageBox,
    QScrollArea, QSlider, QToolBar,
    QToolButton, QWidget,
)

from canvas import PDFCanvas
from engine import LaTeXEngine
from pkgdialog import PackageDialog


# ── Helpers ───────────────────────────────────────────────────────────────────

def _color_icon(color: QColor, size: int = 22) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(color)
    return QIcon(pm)


class _ToolSection(QWidget):
    """A label + child widget stacked vertically (for the toolbar)."""
    def __init__(self, label: str, child: QWidget, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(1)
        lbl = QLabel(label)
        lbl.setStyleSheet("color:#aaa;font-size:9px;")
        lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(lbl)
        v.addWidget(child)


# ── Main Window ───────────────────────────────────────────────────────────────

_TOOLBAR_CSS = """
QToolBar {
    background: #1e1e1e;
    border: none;
    padding: 6px 3px;
    spacing: 2px;
}
QToolButton {
    color: #ddd;
    border: none;
    border-radius: 5px;
    padding: 5px 6px;
    font-size: 20px;
    min-width: 38px;
    min-height: 38px;
}
QToolButton:checked {
    background: #0078d4;
    color: white;
}
QToolButton:hover:!checked {
    background: #333;
}
QToolButton:pressed {
    background: #005fa3;
}
QToolBar::separator {
    background: #444;
    width: 1px;
    margin: 4px 6px;
}
"""

_SCROLL_CSS = "QScrollArea { background: #555; border: none; }"


class EditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.engine = LaTeXEngine()
        self._current_path: str | None = None
        self._pen_color = QColor(220, 30, 30)   # default draw colour

        self.setWindowTitle("LaTeX PDF Editor")
        self.resize(1280, 900)

        self._build_central()
        self._build_toolbar()
        self._build_statusbar()

        # Connect canvas status messages
        self._canvas.status_message.connect(self._on_canvas_status)

        # Set initial tool
        self._set_tool("select")

    # ── Central widget ────────────────────────────────────────────────────────

    def _build_central(self):
        self._scroll = QScrollArea()
        self._scroll.setStyleSheet(_SCROLL_CSS)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setWidgetResizable(False)

        self._canvas = PDFCanvas()
        self._canvas.set_engine(self.engine)
        self._scroll.setWidget(self._canvas)

        self.setCentralWidget(self._scroll)

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = QToolBar("Tools")
        tb.setMovable(False)
        tb.setOrientation(Qt.Vertical)
        tb.setIconSize(QSize(28, 28))
        tb.setStyleSheet(_TOOLBAR_CSS)
        self.addToolBar(Qt.LeftToolBarArea, tb)

        # ── File ──────────────────────────────────────────────────────────
        open_act = self._icon_action(_icon_open(), "Open PDF  [Ctrl+O]", "Ctrl+O", self.open_pdf)
        save_act = self._icon_action(_icon_save(), "Save PDF  [Ctrl+S]", "Ctrl+S", self.save_pdf)
        tb.addAction(open_act)
        tb.addAction(save_act)
        tb.addSeparator()

        # ── Tool group ────────────────────────────────────────────────────
        grp = QActionGroup(self)
        grp.setExclusive(True)

        self._sel_act = self._icon_checkable(_icon_select(),  "Select / Move  [V]", "V",   grp, lambda: self._set_tool("select"))
        self._tex_act = self._icon_checkable(_icon_text(),    "Add LaTeX Box  [L]", "L",   grp, lambda: self._set_tool("text"))
        self._drw_act = self._icon_checkable(_icon_draw(),    "Draw  [D]",          "D",   grp, lambda: self._set_tool("draw"))
        self._ers_act = self._icon_checkable(_icon_erase(),   "Erase  [E]",         "E",   grp, lambda: self._set_tool("erase"))
        self._sel_act.setChecked(True)

        for act in (self._sel_act, self._tex_act, self._drw_act, self._ers_act):
            tb.addAction(act)
        tb.addSeparator()

        # ── Pen colour ────────────────────────────────────────────────────
        self._color_btn = QToolButton()
        self._color_btn.setToolTip("Pen colour")
        self._color_btn.setFixedSize(38, 38)
        self._color_btn.clicked.connect(self._pick_color)
        self._refresh_color_btn()
        tb.addWidget(self._color_btn)

        # ── Pen width slider ──────────────────────────────────────────────
        self._width_slider = QSlider(Qt.Horizontal)
        self._width_slider.setRange(1, 20)
        self._width_slider.setValue(3)
        self._width_slider.setFixedWidth(80)
        self._width_slider.setToolTip("Pen / eraser width")
        self._width_slider.valueChanged.connect(self._on_width_change)
        tb.addWidget(self._width_slider)
        tb.addSeparator()

        # ── Zoom ──────────────────────────────────────────────────────────
        tb.addAction(self._icon_action(_icon_zoom(plus=True),  "Zoom In  [+]",  "+", self._canvas.zoom_in))
        tb.addAction(self._icon_action(_icon_zoom(plus=False), "Zoom Out  [-]", "-", self._canvas.zoom_out))
        tb.addSeparator()

        # ── Undo / Clear ──────────────────────────────────────────────────
        tb.addAction(self._icon_action(_icon_undo(),  "Undo Draw  [Ctrl+Z]", "Ctrl+Z", self._canvas.undo_draw))
        tb.addAction(self._icon_action(_icon_clear(), "Clear All Drawing",   None,     self._confirm_clear))
        tb.addSeparator()

        # ── Packages ─────────────────────────────────────────────────────
        tb.addAction(self._icon_action(_icon_pkg(), "LaTeX Packages", None, self._manage_packages))

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        sb = self.statusBar()
        sb.setStyleSheet("QStatusBar { background:#1e1e1e; color:#bbb; }")
        self._msg_lbl = QLabel("Open a PDF to start  (Ctrl+O)")
        sb.addWidget(self._msg_lbl)
        self._info_lbl = QLabel("")
        sb.addPermanentWidget(self._info_lbl)

    # ── Actions helpers ───────────────────────────────────────────────────────

    def _icon_action(self, icon: QIcon, tip: str,
                     shortcut: str | None, slot) -> QAction:
        act = QAction(icon, "", self)   # empty text — icon only, no font rendering
        act.setToolTip(tip)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        return act

    def _icon_checkable(self, icon: QIcon, tip: str, shortcut: str,
                         group: QActionGroup, slot) -> QAction:
        act = QAction(icon, "", self)
        act.setToolTip(tip)
        act.setShortcut(QKeySequence(shortcut))
        act.setCheckable(True)
        act.triggered.connect(slot)
        group.addAction(act)
        return act

    # ── Tool management ───────────────────────────────────────────────────────

    def _set_tool(self, tool: str):
        self._canvas.set_tool(tool)
        hints = {
            "select": "Select — click a LaTeX box to focus / move it",
            "text":   "LaTeX Text — click anywhere on the PDF to add a box",
            "draw":   "Draw — click and drag to sketch freehand",
            "erase":  "Erase — drag over strokes to remove them",
        }
        self._msg_lbl.setText(hints.get(tool, ""))

    # ── Pen colour ────────────────────────────────────────────────────────────

    def _pick_color(self):
        c = QColorDialog.getColor(self._pen_color, self, "Choose Pen Colour")
        if c.isValid():
            self._pen_color = c
            self._canvas.set_pen_color(c)
            self._refresh_color_btn()

    def _refresh_color_btn(self):
        # Draw a filled circle on the button — no text, no fonts
        size = 28
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(self._pen_color)
        p.setPen(QPen(QColor(255, 255, 255, 180), 2))
        p.drawEllipse(3, 3, size - 6, size - 6)
        p.end()
        self._color_btn.setIcon(QIcon(pm))
        self._color_btn.setToolTip(f"Pen colour: {self._pen_color.name()}")

    def _on_width_change(self, val: int):
        self._canvas.set_pen_width(val)

    # ── Drawing clear ─────────────────────────────────────────────────────────

    def _confirm_clear(self):
        reply = QMessageBox.question(
            self, "Clear Drawing",
            "Delete all freehand drawing? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._canvas.clear_drawing()

    # ── Package manager ───────────────────────────────────────────────────────

    def _manage_packages(self):
        dlg = PackageDialog(self.engine, self)
        dlg.exec_()

    # ── File I/O ──────────────────────────────────────────────────────────────

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", os.path.expanduser("~"), "PDF files (*.pdf)"
        )
        if path:
            self.open_pdf_path(path)

    def open_pdf_path(self, path: str):
        """Load a PDF from *path* directly (used by CLI argument and open dialog)."""
        self._current_path = path
        self._canvas.load_document(path)
        name = os.path.basename(path)
        self.setWindowTitle(f"LaTeX PDF Editor — {name}")
        self._msg_lbl.setText(f"Opened: {name}  ({self._canvas.page_count} pages)")

    def save_pdf(self):
        if not self._current_path:
            self._save_as()
            return
        # Default: suggest "<name>_edited.pdf" next to original
        default = self._current_path.replace(".pdf", "_edited.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF", default, "PDF files (*.pdf)"
        )
        if path:
            self._do_save(path)

    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF As", os.path.expanduser("~/edited.pdf"),
            "PDF files (*.pdf)"
        )
        if path:
            self._do_save(path)

    def _do_save(self, path: str):
        self._msg_lbl.setText("Saving…")
        QApplication.processEvents()
        try:
            ok = self._canvas.export_pdf(path)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
            self._msg_lbl.setText("Save failed")
            return
        if ok:
            self._msg_lbl.setText(f"Saved: {os.path.basename(path)}")
            QMessageBox.information(self, "Saved", f"PDF saved to:\n{path}")
        else:
            QMessageBox.warning(self, "Error", "Could not export PDF.")
            self._msg_lbl.setText("Save failed")

    # ── Canvas status ─────────────────────────────────────────────────────────

    def _on_canvas_status(self, msg: str):
        self._info_lbl.setText(msg)


# ── Toolbar icon factories (pure QPainter — zero font/emoji rendering) ────────
# All icons are drawn as simple geometric shapes on a 28×28 transparent pixmap.

def _make_pm() -> tuple[QPixmap, QPainter]:
    pm = QPixmap(28, 28)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p

def _icon_open() -> QIcon:
    pm, p = _make_pm()
    # folder shape: two rectangles
    p.setBrush(QColor("#f0b429"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 12, 20, 13, 2, 2)       # folder body
    p.drawRoundedRect(4, 9, 9, 5, 1, 1)           # folder tab
    p.end()
    return QIcon(pm)

def _icon_save() -> QIcon:
    pm, p = _make_pm()
    c = QColor("#6cb6ff")
    p.setBrush(c); p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 20, 20, 2, 2)         # outer body
    p.setBrush(QColor("#1e1e1e")); p.drawRect(8, 4, 10, 8)   # label area
    p.setBrush(QColor("#fff")); p.drawRect(10, 5, 6, 6)      # white label
    p.setBrush(QColor("#1e1e1e")); p.drawRoundedRect(8, 14, 12, 9, 1, 1)  # disk slot
    p.end()
    return QIcon(pm)

def _icon_select() -> QIcon:
    pm, p = _make_pm()
    pts = QPolygon([QPoint(6,4), QPoint(6,20), QPoint(10,16), QPoint(13,22),
                    QPoint(16,21), QPoint(13,15), QPoint(18,15)])
    p.setBrush(QColor("#ddd")); p.setPen(QPen(QColor("#888"), 1))
    p.drawPolygon(pts)
    p.end()
    return QIcon(pm)

def _icon_text() -> QIcon:
    pm, p = _make_pm()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#ddd"), 2.5))
    # Letter "T"
    p.drawLine(7, 7, 21, 7)    # top bar
    p.drawLine(14, 7, 14, 22)  # stem
    p.end()
    return QIcon(pm)

def _icon_draw() -> QIcon:
    pm, p = _make_pm()
    p.setPen(QPen(QColor("#ddd"), 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    # Pencil body (diagonal line)
    p.drawLine(8, 20, 20, 8)
    # Pencil tip
    p.setPen(QPen(QColor("#f0b429"), 2))
    p.drawLine(6, 22, 8, 20)
    p.end()
    return QIcon(pm)

def _icon_erase() -> QIcon:
    pm, p = _make_pm()
    p.setBrush(QColor("#e06c75"))
    p.setPen(QPen(QColor("#aaa"), 1))
    p.drawRoundedRect(5, 10, 18, 10, 3, 3)
    # white stripe
    p.setBrush(QColor("#fff")); p.setPen(Qt.NoPen)
    p.drawRect(13, 10, 2, 10)
    p.end()
    return QIcon(pm)

def _icon_zoom(plus: bool) -> QIcon:
    pm, p = _make_pm()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#ddd"), 2))
    p.drawEllipse(5, 5, 15, 15)      # lens circle
    p.drawLine(18, 18, 23, 23)        # handle
    # + or -
    cx, cy = 12, 12
    p.drawLine(cx - 4, cy, cx + 4, cy)
    if plus:
        p.drawLine(cx, cy - 4, cx, cy + 4)
    p.end()
    return QIcon(pm)

def _icon_undo() -> QIcon:
    pm, p = _make_pm()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#ddd"), 2.5, Qt.SolidLine, Qt.RoundCap))
    # Arc (CCW)
    from PyQt5.QtCore import QRectF
    p.drawArc(QRectF(5, 6, 16, 16), 30 * 16, 240 * 16)
    # Arrow head pointing left
    p.drawLine(5, 14, 9, 10)
    p.drawLine(5, 14, 9, 18)
    p.end()
    return QIcon(pm)

def _icon_clear() -> QIcon:
    pm, p = _make_pm()
    p.setBrush(QColor("#888"))
    p.setPen(Qt.NoPen)
    # Bin body
    p.drawRoundedRect(7, 11, 14, 14, 2, 2)
    # Bin lid
    p.setBrush(QColor("#aaa"))
    p.drawRect(5, 8, 18, 3)
    p.setBrush(QColor("#888"))
    p.drawRoundedRect(10, 5, 8, 4, 1, 1)
    # Lines on bin body
    p.setPen(QPen(QColor("#555"), 1.5))
    for x in (11, 14, 17):
        p.drawLine(x, 14, x, 22)
    p.end()
    return QIcon(pm)

def _icon_pkg() -> QIcon:
    pm, p = _make_pm()
    p.setBrush(QColor("#98c379"))
    p.setPen(QPen(QColor("#6a9955"), 1))
    # Box
    p.drawRect(5, 10, 18, 14)
    # Lid
    pts = QPolygon([QPoint(5,10), QPoint(14,5), QPoint(23,10)])
    p.drawPolygon(pts)
    # Ribbon
    p.setPen(QPen(QColor("#fff"), 2))
    p.drawLine(14, 5, 14, 24)
    p.drawLine(5, 12, 23, 12)
    p.end()
    return QIcon(pm)
