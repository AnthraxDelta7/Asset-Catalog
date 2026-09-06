from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from asset_catalogue import broken_textures, db, ingest, settings
from asset_catalogue.catalogue import Catalogue


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_model_asset(conn, staging_folder: Path, pack_name: str, filename: str) -> tuple[int, int]:
    pack_id, _ = ingest.get_or_create_pack(conn, pack_name, pack_name, None, None, None)
    path = staging_folder / pack_name / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"dummy model content for {filename} -- never actually rendered in this test")
    ingest.ingest_pack(conn, staging_folder / pack_name, pack_id)
    asset_id = conn.execute(
        "SELECT id FROM assets WHERE filename = ?", (filename,)
    ).fetchone()["id"]
    return pack_id, asset_id


@pytest.fixture
def catalogue_with_broken_material(tmp_path: Path, monkeypatch):
    """A real, on-disk catalogue with one model asset flagged as having a
    broken material -- MissingTexturesDialog's actions (set_texture_
    override_bg, acknowledge_no_texture_bg, regenerate_model_thumbnail_bg)
    are Catalogue _bg methods, which open their own connection via
    settings.load().db_path(), same reasoning as every other _bg-method
    test in this project: settings must point at this exact database.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))

    conn = db.connect(library / "catalogue.db")
    pack_id, asset_id = _make_model_asset(conn, staging, "Pack", "model.fbx")
    broken_textures.replace_for_asset(conn, asset_id, ["BrokenMat"])

    texture_dir = staging / "Pack" / "Textures"
    texture_dir.mkdir()
    (texture_dir / "found.png").write_bytes(b"")

    catalogue = Catalogue(conn, staging, library / "thumbnails", library / "assets")
    return catalogue, pack_id, asset_id


def _fake_run_job(dialog) -> None:
    """MissingTexturesDialog's Browse.../Add Supplementary File... actions
    run their real Catalogue work inside a job() closure handed to
    _run_job, which normally executes it on a real QThread -- fine for
    the running app, but pytest doesn't pump Qt's event loop, so a
    QueuedConnection-delivered signal from that thread would never
    actually arrive during a synchronous test. Patching _run_job to call
    job() and on_ok() directly, synchronously, tests the exact same
    business logic (path validation, override application, affected-
    asset-id computation, all via the real Catalogue against the real
    tmp DB) without any of the threading involved -- the threading
    itself is _BackgroundWorker's own generic, already-reused machinery,
    not this dialog's logic.
    """
    def fake(fn, _progress_text, on_ok):
        result = fn(lambda _text: None)
        on_ok(result)

    patcher = patch.object(dialog, "_run_job", side_effect=fake)
    patcher.start()
    return patcher


def test_refresh_loads_broken_rows(qapp, catalogue_with_broken_material) -> None:
    from asset_catalogue.ui.main_window import MissingTexturesDialog

    catalogue, _pack_id, asset_id = catalogue_with_broken_material
    dialog = MissingTexturesDialog(catalogue)

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "model.fbx"
    assert dialog.table.item(0, 2).text() == "BrokenMat"
    assert dialog._rows[0]["asset_id"] == asset_id


def test_asset_id_filter_narrows_to_one_asset(qapp, catalogue_with_broken_material) -> None:
    from asset_catalogue.ui.main_window import MissingTexturesDialog

    catalogue, pack_id, asset_id = catalogue_with_broken_material
    conn = catalogue._conn
    _pack_id2, asset_id2 = _make_model_asset(conn, catalogue.staging_folder(), "Pack", "model2.fbx")
    broken_textures.replace_for_asset(conn, asset_id2, ["OtherMat"])

    dialog = MissingTexturesDialog(catalogue, asset_id_filter=asset_id)

    assert dialog.table.rowCount() == 1
    assert dialog._rows[0]["asset_id"] == asset_id


def test_dialog_closes_itself_when_nothing_is_broken(qapp, tmp_path: Path, monkeypatch) -> None:
    """No rows at all (everything already fixed) shouldn't leave an empty
    review dialog sitting open with every action already disabled -- same
    self-closing behavior as PendingConversionsDialog.
    """
    from PySide6.QtWidgets import QDialog

    from asset_catalogue.ui.main_window import MissingTexturesDialog

    staging = tmp_path / "staging"
    staging.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")
    catalogue = Catalogue(conn, staging, library / "thumbnails", library / "assets")

    dialog = MissingTexturesDialog(catalogue)

    assert dialog.result() == QDialog.Accepted


def test_skip_selected_removes_row_without_persisting(qapp, catalogue_with_broken_material) -> None:
    from asset_catalogue.ui.main_window import MissingTexturesDialog

    catalogue, _pack_id, asset_id = catalogue_with_broken_material
    dialog = MissingTexturesDialog(catalogue)
    dialog.table.selectRow(0)

    dialog._skip_selected()

    assert dialog.table.rowCount() == 0
    # Not persisted -- the underlying DB row is still there, so a fresh
    # dialog (the next time someone opens the review) sees it again.
    assert broken_textures.list_for_asset(catalogue._conn, asset_id) == ["BrokenMat"]


def test_no_texture_needed_selected_persists_and_clears_row(qapp, catalogue_with_broken_material) -> None:
    from asset_catalogue.ui.main_window import MissingTexturesDialog

    catalogue, pack_id, asset_id = catalogue_with_broken_material
    dialog = MissingTexturesDialog(catalogue)
    dialog.table.selectRow(0)

    dialog._no_texture_needed_selected()

    assert dialog.table.rowCount() == 0
    assert broken_textures.list_for_asset(catalogue._conn, asset_id) == []
    corrections = catalogue.get_pack_detail("Pack").corrections
    assert corrections["acknowledged_materials"] == ["BrokenMat"]


def test_browse_selected_requires_exactly_one_row(qapp, catalogue_with_broken_material) -> None:
    from PySide6.QtWidgets import QMessageBox

    from asset_catalogue.ui.main_window import MissingTexturesDialog

    catalogue, _pack_id, _asset_id = catalogue_with_broken_material
    dialog = MissingTexturesDialog(catalogue)
    # Nothing selected at all.

    with patch.object(QMessageBox, "information") as mock_information:
        dialog._browse_selected()
        mock_information.assert_called_once()

    assert dialog.table.rowCount() == 1  # untouched


def test_browse_selected_happy_path_applies_override_and_clears_row(
    qapp, catalogue_with_broken_material
) -> None:
    from PySide6.QtWidgets import QFileDialog

    from asset_catalogue.ui.main_window import MissingTexturesDialog

    catalogue, pack_id, asset_id = catalogue_with_broken_material
    found_texture = catalogue.staging_folder() / "Pack" / "Textures" / "found.png"
    dialog = MissingTexturesDialog(catalogue)
    dialog.table.selectRow(0)
    patcher = _fake_run_job(dialog)

    try:
        with (
            patch.object(QFileDialog, "getOpenFileName", return_value=(str(found_texture), "")),
            patch.object(Catalogue, "regenerate_model_thumbnail_bg") as mock_regenerate,
        ):
            dialog._browse_selected()
    finally:
        patcher.stop()

    corrections = catalogue.get_pack_detail("Pack").corrections
    assert corrections["texture_overrides"] == {"BrokenMat": str(Path("Textures") / "found.png")}
    mock_regenerate.assert_called_once()
    assert mock_regenerate.call_args.kwargs["asset_ids"] == [asset_id]
    # The override already clears the broken-material row for the whole
    # pack (see Catalogue.set_texture_override_bg) -- re-rendering is
    # just to refresh the thumbnail, not what makes the row disappear.
    assert broken_textures.list_for_asset(catalogue._conn, asset_id) == []
    assert dialog.table.rowCount() == 0


def test_browse_selected_rejects_a_file_outside_the_pack(qapp, catalogue_with_broken_material, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from asset_catalogue.ui.main_window import MissingTexturesDialog

    catalogue, _pack_id, asset_id = catalogue_with_broken_material
    outside_file = tmp_path / "outside.png"
    outside_file.write_bytes(b"")
    dialog = MissingTexturesDialog(catalogue)
    dialog.table.selectRow(0)
    patcher = _fake_run_job(dialog)

    try:
        with (
            patch.object(QFileDialog, "getOpenFileName", return_value=(str(outside_file), "")),
            patch.object(QMessageBox, "warning") as mock_warning,
        ):
            dialog._browse_selected()
            mock_warning.assert_called_once()
    finally:
        patcher.stop()

    corrections = catalogue.get_pack_detail("Pack").corrections
    assert not corrections.get("texture_overrides")
    assert dialog.table.rowCount() == 1


def test_add_supplementary_file_selected_does_not_clear_the_row(
    qapp, catalogue_with_broken_material
) -> None:
    """Unlike Browse..., attaching a supplementary file doesn't resolve
    the still-broken Base Color -- the row must stay in the list.
    """
    from PySide6.QtWidgets import QFileDialog

    from asset_catalogue.ui.main_window import MissingTexturesDialog

    catalogue, pack_id, asset_id = catalogue_with_broken_material
    found_texture = catalogue.staging_folder() / "Pack" / "Textures" / "found.png"
    dialog = MissingTexturesDialog(catalogue)
    dialog.table.selectRow(0)
    patcher = _fake_run_job(dialog)

    try:
        with (
            patch.object(QFileDialog, "getOpenFileName", return_value=(str(found_texture), "")),
            patch.object(Catalogue, "regenerate_model_thumbnail_bg") as mock_regenerate,
            patch("asset_catalogue.ui.main_window.QMessageBox.information"),
        ):
            dialog._add_supplementary_file_selected()
    finally:
        patcher.stop()

    corrections = catalogue.get_pack_detail("Pack").corrections
    assert corrections["texture_extras"] == {"BrokenMat": str(Path("Textures") / "found.png")}
    mock_regenerate.assert_called_once()
    assert broken_textures.list_for_asset(catalogue._conn, asset_id) == ["BrokenMat"]
    assert dialog.table.rowCount() == 1
