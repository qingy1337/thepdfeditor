"""
latexbox.py — Draggable, resizable LaTeX text-box overlay widget.

The box keeps editing and preview content front-and-center. Controls float as a
small overlay when the box is selected instead of consuming a permanent header.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QPoint, QRect, QEvent, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap, QPolygon
from PyQt5.QtWidgets import (
    QApplication, QLabel, QPushButton, QSizePolicy,
    QTextEdit, QVBoxLayout, QHBoxLayout, QWidget,
)

_HANDLE = 12
_MIN_W = 28
_MIN_H = 28


class LaTeXBox(QWidget):
    """Movable, resizable widget for LaTeX input and inline preview."""

    removed = pyqtSignal(object)
    activated = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        engine=None,
        pos: QPoint | None = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self._mode = "edit"
        self._hi_res_pixmap: QPixmap | None = None
        self._latex_pdf_path: str | None = None

        self._action: str | None = None
        self._press_global = QPoint()
        self._press_geom = QRect()

        self._collapsed = False
        self._selected = True
        self._saved_height = 180
        self._saved_width = 360

        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(360, 180)
        if pos is not None:
            self.move(pos)

        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui()
        self._apply_style(selected=True)
        self.raise_()

    # ── Styling ───────────────────────────────────────────────────────────

    def _apply_style(self, selected: bool):
        self._selected = selected
        border = "#1b82d1" if selected else "#9aa6b2"
        self.setStyleSheet(
            "LaTeXBox {"
            f"background: rgba(255,255,250,238); border: 1px solid {border};"
            "border-radius: 7px; }"
        )
        if hasattr(self, "_controls"):
            self._controls.setVisible(selected and not self._collapsed)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(3, 3, 3, 3)
        root.setSpacing(2)

        self._collapsed_view = QLabel()
        self._collapsed_view.setAlignment(Qt.AlignCenter)
        self._collapsed_view.setStyleSheet("background: transparent; border: none;")
        self._collapsed_view.hide()
        root.addWidget(self._collapsed_view)

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(1, 1, 1, 1)
        body_layout.setSpacing(2)

        self._editor = QTextEdit()
        self._editor.setFont(QFont("Menlo", 11))
        self._editor.setPlaceholderText(
            "Enter LaTeX here...\n\n"
            "Examples:\n"
            "  $E = mc^2$\n"
            "  \\[ \\int_0^\\infty e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2} \\]\n"
            "  Some text $\\Braket{u,v} = \\sum_{i=1}^n u_i v_i$"
        )
        self._editor.setStyleSheet(
            "QTextEdit { background: rgba(255,255,255,235); border: 1px solid #d9dee4;"
            "border-radius: 5px; padding: 1px 2px; }"
        )
        body_layout.addWidget(self._editor)

        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._preview.setStyleSheet(
            "background: rgba(255,255,255,245); border: 1px solid #d9dee4; border-radius: 5px;"
        )
        self._preview.hide()
        body_layout.addWidget(self._preview)

        self._err_lbl = QLabel()
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setStyleSheet(
            "color: #b42318; font-size: 10px; font-family: Menlo,monospace;"
        )
        self._err_lbl.hide()
        body_layout.addWidget(self._err_lbl)

        root.addWidget(self._body)

        controls_parent = self.parentWidget() if self.parentWidget() is not None else self
        self._controls = QWidget(controls_parent)
        self._controls.setStyleSheet(
            "background: rgba(24,32,40,185); border: none; border-radius: 10px;"
        )
        controls_layout = QHBoxLayout(self._controls)
        controls_layout.setContentsMargins(6, 3, 6, 3)
        controls_layout.setSpacing(4)

        self._move_handle = QLabel("Move")
        self._move_handle.setStyleSheet(
            "color: rgba(255,255,255,220); font-size: 10px; font-weight: 600; padding: 0 2px;"
        )
        controls_layout.addWidget(self._move_handle)

        self._render_btn = QPushButton("Render")
        self._render_btn.setFixedHeight(18)
        self._render_btn.setStyleSheet(_btn_css("#22863a", "#2ea043"))
        self._render_btn.clicked.connect(self._do_render)
        controls_layout.addWidget(self._render_btn)

        self._edit_btn = QPushButton("Edit")
        self._edit_btn.setFixedHeight(18)
        self._edit_btn.setStyleSheet(_btn_css("#475467", "#667085"))
        self._edit_btn.clicked.connect(self._show_edit)
        controls_layout.addWidget(self._edit_btn)

        close_btn = QPushButton("X")
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(_btn_css("#b42318", "#d92d20"))
        close_btn.clicked.connect(lambda: self.removed.emit(self))
        controls_layout.addWidget(close_btn)

        for child in self.findChildren(QWidget):
            child.installEventFilter(self)
        self._controls.installEventFilter(self)
        self._move_handle.installEventFilter(self)
        self.destroyed.connect(self._controls.deleteLater)
        self.setMinimumHeight(_MIN_H)
        self._position_controls()

    def _position_controls(self):
        if not hasattr(self, "_controls"):
            return
        self._controls.adjustSize()
        controls_parent = self._controls.parentWidget()
        parent_w = controls_parent.width() if controls_parent is not None else self.width()
        margin = 4
        x = self.x() + self.width() - self._controls.width()
        y = self.y() - self._controls.height() - margin
        x = max(0, min(x, parent_w - self._controls.width()))
        y = max(0, y)
        self._controls.move(x, y)
        self._controls.raise_()

    # ── Event filter ──────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.activated.emit(self)
            if obj in (self._move_handle, self._controls):
                self._start_action("move", event.globalPos())
                return True
        if event.type() == QEvent.MouseMove and self._action == "move" and obj in (self._move_handle, self._controls):
            self._drag_move(event.globalPos())
            return True
        if event.type() == QEvent.MouseButtonRelease and obj in (self._move_handle, self._controls):
            self._action = None
            return True
        return False

    # ── Collapse / expand ────────────────────────────────────────────────

    def collapse(self):
        if self._collapsed:
            return
        self._saved_height = self.height()
        self._saved_width = self.width()
        self._body.hide()

        if self._hi_res_pixmap and not self._hi_res_pixmap.isNull():
            pm = self._hi_res_pixmap.scaledToHeight(110, Qt.SmoothTransformation)
            if pm.width() > 520:
                pm = pm.scaledToWidth(520, Qt.SmoothTransformation)
            self._collapsed_view.setPixmap(pm)
            self._collapsed_view.show()
            new_w = pm.width() + 8
            new_h = pm.height() + 8
        else:
            self._collapsed_view.hide()
            new_w = max(140, self.width())
            new_h = 34

        self._collapsed = True
        self._apply_style(selected=False)
        self.setFixedHeight(new_h)
        self.resize(new_w, new_h)
        self._position_controls()

    def expand(self):
        if not self._collapsed:
            return
        self._collapsed_view.hide()
        self._body.show()
        self._collapsed = False
        self._apply_style(selected=True)
        self.setMinimumHeight(_MIN_H)
        self.setMaximumHeight(16_777_215)
        self.resize(max(self._saved_width, _MIN_W), max(self._saved_height, _MIN_H))
        self._position_controls()

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ── Rendering ────────────────────────────────────────────────────────

    def _do_render(self):
        code = self._editor.toPlainText().strip()
        if not code or self.engine is None:
            return

        self._render_btn.setEnabled(False)
        self._render_btn.setText("...")
        self._err_lbl.hide()
        QApplication.processEvents()

        path, pdf_path, err = self.engine.render(code)
        self._render_btn.setEnabled(True)
        self._render_btn.setText("Render")
        if err:
            self._err_lbl.setText(err)
            self._err_lbl.show()
            return

        self._latex_pdf_path = pdf_path
        self._hi_res_pixmap = QPixmap(path)
        self._mode = "preview"
        self._editor.hide()
        self._err_lbl.hide()
        self._preview.show()
        self._refresh_preview()

    def _show_edit(self):
        self._mode = "edit"
        self._preview.hide()
        self._editor.show()

    def _refresh_preview(self):
        if self._hi_res_pixmap is None or self._hi_res_pixmap.isNull():
            return
        available = self._preview.size()
        if available.width() < 10 or available.height() < 10:
            return

        pix = self._hi_res_pixmap.scaled(
            available, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._preview.setPixmap(pix)

    # ── Resize / paint ────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_controls()
        if self._mode == "preview":
            QTimer.singleShot(0, self._refresh_preview)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._position_controls()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        pts = QPolygon([
            r.bottomRight(),
            QPoint(r.right() - _HANDLE * 2, r.bottom()),
            QPoint(r.right(), r.bottom() - _HANDLE * 2),
        ])
        p.setBrush(QColor("#1b82d1"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(pts)
        p.end()

    # ── Mouse interactions ────────────────────────────────────────────────

    def _start_action(self, action: str, global_pos: QPoint):
        self._action = action
        self._press_global = global_pos
        self._press_geom = self.geometry()
        self.raise_()
        self._controls.raise_()

    def _drag_move(self, global_pos: QPoint):
        delta = global_pos - self._press_global
        self.move(self._press_geom.topLeft() + delta)

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "_controls"):
            self._controls.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self._position_controls()
        if hasattr(self, "_controls"):
            self._controls.setVisible(self._selected and not self._collapsed)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.activated.emit(self)
        if self._in_resize_corner(event.pos()):
            self._start_action("resize", event.globalPos())
            event.accept()
            return
        event.accept()

    def mouseMoveEvent(self, event):
        if self._action == "resize":
            delta = event.globalPos() - self._press_global
            g = self._press_geom
            self.resize(max(_MIN_W, g.width() + delta.x()), max(_MIN_H, g.height() + delta.y()))
            event.accept()
            return

        if self._in_resize_corner(event.pos()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._action = None

    def _in_resize_corner(self, pos: QPoint) -> bool:
        r = self.rect()
        return pos.x() >= r.right() - _HANDLE * 2 and pos.y() >= r.bottom() - _HANDLE * 2

    # ── Public API ────────────────────────────────────────────────────────

    def get_latex(self) -> str:
        return self._editor.toPlainText()

    def get_hi_res_pixmap(self) -> QPixmap | None:
        return self._hi_res_pixmap

    def get_latex_pdf_path(self) -> str | None:
        return self._latex_pdf_path


def _btn_css(bg: str, hover: str) -> str:
    return (
        "QPushButton {"
        f"background: {bg};"
        "color: white;"
        "border: none;"
        "border-radius: 4px;"
        "font-size: 10px;"
        "font-weight: bold;"
        "padding: 0 6px;"
        "}"
        "QPushButton:hover {"
        f"background: {hover};"
        "}"
    )
