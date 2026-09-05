"""The interactive orbit/zoom 3D preview -- separate module from
main_window.py so its heavier imports (pyqtgraph, PyOpenGL, trimesh) are
only ever paid for when a user actually opens a 3D preview, not on every
app launch. Not a full PBR/texture-mapped renderer -- pyqtgraph's GLMeshItem
has no UV-mapped texture support, so each part's material/texture is baked
down to per-vertex colors instead (trimesh's Visuals.to_color()), which
gets a real asset's actual look across without needing a custom OpenGL
texture-mapping shader. Good enough for checking topology, proportions,
color, and orientation by spinning the model around -- the existing
Blender-rendered static thumbnail is still the accurate/final-look render.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph.opengl as gl
import trimesh
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

BACKGROUND_COLOR = (0.35, 0.35, 0.35, 1.0)
FALLBACK_COLOR = np.array([0.75, 0.75, 0.78, 1.0], dtype=np.float32)


def _part_vertex_colors(part: trimesh.Trimesh) -> np.ndarray:
    """Per-vertex RGBA in 0-1 range, baked from whatever material/texture
    the part actually has. TextureVisuals.to_color() returns either a
    single flat RGBA (a part with a plain material color, no image
    texture -- common for low-poly/stylized packs) or real per-vertex
    colors sampled at each vertex's UV against an actual texture image;
    both cases are normalized to a full per-vertex array here so the
    caller never needs to care which one it got.
    """
    try:
        colors = np.asarray(part.visual.to_color().vertex_colors, dtype=np.float32) / 255.0
    except Exception:  # noqa: BLE001 - any visual/material quirk falls back to plain grey
        colors = FALLBACK_COLOR
    if colors.ndim == 1:
        colors = np.tile(colors, (len(part.vertices), 1))
    return colors


def _load_scene_parts(path: Path) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Returns (vertices, faces, vertex_colors) per mesh part in the
    scene, already in world space (Scene.dump bakes each part's node
    transform in) -- unlike collapsing everything into one merged mesh,
    this keeps each part's own material/texture distinct, which matters
    for a multi-material asset (a cauldron's body vs. its handle, say).
    """
    loaded = trimesh.load(str(path))
    parts = loaded.dump(concatenate=False) if isinstance(loaded, trimesh.Scene) else [loaded]
    result = []
    for part in parts:
        if not isinstance(part, trimesh.Trimesh) or len(part.vertices) == 0:
            continue
        vertices = np.asarray(part.vertices, dtype=np.float32)
        faces = np.asarray(part.faces, dtype=np.int32)
        result.append((vertices, faces, _part_vertex_colors(part)))
    return result


class Model3DPreviewDialog(QDialog):
    """Loads a model asset's cached preview .glb (see model_preview.py --
    generated alongside the static thumbnail by blender_thumbnail_script.py)
    into a real orbit/pan/zoom viewer. Left-drag to rotate, right-drag or
    the wheel to zoom, and shift+left-drag to pan -- all built into
    pyqtgraph's GLViewWidget, no custom mouse handling needed.
    """

    def __init__(self, filename: str, preview_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"3D Preview -- {filename}")
        self.resize(640, 560)

        layout = QVBoxLayout(self)
        hint = QLabel("Drag to rotate · wheel or right-drag to zoom · Shift+drag to pan")
        layout.addWidget(hint)

        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(BACKGROUND_COLOR)
        layout.addWidget(self.view, stretch=1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self._load(preview_path)

    def _load(self, preview_path: Path) -> None:
        try:
            parts = _load_scene_parts(preview_path)
        except Exception as exc:  # noqa: BLE001 - surface as a preview error, not a crash
            self._show_error(f"Couldn't load preview: {exc}")
            return
        if not parts:
            self._show_error("Preview file has no usable geometry.")
            return

        all_mins = []
        all_maxs = []
        for vertices, faces, colors in parts:
            mesh_data = gl.MeshData(vertexes=vertices, faces=faces, vertexColors=colors)
            mesh_item = gl.GLMeshItem(
                meshdata=mesh_data,
                smooth=False,
                drawFaces=True,
                drawEdges=False,
                shader="shaded",
            )
            self.view.addItem(mesh_item)
            all_mins.append(vertices.min(axis=0))
            all_maxs.append(vertices.max(axis=0))

        # Frame the camera on the combined bounding sphere of every part --
        # same "center + radius, back off by a fixed factor" approach as
        # the Blender thumbnail's own camera framing (see
        # blender_thumbnail_script.py's frame_and_render), just expressed
        # in pyqtgraph's camera API.
        bounds_min = np.min(all_mins, axis=0)
        bounds_max = np.max(all_maxs, axis=0)
        center = (bounds_min + bounds_max) / 2
        radius = max(float(np.linalg.norm(bounds_max - bounds_min)) / 2, 0.001)
        self.view.opts["center"] = _to_vector3d(center)
        self.view.setCameraPosition(distance=radius * 3.0)

    def _show_error(self, message: str) -> None:
        self.view.setVisible(False)
        error_label = QLabel(message)
        error_label.setWordWrap(True)
        self.layout().insertWidget(1, error_label)


def _to_vector3d(point) -> "object":
    from PySide6.QtGui import QVector3D

    return QVector3D(float(point[0]), float(point[1]), float(point[2]))
