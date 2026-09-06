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

import io
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph.opengl as gl
import trimesh
from PIL import Image
from pyqtgraph.opengl.shaders import FragmentShader, ShaderProgram, VertexShader
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

BACKGROUND_COLOR = (128, 128, 128, 255)  # GLViewWidget.setBackgroundColor -> mkColor expects 0-255, not 0-1
FALLBACK_COLOR = np.array([0.75, 0.75, 0.78, 1.0], dtype=np.float32)

# A recognized collider part (see _looks_like_collider_name below --
# either godot_export_script.gd's own injected "<name>_collider" debug
# meshes, or a third-party pack's own collision mesh by common naming
# convention) usually has no real material of its own, so it would
# otherwise bake down to the same flat FALLBACK_COLOR as any other
# untextured part -- indistinguishable from real geometry at a glance.
# Overriding to a translucent, unmistakably-not-a-real-material
# orange (rendered with alpha blending, see the "translucent" glOptions
# below) makes it read as a diagnostic overlay rather than actual surface
# color, the same visual language most game engines use for collision
# gizmos in-editor.
COLLIDER_COLOR = np.array([1.0, 0.4, 0.1, 0.35], dtype=np.float32)

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


def _part_vertex_colors(part: trimesh.Trimesh, is_collider: bool) -> np.ndarray:
    """Per-vertex RGBA in 0-1 range, baked from whatever material/texture
    the part actually has. TextureVisuals.to_color() returns either a
    single flat RGBA (a part with a plain material color, no image
    texture -- common for low-poly/stylized packs) or real per-vertex
    colors sampled at each vertex's UV against an actual texture image;
    both cases are normalized to a full per-vertex array here so the
    caller never needs to care which one it got.

    A collider stand-in mesh is given a fixed diagnostic tint instead --
    see COLLIDER_COLOR -- rather than whatever it would otherwise bake
    down to (there's no real material on a get_debug_mesh() result, so
    it'd just be the same flat FALLBACK_COLOR as any other untextured
    part, indistinguishable from real geometry).
    """
    if is_collider:
        return np.tile(COLLIDER_COLOR, (len(part.vertices), 1))
    try:
        colors = np.asarray(part.visual.to_color().vertex_colors, dtype=np.float32) / 255.0
    except Exception:  # noqa: BLE001 - any visual/material quirk falls back to plain grey
        colors = FALLBACK_COLOR
    if colors.ndim == 1:
        colors = np.tile(colors, (len(part.vertices), 1))
    colors = colors.copy()
    colors[:, :3] = _linear_to_srgb(colors[:, :3])
    return colors


def _part_texture_image(part: trimesh.Trimesh) -> Image.Image | None:
    """The actual source texture image behind a part's baked vertex
    colors, if it has one -- trimesh's two material classes disagree on
    the attribute name (PBRMaterial.baseColorTexture vs.
    SimpleMaterial.image), so both are tried. Used for the "Textures"
    gallery, which shows the real image rather than just the per-vertex
    colors sampled from it.
    """
    material = getattr(part.visual, "material", None)
    if material is None:
        return None
    image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
    return image if isinstance(image, Image.Image) else None


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


@dataclass
class PreviewPart:
    name: str
    vertices: np.ndarray
    faces: np.ndarray
    colors: np.ndarray
    texture_image: Image.Image | None
    is_collider: bool


# No single naming convention for a collision mesh exists across sources,
# so this recognizes several real ones by their part name: godot_export_
# script.gd's own injected debug meshes (node.name + "_collider"), plus
# Unreal/FBX's long-standing UCX_/UBX_/USP_/UCP_/MCDCX_ prefix convention
# (also carried into some glb pipelines) and generic "collision"/
# "collider"/"hitbox"/"phys"/"col" tokens used by many marketplace packs.
# Meaningless (and harmless) for a part that matches none of these -- it's
# just treated as ordinary mesh geometry, same as before this existed.
#
# Matched on whole underscore/space/dash-delimited tokens, not a raw
# substring -- a bare substring check would treat "Column" or "Colonial"
# as a collider hit just for containing "col". Checked against the token
# set, not a suffix, because trimesh appends its own uniquifying hash to
# scene-graph node names on load (e.g. "CollisionShape3D_collider_65fc2a"),
# confirmed against a real glb round trip before relying on this.
_COLLIDER_NAME_TOKENS = {"collider", "collision", "hitbox", "phys", "col"}
_COLLIDER_NAME_PREFIXES = ("ucx_", "ubx_", "usp_", "ucp_", "mcdcx_")


def _looks_like_collider_name(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith(_COLLIDER_NAME_PREFIXES):
        return True
    tokens = re.split(r"[^a-z0-9]+", lowered)
    return any(token in _COLLIDER_NAME_TOKENS for token in tokens)


def load_preview_parts(path: Path) -> list[PreviewPart]:
    """Returns one PreviewPart per mesh part in the scene, already in
    world space (Scene.dump bakes each part's node transform in) -- unlike
    collapsing everything into one merged mesh, this keeps each part's own
    material/texture distinct, which matters for a multi-material asset (a
    cauldron's body vs. its handle, say).

    Pure CPU work (trimesh parsing, color baking, the axis fix) -- no Qt
    or OpenGL calls -- so it's safe to run on a background thread. See
    MainWindow._open_model_preview: this is exactly the (surprisingly
    slow, first-time-only) part that used to run synchronously on the
    GUI thread with zero feedback, which is what made the first 3D
    preview in a session look like the app had frozen or crashed.
    """
    # A raw .gltf's own image URI is one thing trimesh's default resolver
    # refuses to follow: a shared texture atlas living one level above the
    # model's own folder (a real, common pack layout -- Models/x.gltf
    # referencing ../Textures/atlas.png -- confirmed against a real Synty
    # POLYGON pack) is blocked as "escapes resolver root" unless told
    # otherwise. Silent failure, not an exception: the material loads fine
    # with baseColorTexture simply absent, so this would otherwise look
    # like the asset just has no texture at all rather than a resolver
    # being overly cautious about a path that's completely legitimate for
    # a local file already sitting on disk (there's no untrusted-input
    # concern here the way there would be for a downloaded/remote asset).
    resolver = trimesh.resolvers.FilePathResolver(str(path), allow_anywhere=True)
    loaded = trimesh.load(str(path), resolver=resolver)
    parts = loaded.dump(concatenate=False) if isinstance(loaded, trimesh.Scene) else [loaded]
    result = []
    for part in parts:
        if not isinstance(part, trimesh.Trimesh) or len(part.vertices) == 0:
            continue
        # dump(concatenate=False) stamps the originating scene-graph node
        # name into metadata['node'] -- verified to survive a real glb
        # export/reload round trip, not just an in-memory Scene, before
        # relying on it here.
        name = str(part.metadata.get("node") or part.metadata.get("name") or "")
        is_collider = _looks_like_collider_name(name)
        vertices = _y_up_to_z_up(np.asarray(part.vertices, dtype=np.float32))
        faces = np.asarray(part.faces, dtype=np.int32)
        result.append(
            PreviewPart(
                name=name,
                vertices=vertices,
                faces=faces,
                colors=_part_vertex_colors(part, is_collider),
                texture_image=_part_texture_image(part) if not is_collider else None,
                is_collider=is_collider,
            )
        )
    return result


class Model3DPreviewDialog(QDialog):
    """Displays a model asset's already-loaded preview parts (see
    load_preview_parts -- generated from the cached .glb alongside the
    static thumbnail by blender_thumbnail_script.py) in a real orbit/pan/
    zoom viewer. Left-drag to rotate, wheel to zoom, Ctrl+left-drag to pan
    -- all built into pyqtgraph's GLViewWidget (confirmed by reading its
    mouseMoveEvent/wheelEvent directly), no custom mouse handling needed.
    There is no right-drag handling anywhere in the widget, and no Shift-
    modifier handling either -- an earlier version of this hint claimed
    both, neither of which the widget has ever actually done.

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
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        # Matches pyqtgraph's own GLViewWidget.mouseMoveEvent exactly (read
        # the source rather than assumed): plain left-drag orbits, wheel
        # zooms, and pan is Ctrl+left-drag -- there is no right-drag
        # handling anywhere in the widget, and no Shift-modifier handling
        # either. A previous version of this hint claimed "right-drag to
        # zoom" and "Shift+drag to pan", neither of which the underlying
        # widget has ever actually done.
        hint = QLabel("Drag to rotate · wheel to zoom · Ctrl+drag to pan")
        layout.addWidget(hint)

        body = QHBoxLayout()
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(BACKGROUND_COLOR)
        body.addWidget(self.view, stretch=1)

        # Populated by _build. Every part in the file gets its own row here
        # regardless of name -- a naming convention (Godot's own
        # "_collider" suffix, Unreal's UCX_ prefix, etc.) is only ever a
        # hint for the "likely collider" bulk-toggle default below, never
        # the thing that decides whether a part is even shown as an option:
        # an unrecognized name -- which is the common case for a random
        # downloaded pack -- still gets a checkbox, not silently no way to
        # isolate it.
        self._part_rows: list[tuple[QListWidgetItem, gl.GLMeshItem, bool]] = []
        self._textures: list[tuple[str, Image.Image]] = []

        self.parts_panel = QWidget()
        self.parts_panel.setFixedWidth(220)
        parts_layout = QVBoxLayout(self.parts_panel)
        parts_layout.setContentsMargins(0, 0, 0, 0)
        parts_layout.addWidget(QLabel("Parts:"))
        self.parts_list = QListWidget()
        self.parts_list.itemChanged.connect(self._on_part_item_changed)
        parts_layout.addWidget(self.parts_list, stretch=1)

        bulk_row = QHBoxLayout()
        show_all_button = QPushButton("Show All")
        show_all_button.clicked.connect(lambda: self._set_all_parts_checked(True))
        hide_colliders_button = QPushButton("Hide Likely Colliders")
        hide_colliders_button.clicked.connect(self._hide_likely_colliders)
        bulk_row.addWidget(show_all_button)
        parts_layout.addLayout(bulk_row)
        parts_layout.addWidget(hide_colliders_button)

        self.textures_button = QPushButton("Textures...")
        self.textures_button.clicked.connect(self._open_texture_gallery)
        parts_layout.addWidget(self.textures_button)

        body.addWidget(self.parts_panel)
        layout.addLayout(body, stretch=1)

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
        seen_texture_ids: set[int] = set()
        for index, part in enumerate(parts):
            mesh_data = gl.MeshData(vertexes=part.vertices, faces=part.faces, vertexColors=part.colors)
            mesh_item = gl.GLMeshItem(
                meshdata=mesh_data,
                smooth=False,
                drawFaces=True,
                drawEdges=part.is_collider,
                edgeColor=(1.0, 1.0, 1.0, 0.6),
                shader="catalogue_preview",
                glOptions="translucent" if part.is_collider else "opaque",
            )
            self.view.addItem(mesh_item)

            label = part.name or f"part {index + 1}"
            if part.is_collider:
                label += "  (likely collider)"
            list_item = QListWidgetItem(label)
            list_item.setFlags(list_item.flags() | Qt.ItemIsUserCheckable)
            list_item.setCheckState(Qt.Checked)
            self.parts_list.addItem(list_item)
            self._part_rows.append((list_item, mesh_item, part.is_collider))

            if part.texture_image is not None and id(part.texture_image) not in seen_texture_ids:
                seen_texture_ids.add(id(part.texture_image))
                self._textures.append((part.name or "texture", part.texture_image))
            all_mins.append(part.vertices.min(axis=0))
            all_maxs.append(part.vertices.max(axis=0))

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

        # Always shown, even for a single-part asset -- seeing "this file
        # contains exactly one part, named X" is itself useful confirmation
        # (that nothing else -- no separate collider, no hidden extra
        # geometry -- is bundled in), not just something to isolate among
        # several.
        self.textures_button.setVisible(bool(self._textures))

    def _on_part_item_changed(self, changed_item: QListWidgetItem) -> None:
        for list_item, mesh_item, _is_collider in self._part_rows:
            if list_item is changed_item:
                mesh_item.setVisible(changed_item.checkState() == Qt.Checked)
                return

    def _set_all_parts_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for list_item, _mesh_item, _is_collider in self._part_rows:
            list_item.setCheckState(state)

    def _hide_likely_colliders(self) -> None:
        for list_item, _mesh_item, is_collider in self._part_rows:
            if is_collider:
                list_item.setCheckState(Qt.Unchecked)

    def _open_texture_gallery(self) -> None:
        dialog = TextureGalleryDialog(self._textures, self)
        dialog.exec()

    def _show_error(self, message: str) -> None:
        self.view.setVisible(False)
        self.parts_panel.setVisible(False)
        error_label = QLabel(message)
        error_label.setWordWrap(True)
        self.layout().insertWidget(1, error_label)


def _to_vector3d(point) -> "object":
    from PySide6.QtGui import QVector3D

    return QVector3D(float(point[0]), float(point[1]), float(point[2]))


def _pil_image_to_qpixmap(image: Image.Image) -> QPixmap:
    """The texture lives only as an in-memory PIL Image decoded out of the
    glb (never a standalone file on disk), so this goes through an
    in-memory PNG buffer rather than a temp file -- QPixmap has no
    from-PIL constructor of its own.
    """
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    return pixmap


_GALLERY_THUMBNAIL_SIZE = 96
_LARGE_TEXTURE_SIZE = 512


class TextureGalleryDialog(QDialog):
    """Every distinct source texture actually used by the previewed
    model's parts, as real images -- not the per-vertex colors sampled
    from them, which is all the 3D view itself ever shows. Click a
    thumbnail for a bigger view (up to 512px), the same up-close-look
    pattern as double-clicking a grid thumbnail elsewhere in this app.
    """

    def __init__(self, textures: list[tuple[str, Image.Image]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Textures")
        self.resize(420, 420)

        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, stretch=1)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        columns = 3
        for index, (name, image) in enumerate(textures):
            cell = QVBoxLayout()
            thumb_button = QPushButton()
            pixmap = _pil_image_to_qpixmap(image).scaled(
                _GALLERY_THUMBNAIL_SIZE,
                _GALLERY_THUMBNAIL_SIZE,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            thumb_button.setIcon(QIcon(pixmap))
            thumb_button.setIconSize(pixmap.size())
            thumb_button.setFixedSize(_GALLERY_THUMBNAIL_SIZE + 16, _GALLERY_THUMBNAIL_SIZE + 16)
            thumb_button.clicked.connect(lambda _checked=False, img=image, n=name: self._open_large(n, img))
            cell.addWidget(thumb_button)
            name_label = QLabel(name)
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setWordWrap(True)
            cell.addWidget(name_label)
            grid.addLayout(cell, index // columns, index % columns)
        scroll.setWidget(grid_widget)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def _open_large(self, name: str, image: Image.Image) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(name)
        layout = QVBoxLayout(dialog)
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        pixmap = _pil_image_to_qpixmap(image).scaled(
            _LARGE_TEXTURE_SIZE, _LARGE_TEXTURE_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        image_label.setPixmap(pixmap)
        layout.addWidget(image_label)
        dialog.exec()
