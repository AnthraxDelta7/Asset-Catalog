"""The interactive orbit/zoom 3D preview -- separate module from
main_window.py so its heavier imports (pyqtgraph, PyOpenGL, trimesh) are
only ever paid for when a user actually opens a 3D preview, not on every
app launch. Deliberately not a full renderer: plain flat-shaded geometry,
no materials/textures loaded from the .glb -- that's what the existing
Blender-rendered static thumbnail is for. This is for checking topology,
proportions, and orientation by spinning the model around, which doesn't
need real shading to be useful.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph.opengl as gl
import trimesh
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget


def _load_merged_mesh(path: Path) -> trimesh.Trimesh | None:
    loaded = trimesh.load(str(path), force="mesh", process=False)
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.vertices) == 0:
        return None
    return loaded


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
        layout.addWidget(self.view, stretch=1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self._load(preview_path)

    def _load(self, preview_path: Path) -> None:
        try:
            mesh = _load_merged_mesh(preview_path)
        except Exception as exc:  # noqa: BLE001 - surface as a preview error, not a crash
            self._show_error(f"Couldn't load preview: {exc}")
            return
        if mesh is None:
            self._show_error("Preview file has no usable geometry.")
            return

        vertices = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.faces, dtype=np.int32)
        mesh_data = gl.MeshData(vertexes=vertices, faces=faces)
        mesh_item = gl.GLMeshItem(
            meshdata=mesh_data,
            smooth=False,
            drawFaces=True,
            drawEdges=False,
            shader="shaded",
            color=(0.75, 0.75, 0.78, 1.0),
        )
        self.view.addItem(mesh_item)

        # Frame the camera on the mesh's bounding sphere -- same "center +
        # radius, back off by a fixed factor" approach as the Blender
        # thumbnail's own camera framing (see blender_thumbnail_script.py's
        # frame_and_render), just expressed in pyqtgraph's camera API.
        center = mesh.bounds.mean(axis=0)
        radius = max(float(np.linalg.norm(mesh.bounds[1] - mesh.bounds[0])) / 2, 0.001)
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
