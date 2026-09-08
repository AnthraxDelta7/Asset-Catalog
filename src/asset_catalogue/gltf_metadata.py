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


@dataclass
class GltfMetadata:
    joint_count: int = 0
    animation_names: list[str] = field(default_factory=list)
    mesh_count: int = 0

    @property
    def is_rigged(self) -> bool:
        return self.joint_count > 0

    @property
    def is_animated(self) -> bool:
        return bool(self.animation_names)

    @property
    def needs_native_import(self) -> bool:
        """Whether flattening this to a plain mesh would destroy
        something. A rig or an animation can't survive being reduced to
        static geometry, so these have to reach an engine as the glTF
        they are and be imported natively.
        """
        return self.is_rigged or self.is_animated


def _read_gltf_json(path: Path) -> dict | None:
    """The glTF document as a dict, from either container form: `.glb`'s
    binary JSON chunk or a plain-text `.gltf`. None if it isn't readable
    as one -- a truncated download, or something with a glTF extension
    that isn't glTF at all. Never raises; callers treat "unknown" the
    same as "nothing special", which is the safe direction.
    """
    try:
        if path.suffix.lower() == ".gltf":
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
    return GltfMetadata(
        joint_count=joints,
        animation_names=names,
        mesh_count=len(document.get("meshes") or []),
    )


def describe(metadata: GltfMetadata | None) -> str:
    """One short human-readable line, or "" when there's nothing worth
    saying -- a plain static prop shouldn't get a label announcing that
    it has no rig.
    """
    if metadata is None or not metadata.needs_native_import:
        return ""
    parts = []
    if metadata.is_rigged:
        parts.append(f"rigged ({metadata.joint_count} joints)")
    if metadata.is_animated:
        count = len(metadata.animation_names)
        parts.append(f"{count} animation{'s' if count != 1 else ''}")
    return ", ".join(parts)
