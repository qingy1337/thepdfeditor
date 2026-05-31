"""
draw_overlay.py — Transparent freehand-drawing overlay.

The overlay keeps vector stroke data as the source of truth and rasterizes it
for display. This keeps zooming stable, makes saved output match the on-screen
look much more closely, and lets the eraser remove only annotation strokes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import (
    QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt5.QtWidgets import QApplication, QWidget


_MAX_UNDO = 40
_MIN_POINT_SPACING = 0.35


@dataclass
class Stroke:
    """One continuous pen stroke in natural (zoom=1) logical coordinates."""

    tool: str = "draw"
    color_rgba: tuple[int, int, int, int] = (220, 30, 30, 255)
    width_natural: float = 3.0
    points: list[tuple[float, float]] = field(default_factory=list)


def _clone_strokes(strokes: list[Stroke]) -> list[Stroke]:
    return [
        Stroke(
            tool=stroke.tool,
            color_rgba=stroke.color_rgba,
            width_natural=stroke.width_natural,
            points=list(stroke.points),
        )
        for stroke in strokes
    ]


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _distance_point_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    sx, sy = start
    ex, ey = end
    px, py = point
    dx = ex - sx
    dy = ey - sy
    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)
    t = ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx = sx + t * dx
    qy = sy + t * dy
    return math.hypot(px - qx, py - qy)


def _path_length(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(_distance(points[i - 1], points[i]) for i in range(1, len(points)))


def _densify_points(
    points: list[tuple[float, float]],
    step: float,
) -> list[tuple[float, float]]:
    if len(points) <= 1:
        return list(points)

    dense = [points[0]]
    for idx in range(1, len(points)):
        x0, y0 = points[idx - 1]
        x1, y1 = points[idx]
        seg_len = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(math.ceil(seg_len / max(0.25, step))))
        for j in range(1, steps + 1):
            t = j / steps
            dense.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return dense


def _compress_points(
    points: list[tuple[float, float]],
    step: float,
) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return list(points)

    kept = [points[0]]
    for pt in points[1:-1]:
        if _distance(kept[-1], pt) >= step:
            kept.append(pt)
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return kept


def _relax_points(
    points: list[tuple[float, float]],
    strength: float = 0.2,
) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return list(points)

    relaxed = [points[0]]
    keep = 1.0 - 2.0 * strength
    for idx in range(1, len(points) - 1):
        x0, y0 = points[idx - 1]
        x1, y1 = points[idx]
        x2, y2 = points[idx + 1]
        relaxed.append((
            x1 * keep + (x0 + x2) * strength,
            y1 * keep + (y0 + y2) * strength,
        ))
    relaxed.append(points[-1])
    return relaxed


def _chaikin(
    points: list[tuple[float, float]],
    ratio: float = 0.22,
    iterations: int = 2,
) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return list(points)

    out = list(points)
    for _ in range(iterations):
        refined = [out[0]]
        for idx in range(len(out) - 1):
            x0, y0 = out[idx]
            x1, y1 = out[idx + 1]
            refined.append((
                (1.0 - ratio) * x0 + ratio * x1,
                (1.0 - ratio) * y0 + ratio * y1,
            ))
            refined.append((
                ratio * x0 + (1.0 - ratio) * x1,
                ratio * y0 + (1.0 - ratio) * y1,
            ))
        refined.append(out[-1])
        out = refined
    return out


def smooth_stroke_points(
    points: list[tuple[float, float]],
    width_natural: float,
) -> list[tuple[float, float]]:
    """Apply light resampling and curve-friendly smoothing to raw stroke points."""
    if len(points) <= 2:
        return list(points)

    dense_step = max(0.7, min(2.0, width_natural * 0.5))
    dense = _densify_points(points, dense_step)
    relaxed = _relax_points(dense, strength=0.18)
    smoothed = _chaikin(relaxed, ratio=0.22, iterations=2)
    return _compress_points(smoothed, max(0.35, dense_step * 0.35))


def cubic_bezier_segments(
    points: list[tuple[float, float]],
    tension: float = 1.0,
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """Convert a point chain into cubic Bezier segments using Catmull-Rom style handles."""
    if len(points) < 2:
        return []

    scale = tension / 6.0
    segments = []
    for idx in range(len(points) - 1):
        p0 = points[idx - 1] if idx > 0 else points[idx]
        p1 = points[idx]
        p2 = points[idx + 1]
        p3 = points[idx + 2] if idx + 2 < len(points) else points[idx + 1]

        c1 = (
            p1[0] + (p2[0] - p0[0]) * scale,
            p1[1] + (p2[1] - p0[1]) * scale,
        )
        c2 = (
            p2[0] - (p3[0] - p1[0]) * scale,
            p2[1] - (p3[1] - p1[1]) * scale,
        )
        segments.append((c1, c2, p2))
    return segments


class DrawOverlay(QWidget):
    """Transparent overlay for freehand pen and eraser."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._canvas: QPixmap | None = None
        self._dpr: float = 1.0
        self._zoom: float = 1.0
        self._drawing = False
        self._last_pt: QPointF | None = None
        self._undo_stack: list[list[Stroke]] = []

        self._strokes: list[Stroke] = []
        self._current_stroke: Stroke | None = None

        self.tool: str = "draw"
        self.pen_color: QColor = QColor(220, 30, 30)
        self.pen_width: int = 3
        self.eraser_width: int = 28

    # ── Activation ────────────────────────────────────────────────────────

    def set_zoom(self, zoom: float):
        self._zoom = max(0.2, zoom)
        self._rerender_canvas(include_current=bool(self._current_stroke))

    def set_active(self, active: bool):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not active)
        self._refresh_cursor()

    def _refresh_cursor(self):
        if not self.testAttribute(Qt.WA_TransparentForMouseEvents):
            if self.tool == "erase":
                d = self.eraser_width + 6
                pm = QPixmap(d, d)
                pm.fill(Qt.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(QColor(80, 80, 80, 200), 2))
                p.drawEllipse(3, 3, d - 6, d - 6)
                p.end()
                self.setCursor(QCursor(pm, d // 2, d // 2))
            else:
                self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # ── Canvas management ────────────────────────────────────────────────

    def init_canvas(self, w: int, h: int):
        self._dpr = QApplication.primaryScreen().devicePixelRatio()
        pw, ph = round(w * self._dpr), round(h * self._dpr)
        self._canvas = QPixmap(pw, ph)
        self._canvas.fill(Qt.transparent)
        self._rerender_canvas(include_current=bool(self._current_stroke))

    def reset_canvas(self):
        self._drawing = False
        self._last_pt = None
        self._current_stroke = None
        self._undo_stack.clear()
        self._strokes.clear()
        if self._canvas is not None:
            self._canvas.fill(Qt.transparent)
            self.update()

    # ── Painting ─────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        if self._canvas is not None:
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self._canvas)

    # ── Mouse events ────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self._canvas is None:
            return

        self._push_undo()
        self._drawing = True
        self._last_pt = QPointF(event.pos())

        if self.tool == "draw":
            color = self.pen_color
            self._current_stroke = Stroke(
                tool="draw",
                color_rgba=(color.red(), color.green(), color.blue(), color.alpha()),
                width_natural=float(self.pen_width) / self._zoom,
                points=[],
            )
            self._append_point(self._current_stroke, self._to_natural(self._last_pt))
        else:
            nat = self._to_natural(self._last_pt)
            radius = (self.eraser_width / self._zoom) / 2.0
            self._erase_capsule(nat, nat, radius)

        self._rerender_canvas(include_current=bool(self._current_stroke))

    def mouseMoveEvent(self, event):
        if not self._drawing or self._last_pt is None:
            return

        cur = QPointF(event.pos())
        if self.tool == "draw":
            if self._current_stroke is not None:
                self._append_point(self._current_stroke, self._to_natural(cur))
        else:
            start = self._to_natural(self._last_pt)
            end = self._to_natural(cur)
            radius = (self.eraser_width / self._zoom) / 2.0
            self._erase_capsule(start, end, radius)

        self._last_pt = cur
        self._rerender_canvas(include_current=bool(self._current_stroke))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        self._drawing = False
        self._last_pt = None
        if self._current_stroke is not None and self._current_stroke.points:
            self._strokes.append(self._current_stroke)
        self._current_stroke = None
        self._rerender_canvas()

    # ── Rasterization ────────────────────────────────────────────────────

    def _to_natural(self, pt: QPointF) -> tuple[float, float]:
        return (pt.x() / self._zoom, pt.y() / self._zoom)

    def _from_natural(self, pt: tuple[float, float]) -> QPointF:
        return QPointF(pt[0] * self._zoom * self._dpr, pt[1] * self._zoom * self._dpr)

    def _append_point(self, stroke: Stroke, pt: tuple[float, float]):
        if stroke.points and _distance(stroke.points[-1], pt) < _MIN_POINT_SPACING:
            stroke.points[-1] = pt
            return
        stroke.points.append(pt)

    def _rerender_canvas(self, include_current: bool = False):
        if self._canvas is None:
            return

        self._canvas.fill(Qt.transparent)
        painter = QPainter(self._canvas)
        painter.setRenderHint(QPainter.Antialiasing)

        for stroke in self._strokes:
            self._paint_stroke(painter, stroke)
        if include_current and self._current_stroke is not None:
            self._paint_stroke(painter, self._current_stroke)

        painter.end()
        self.update()

    def _paint_stroke(self, painter: QPainter, stroke: Stroke):
        smooth_pts = smooth_stroke_points(stroke.points, stroke.width_natural)
        pts = [self._from_natural(pt) for pt in smooth_pts]
        if not pts:
            return

        r, g, b, a = stroke.color_rgba
        color = QColor(r, g, b, a)
        width = max(1.0, stroke.width_natural * self._zoom * self._dpr)

        if len(pts) == 1:
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(pts[0], width / 2.0, width / 2.0)
            return

        path = QPainterPath()
        if len(pts) == 2:
            path.moveTo(pts[0])
            path.lineTo(pts[1])
        else:
            path.moveTo(pts[0])
            for c1, c2, end in cubic_bezier_segments([(pt.x(), pt.y()) for pt in pts]):
                path.cubicTo(
                    QPointF(c1[0], c1[1]),
                    QPointF(c2[0], c2[1]),
                    QPointF(end[0], end[1]),
                )

        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    # ── Eraser ────────────────────────────────────────────────────────────

    def _erase_capsule(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        radius: float,
    ):
        new_strokes: list[Stroke] = []
        for stroke in self._strokes:
            new_strokes.extend(self._erase_from_stroke(stroke, start, end, radius))
        self._strokes = new_strokes

    def _erase_from_stroke(
        self,
        stroke: Stroke,
        start: tuple[float, float],
        end: tuple[float, float],
        radius: float,
    ) -> list[Stroke]:
        if not stroke.points:
            return []

        sample_step = max(1.0, min(3.0, stroke.width_natural * 0.8))
        samples = _densify_points(stroke.points, sample_step)
        clearance = radius + stroke.width_natural / 2.0
        hits = [
            _distance_point_to_segment(pt, start, end) <= clearance
            for pt in samples
        ]
        if not any(hits):
            return [stroke]
        if all(hits):
            return []

        kept_runs: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        for pt, hit in zip(samples, hits):
            if hit:
                if current:
                    kept_runs.append(current)
                    current = []
                continue
            current.append(pt)
        if current:
            kept_runs.append(current)

        pieces: list[Stroke] = []
        compress_step = max(0.8, stroke.width_natural * 0.65)
        min_len = max(0.5, stroke.width_natural * 0.2)
        for run in kept_runs:
            pts = _compress_points(run, compress_step)
            if len(pts) == 1 or _path_length(pts) >= min_len:
                pieces.append(
                    Stroke(
                        tool="draw",
                        color_rgba=stroke.color_rgba,
                        width_natural=stroke.width_natural,
                        points=pts,
                    )
                )
        return pieces

    # ── Undo ──────────────────────────────────────────────────────────────

    def _push_undo(self):
        if len(self._undo_stack) >= _MAX_UNDO:
            self._undo_stack.pop(0)
        self._undo_stack.append(_clone_strokes(self._strokes))

    def undo(self):
        if not self._undo_stack:
            return
        self._strokes = self._undo_stack.pop()
        self._drawing = False
        self._last_pt = None
        self._current_stroke = None
        self._rerender_canvas()

    def clear(self):
        if not self._strokes:
            return
        self._push_undo()
        self._drawing = False
        self._last_pt = None
        self._current_stroke = None
        self._strokes.clear()
        self._rerender_canvas()

    # ── Export helpers ────────────────────────────────────────────────────

    def get_pixmap(self) -> QPixmap | None:
        return self._canvas

    def get_strokes(self) -> list[Stroke]:
        return _clone_strokes(self._strokes)
