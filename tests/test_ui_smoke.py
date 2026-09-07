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
    assert _is_godot_export_eligible([_make_asset_summary("a.stl")]) is False
    assert _is_godot_export_eligible([_make_asset_summary("a.glb", 1), _make_asset_summary("b.png", 2)]) is False


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
