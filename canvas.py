"""
canvas.py — PDF rendering canvas.

Renders all pages of a fitz.Document vertically with a small gap between
pages.  A transparent DrawOverlay sits on top for freehand drawing; LaTeXBox
child widgets float above the pages.

Coordinate system
-----------------
Everything lives in *canvas pixels*.  The PDF pages are rendered at
  render_scale  =  zoom * PIXEL_RATIO
where PIXEL_RATIO accounts for HiDPI displays.  The canvas widget's logical
size (what Qt layouts see) uses
  logical_scale  =  zoom
and the QPixmaps are painted scaled-to-fit into the logical rects, so on a
Retina display the PDF text is still crisp.
"""

from __future__ import annotations

import os

import fitz  # PyMuPDF
from PyQt5.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication, QSizePolicy, QWidget

from draw_overlay import DrawOverlay, cubic_bezier_segments, smooth_stroke_points
from latexbox import LaTeXBox


PAGE_GAP = 20       # logical pixels between pages
RENDER_DPI = 150    # base render DPI (zoom=1.0)
def _pixmap_from_fitz(pix: fitz.Pixmap) -> QPixmap:
    """
    Safely convert a fitz.Pixmap to a QPixmap.

    On Apple Silicon (ARM64) passing pix.samples (a memoryview) directly to
    QImage causes EXC_BAD_ACCESS / SIGBUS because the underlying buffer may
    not be aligned on an 8-byte boundary.  Calling bytes() forces Python to
    allocate a fresh, properly-aligned bytes object before handing it to Qt.
    """
    # bytes() always produces a new, 8-byte-aligned allocation — safe on ARM.
    raw = bytes(pix.samples)
    fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
    img = QImage(raw, pix.width, pix.height, pix.stride, fmt)
    # img.copy() makes Qt own the pixel data independently of `raw`
    return QPixmap.fromImage(img.copy())


class PDFCanvas(QWidget):
    """Widget that shows all PDF pages + draw overlay + LaTeX boxes."""

    status_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._doc: fitz.Document | None = None
        self._page_rects: list[QRect] = []      # logical canvas rects per page
        self._page_pixmaps: list[QPixmap] = []  # rendered page pixmaps
        self._zoom: float = 1.0
        self._nat_canvas_w: int = 800           # natural (zoom=1) canvas width
        self._nat_canvas_h: int = 600           # natural (zoom=1) canvas height
        self._engine = None
        self._tool: str = "select"
        self._latex_boxes: list[LaTeXBox] = []

        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMinimumSize(400, 600)
        self.resize(800, 600)   # sensible initial size before any doc is loaded

        # Draw overlay — child widget, same logical size as canvas
        self._draw = DrawOverlay(self)
        self._draw.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._draw.resize(800, 600)

    # ── Document ──────────────────────────────────────────────────────────────

    def load_document(self, path: str):
        self._doc = fitz.open(path)
        for box in self._latex_boxes:
            box.hide()
            box.deleteLater()
        self._latex_boxes.clear()
        self._rebuild()
        self._draw.reset_canvas()
        self._draw.init_canvas(self.width(), self.height())
        self._draw.set_zoom(self._zoom)

    def set_engine(self, engine):
        self._engine = engine

    # ── Zoom ─────────────────────────────────────────────────────────────────

    def set_zoom(self, zoom: float):
        old_zoom = self._zoom
        self._zoom = max(0.2, min(4.0, zoom))
        ratio = self._zoom / old_zoom

        # ── Scale LaTeX box positions ─────────────────────────────────────
        for box in self._latex_boxes:
            g = box.geometry()
            box.setGeometry(round(g.x() * ratio), round(g.y() * ratio),
                            round(g.width() * ratio), round(g.height() * ratio))

        # ── Rebuild pages ─────────────────────────────────────────────────
        self._rebuild()

        self._draw.init_canvas(self.width(), self.height())
        self._draw.set_zoom(self._zoom)

    def zoom_in(self):
        self.set_zoom(self._zoom + 0.25)

    def zoom_out(self):
        self.set_zoom(self._zoom - 0.25)

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def page_count(self) -> int:
        return len(self._doc) if self._doc else 0

    # ── Tool ─────────────────────────────────────────────────────────────────

    def set_tool(self, tool: str):
        self._tool = tool
        draw_active = tool in ("draw", "erase")
        self._draw.tool = tool if draw_active else self._draw.tool
        self._draw.set_active(draw_active)

        if draw_active:
            self._draw.raise_()
            self.setCursor(Qt.ArrowCursor)
        else:
            self.setCursor(Qt.CrossCursor if tool == "text" else Qt.ArrowCursor)

    def set_pen_color(self, color):
        self._draw.pen_color = color

    def set_pen_width(self, w: int):
        self._draw.pen_width = w
        self._draw.eraser_width = max(10, w * 7)
        self._draw._refresh_cursor()

    # ── LaTeX boxes ──────────────────────────────────────────────────────────

    def add_latex_box(self, pos: QPoint):
        box = LaTeXBox(self, self._engine, pos)
        box.removed.connect(self._remove_box)
        box.activated.connect(self._on_box_activated)
        box.show()
        box.raise_()
        self._latex_boxes.append(box)
        return box

    def _on_box_activated(self, active_box: LaTeXBox):
        """Highlight the clicked box; dim all others."""
        for box in self._latex_boxes:
            box._apply_style(selected=(box is active_box))

    def _remove_box(self, box: LaTeXBox):
        if box in self._latex_boxes:
            self._latex_boxes.remove(box)
        box.hide()
        box.deleteLater()

    # ── Drawing undo / clear ──────────────────────────────────────────────────

    def undo_draw(self):
        self._draw.undo()

    def clear_drawing(self):
        self._draw.clear()

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._tool == "text":
            self.add_latex_box(event.pos() - QPoint(8, 8))
        else:
            # Click on blank canvas — deselect all boxes
            for box in self._latex_boxes:
                box._apply_style(selected=False)

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#6a6a6a"))   # grey "desk"

        for rect, pix in zip(self._page_rects, self._page_pixmaps):
            # subtle drop shadow
            shadow = rect.adjusted(4, 4, 4, 4)
            p.fillRect(shadow, QColor(0, 0, 0, 55))
            p.drawPixmap(rect, pix)

    # ── Internal rebuild ──────────────────────────────────────────────────────

    def _rebuild(self):
        if self._doc is None:
            return

        self._page_pixmaps.clear()
        self._page_rects.clear()

        dpr = QApplication.primaryScreen().devicePixelRatio()
        y = 0
        max_w = 0

        for idx in range(len(self._doc)):
            pix = self._render_page(idx)
            self._page_pixmaps.append(pix)
            # Use logical size (physical px / dpr) for layout
            logical_w = round(pix.width() / dpr)
            logical_h = round(pix.height() / dpr)
            rect = QRect(0, y, logical_w, logical_h)
            self._page_rects.append(rect)
            y += logical_h + PAGE_GAP
            max_w = max(max_w, logical_w)

        total_h = max(600, y - PAGE_GAP + PAGE_GAP)
        self.setFixedSize(max_w, total_h)

        # Store natural (zoom=1) canvas dims so load_document can init the
        # draw canvas at the right fixed resolution.
        self._nat_canvas_w = round(max_w   / self._zoom)
        self._nat_canvas_h = round(total_h / self._zoom)

        # Resize the draw overlay widget to match display canvas.
        # The draw pixmap itself is NOT recreated here — it lives at natural
        # resolution and paintEvent stretches it to the widget rect.
        self._draw.setFixedSize(max_w, total_h)
        self._draw.raise_()

        self.update()
        self.status_message.emit(
            f"Pages: {self.page_count}  |  Zoom: {int(self._zoom * 100)}%"
        )

    def _render_page(self, idx: int) -> QPixmap:
        page = self._doc[idx]
        dpr = QApplication.primaryScreen().devicePixelRatio()
        scale = self._zoom * (RENDER_DPI / 72.0) * dpr
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        qpix = _pixmap_from_fitz(pix)
        qpix.setDevicePixelRatio(dpr)
        return qpix

    # ── Export ────────────────────────────────────────────────────────────────

    def export_pdf(self, output_path: str) -> bool:
        """
        Overlay annotations onto the original PDF and write to *output_path*.

        Strategy
        --------
        Each original page is copied verbatim (vector text, fonts, images all
        preserved).  Draw strokes are written as PDF vector paths.  LaTeX boxes
        are embedded via show_pdf_page (vector, lossless).
        """
        if not self._doc or not self._page_rects:
            return False

        new_doc = fitz.open()

        # Natural-resolution (zoom=1) logical pixels per PDF point.
        nat_px_per_pt = RENDER_DPI / 72.0

        strokes = self._draw.get_strokes()

        for page_idx, log_rect in enumerate(self._page_rects):
            orig_page = self._doc[page_idx]

            # Natural (zoom=1) rect for this page on the draw canvas
            nat_y_top    = log_rect.y()      / self._zoom
            nat_y_bottom = (log_rect.y() + log_rect.height()) / self._zoom
            nat_x_left   = log_rect.x()      / self._zoom

            # ── Copy original page (vector content preserved) ─────────────
            new_doc.insert_pdf(self._doc, from_page=page_idx, to_page=page_idx)
            new_page = new_doc[-1]
            page_rect = orig_page.rect   # PDF coordinate space (points)

            # ── Draw-stroke vector overlay ────────────────────────────────
            for stroke in strokes:
                smoothed_points = smooth_stroke_points(stroke.points, stroke.width_natural)
                point_runs = self._stroke_runs_for_page(smoothed_points, nat_y_top, nat_y_bottom)
                if not point_runs:
                    continue

                r, g, b, a = stroke.color_rgba
                w = stroke.width_natural / nat_px_per_pt
                col = (r / 255.0, g / 255.0, b / 255.0)
                opacity = a / 255.0

                for run in point_runs:
                    pdf_pts = [
                        fitz.Point(
                            (x - nat_x_left) / nat_px_per_pt,
                            (y - nat_y_top) / nat_px_per_pt,
                        )
                        for (x, y) in run
                    ]
                    self._commit_smooth_stroke(new_page, pdf_pts, col, w, opacity)

            # ── LaTeX box vector overlays ─────────────────────────────────
            for box in self._latex_boxes:
                box_geom = box.geometry()
                if not box_geom.intersects(log_rect):
                    continue

                cx = (box_geom.x() - log_rect.x())
                cy = (box_geom.y() - log_rect.y())
                cw = box_geom.width()
                ch = max(1, box_geom.height())

                x0 = cx / (RENDER_DPI / 72.0 * self._zoom)
                y0 = cy / (RENDER_DPI / 72.0 * self._zoom)
                x1 = x0 + cw / (RENDER_DPI / 72.0 * self._zoom)
                y1 = y0 + ch / (RENDER_DPI / 72.0 * self._zoom)
                dest = fitz.Rect(x0, y0, x1, y1)

                pdf_path = self._latex_pdf_path_for_export(box)
                if not pdf_path or not os.path.exists(pdf_path):
                    hi_pix = box.get_hi_res_pixmap()
                    if hi_pix is not None and not hi_pix.isNull():
                        self.status_message.emit("Could not export LaTeX as vector PDF. Re-render the box and try again.")
                        new_doc.close()
                        return False
                    continue

                # Vector embed: show_pdf_page preserves fonts & math shapes.
                latex_doc = fitz.open(pdf_path)
                new_page.show_pdf_page(dest, latex_doc, 0, overlay=True)
                latex_doc.close()

        new_doc.save(output_path)
        new_doc.close()
        return True

    def _latex_pdf_path_for_export(self, box: LaTeXBox) -> str | None:
        pdf_path = box.get_latex_pdf_path()
        if pdf_path and os.path.exists(pdf_path):
            return pdf_path

        engine = getattr(box, "engine", None) or self._engine
        if engine is None:
            return None

        code = box.get_latex().strip()
        if not code:
            return None

        _png_path, rerendered_pdf_path, err = engine.render(code)
        if err or not rerendered_pdf_path or not os.path.exists(rerendered_pdf_path):
            return None
        return rerendered_pdf_path

    @staticmethod
    def _stroke_runs_for_page(
        points: list[tuple[float, float]],
        y_top: float,
        y_bottom: float,
    ) -> list[list[tuple[float, float]]]:
        runs: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []

        for pt in points:
            if y_top <= pt[1] < y_bottom:
                current.append(pt)
                continue
            if current:
                runs.append(current)
                current = []

        if current:
            runs.append(current)
        return runs

    @staticmethod
    def _commit_smooth_stroke(page, points, color, width, opacity):
        if not points:
            return

        if len(points) == 1:
            shape = page.new_shape()
            shape.draw_circle(points[0], width / 2.0)
            shape.finish(
                fill=color,
                color=color,
                width=0,
                fill_opacity=opacity,
                stroke_opacity=opacity,
            )
            shape.commit()
            return

        shape = page.new_shape()
        if len(points) == 2:
            shape.draw_line(points[0], points[1])
        else:
            xy_points = [(pt.x, pt.y) for pt in points]
            for idx, (c1, c2, end) in enumerate(cubic_bezier_segments(xy_points)):
                start = xy_points[idx]
                shape.draw_bezier(
                    fitz.Point(*start),
                    fitz.Point(*c1),
                    fitz.Point(*c2),
                    fitz.Point(*end),
                )

        shape.finish(
            color=color,
            width=width,
            lineCap=1,
            closePath=False,
            stroke_opacity=opacity,
        )
        shape.commit()
