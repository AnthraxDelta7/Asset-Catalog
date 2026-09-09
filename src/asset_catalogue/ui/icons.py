"""Small vector icons drawn at runtime.

Drawn rather than typed as emoji: a character like U+1F50D renders as a
full-colour emoji glyph on Windows, which looks pasted-on next to a flat
monochrome UI and can't follow the theme's text colour. Drawn rather than
shipped as image files too, since these are simple enough that a few
lines of QPainter beats adding binary assets to the build and a second
set at 2x for high-DPI screens.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def magnifier_icon(color: QColor, size: int = 32) -> QIcon:
    """A search glyph: a circle with a handle at the usual 45 degrees.

    Drawn large (the default 32px) and left for Qt to scale down to
    whatever iconSize the button asks for. An earlier version painted at
    the final 14px with a devicePixelRatio set on the pixmap, which
    double-applied against the painter's own scale and rendered a quarter
    of the circle -- downscaling a crisp large drawing is both simpler
    and sharper than trying to hint a tiny one.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, max(1.5, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    radius = size * 0.26
    centre = QPointF(size * 0.40, size * 0.40)
    painter.drawEllipse(centre, radius, radius)

    # The handle starts on the circle's edge rather than its centre, so
    # the strokes meet cleanly instead of the line crossing into the lens.
    offset = radius * math.cos(math.pi / 4)
    painter.drawLine(
        QPointF(centre.x() + offset, centre.y() + offset),
        QPointF(size * 0.86, size * 0.86),
    )
    painter.end()
    return QIcon(pixmap)
