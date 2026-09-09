"""A progress bar that always looks alive, whether or not the total is known.

Two jobs at once, which is why this is painted rather than a styled
QProgressBar: it has to answer "is this thing still running?" and "how
far along is it?" independently. A long Blender or Godot pass can sit on
one step for a minute, so a bar that only moves when the count changes
reads as frozen -- the sheen keeps travelling regardless. Animating a
Qt stylesheet gradient would mean re-parsing the sheet every frame,
which judders; painting is both smoother and less code.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

TRACK_COLOR = QColor("#2a2a2a")
FILL_COLOR = QColor("#2d6cdf")
SHEEN_COLOR = QColor(255, 255, 255, 60)
BAR_HEIGHT = 10
# ~30fps. Fast enough to read as motion, slow enough that it costs
# nothing next to the Blender/Godot subprocesses this sits in front of.
FRAME_MS = 33


class FlowingProgressBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())
        self._fraction: float | None = None  # None until a real count arrives
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(FRAME_MS)

    def set_progress(self, current: int, total: int) -> None:
        """Switches to determinate. Clamped rather than trusted: these
        counts come from parsing job output, so a malformed pair must
        never send the fill off the end of the widget.
        """
        if total > 0:
            self._fraction = max(0.0, min(1.0, current / total))

    def set_indeterminate(self) -> None:
        self._fraction = None

    def stop(self) -> None:
        self._timer.stop()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        radius = height / 2

        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
        painter.fillPath(track, TRACK_COLOR)

        if self._fraction is None:
            # Unknown total: a band sweeps the full width so the bar still
            # says "running" without implying a position it doesn't know.
            band = width * 0.28
            travel = (width + band) * self._phase - band
            fill_rect = QRectF(travel, 0, band, height)
        else:
            fill_rect = QRectF(0, 0, width * self._fraction, height)
        if fill_rect.width() <= 0:
            return

        painter.save()
        painter.setClipPath(track)
        painter.fillRect(fill_rect, FILL_COLOR)

        # The travelling highlight -- the part that distinguishes "working"
        # from "stalled on a step that takes a minute".
        sheen = QLinearGradient(fill_rect.left(), 0, fill_rect.right(), 0)
        centre = self._phase if self._fraction is not None else 0.5
        sheen.setColorAt(max(0.0, centre - 0.18), Qt.GlobalColor.transparent)
        sheen.setColorAt(centre, SHEEN_COLOR)
        sheen.setColorAt(min(1.0, centre + 0.18), Qt.GlobalColor.transparent)
        painter.fillRect(fill_rect, sheen)
        painter.restore()
