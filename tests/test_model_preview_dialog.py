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
