from __future__ import annotations

import json
import struct
from pathlib import Path

from asset_catalogue import gltf_metadata


def _write_glb(path: Path, document: dict) -> None:
    payload = json.dumps(document).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)  # chunks are 4-byte aligned
    chunk = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(chunk)) + chunk)


def test_reads_joints_and_animation_names_from_a_glb(tmp_path: Path) -> None:
    path = tmp_path / "character.glb"
    _write_glb(
        path,
        {
            "meshes": [{}, {}, {}],
            "skins": [{"joints": list(range(79))}],
            "animations": [{"name": "@idle"}, {"name": "@walk"}, {}],
        },
    )

    meta = gltf_metadata.read(path)

    assert meta.joint_count == 79
    assert meta.mesh_count == 3
    # An unnamed clip still counts; it just gets a positional label.
    assert meta.animation_names == ["@idle", "@walk", "animation 2"]
    assert meta.is_rigged and meta.is_animated and meta.needs_native_import
    assert gltf_metadata.describe(meta) == "rigged (79 joints), 3 animations"


def test_a_static_model_needs_no_native_import(tmp_path: Path) -> None:
    path = tmp_path / "prop.glb"
    _write_glb(path, {"meshes": [{}]})

    meta = gltf_metadata.read(path)

    assert meta.needs_native_import is False
    # Nothing worth saying about a plain prop -- no label for it.
    assert gltf_metadata.describe(meta) == ""


def test_unreadable_or_non_gltf_input_is_reported_as_unknown(tmp_path: Path) -> None:
    """Deliberately not an exception: "we couldn't tell" is treated the
    same as "nothing special", which keeps a corrupt or unrelated file
    from breaking an export or a panel refresh.
    """
    not_gltf = tmp_path / "notes.txt"
    not_gltf.write_text("plain text", encoding="utf-8")
    truncated = tmp_path / "broken.glb"
    truncated.write_bytes(b"glTF")

    assert gltf_metadata.read(not_gltf) is None
    assert gltf_metadata.read(truncated) is None
    assert gltf_metadata.describe(None) == ""
