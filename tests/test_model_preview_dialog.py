from __future__ import annotations

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


def test_single_part_asset_hides_the_parts_panel(qapp) -> None:
    """Nothing to isolate in a one-part asset -- the panel stays out of
    the way, same as the dialog looked before this feature existed.
    """
    from asset_catalogue.ui.model_preview_dialog import Model3DPreviewDialog

    dialog = Model3DPreviewDialog("plain.glb", [_make_part("MainMesh", is_collider=False)])
    # isHidden() (not isVisible()) since the dialog is never actually shown
    # in this test -- isVisible() is always False for an unshown widget
    # regardless of its own explicit visibility flag, which would make
    # this assertion pass trivially even if the panel-hiding logic broke.
    assert dialog.parts_panel.isHidden() is True
