"""Reads the rig/animation facts out of a glTF file without importing it.

A `.glb` is a tiny binary header followed by its whole scene description
as one JSON chunk, so `skins` and `animations` can be read straight off
it -- no Blender, no Godot, no trimesh, and no cost proportional to the
file's size (a 56MB character model is answered by reading its first few
hundred KB). That matters because this runs on selection in the UI and
once per model during an export.

Exists because glTF supports far more than static geometry -- a real
Sketchfab-style character ships as a single `.glb` carrying skinned
meshes, a full joint hierarchy and named animation clips -- and the rest
of this app used to treat every model as static geometry, silently
flattening exactly that away.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

_GLB_MAGIC = 0x46546C67  # "glTF"
_CHUNK_JSON = 0x4E4F534A  # "JSON"

# Only applies to text .gltf; a .glb of any size is fine, since only its
# JSON chunk is ever read.
MAX_GLTF_TEXT_BYTES = 64 * 1024 * 1024


# glTF extensions that survive being flattened, because they are
# resolved during Godot's own import into the material or mesh that the
# flattener then reuses wholesale -- material parameters and mesh
# compression. Anything NOT in here is treated as a reason to preserve
# the file untouched. Deliberately an allowlist: a new extension nobody
# here has heard of should make the app cautious, not confident.
_FLATTENING_SAFE_EXTENSIONS = frozenset(
    {
        "KHR_draco_mesh_compression",
        "KHR_materials_clearcoat",
        "KHR_materials_emissive_strength",
        "KHR_materials_ior",
        "KHR_materials_sheen",
        "KHR_materials_specular",
        "KHR_materials_transmission",
        "KHR_materials_unlit",
        "KHR_materials_volume",
        "KHR_mesh_quantization",
        "KHR_texture_transform",
    }
)


@dataclass
class GltfMetadata:
    joint_count: int = 0
    animation_names: list[str] = field(default_factory=list)
    mesh_count: int = 0
    morph_target_count: int = 0
    has_vertex_colors: bool = False
    extra_uv_sets: bool = False
    camera_count: int = 0
    unsupported_extensions: list[str] = field(default_factory=list)

    @property
    def is_rigged(self) -> bool:
        return self.joint_count > 0

    @property
    def is_animated(self) -> bool:
        return bool(self.animation_names)

    @property
    def preservation_reasons(self) -> list[str]:
        """Everything in this file that flattening it to a plain Mesh
        would destroy, in plain language.

        Deliberately an allowlist rather than a blacklist: the app
        flattens only when it can see the file holds nothing but static
        geometry, and preserves it otherwise. The two outcomes are not
        symmetric -- preserving a file that could safely have been
        flattened costs a slightly less convenient import, while
        flattening one that shouldn't have been destroys data the user
        cannot get back from what lands in their project.
        """
        reasons = []
        if self.is_rigged:
            reasons.append(f"a {self.joint_count}-joint skeleton")
        if self.is_animated:
            count = len(self.animation_names)
            reasons.append(f"{count} animation{'s' if count != 1 else ''}")
        if self.morph_target_count:
            # SurfaceTool, which does the flattening, has no blend-shape
            # support whatsoever -- these would vanish silently.
            reasons.append(f"{self.morph_target_count} morph targets (blend shapes)")
        if self.has_vertex_colors:
            reasons.append("vertex colors")
        if self.extra_uv_sets:
            reasons.append("a second UV set (lightmap/detail UVs)")
        if self.camera_count:
            reasons.append(f"{self.camera_count} camera{'s' if self.camera_count != 1 else ''}")
        if self.unsupported_extensions:
            reasons.append("glTF extensions: " + ", ".join(sorted(self.unsupported_extensions)))
        return reasons

    @property
    def needs_native_import(self) -> bool:
        """Whether flattening this to a plain mesh would destroy
        something, in which case it has to reach an engine as the glTF it
        is and be imported natively.
        """
        return bool(self.preservation_reasons)


def _read_gltf_json(path: Path) -> dict | None:
    """The glTF document as a dict, from either container form: `.glb`'s
    binary JSON chunk or a plain-text `.gltf`. None if it isn't readable
    as one -- a truncated download, or something with a glTF extension
    that isn't glTF at all. Never raises; callers treat "unknown" the
    same as "nothing special", which is the safe direction.
    """
    try:
        if path.suffix.lower() == ".gltf":
            # A .gltf is JSON all the way down, and one with its buffers
            # embedded as base64 data URIs can run to hundreds of MB --
            # unlike a .glb, there's no header to read in isolation. This
            # runs on every grid selection, so an unbounded read here
            # would visibly freeze the UI. Past the cap it reports
            # "unknown", which callers treat as a reason to leave the
            # file alone rather than rewrite it.
            if path.stat().st_size > MAX_GLTF_TEXT_BYTES:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        with path.open("rb") as f:
            magic, _version, _total = struct.unpack("<III", f.read(12))
            if magic != _GLB_MAGIC:
                return None
            chunk_length, chunk_type = struct.unpack("<II", f.read(8))
            if chunk_type != _CHUNK_JSON:
                return None
            return json.loads(f.read(chunk_length).decode("utf-8"))
    except Exception:
        return None


def read(path: Path) -> GltfMetadata | None:
    """None for anything that isn't a readable glTF -- including every
    other model format, which this deliberately says nothing about
    rather than guessing.
    """
    document = _read_gltf_json(Path(path))
    if document is None:
        return None
    joints = 0
    for skin in document.get("skins") or []:
        joints += len(skin.get("joints") or [])
    names = []
    for index, animation in enumerate(document.get("animations") or []):
        names.append(animation.get("name") or f"animation {index}")

    morph_targets = 0
    vertex_colors = False
    extra_uvs = False
    for mesh in document.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            morph_targets += len(primitive.get("targets") or [])
            for attribute in primitive.get("attributes") or {}:
                if attribute.startswith("COLOR_"):
                    vertex_colors = True
                # TEXCOORD_0 is the ordinary UV set every textured mesh
                # has and which flattening keeps; a second one is what
                # would be lost.
                elif attribute.startswith("TEXCOORD_") and attribute != "TEXCOORD_0":
                    extra_uvs = True

    used = set(document.get("extensionsUsed") or [])
    return GltfMetadata(
        joint_count=joints,
        animation_names=names,
        mesh_count=len(document.get("meshes") or []),
        morph_target_count=morph_targets,
        has_vertex_colors=vertex_colors,
        extra_uv_sets=extra_uvs,
        camera_count=len(document.get("cameras") or []),
        unsupported_extensions=sorted(used - _FLATTENING_SAFE_EXTENSIONS),
    )


def describe(metadata: GltfMetadata | None) -> str:
    """One short human-readable line, or "" when there's nothing worth
    saying -- a plain static prop shouldn't get a label announcing what
    it doesn't contain.
    """
    if metadata is None:
        return ""
    return ", ".join(metadata.preservation_reasons)
