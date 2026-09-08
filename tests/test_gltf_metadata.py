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
    assert gltf_metadata.describe(meta) == "a 79-joint skeleton, 3 animations"


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


def test_morph_targets_prevent_flattening(tmp_path: Path) -> None:
    """SurfaceTool, which does the flattening, has no blend-shape support
    at all -- a face rig's morph targets would vanish without a word.
    """
    path = tmp_path / "face.glb"
    _write_glb(
        path,
        {"meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "targets": [{}, {}, {}]}]}]},
    )

    meta = gltf_metadata.read(path)

    assert meta.morph_target_count == 3
    assert meta.needs_native_import
    assert "3 morph targets (blend shapes)" in meta.preservation_reasons


def test_vertex_colors_and_second_uv_set_prevent_flattening(tmp_path: Path) -> None:
    path = tmp_path / "painted.glb"
    _write_glb(
        path,
        {
            "meshes": [
                {
                    "primitives": [
                        {
                            "attributes": {
                                "POSITION": 0,
                                "COLOR_0": 1,
                                "TEXCOORD_0": 2,
                                "TEXCOORD_1": 3,
                            }
                        }
                    ]
                }
            ]
        },
    )

    meta = gltf_metadata.read(path)

    assert meta.has_vertex_colors
    assert meta.extra_uv_sets
    assert meta.needs_native_import


def test_the_ordinary_uv_set_alone_still_flattens(tmp_path: Path) -> None:
    """TEXCOORD_0 is what every textured mesh has and flattening keeps --
    it must not be mistaken for the second set that would be lost.
    """
    path = tmp_path / "prop.glb"
    _write_glb(
        path,
        {"meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}}]}]},
    )

    meta = gltf_metadata.read(path)

    assert meta.extra_uv_sets is False
    assert meta.needs_native_import is False


def test_unknown_extensions_are_treated_as_a_reason_to_preserve(tmp_path: Path) -> None:
    """An allowlist, deliberately: an extension nobody here has heard of
    should make the app cautious, not confident. Known material-only ones
    still flatten, because Godot resolves them into the material the
    flattener reuses wholesale.
    """
    exotic = tmp_path / "exotic.glb"
    _write_glb(exotic, {"meshes": [{}], "extensionsUsed": ["EXT_something_new"]})
    known = tmp_path / "known.glb"
    _write_glb(
        known,
        {"meshes": [{}], "extensionsUsed": ["KHR_texture_transform", "KHR_materials_ior"]},
    )

    assert gltf_metadata.read(exotic).needs_native_import is True
    assert gltf_metadata.read(exotic).unsupported_extensions == ["EXT_something_new"]
    assert gltf_metadata.read(known).needs_native_import is False


def test_cameras_prevent_flattening(tmp_path: Path) -> None:
    path = tmp_path / "scene.glb"
    _write_glb(path, {"meshes": [{}], "cameras": [{"type": "perspective"}]})

    assert gltf_metadata.read(path).needs_native_import is True


def test_an_oversized_text_gltf_is_reported_as_unknown(tmp_path: Path, monkeypatch) -> None:
    """A .gltf with embedded base64 buffers has no header to read in
    isolation, and this runs on every grid selection -- so past a size
    cap it declines to parse rather than freezing the UI. Unknown means
    preserved, never rewritten.
    """
    path = tmp_path / "huge.gltf"
    path.write_text(json.dumps({"meshes": [{}]}), encoding="utf-8")
    assert gltf_metadata.read(path) is not None

    monkeypatch.setattr(gltf_metadata, "MAX_GLTF_TEXT_BYTES", 4)
    assert gltf_metadata.read(path) is None
