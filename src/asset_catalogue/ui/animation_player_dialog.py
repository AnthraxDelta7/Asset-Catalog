"""Plays back an already-rendered animation clip as a looping image
sequence.

Deliberately a frame flipbook rather than real-time playback in the
interactive 3D preview: that viewer draws a static mesh loaded with
trimesh and has no skinning of its own, so animating there would mean
implementing joint evaluation and vertex skinning from scratch. Blender
already does both correctly, so it renders the frames (once, on demand --
see animation_preview.py) and this just shows them in order.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class AnimationPlayerDialog(QDialog):
    def __init__(
        self,
        clip_name: str,
        frames: list[Path],
        interval_ms: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Animation: {clip_name}")
        # Loaded up front: a couple of dozen small PNGs is nothing to hold,
        # and decoding one per tick would make playback stutter on the
        # first loop through.
        self._pixmaps = [QPixmap(str(path)) for path in frames]
        self._index = 0

        layout = QVBoxLayout(self)
        self.frame_label = QLabel()
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.setMinimumSize(320, 320)
        layout.addWidget(self.frame_label)

        self.status_label = QLabel(
            f"{len(self._pixmaps)} frames — looping" if self._pixmaps else "No frames to play"
        )
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.play_button = QPushButton("⏸ Pause")
        self.play_button.clicked.connect(self._toggle)
        buttons.addWidget(self.play_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(max(1, interval_ms))
        self._timer.timeout.connect(self._advance)
        if self._pixmaps:
            self._show_frame()
            self._timer.start()
        else:
            self.play_button.setEnabled(False)

    def _show_frame(self) -> None:
        self.frame_label.setPixmap(self._pixmaps[self._index])

    def _advance(self) -> None:
        self._index = (self._index + 1) % len(self._pixmaps)
        self._show_frame()

    def _toggle(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.play_button.setText("▶ Play")
        else:
            self._timer.start()
            self.play_button.setText("⏸ Pause")

    def done(self, result: int) -> None:
        # Without this the timer keeps firing against a closed dialog's
        # label, which Qt tolerates but which keeps the whole pixmap list
        # alive for as long as the timer does.
        self._timer.stop()
        super().done(result)
