from __future__ import annotations

import json
import struct
from pathlib import Path

from asset_catalogue import model_metadata


def _write_glb(path: Path, document: dict) -> None:
    payload = json.dumps(document).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    chunk = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(chunk)) + chunk)


def test_a_gltf_container_is_read_directly_without_any_cache(tmp_path: Path) -> None:
    """Free and exact for glTF, so it never depends on having been
    rendered first -- the cache exists only for formats that can't be
    read without Blender.
    """
    path = tmp_path / "hero.glb"
    _write_glb(path, {"skins": [{"joints": [0, 1, 2]}], "animations": [{"name": "@idle"}]})

    meta = model_metadata.read(path, preview_dir=tmp_path / "nonexistent", content_hash="abc")

    assert meta.joint_count == 3
    assert meta.animation_names == ["@idle"]


def test_a_non_gltf_model_falls_back_to_what_the_render_recorded(tmp_path: Path) -> None:
    """An .fbx is a binary blob whose only reliable reader is Blender, so
    its rig is captured during the thumbnail render and read back here.
    """
    fbx = tmp_path / "hero.fbx"
    fbx.write_bytes(b"not really an fbx")
    preview_dir = tmp_path / "previews"

    assert model_metadata.read(fbx, preview_dir, "hash123") is None

    # Blender action names routinely contain "|", so the cache has to
    # round-trip them intact.
    model_metadata.write_cache(preview_dir, "hash123", 79, ["Rig|Rig|Walk", "Rig|Rig|Run"])
    meta = model_metadata.read(fbx, preview_dir, "hash123")

    assert meta.joint_count == 79
    assert meta.animation_names == ["Rig|Rig|Walk", "Rig|Rig|Run"]
    assert meta.needs_native_import is True


def test_unknown_stays_unknown_without_a_cache_location(tmp_path: Path) -> None:
    """Callers that have no preview dir or hash to look one up with get
    None rather than a confident "contains nothing".
    """
    fbx = tmp_path / "hero.fbx"
    fbx.write_bytes(b"not really an fbx")

    assert model_metadata.read(fbx) is None
    assert model_metadata.read(fbx, preview_dir=tmp_path, content_hash="") is None


def test_a_corrupt_cache_entry_is_treated_as_unknown(tmp_path: Path) -> None:
    preview_dir = tmp_path / "previews"
    path = model_metadata.cache_path(preview_dir, "hash123")
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    fbx = tmp_path / "hero.fbx"
    fbx.write_bytes(b"nope")
    assert model_metadata.read(fbx, preview_dir, "hash123") is None
