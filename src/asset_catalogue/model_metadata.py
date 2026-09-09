"""What a model contains -- rig, animation clips, mesh count -- for any
format, not just glTF.

glTF answers this for free: a `.glb` carries its whole scene description
as a JSON header, so `skins` and `animations` can be read without
touching the payload (see gltf_metadata). No other format does. An `.fbx`
is a binary blob whose only reliable reader is Blender, which costs a
process launch measured in seconds -- far too slow for something the
detail panel asks on every grid selection.

So it's captured, not queried: the thumbnail render already imports every
model into Blender, and now reports its rig on the way past. The result
is cached under the same content-hash identity the thumbnails and
previews use, which means the answer is already on disk by the time
anyone selects the asset, and identical content is never inspected twice.

The consequence worth knowing: a non-glTF model shows nothing until it
has been rendered once. Regenerating its thumbnail fills this in.
"""

from __future__ import annotations

import json
from pathlib import Path

from asset_catalogue import gltf_metadata
from asset_catalogue.gltf_metadata import GltfMetadata


def cache_path(preview_dir: Path, content_hash: str) -> Path:
    return preview_dir / "rigs" / f"{content_hash}.json"


def write_cache(preview_dir: Path, content_hash: str, joints: int, animations: list[str]) -> None:
    path = cache_path(preview_dir, content_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"joint_count": joints, "animation_names": animations}), encoding="utf-8"
    )


def _read_cache(preview_dir: Path, content_hash: str) -> GltfMetadata | None:
    path = cache_path(preview_dir, content_hash)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return GltfMetadata(
        joint_count=int(data.get("joint_count") or 0),
        animation_names=list(data.get("animation_names") or []),
    )


def read(source: Path, preview_dir: Path | None = None, content_hash: str | None = None):
    """What this model contains, or None if it can't be determined.

    A glTF container is read directly, since that's both exact and free.
    Anything else falls back to what the last thumbnail render recorded,
    which is why preview_dir/content_hash are needed to find it.

    None means "unknown", never "nothing" -- callers treat the two the
    same everywhere except where being certain matters (Export to Godot
    preserves an unreadable file rather than rewriting it).
    """
    direct = gltf_metadata.read(source)
    if direct is not None:
        return direct
    if preview_dir is None or not content_hash:
        return None
    return _read_cache(preview_dir, content_hash)


def describe(metadata) -> str:
    return gltf_metadata.describe(metadata)
