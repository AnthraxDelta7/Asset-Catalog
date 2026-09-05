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
from pyqtgraph.opengl.shaders import FragmentShader, ShaderProgram, VertexShader
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

BACKGROUND_COLOR = (128, 128, 128, 255)  # GLViewWidget.setBackgroundColor -> mkColor expects 0-255, not 0-1
FALLBACK_COLOR = np.array([0.75, 0.75, 0.78, 1.0], dtype=np.float32)

# pyqtgraph's built-in 'shaded' shader floors at 0.2 ambient (rgb * (0.2 +
# up to 0.8 directional)) -- fine for a scientific-plotting tool where
# shape is all that matters, but it made real assets look "suuuuuper
# dark" here, especially anything already on the darker side of a PBR
# base color. A custom shader (same attribute/uniform names as 'shaded',
# se GLMeshItem's paint() binds by those exact names regardless of which
# shader is active) with a much higher ambient floor keeps the same
# vertex-shader/lighting-direction setup but never lets a face go below
# ~55% brightness, closer to what a "just let me see the model" preview
# viewer needs. Registers into pyqtgraph's global shader registry as a
# side effect of construction -- see ShaderProgram.__init__.
ShaderProgram(
    "catalogue_preview",
    [
        VertexShader("""
            uniform mat4 u_mvp;
            uniform mat3 u_normal;
            attribute vec4 a_position;
            attribute vec3 a_normal;
            attribute vec4 a_color;
            varying vec4 v_color;
            varying vec3 v_normal;
            void main() {
                v_normal = normalize(u_normal * a_normal);
                v_color = a_color;
                gl_Position = u_mvp * a_position;
            }
        """),
        FragmentShader("""
            #ifdef GL_ES
            precision mediump float;
            #endif
            varying vec4 v_color;
            varying vec3 v_normal;
            void main() {
                float p = dot(v_normal, normalize(vec3(1.0, -1.0, 1.0)));
                p = p < 0. ? 0. : p * 0.45;
                vec3 rgb = v_color.rgb * (0.55 + p);
                gl_FragColor = vec4(rgb, v_color.a);
            }
        """),
    ],
)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    """glTF's baseColorFactor is defined in linear color space (per spec),
    but our simple vertex-color shader has no HDR/tonemapping pipeline --
    it just multiplies color by a lighting term and hands the result
    straight to the display, which expects sRGB-encoded values. Skipping
    this step is what made every render look "suuuuuper dark": a linear
    0.094 (measured from a real asset's dark PBR material) is a
    perceptually much brighter 0.34 once properly sRGB-encoded -- any
    real glTF/PBR viewer does this conversion, it's not optional.
    """
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


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
    colors = colors.copy()
    colors[:, :3] = _linear_to_srgb(colors[:, :3])
    return colors


def _y_up_to_z_up(vertices: np.ndarray) -> np.ndarray:
    """glTF is Y-up by spec (and Blender's exporter enforces this on
    export regardless of Blender's own native Z-up scene, which is
    itself already been rotated to match a pack's up_axis correction --
    see blender_common.py's apply_corrections and the calibration-preview
    workflow), but pyqtgraph's GLViewWidget camera (orbit/elevation math)
    assumes Z is up, same convention as Blender's own viewport. Without
    this, every preview came out rotated 90 degrees from the static
    thumbnail's orientation -- rotating +90 degrees about X here (the
    standard Y-up -> Z-up conversion) undoes that mismatch, so what's
    "up" in the 3D preview always matches what's "up" in the thumbnail
    and in Blender's own calibration-preview render.
    """
    x, y, z = vertices[:, 0], vertices[:, 1], vertices[:, 2]
    return np.stack([x, -z, y], axis=1)


PreviewPart = tuple[np.ndarray, np.ndarray, np.ndarray]


def load_preview_parts(path: Path) -> list[PreviewPart]:
    """Returns (vertices, faces, vertex_colors) per mesh part in the
    scene, already in world space (Scene.dump bakes each part's node
    transform in) -- unlike collapsing everything into one merged mesh,
    this keeps each part's own material/texture distinct, which matters
    for a multi-material asset (a cauldron's body vs. its handle, say).

    Pure CPU work (trimesh parsing, color baking, the axis fix) -- no Qt
    or OpenGL calls -- so it's safe to run on a background thread. See
    MainWindow._open_model_preview: this is exactly the (surprisingly
    slow, first-time-only) part that used to run synchronously on the
    GUI thread with zero feedback, which is what made the first 3D
    preview in a session look like the app had frozen or crashed.
    """
    loaded = trimesh.load(str(path))
    parts = loaded.dump(concatenate=False) if isinstance(loaded, trimesh.Scene) else [loaded]
    result = []
    for part in parts:
        if not isinstance(part, trimesh.Trimesh) or len(part.vertices) == 0:
            continue
        vertices = _y_up_to_z_up(np.asarray(part.vertices, dtype=np.float32))
        faces = np.asarray(part.faces, dtype=np.int32)
        result.append((vertices, faces, _part_vertex_colors(part)))
    return result


class Model3DPreviewDialog(QDialog):
    """Displays a model asset's already-loaded preview parts (see
    load_preview_parts -- generated from the cached .glb alongside the
    static thumbnail by blender_thumbnail_script.py) in a real orbit/pan/
    zoom viewer. Left-drag to rotate, right-drag or the wheel to zoom, and
    shift+left-drag to pan -- all built into pyqtgraph's GLViewWidget, no
    custom mouse handling needed.

    Takes pre-loaded parts rather than a file path deliberately: building
    the GL widgets themselves is fast, so it's safe to do synchronously
    here on the GUI thread (required -- Qt/OpenGL widgets can't be built
    off it), while the genuinely slow work (parsing the file, baking
    colors) happens beforehand on a background thread. See
    MainWindow._open_model_preview.
    """

    def __init__(self, filename: str, parts: list[PreviewPart], parent: QWidget | None = None) -> None:
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

        self._build(parts)

    def _build(self, parts: list[PreviewPart]) -> None:
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
                shader="catalogue_preview",
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
