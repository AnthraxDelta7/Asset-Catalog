"""The interactive orbit/zoom 3D preview -- separate module from
main_window.py so its heavier imports (pyqtgraph, PyOpenGL, trimesh) are
only ever paid for when a user actually opens a 3D preview, not on every
app launch. A textured part is rendered with real per-pixel UV-mapped
texture sampling (see TexturedGLMeshItem) rather than pyqtgraph's own
GLMeshItem, which has no texture support at all (confirmed by reading its
paint() -- it only ever binds position/normal/vertex-color attributes) --
without that, a fine repeating pattern (a caution-stripe texture, say)
collapsing onto a low-poly mesh's handful of vertices per face smeared
into a flat blob with no relation to the real, crisp texture. A part with
no texture (or one this couldn't extract a valid UV set for -- see
_part_uv) still falls back to the older per-vertex color baking (trimesh's
Visuals.to_color()), same as before this existed.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph.opengl as gl
import trimesh
from OpenGL import GL
from PIL import Image
from pyqtgraph.opengl.shaders import FragmentShader, ShaderProgram, VertexShader
from PySide6.QtCore import Qt
from PySide6.QtGui import QOpenGLContext, QPixmap
from PySide6.QtOpenGL import QOpenGLBuffer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStackedWidget,
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

# Same lighting model as catalogue_preview above, just sampling an actual
# bound texture per-pixel (u_texture, at v_texcoord) instead of an
# interpolated vertex color -- see TexturedGLMeshItem, which is the only
# thing that ever uses this (it uploads a_texcoord itself; plain
# GLMeshItem has no texcoord attribute to feed it). The sampled color is
# already sRGB-encoded (raw PNG bytes), same reasoning as the vertex-
# color path's "textured -> no _linear_to_srgb" branch, so it's used
# as-is rather than gamma-corrected a second time.
ShaderProgram(
    "catalogue_preview_textured",
    [
        VertexShader("""
            uniform mat4 u_mvp;
            uniform mat3 u_normal;
            attribute vec4 a_position;
            attribute vec3 a_normal;
            attribute vec2 a_texcoord;
            varying vec2 v_texcoord;
            varying vec3 v_normal;
            void main() {
                v_normal = normalize(u_normal * a_normal);
                v_texcoord = a_texcoord;
                gl_Position = u_mvp * a_position;
            }
        """),
        FragmentShader("""
            #ifdef GL_ES
            precision mediump float;
            #endif
            uniform sampler2D u_texture;
            varying vec2 v_texcoord;
            varying vec3 v_normal;
            void main() {
                float p = dot(v_normal, normalize(vec3(1.0, -1.0, 1.0)));
                p = p < 0. ? 0. : p * 0.45;
                vec4 texel = texture2D(u_texture, v_texcoord);
                gl_FragColor = vec4(texel.rgb * (0.55 + p), texel.a);
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

    Only correct for a *flat* baseColorFactor, though -- see
    _part_vertex_colors for why a texture-sampled color must never be
    passed through this without first being decoded back to linear (see
    _srgb_to_linear).
    """
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """Inverse of _linear_to_srgb -- decodes a texture-sampled sRGB color
    (raw PNG pixel bytes, already display-ready on their own) back to
    linear so it can be correctly multiplied by a linear baseColorFactor
    (glTF's actual compositing rule) before being re-encoded to sRGB for
    display. Only needed when material_metadata says a real baseColorFactor
    accompanies the texture -- see _part_vertex_colors.
    """
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, np.power((c + 0.055) / 1.055, 2.4))


def _part_texture_image(part: trimesh.Trimesh) -> Image.Image | None:
    """The actual source texture image behind a part's baked vertex
    colors, if it has one -- trimesh's two material classes disagree on
    the attribute name (PBRMaterial.baseColorTexture vs.
    SimpleMaterial.image), so both are tried. Used for the "Textures"
    gallery, which shows the real image rather than just the per-vertex
    colors sampled from it, and by _part_vertex_colors to decide whether
    this part's colors need the linear->sRGB fix-up at all.
    """
    material = getattr(part.visual, "material", None)
    if material is None:
        return None
    image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
    return image if isinstance(image, Image.Image) else None


def _part_uv(part: trimesh.Trimesh) -> np.ndarray | None:
    """Per-vertex UV coordinates aligned with part.vertices (one (u, v)
    pair per vertex, same indexing trimesh itself uses -- not yet
    expanded per-face), if this part's visual carries any. None for a
    part with no texture at all, or the rare TextureVisuals with a
    material but no uv array (glTF technically allows a textured
    material with no UV set, though nothing sane actually ships that).
    """
    uv = getattr(part.visual, "uv", None)
    if uv is None or len(uv) != len(part.vertices):
        return None
    return np.asarray(uv, dtype=np.float32)


def _part_vertex_colors(
    part: trimesh.Trimesh, is_collider: bool, material_metadata: dict | None = None
) -> np.ndarray:
    """Per-vertex RGBA in 0-1 range, baked from whatever material/texture
    the part actually has. TextureVisuals.to_color() returns either a
    single flat RGBA (a part with a plain material color, no image
    texture -- common for low-poly/stylized packs) or real per-vertex
    colors sampled at each vertex's UV against an actual texture image;
    both cases are normalized to a full per-vertex array here so the
    caller never needs to care which one it got.

    The linear->sRGB fix-up in _linear_to_srgb only belongs on the flat
    case. Confirmed directly against a real glb (trimesh's raw 0-255
    output, no display processing at all): an untextured part's flat
    color came out as a genuinely-linear PBRMaterial.baseColorFactor
    (e.g. (2, 8, 41) -- implausibly dark unless it's linear and needs the
    boost), while a textured part's per-vertex colors were plain sampled
    PNG pixel bytes (e.g. a caution-stripe texture's (255, 203, 0) --
    already exactly the display-ready yellow it should be). Applying the
    same sRGB boost to that second case double-encodes it, washing every
    textured part out toward white -- confirmed as the actual cause of a
    real bug report where a textured part rendered near-white in this
    preview while the same file's Blender-rendered thumbnail showed its
    real color.

    material_metadata (see model_preview.colors_path / blender_common.py's
    resolve_material_metadata) is Blender's own authoritative answer to
    "does this material actually have a texture on Base Color, and what's
    its baseColorFactor" -- read straight from the same node graph the
    thumbnail rendered from, instead of re-guessing it from the reloaded
    glb's material fields the way this function used to (via
    _part_texture_image). When present for this part's material:
    - textured: the sampled sRGB pixel is decoded to linear, multiplied by
      the (linear) baseColorFactor -- glTF's actual compositing rule,
      which a bare texture sample alone ignores -- then re-encoded to sRGB.
      Usually a no-op (factor defaults to white) but not always.
    - flat: same sRGB fix-up as before, just driven by a known fact
      instead of an inference.
    When absent entirely (a preview exported before this metadata existed)
    or this specific material isn't in it, falls back to the old
    guess-by-_part_texture_image behavior -- old cached previews keep
    working, just without the factor-compositing correctness.

    A collider stand-in mesh is given a fixed diagnostic tint instead --
    see COLLIDER_COLOR -- rather than whatever it would otherwise bake
    down to (there's no real material on a get_debug_mesh() result, so
    it'd just be the same flat FALLBACK_COLOR as any other untextured
    part, indistinguishable from real geometry).
    """
    if is_collider:
        return np.tile(COLLIDER_COLOR, (len(part.vertices), 1))

    material_name = getattr(getattr(part.visual, "material", None), "name", None)
    meta = material_metadata.get(material_name) if material_metadata else None

    try:
        colors = np.asarray(part.visual.to_color().vertex_colors, dtype=np.float32) / 255.0
    except Exception:  # noqa: BLE001 - any visual/material quirk falls back to plain grey
        colors = FALLBACK_COLOR
        meta = None
    if colors.ndim == 1:
        colors = np.tile(colors, (len(part.vertices), 1))
    colors = colors.copy()

    is_textured = meta["has_texture"] if meta is not None else _part_texture_image(part) is not None
    if is_textured:
        if meta is not None:
            factor = np.asarray(meta["base_color_factor"], dtype=np.float32)
            linear = _srgb_to_linear(colors[:, :3]) * factor
            colors[:, :3] = _linear_to_srgb(linear)
        # else: no metadata to composite with -- trust the raw sampled
        # texture color as-is (already sRGB), same as before this existed.
    else:
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


@dataclass
class PreviewPart:
    name: str
    vertices: np.ndarray
    faces: np.ndarray
    colors: np.ndarray
    texture_image: Image.Image | None
    uv: np.ndarray | None
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


def load_preview_parts(path: Path, colors_path: Path | None = None) -> list[PreviewPart]:
    """Returns one PreviewPart per mesh part in the scene, already in
    world space (Scene.dump bakes each part's node transform in) -- unlike
    collapsing everything into one merged mesh, this keeps each part's own
    material/texture distinct, which matters for a multi-material asset (a
    cauldron's body vs. its handle, say).

    colors_path, if given and it exists, is the per-material metadata
    Blender itself resolved at export time (see model_preview.colors_path
    / blender_common.py's resolve_material_metadata) -- read once here and
    handed to _part_vertex_colors for every part, rather than each part
    re-deriving the same facts by guesswork. Silently ignored if missing
    (a preview exported before this metadata existed) or unparsable --
    this is a display nicety, not something worth failing the whole
    preview load over.

    Pure CPU work (trimesh parsing, color baking, the axis fix) -- no Qt
    or OpenGL calls -- so it's safe to run on a background thread. See
    MainWindow._open_model_preview: this is exactly the (surprisingly
    slow, first-time-only) part that used to run synchronously on the
    GUI thread with zero feedback, which is what made the first 3D
    preview in a session look like the app had frozen or crashed.
    """
    material_metadata: dict | None = None
    if colors_path is not None and colors_path.exists():
        try:
            material_metadata = json.loads(colors_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt/unreadable cache falls back to guessing
            material_metadata = None

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
        texture_image = _part_texture_image(part) if not is_collider else None
        result.append(
            PreviewPart(
                name=name,
                vertices=vertices,
                faces=faces,
                colors=_part_vertex_colors(part, is_collider, material_metadata),
                texture_image=texture_image,
                uv=_part_uv(part) if texture_image is not None else None,
                is_collider=is_collider,
            )
        )
    return result


class TexturedGLMeshItem(gl.GLMeshItem):
    """A GLMeshItem that samples a real, bound texture per-pixel via UV
    coordinates instead of only ever interpolating the few colors baked at
    each vertex -- see the module docstring for why plain GLMeshItem can't
    do this on its own. Everything about vertex/normal/face handling is
    inherited unchanged from GLMeshItem; this only adds a parallel texcoord
    vertex buffer and a bound GL texture, and always draws with the
    "catalogue_preview_textured" shader regardless of what's passed in.

    texcoords must already be face-indexed (shape (num_faces, 3, 2)) in the
    same order GLMeshItem itself expands vertex positions into when built
    with smooth=False -- i.e. texcoords = part.uv[part.faces], mirroring
    MeshData's own internal `self._vertexes[self.faces()]`. Computed by the
    caller (not here) since GLMeshItem's own indexed-vs-not expansion logic
    is internal to MeshData/parseMeshData and awkward to hook into cleanly
    from a subclass; precomputing it up front, the same way vertex colors
    already are, is simpler than reimplementing that logic.

    The GL texture itself is uploaded lazily on first paint (create() needs
    an active GL context, which doesn't exist yet at __init__ time) and
    cached from then on -- one upload per dialog, not per frame. If that
    upload ever fails (an unexpected image mode, a GL error), this falls
    back permanently to GLMeshItem's own vertex-color rendering for the
    rest of this item's life rather than drawing nothing.
    """

    def __init__(self, texcoords: np.ndarray, texture_image: Image.Image, **kwds) -> None:
        self._texcoords = np.ascontiguousarray(texcoords, dtype=np.float32)
        self._texture_image = texture_image
        self._gl_texture_id: int | None = None
        self._texture_upload_failed = False
        self.m_vbo_texcoord = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        kwds["shader"] = "catalogue_preview_textured"
        super().__init__(**kwds)

    def _ensure_texture_uploaded(self) -> None:
        if self._gl_texture_id is not None or self._texture_upload_failed:
            return
        try:
            # glTexImage2D's row 0 is the texture's *bottom* row (v=0), but
            # PIL/glTF both put row 0 at the top (v=0) -- flipping here once,
            # at upload time, keeps v_texcoord matching glTF's own UV
            # convention untouched rather than flipping it per-vertex.
            image = self._texture_image.convert("RGBA").transpose(Image.FLIP_TOP_BOTTOM)
            width, height = image.size
            data = image.tobytes()

            texture_id = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture_id)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, width, height, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data
            )
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

            if not self.m_vbo_texcoord.isCreated():
                self.m_vbo_texcoord.create()
            self.m_vbo_texcoord.bind()
            self.m_vbo_texcoord.allocate(self._texcoords, self._texcoords.nbytes)
            self.m_vbo_texcoord.release()

            self._gl_texture_id = texture_id
        except Exception:  # noqa: BLE001 -- fall back to vertex colors, never a blank/crashed preview
            self._texture_upload_failed = True
            self.setShader("catalogue_preview")

    def paint(self) -> None:
        self._ensure_texture_uploaded()
        if self._texture_upload_failed:
            super().paint()
            return

        self.setupGLState()
        if (dirty_bits := self.parseMeshData()):
            self.upload_vertex_buffers(dirty_bits)

        if not (self.opts["drawFaces"] and self.vertexes is not None):
            return

        mat_mvp = np.array(self.mvpMatrix().data(), dtype=np.float32)
        mat_normal = np.array(self.modelViewMatrix().normalMatrix().data(), dtype=np.float32)

        # Same ES2-compat detection GLMeshItem.paint() itself does -- the
        # shader source is written to the ES2/GLSL-1.20-compatible common
        # subset already, so this only matters for whichever GLSL version
        # string the compiled program gets tagged with.
        es2_compat = QOpenGLContext.currentContext().hasExtension(b"GL_ARB_ES2_compatibility")
        shader = self.shader()
        program = shader.program(es2_compat=es2_compat)
        enabled_locs = []

        if (loc := GL.glGetAttribLocation(program, "a_position")) != -1:
            self.m_vbo_position.bind()
            GL.glVertexAttribPointer(loc, 3, GL.GL_FLOAT, False, 0, None)
            self.m_vbo_position.release()
            enabled_locs.append(loc)

        if (loc := GL.glGetAttribLocation(program, "a_normal")) != -1:
            if self.normals is None:
                GL.glVertexAttrib3f(loc, 0, 0, 1)
            else:
                self.m_vbo_normal.bind()
                GL.glVertexAttribPointer(loc, 3, GL.GL_FLOAT, False, 0, None)
                self.m_vbo_normal.release()
                enabled_locs.append(loc)

        if (loc := GL.glGetAttribLocation(program, "a_texcoord")) != -1:
            self.m_vbo_texcoord.bind()
            GL.glVertexAttribPointer(loc, 2, GL.GL_FLOAT, False, 0, None)
            self.m_vbo_texcoord.release()
            enabled_locs.append(loc)

        for loc in enabled_locs:
            GL.glEnableVertexAttribArray(loc)

        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._gl_texture_id)

        with shader:
            loc = GL.glGetUniformLocation(program, "u_mvp")
            GL.glUniformMatrix4fv(loc, 1, False, mat_mvp)
            if (uloc_normal := GL.glGetUniformLocation(program, "u_normal")) != -1:
                GL.glUniformMatrix3fv(uloc_normal, 1, False, mat_normal)
            if (uloc_texture := GL.glGetUniformLocation(program, "u_texture")) != -1:
                GL.glUniform1i(uloc_texture, 0)

            if (faces := self.faces) is None:
                GL.glDrawArrays(GL.GL_TRIANGLES, 0, int(np.prod(self.vertexes.shape[:-1])))
            else:
                self.m_ibo_faces.bind()
                GL.glDrawElements(GL.GL_TRIANGLES, faces.size, GL.GL_UNSIGNED_INT, None)
                self.m_ibo_faces.release()

        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        for loc in enabled_locs:
            GL.glDisableVertexAttribArray(loc)


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

    The "Layers:" list combines two different kinds of row: a mesh part's
    checkbox shows/hides it in the 3D view (independent of every other
    part), while a texture's checkbox instead selects it for inline
    preview -- checking one texture unchecks any other (there's only one
    preview area to show it in), swapping that area to the real image in
    place of the 3D view. Right-click the preview to copy or save it.
    """

    def __init__(self, filename: str, parts: list[PreviewPart], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"3D Preview -- {filename}")
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        # See the class docstring above for why these specific controls.
        hint = QLabel("Drag to rotate · wheel to zoom · Ctrl+drag to pan")
        layout.addWidget(hint)

        body = QHBoxLayout()
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(BACKGROUND_COLOR)

        # Checking a texture row below swaps this stack to show that
        # texture's real image in place of the 3D view, instead of the
        # separate "Textures..." popup dialog this used to open -- one
        # inline preview area, same size as the viewport, rather than a
        # second window to manage.
        self._texture_preview_label = QLabel()
        self._texture_preview_label.setAlignment(Qt.AlignCenter)
        self._texture_preview_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self._texture_preview_label.customContextMenuRequested.connect(self._show_texture_context_menu)
        self._current_texture: tuple[str, Image.Image] | None = None
        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(self.view)
        self._view_stack.addWidget(self._texture_preview_label)
        body.addWidget(self._view_stack, stretch=1)

        # Populated by _build. Every part in the file gets its own row here
        # regardless of name -- a naming convention (Godot's own
        # "_collider" suffix, Unreal's UCX_ prefix, etc.) is only ever a
        # hint for the "likely collider" bulk-toggle default below, never
        # the thing that decides whether a part is even shown as an option:
        # an unrecognized name -- which is the common case for a random
        # downloaded pack -- still gets a checkbox, not silently no way to
        # isolate it.
        self._part_rows: list[tuple[QListWidgetItem, gl.GLMeshItem, bool]] = []
        # Textures share the same list widget as parts (see _build) so both
        # sets of "layers" are toggled from one place -- a texture's toggle
        # means something different (select for inline preview, mutually
        # exclusive with every other texture) than a part's (show/hide in
        # the 3D view, independent of every other part), so they're tracked
        # in a separate list rather than reusing _part_rows' shape.
        self._texture_rows: list[tuple[QListWidgetItem, str, Image.Image]] = []

        self.parts_panel = QWidget()
        self.parts_panel.setFixedWidth(220)
        parts_layout = QVBoxLayout(self.parts_panel)
        parts_layout.setContentsMargins(0, 0, 0, 0)
        parts_layout.addWidget(QLabel("Layers:"))
        self.parts_list = QListWidget()
        self.parts_list.itemChanged.connect(self._on_list_item_changed)
        parts_layout.addWidget(self.parts_list, stretch=1)

        bulk_row = QHBoxLayout()
        show_all_button = QPushButton("Show All")
        show_all_button.clicked.connect(lambda: self._set_all_parts_checked(True))
        hide_colliders_button = QPushButton("Hide Likely Colliders")
        hide_colliders_button.clicked.connect(self._hide_likely_colliders)
        bulk_row.addWidget(show_all_button)
        parts_layout.addLayout(bulk_row)
        parts_layout.addWidget(hide_colliders_button)

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
        textures: list[tuple[str, Image.Image]] = []
        for index, part in enumerate(parts):
            mesh_data = gl.MeshData(vertexes=part.vertices, faces=part.faces, vertexColors=part.colors)
            mesh_kwargs = dict(
                meshdata=mesh_data,
                smooth=False,
                drawFaces=True,
                drawEdges=part.is_collider,
                edgeColor=(1.0, 1.0, 1.0, 0.6),
                glOptions="translucent" if part.is_collider else "opaque",
            )
            if part.texture_image is not None and part.uv is not None:
                # Face-indexed to match how GLMeshItem itself expands vertex
                # positions with smooth=False (MeshData.vertexes(indexed=
                # 'faces') internally does self._vertexes[self.faces()]) --
                # see TexturedGLMeshItem's docstring for why this is
                # precomputed here rather than inside that class.
                texcoords = part.uv[part.faces]
                mesh_item = TexturedGLMeshItem(
                    texcoords=texcoords, texture_image=part.texture_image, **mesh_kwargs
                )
            else:
                mesh_item = gl.GLMeshItem(shader="catalogue_preview", **mesh_kwargs)
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
                textures.append((part.name or "texture", part.texture_image))
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

        # Textures share the parts_list widget (see class docstring) rather
        # than the separate "Textures..." popup this used to open --
        # checking one shows it inline in place of the 3D view (see
        # _on_texture_toggled), a plain non-checkable row separates them
        # visually from the part rows above.
        if textures:
            separator = QListWidgetItem("Textures:")
            separator.setFlags(Qt.NoItemFlags)
            self.parts_list.addItem(separator)
            for name, image in textures:
                list_item = QListWidgetItem(f"  {name}")
                list_item.setFlags(list_item.flags() | Qt.ItemIsUserCheckable)
                list_item.setCheckState(Qt.Unchecked)
                self.parts_list.addItem(list_item)
                self._texture_rows.append((list_item, name, image))

    def _on_list_item_changed(self, changed_item: QListWidgetItem) -> None:
        for list_item, mesh_item, _is_collider in self._part_rows:
            if list_item is changed_item:
                mesh_item.setVisible(changed_item.checkState() == Qt.Checked)
                return
        for list_item, _name, _image in self._texture_rows:
            if list_item is changed_item:
                self._on_texture_toggled(changed_item)
                return

    def _on_texture_toggled(self, changed_item: QListWidgetItem) -> None:
        if changed_item.checkState() != Qt.Checked:
            self._current_texture = None
            self._view_stack.setCurrentWidget(self.view)
            return

        # Selecting one texture for inline preview -- mutually exclusive
        # with every other texture row, unlike parts (which can all be
        # shown/hidden independently). Signals blocked while un-checking
        # the rest so that doesn't itself recurse back into this method.
        self.parts_list.blockSignals(True)
        try:
            for list_item, _name, _image in self._texture_rows:
                if list_item is not changed_item:
                    list_item.setCheckState(Qt.Unchecked)
        finally:
            self.parts_list.blockSignals(False)

        name, image = next((n, img) for li, n, img in self._texture_rows if li is changed_item)
        self._current_texture = (name, image)
        self._update_texture_preview_pixmap()
        self._view_stack.setCurrentWidget(self._texture_preview_label)

    def _update_texture_preview_pixmap(self) -> None:
        if self._current_texture is None:
            return
        _name, image = self._current_texture
        pixmap = _pil_image_to_qpixmap(image)
        self._texture_preview_label.setPixmap(
            pixmap.scaled(self._texture_preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # QLabel doesn't auto-rescale a pixmap set via setPixmap() on its
        # own resize (setScaledContents(True) would, but stretches without
        # preserving aspect ratio) -- re-derive it from the original PIL
        # image at the new size instead of scaling the already-scaled
        # QPixmap repeatedly, which would compound quality loss.
        self._update_texture_preview_pixmap()

    def _show_texture_context_menu(self, pos) -> None:
        # The actual copy/save logic lives in _copy_current_texture /
        # _save_current_texture, not here -- QMenu.exec() is a Qt/C++-bound
        # method that mock.patch can't reliably intercept from a test (it
        # silently falls through to a real, blocking popup instead), so
        # keeping this method to just "show the menu, dispatch by action"
        # lets a test call those two directly instead of fighting exec().
        if self._current_texture is None:
            return

        menu = QMenu(self)
        copy_action = menu.addAction("Copy Image")
        save_action = menu.addAction("Save Image As...")
        chosen = menu.exec(self._texture_preview_label.mapToGlobal(pos))

        if chosen is copy_action:
            self._copy_current_texture()
        elif chosen is save_action:
            self._save_current_texture()

    def _copy_current_texture(self) -> None:
        if self._current_texture is None:
            return
        _name, image = self._current_texture
        QApplication.clipboard().setPixmap(_pil_image_to_qpixmap(image))

    def _save_current_texture(self) -> None:
        if self._current_texture is None:
            return
        name, image = self._current_texture
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", name) or "texture"
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "Save Texture", f"{safe_name}.png", "PNG Image (*.png)"
        )
        if path:
            image.save(path)

    def _set_all_parts_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for list_item, _mesh_item, _is_collider in self._part_rows:
            list_item.setCheckState(state)

    def _hide_likely_colliders(self) -> None:
        for list_item, _mesh_item, is_collider in self._part_rows:
            if is_collider:
                list_item.setCheckState(Qt.Unchecked)

    def _show_error(self, message: str) -> None:
        self._view_stack.setVisible(False)
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


