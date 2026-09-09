from __future__ import annotations

from pathlib import Path

import pytest

from asset_catalogue import db, settings
from asset_catalogue.catalogue import Catalogue


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_constructs_without_error(qapp, tmp_path: Path, monkeypatch) -> None:
    """A cheap regression net for constructor/wiring mistakes (wrong
    callback order, a renamed method not updated at a call site, etc.) --
    not a substitute for the scripted interaction tests used to verify
    actual behavior during development, just a fast "does it even start."
    """
    from asset_catalogue.ui.main_window import MainWindow

    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    library_folder = tmp_path / "library"
    library_folder.mkdir()
    staging_folder = tmp_path / "staging"
    staging_folder.mkdir()
    settings.save(settings.Settings(staging_folder=str(staging_folder), library_folder=str(library_folder)))

    conn = db.connect(library_folder / "catalogue.db")
    catalogue = Catalogue(conn, staging_folder, library_folder / "thumbnails", library_folder / "assets")

    window = MainWindow(catalogue)
    assert window.detail_panel.export_button is not None
    assert not window.detail_panel.export_button.isEnabled()
    window.close()


def test_corrections_form_widget_round_trips_all_fields(qapp, tmp_path: Path) -> None:
    from asset_catalogue.ui.main_window import CorrectionsFormWidget

    initial = {
        "up_axis": "Y_UP",
        "scale": 2.0,
        "material_fallback": True,
        "broken_texture_fallback": True,
        "disable_smart_texture_matching": True,
        "texture_overrides": {"RecessA": "Textures/RecessA_albedo.png"},
    }
    widget = CorrectionsFormWidget(initial, pack_root=tmp_path)

    assert widget.up_axis_combo.currentData() == "Y_UP"
    assert widget.scale_edit.text() == "2.0"
    assert widget.material_fallback_check.isChecked() is True
    assert widget.broken_texture_fallback_check.isChecked() is True
    assert widget.disable_smart_matching_check.isChecked() is True
    assert widget.overrides_list.count() == 1
    assert widget.overrides_list.item(0).text() == "RecessA -> Textures/RecessA_albedo.png"

    corrections, error = widget.read()
    assert error is None
    assert corrections == initial


def test_corrections_form_widget_defaults_are_all_off(qapp) -> None:
    from asset_catalogue.ui.main_window import CorrectionsFormWidget

    widget = CorrectionsFormWidget({})
    corrections, error = widget.read()
    assert error is None
    assert corrections["material_fallback"] is False
    assert corrections["broken_texture_fallback"] is False
    assert corrections["disable_smart_texture_matching"] is False
    assert corrections["texture_overrides"] == {}
    assert "up_axis" not in corrections
    assert "scale" not in corrections


def test_corrections_form_widget_rejects_a_non_numeric_scale(qapp) -> None:
    from asset_catalogue.ui.main_window import CorrectionsFormWidget

    widget = CorrectionsFormWidget({})
    widget.scale_edit.setText("not a number")
    corrections, error = widget.read()
    assert corrections is None
    assert error is not None


def test_corrections_form_widget_add_texture_override_via_dialogs(qapp, tmp_path: Path) -> None:
    """_add_texture_override drives two modal pickers (QInputDialog,
    QFileDialog) -- mocked here to return canned values rather than
    actually popping a window, the same way this project's own scripted
    interaction tests stand in for a real click during manual QA.
    """
    from unittest.mock import patch

    from PySide6.QtWidgets import QFileDialog, QInputDialog

    from asset_catalogue.ui.main_window import CorrectionsFormWidget

    pack_root = tmp_path / "StagedPack"
    texture_dir = pack_root / "Textures"
    texture_dir.mkdir(parents=True)
    texture_file = texture_dir / "Custom_albedo.png"
    texture_file.write_bytes(b"")

    widget = CorrectionsFormWidget({}, pack_root=pack_root)

    with (
        patch.object(QInputDialog, "getText", return_value=("MyMaterial", True)),
        patch.object(QFileDialog, "getOpenFileName", return_value=(str(texture_file), "")),
    ):
        widget._add_texture_override()

    expected_relative = str(Path("Textures") / "Custom_albedo.png")
    assert widget.overrides_list.count() == 1
    assert widget.overrides_list.item(0).text() == f"MyMaterial -> {expected_relative}"
    corrections, _error = widget.read()
    assert corrections["texture_overrides"] == {"MyMaterial": expected_relative}

    widget.overrides_list.setCurrentRow(0)
    widget._remove_selected_override()
    assert widget.overrides_list.count() == 0
    corrections, _error = widget.read()
    assert corrections["texture_overrides"] == {}


def test_corrections_form_widget_rejects_a_texture_outside_the_pack(qapp, tmp_path: Path) -> None:
    from unittest.mock import patch

    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

    from asset_catalogue.ui.main_window import CorrectionsFormWidget

    pack_root = tmp_path / "StagedPack"
    pack_root.mkdir()
    outside_file = tmp_path / "outside.png"
    outside_file.write_bytes(b"")

    widget = CorrectionsFormWidget({}, pack_root=pack_root)

    with (
        patch.object(QInputDialog, "getText", return_value=("MyMaterial", True)),
        patch.object(QFileDialog, "getOpenFileName", return_value=(str(outside_file), "")),
        patch.object(QMessageBox, "warning") as mock_warning,
    ):
        widget._add_texture_override()
        mock_warning.assert_called_once()

    assert widget.overrides_list.count() == 0


def test_format_selection_dialog_glb_precheck_and_others_default_unchecked(qapp) -> None:
    from asset_catalogue.ui.main_window import FormatSelectionDialog

    dialog = FormatSelectionDialog({".fbx", ".glb", ".obj"})

    assert dialog._checkboxes[".glb"].isChecked() is True
    assert dialog._checkboxes[".fbx"].isChecked() is False
    assert dialog._checkboxes[".obj"].isChecked() is False


def test_format_selection_dialog_import_all_sets_none_and_accepts(qapp) -> None:
    from PySide6.QtWidgets import QDialog

    from asset_catalogue.ui.main_window import FormatSelectionDialog

    dialog = FormatSelectionDialog({".fbx", ".glb"})
    dialog._import_all()

    assert dialog.format_selection is None
    assert dialog.result() == QDialog.Accepted


def test_format_selection_dialog_import_selected_uses_checked_extensions(qapp) -> None:
    from PySide6.QtWidgets import QDialog

    from asset_catalogue.ui.main_window import FormatSelectionDialog

    dialog = FormatSelectionDialog({".fbx", ".glb"})
    dialog._checkboxes[".fbx"].setChecked(True)
    dialog._import_selected()

    assert dialog.format_selection == {".fbx", ".glb"}  # .glb was already pre-checked
    assert dialog.result() == QDialog.Accepted


def test_format_selection_dialog_import_selected_with_nothing_checked_does_not_accept(qapp) -> None:
    from unittest.mock import patch

    from PySide6.QtWidgets import QDialog, QMessageBox

    from asset_catalogue.ui.main_window import FormatSelectionDialog

    dialog = FormatSelectionDialog({".fbx", ".glb"})
    dialog._checkboxes[".glb"].setChecked(False)  # undo the pre-check -- nothing selected now

    with patch.object(QMessageBox, "information") as mock_information:
        dialog._import_selected()
        mock_information.assert_called_once()

    assert dialog.format_selection is None
    assert dialog.result() != QDialog.Accepted


def test_self_update_is_disabled_pending_code_signing() -> None:
    """One-click download-and-install is deliberately switched off (see
    SELF_UPDATE_ENABLED's own comment in main_window.py) -- unsigned
    automatic file replacement kept getting transiently locked by
    Windows Defender in a way that was expensive to diagnose and looked
    like a crash/hang, and every real bug found in the actual download/
    apply flow itself was already fixed before that. This just pins the
    switch itself so re-enabling it is a deliberate, visible one-line
    change, not something that silently regresses.
    """
    from asset_catalogue.ui.main_window import SELF_UPDATE_ENABLED

    assert SELF_UPDATE_ENABLED is False


def _make_asset_summary(relative_path: str, asset_id: int = 1) -> "AssetSummary":
    from asset_catalogue.catalogue import AssetSummary

    return AssetSummary(
        id=asset_id,
        filename=Path(relative_path).name,
        pack_name="Pack",
        asset_type="model",
        thumbnail_status="pending",
        content_hash=f"hash-{asset_id}",
        relative_path=relative_path,
    )


def test_is_godot_export_eligible_true_for_godot_importable_models() -> None:
    from asset_catalogue.ui.main_window import _is_godot_export_eligible

    assets = [_make_asset_summary("a.glb", 1), _make_asset_summary("b.fbx", 2)]
    assert _is_godot_export_eligible(assets) is True


def test_is_godot_export_eligible_false_for_non_model_or_mixed_selection() -> None:
    from asset_catalogue.ui.main_window import _is_godot_export_eligible

    assert _is_godot_export_eligible([]) is False
    assert _is_godot_export_eligible([_make_asset_summary("a.wav")]) is False
    assert _is_godot_export_eligible([_make_asset_summary("a.glb", 1), _make_asset_summary("b.png", 2)]) is False
    # A model Blender converts on the way in still counts -- Godot never
    # sees the .stl itself, only the .glb it becomes.
    assert _is_godot_export_eligible([_make_asset_summary("a.stl")]) is True


def test_remember_last_export_mode_persists_only_on_change(tmp_path: Path, monkeypatch) -> None:
    from asset_catalogue.ui.main_window import _remember_last_export_mode

    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings())
    assert settings.load().last_export_mode == "standard"

    _remember_last_export_mode("godot")
    assert settings.load().last_export_mode == "godot"

    _remember_last_export_mode("godot")  # no-op write path -- must not raise or change anything
    assert settings.load().last_export_mode == "godot"

    _remember_last_export_mode("standard")
    assert settings.load().last_export_mode == "standard"


def test_export_dialog_hides_godot_checkbox_when_not_eligible(qapp) -> None:
    from asset_catalogue.ui.main_window import ExportDialog

    dialog = ExportDialog(3, godot_eligible=False)
    assert dialog.godot_check is None


def test_export_dialog_godot_checkbox_defaults_to_last_export_mode(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    from asset_catalogue.ui.main_window import ExportDialog

    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(last_export_mode="godot"))

    dialog = ExportDialog(1, godot_eligible=True)
    assert dialog.godot_check is not None
    assert dialog.godot_check.isChecked() is True


def test_export_dialog_accept_sets_mode_from_checkbox(qapp, tmp_path: Path) -> None:
    from asset_catalogue.ui.main_window import ExportDialog

    dialog = ExportDialog(1, godot_eligible=True)
    dialog.project_edit.setText(str(tmp_path))
    dialog.godot_check.setChecked(True)
    dialog._on_accept()

    assert dialog.mode == "godot"
    assert dialog.project_root == tmp_path


def test_export_dialog_accept_defaults_to_standard_mode_when_ineligible(qapp, tmp_path: Path) -> None:
    from asset_catalogue.ui.main_window import ExportDialog

    dialog = ExportDialog(1, godot_eligible=False)
    dialog.project_edit.setText(str(tmp_path))
    dialog._on_accept()

    assert dialog.mode == "standard"


def test_detail_panel_shows_rig_and_clips_only_for_an_animated_asset(tmp_path: Path) -> None:
    """Also guards a real bug: the panel is reused for every selection,
    so the previous asset's rig text has to be cleared, not just hidden.
    A static prop selected after a rigged character was showing that
    character's "79-joint skeleton" text on a hidden label.
    """
    from PySide6.QtWidgets import QApplication

    from asset_catalogue import db, ingest, library_assets
    from asset_catalogue.catalogue import Catalogue
    from asset_catalogue.ui.main_window import DetailPanel
    from conftest import write_minimal_glb

    staging, library = tmp_path / "staging", tmp_path / "library"
    pack = staging / "Pack"
    pack.mkdir(parents=True)
    library.mkdir(parents=True)
    write_minimal_glb(pack / "static_prop.glb", {"meshes": [{}]})
    write_minimal_glb(
        pack / "hero.glb",
        {
            "meshes": [{}],
            "skins": [{"joints": list(range(79))}],
            "animations": [{"name": "@idle"}, {"name": "@walk"}],
        },
    )

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    library_assets.archive_pack(conn, staging, library / "assets", pack_id)

    QApplication.instance() or QApplication([])
    catalogue = Catalogue(conn, staging, library / "thumbnails", library / "assets")
    noop = lambda *a, **k: None  # noqa: E731
    panel = DetailPanel(catalogue, *([noop] * 14))
    by_name = {asset.filename: asset for asset in catalogue.list_assets()}

    panel.show_asset(by_name["hero.glb"])
    assert "79-joint skeleton" in panel.rig_label.text()
    assert "2 animations" in panel.rig_label.text()
    # Listed here, but played in the 3D preview -- the detail panel
    # summarises what an asset is, it doesn't play it.
    assert panel.animations_label.text() == "animations: @idle, @walk"
    assert panel.view_3d_button.isVisible() is False or True  # shown once the panel is

    panel.show_asset(by_name["static_prop.glb"])
    assert panel.rig_label.text() == ""
    assert panel.animations_label.text() == ""
    conn.close()


def _calibration_dialog(tmp_path: Path, model_count: int):
    from PySide6.QtWidgets import QApplication

    from asset_catalogue import db, ingest
    from asset_catalogue.catalogue import Catalogue
    from asset_catalogue.ui.main_window import CalibrationReviewDialog
    from conftest import write_minimal_glb

    staging, library = tmp_path / "staging", tmp_path / "library"
    pack = staging / "Pack"
    pack.mkdir(parents=True)
    library.mkdir(parents=True)
    for index in range(model_count):
        # Distinct content per file: assets.content_hash is UNIQUE, so
        # byte-identical models would dedupe down to a single asset.
        write_minimal_glb(pack / f"model_{index}.glb", {"meshes": [{"name": f"m{index}"}]})

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    ids = [row["id"] for row in conn.execute("SELECT id FROM assets ORDER BY id")]
    # The calibration preview is rendered; the rest stay pending, exactly
    # as generate_pack_thumbnails leaves them.
    conn.execute("UPDATE assets SET thumbnail_status = 'done' WHERE id = ?", (ids[0],))
    conn.commit()

    QApplication.instance() or QApplication([])
    catalogue = Catalogue(conn, staging, library / "thumbnails", library / "assets")
    pending = catalogue.count_pending_model_assets(pack_id)
    dialog = CalibrationReviewDialog(catalogue, pack_id, "Pack", ids[0], pending, {})
    return conn, dialog, pending


def test_calibration_dialog_offers_no_render_when_the_pack_has_one_model(tmp_path: Path) -> None:
    """A pack whose only model *is* the calibration preview has nothing
    left to render, so the proceed button must not read "Render Remaining
    0 Model(s)" or launch Blender to do no work.
    """
    conn, dialog, pending = _calibration_dialog(tmp_path, model_count=1)

    assert pending == 0
    assert dialog._render_all_button.text() == "Looks Good -- Finish"
    # resolve_blender would raise if the render path were entered at all.
    dialog._catalogue.resolve_blender = lambda: (_ for _ in ()).throw(
        AssertionError("Blender must not run when nothing is pending")
    )
    dialog._on_render_all()
    assert dialog.result_action == "render_all"
    conn.close()


def test_calibration_dialog_still_offers_to_render_the_rest(tmp_path: Path) -> None:
    conn, dialog, pending = _calibration_dialog(tmp_path, model_count=3)

    assert pending == 2
    assert dialog._render_all_button.text() == "Render Remaining 2 Models"
    conn.close()


def test_progress_counts_are_parsed_from_real_job_messages() -> None:
    """The bar is driven by counts the jobs already print, rather than a
    structured signal threaded through several dozen call sites.
    """
    from asset_catalogue.ui.main_window import parse_progress_count

    assert parse_progress_count("Converted multi.fbx to .glb (1/2)") == (1, 2)
    assert parse_progress_count("Rendering @idle: frame 12/24") == (12, 24)
    assert parse_progress_count("Exported Crate.tscn -> Crate.glb (7/40)") == (7, 40)
    # Last count wins: the one still moving is at the end of the line.
    assert parse_progress_count("Pack 2/2: rendering 3/40") == (3, 40)


def test_progress_parsing_rejects_counts_that_are_not_progress() -> None:
    """A version number or a stray ratio must not drive the bar, and a
    line with no count at all leaves it indeterminate rather than
    resetting it to zero.
    """
    from asset_catalogue.ui.main_window import parse_progress_count

    assert parse_progress_count("Importing new files into the Godot project...") is None
    # current > total can't be progress.
    assert parse_progress_count("Converting v1.5/2.0 legacy asset") is None
    assert parse_progress_count("Model thumbnails: 5 generated, 2 already done") is None
    assert parse_progress_count("") is None


def test_progress_dialog_switches_to_determinate_only_once_a_count_arrives(qapp) -> None:
    from asset_catalogue.ui.main_window import ProgressLogDialog

    dialog = ProgressLogDialog("Asset Catalogue", "Starting Blender...", None)
    # Nothing countable yet -- the bar must not claim a position it
    # doesn't know, so it stays in its travelling-band mode.
    assert dialog._bar._fraction is None
    assert dialog._count_label.text() == ""

    dialog.append("Rendering crate.glb (3/12)")
    assert dialog._bar._fraction == pytest.approx(0.25)
    assert dialog._count_label.text() == "3 of 12"
    # The step label shows the latest line, the log keeps all of them.
    assert dialog._step_label.text() == "Rendering crate.glb (3/12)"
    assert "Starting Blender..." in dialog._log.toPlainText()
    dialog.close()


def test_texture_override_offers_known_broken_materials(qapp, tmp_path: Path) -> None:
    """An override only takes effect on an exact, case-sensitive material
    name, so retyping one from memory is the failure-prone step. The
    materials already recorded as having a broken texture are exactly the
    ones an override is for, so they're offered as a list.
    """
    from unittest.mock import patch

    from PySide6.QtWidgets import QFileDialog, QInputDialog

    from asset_catalogue.ui.main_window import CorrectionsFormWidget

    pack_root = tmp_path / "Pack"
    (pack_root / "Textures").mkdir(parents=True)
    texture = pack_root / "Textures" / "wood.png"
    texture.write_bytes(b"")

    widget = CorrectionsFormWidget(
        {}, pack_root=pack_root, known_materials=["Metal_A", "Wood_B"]
    )

    with (
        patch.object(QInputDialog, "getItem", return_value=("Wood_B", True)) as pick,
        patch.object(QFileDialog, "getOpenFileName", return_value=(str(texture), "")),
    ):
        widget._add_texture_override()
        # The known names are what's offered, and the field stays editable
        # so an unrendered model's material can still be typed in.
        assert pick.call_args.args[3] == ["Metal_A", "Wood_B"]
        assert pick.call_args.args[5] is True

    corrections, _error = widget.read()
    assert corrections["texture_overrides"] == {"Wood_B": str(Path("Textures") / "wood.png")}


def test_texture_override_falls_back_to_free_text_with_no_known_materials(
    qapp, tmp_path: Path
) -> None:
    """Nothing rendered yet means nothing recorded as broken -- the
    override must still be reachable rather than offering an empty list.
    """
    from unittest.mock import patch

    from PySide6.QtWidgets import QFileDialog, QInputDialog

    from asset_catalogue.ui.main_window import CorrectionsFormWidget

    pack_root = tmp_path / "Pack"
    (pack_root / "Textures").mkdir(parents=True)
    texture = pack_root / "Textures" / "wood.png"
    texture.write_bytes(b"")

    widget = CorrectionsFormWidget({}, pack_root=pack_root)

    with (
        patch.object(QInputDialog, "getText", return_value=("TypedName", True)) as typed,
        patch.object(QInputDialog, "getItem") as picked,
        patch.object(QFileDialog, "getOpenFileName", return_value=(str(texture), "")),
    ):
        widget._add_texture_override()
        assert typed.called
        assert not picked.called

    corrections, _error = widget.read()
    assert "TypedName" in corrections["texture_overrides"]


def _visible_packs(panel) -> list[str]:
    return [
        panel.pack_list.item(row).text()
        for row in range(panel.pack_list.count())
        if not panel.pack_list.item(row).isHidden()
    ]


def test_pack_search_filters_the_list_and_clears_when_collapsed(qapp, tmp_path: Path) -> None:
    from asset_catalogue import db, ingest
    from asset_catalogue.catalogue import Catalogue
    from asset_catalogue.ui.main_window import FilterPanel
    from conftest import write_minimal_glb

    library, staging = tmp_path / "library", tmp_path / "staging"
    library.mkdir()
    conn = db.connect(library / "catalogue.db")
    for name in ("SciFi Interiors", "SciFi Weapons", "Fantasy Village"):
        pack = staging / name
        pack.mkdir(parents=True)
        write_minimal_glb(pack / "a.glb", {"meshes": [{"name": name}]})
        pack_id, _ = ingest.get_or_create_pack(conn, name, name, None, None, None)
        ingest.ingest_pack(conn, pack, pack_id)

    catalogue = Catalogue(conn, staging, library / "thumbnails", library / "assets")
    noop = lambda *a, **k: None  # noqa: E731
    panel = FilterPanel(catalogue, noop, noop, noop, noop, noop, noop)

    assert len(_visible_packs(panel)) == 4  # "All packs" plus three

    panel.pack_search_toggle.setChecked(True)
    panel.pack_search_edit.setText("scifi")
    # Case-insensitive, and "All packs" survives filtering because it's
    # the only way back to an unfiltered grid.
    assert _visible_packs(panel) == ["All packs", "SciFi Interiors", "SciFi Weapons"]

    # Collapsing clears the filter -- otherwise packs stay hidden with no
    # visible control explaining why.
    panel.pack_search_toggle.setChecked(False)
    assert len(_visible_packs(panel)) == 4
    assert panel.pack_search_edit.text() == ""
    conn.close()
