"""An activity bar: a band that keeps travelling while a job runs.

Deliberately not a percentage. A job moves through phases whose counts
are unrelated to each other -- "Pack 1/2", then "frame 5/24" -- and
nothing in that says how much of the whole job a phase is worth, so any
position derived from it misleads more than it informs. What a long
Blender or Godot run needs to convey is that it hasn't died, which this
answers honestly.

Painted rather than a styled QProgressBar because animating a Qt
stylesheet gradient means re-parsing the sheet every frame, which
judders; painting is both smoother and less code.
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
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(FRAME_MS)

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

        # A band sweeps the full width, so the bar says "running" without
        # implying a position it has no way to know.
        band = width * 0.28
        travel = (width + band) * self._phase - band
        fill_rect = QRectF(travel, 0, band, height)

        painter.save()
        painter.setClipPath(track)
        painter.fillRect(fill_rect, FILL_COLOR)

        # Softens the band's leading and trailing edges so it reads as a
        # travelling highlight rather than a sliding block.
        sheen = QLinearGradient(fill_rect.left(), 0, fill_rect.right(), 0)
        sheen.setColorAt(0.0, Qt.GlobalColor.transparent)
        sheen.setColorAt(0.5, SHEEN_COLOR)
        sheen.setColorAt(1.0, Qt.GlobalColor.transparent)
        painter.fillRect(fill_rect, sheen)
        painter.restore()
