from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize(
    "name, expected",
    [
        ("CollisionShape3D_collider_c79470", True),  # godot_export_script.gd's own naming, plus trimesh's hash suffix
        ("UCX_Prop01", True),  # Unreal/FBX convention
        ("ucx_prop01", True),
        ("Collision_Mesh", True),
        ("hitbox_01", True),
        ("phys_mesh", True),
        ("Col", True),
        ("Visual", False),
        ("Column_01", False),  # must not false-positive on a "col" substring
        ("Colonial_House", False),
        ("MainMesh", False),
        ("Body", False),
    ],
)
def test_looks_like_collider_name(name: str, expected: bool) -> None:
    from asset_catalogue.ui.model_preview_dialog import _looks_like_collider_name

    assert _looks_like_collider_name(name) is expected


def _make_part(name: str, is_collider: bool):
    from asset_catalogue.ui.model_preview_dialog import PreviewPart

    return PreviewPart(
        name=name,
        vertices=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
        faces=np.array([[0, 1, 2]], dtype=np.int32),
        colors=np.tile(np.array([0.5, 0.5, 0.5, 1.0], dtype=np.float32), (3, 1)),
        texture_image=None,
        is_collider=is_collider,
    )


def test_every_part_gets_its_own_toggle_regardless_of_name(qapp) -> None:
    """The point of the per-part checklist: an unrecognized name (the
    common case for a random downloaded pack) still gets listed and can
    still be individually hidden -- detection is only ever a convenience
    default for the bulk "Hide Likely Colliders" button, never a filter on
    what's shown at all.
    """
    from PySide6.QtCore import Qt

    from asset_catalogue.ui.model_preview_dialog import Model3DPreviewDialog

    parts = [_make_part("Mesh2_LOD_alt", is_collider=False), _make_part("Body01", is_collider=False)]
    dialog = Model3DPreviewDialog("weird.glb", parts)

    assert dialog.parts_list.count() == 2
    names = {dialog.parts_list.item(i).text() for i in range(2)}
    assert names == {"Mesh2_LOD_alt", "Body01"}

    dialog._part_rows[0][0].setCheckState(Qt.Unchecked)
    assert dialog._part_rows[0][1].visible() is False
    assert dialog._part_rows[1][1].visible() is True


def test_hide_likely_colliders_only_affects_flagged_parts(qapp) -> None:
    from PySide6.QtCore import Qt

    from asset_catalogue.ui.model_preview_dialog import Model3DPreviewDialog

    parts = [_make_part("Visual", is_collider=False), _make_part("UCX_Visual", is_collider=True)]
    dialog = Model3DPreviewDialog("asset.glb", parts)

    mesh_row = next(r for r in dialog._part_rows if not r[2])
    collider_row = next(r for r in dialog._part_rows if r[2])

    dialog._hide_likely_colliders()
    assert collider_row[1].visible() is False
    assert mesh_row[1].visible() is True

    dialog._set_all_parts_checked(True)
    assert collider_row[1].visible() is True
    assert collider_row[0].checkState() == Qt.Checked


def test_single_part_asset_still_shows_the_parts_panel(qapp) -> None:
    """Seeing "this file contains exactly one part, named X" is itself
    useful confirmation that nothing else is bundled in -- the panel isn't
    gated on there being more than one part to compare.
    """
    from asset_catalogue.ui.model_preview_dialog import Model3DPreviewDialog

    dialog = Model3DPreviewDialog("plain.glb", [_make_part("MainMesh", is_collider=False)])
    assert dialog.parts_panel.isHidden() is False
    assert dialog.parts_list.count() == 1
    assert dialog.parts_list.item(0).text() == "MainMesh"


def test_load_preview_parts_resolves_a_texture_one_folder_above_the_model(tmp_path: Path) -> None:
    """A raw multi-file .gltf referencing a shared texture atlas one folder
    above its own (Models/x.gltf -> ../Textures/atlas.png) is a real,
    common pack layout -- confirmed against an actual Synty POLYGON asset
    pack. trimesh's default resolver refuses to follow a path that escapes
    the model's own directory (a reasonable default for untrusted/remote
    content, but wrong here: this is a local file already on disk with
    nothing to sandbox against) and fails *silently* -- the material loads
    fine with baseColorTexture simply absent, so an asset like this looked
    completely untextured despite having a perfectly real, present texture.
    """
    import trimesh

    from asset_catalogue.ui.model_preview_dialog import load_preview_parts

    model_dir = tmp_path / "Models"
    model_dir.mkdir()
    texture_dir = tmp_path / "Textures"
    texture_dir.mkdir()

    from PIL import Image

    Image.new("RGB", (4, 4), (200, 50, 200)).save(texture_dir / "atlas.png")

    gltf_path = model_dir / "box.gltf"
    trimesh.creation.box(extents=(1, 1, 1)).export(gltf_path)

    with open(gltf_path) as f:
        doc = json.load(f)
    doc["images"] = [{"uri": "../Textures/atlas.png"}]
    doc["textures"] = [{"source": 0}]
    doc["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
    doc["meshes"][0]["primitives"][0]["material"] = 0
    with open(gltf_path, "w") as f:
        json.dump(doc, f)

    parts = load_preview_parts(gltf_path)
    assert len(parts) == 1
    assert parts[0].texture_image is not None
    assert parts[0].texture_image.size == (4, 4)


def test_y_up_to_z_up_rotates_plus_90_about_x() -> None:
    from asset_catalogue.ui.model_preview_dialog import _y_up_to_z_up

    vertices = np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    rotated = _y_up_to_z_up(vertices)

    # Documented mapping: (x, y, z) -> (x, -z, y).
    np.testing.assert_allclose(rotated, [[1.0, -3.0, 2.0], [0.0, -1.0, 0.0]])


def test_linear_to_srgb_matches_known_reference_points() -> None:
    from asset_catalogue.ui.model_preview_dialog import _linear_to_srgb

    result = _linear_to_srgb(np.array([0.0, 0.5, 1.0], dtype=np.float32))
    # 0.5 linear -> ~0.735 sRGB is a standard, widely-cited reference value
    # for this exact conversion -- not just re-deriving the function's own
    # formula as the test.
    np.testing.assert_allclose(result, [0.0, 0.735, 1.0], atol=0.005)


def test_linear_to_srgb_clips_out_of_range_input() -> None:
    from asset_catalogue.ui.model_preview_dialog import _linear_to_srgb

    result = _linear_to_srgb(np.array([-0.5, 1.5], dtype=np.float32))
    np.testing.assert_allclose(result, [0.0, 1.0])


def test_part_vertex_colors_collider_gets_fixed_diagnostic_tint() -> None:
    import trimesh

    from asset_catalogue.ui.model_preview_dialog import COLLIDER_COLOR, _part_vertex_colors

    box = trimesh.creation.box(extents=(1, 1, 1))
    colors = _part_vertex_colors(box, is_collider=True)

    assert colors.shape == (len(box.vertices), 4)
    np.testing.assert_allclose(colors, np.tile(COLLIDER_COLOR, (len(box.vertices), 1)))


def test_part_vertex_colors_flat_material_broadcasts_and_srgb_encodes() -> None:
    import trimesh

    box = trimesh.creation.box(extents=(1, 1, 1))
    box.visual = trimesh.visual.ColorVisuals(box, face_colors=[200, 50, 50, 255])

    from asset_catalogue.ui.model_preview_dialog import _part_vertex_colors

    colors = _part_vertex_colors(box, is_collider=False)
    assert colors.shape == (len(box.vertices), 4)
    # Every vertex should end up identically colored (a single flat
    # material, no per-vertex variation), and RGB channels sRGB-encoded --
    # brighter than the raw 200/255 input, alpha left alone at 1.0.
    assert np.allclose(colors[:, 3], 1.0)
    assert np.all(colors[0] == colors[-1])
    assert colors[0, 0] > 200 / 255


def test_part_vertex_colors_falls_back_to_plain_grey_on_any_error() -> None:
    from asset_catalogue.ui.model_preview_dialog import (
        FALLBACK_COLOR,
        _linear_to_srgb,
        _part_vertex_colors,
    )

    class _BrokenVisual:
        def to_color(self):
            raise RuntimeError("simulated visual/material quirk")

    class _BrokenPart:
        visual = _BrokenVisual()
        vertices = np.zeros((3, 3))

    colors = _part_vertex_colors(_BrokenPart(), is_collider=False)
    assert colors.shape == (3, 4)
    # The fallback color still goes through the same sRGB encode as any
    # other color (applied unconditionally after the try/except, not
    # skipped for the fallback case) -- expected value is FALLBACK_COLOR
    # post-encode, not the raw constant itself.
    expected = FALLBACK_COLOR.copy()
    expected[:3] = _linear_to_srgb(expected[:3])
    np.testing.assert_allclose(colors, np.tile(expected, (3, 1)))


def test_part_texture_image_none_when_no_material() -> None:
    from asset_catalogue.ui.model_preview_dialog import _part_texture_image

    class _NoMaterialVisual:
        material = None

    class _Part:
        visual = _NoMaterialVisual()

    assert _part_texture_image(_Part()) is None


def test_part_texture_image_reads_pbr_material_base_color_texture() -> None:
    from PIL import Image

    from asset_catalogue.ui.model_preview_dialog import _part_texture_image

    image = Image.new("RGB", (2, 2), (10, 20, 30))

    class _Material:
        baseColorTexture = image

    class _Visual:
        material = _Material()

    class _Part:
        visual = _Visual()

    assert _part_texture_image(_Part()) is image


def test_part_texture_image_reads_simple_material_image() -> None:
    from PIL import Image

    from asset_catalogue.ui.model_preview_dialog import _part_texture_image

    texture_image = Image.new("RGB", (2, 2), (10, 20, 30))

    class _Material:
        image = texture_image

    class _Visual:
        material = _Material()

    class _Part:
        visual = _Visual()

    assert _part_texture_image(_Part()) is texture_image


def test_part_texture_image_none_when_neither_attribute_is_a_real_image() -> None:
    from asset_catalogue.ui.model_preview_dialog import _part_texture_image

    class _Material:
        pass

    class _Visual:
        material = _Material()

    class _Part:
        visual = _Visual()

    assert _part_texture_image(_Part()) is None


def test_pil_image_to_qpixmap_round_trips_dimensions(qapp) -> None:
    from PIL import Image

    from asset_catalogue.ui.model_preview_dialog import _pil_image_to_qpixmap

    image = Image.new("RGBA", (16, 24), (255, 0, 0, 255))
    pixmap = _pil_image_to_qpixmap(image)

    assert pixmap.isNull() is False
    assert pixmap.width() == 16
    assert pixmap.height() == 24


def test_load_preview_parts_flags_collider_and_skips_empty_parts(tmp_path: Path) -> None:
    """An integration-level check spanning the whole function, distinct
    from the single-part resolver-focused test above: a real multi-node
    scene with a plain mesh, a collider-named mesh, and an all-zero-vertex
    mesh (the shape a real Godot export of a mesh-less scene produces --
    see godot_export.py's _has_real_geometry) mixed together.
    """
    import trimesh

    from asset_catalogue.ui.model_preview_dialog import load_preview_parts

    visual_box = trimesh.creation.box(extents=(1, 1, 1))
    collider_box = trimesh.creation.box(extents=(2, 2, 2))
    empty_mesh = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=int))

    scene = trimesh.Scene()
    scene.add_geometry(visual_box, node_name="Visual", geom_name="mesh_visual")
    scene.add_geometry(collider_box, node_name="CollisionShape3D_collider", geom_name="mesh_collider")
    scene.add_geometry(empty_mesh, node_name="EmptyMarker", geom_name="mesh_empty")

    glb_path = tmp_path / "multi_part.glb"
    scene.export(glb_path)

    parts = load_preview_parts(glb_path)

    # The zero-vertex part never made it into the exported glb at all (see
    # test_has_real_geometry_false_for_an_empty_scene's own note on this),
    # so only the two real parts should come back.
    assert len(parts) == 2
    by_collider = {part.is_collider: part for part in parts}
    assert set(by_collider) == {True, False}
    assert "collider" in by_collider[True].name.lower()
    assert by_collider[True].texture_image is None
    assert len(by_collider[False].vertices) == len(visual_box.vertices)
