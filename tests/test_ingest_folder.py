"""Picking packs to ingest: folders, zips and single files, from anywhere.

The picker is a standard QFileDialog now. It used to be a hand-built
browser, on the documented belief that a QFileDialog is "either a folder
picker or a file picker, never both" -- true of the *native* Windows
dialog, and only of that. Qt's own dialog lists both and multi-selects
across them, which is what these tests drive.

The dialog is driven through its real internal view with real mouse
events. Setting selection state directly passes under selection modes
that do not work when clicked, which is exactly how a multi-select bug
survived several rounds of "verified working".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from asset_catalogue import db, ingest, settings
from asset_catalogue.catalogue import Catalogue

from conftest import write_minimal_glb


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _rows(dialog) -> list[str]:
    """What the dialog is showing, in order."""
    from PySide6.QtWidgets import QListView

    view = dialog.findChild(QListView, "listView")
    model = view.model()
    return [
        model.index(row, 0, view.rootIndex()).data()
        for row in range(model.rowCount(view.rootIndex()))
    ]


def _click_every_row(dialog) -> None:
    """Ctrl-click every visible row, the way someone picks a batch.

    Qt caches the modifier state from synthesised events and does not
    clear it, so a Ctrl-click here leaves QGuiApplication believing Ctrl
    is still held -- for the rest of the process. A later test calling
    selectRow() twice then gets *both* rows, because Qt reads it as a
    ctrl-select. Verified: that is what made two pack-manager tests fail
    only when run after this one. The release at the end puts it back.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QListView

    view = dialog.findChild(QListView, "listView")
    model = view.model()
    count = model.rowCount(view.rootIndex())
    for row in range(count):
        index = model.index(row, 0, view.rootIndex())
        QTest.mouseClick(
            view.viewport(),
            Qt.LeftButton,
            Qt.NoModifier if row == 0 else Qt.ControlModifier,
            view.visualRect(index).center(),
        )
        QApplication.processEvents()
    # QTest.keyRelease, not a hand-built QKeyEvent through sendEvent --
    # only the QTest call updates QTest's own modifier bookkeeping, which
    # is what QGuiApplication.keyboardModifiers() reports. Checked both
    # ways: sendEvent leaves it stuck on Control.
    QTest.keyRelease(view, Qt.Key_Control)
    QApplication.processEvents()


def _shown(dialog, qapp) -> None:
    dialog.resize(780, 470)
    dialog.show()
    qapp.processEvents()
    qapp.processEvents()


# -- path recording ----------------------------------------------------


def test_an_absolute_pack_folder_resolves_through_the_normal_join() -> None:
    """The property the whole out-of-folder design rests on. If this
    stopped being true, every pack outside the ingest folder would
    silently resolve to a path under it that does not exist.
    """
    root = Path("D:/GameDev/Ingest")
    assert root / "InsidePack" == Path("D:/GameDev/Ingest/InsidePack")
    assert root / "D:/Elsewhere/Pack" == Path("D:/Elsewhere/Pack")
    assert root / "C:/Users/someone/Downloads/Pack" == Path("C:/Users/someone/Downloads/Pack")


def test_paths_are_relative_inside_the_ingest_folder_and_absolute_outside(
    qapp, tmp_path: Path
) -> None:
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    outside = tmp_path / "somewhere_else"
    root.mkdir()
    outside.mkdir()

    dialog = StagingBrowserDialog(root)
    # Relative inside, so moving the ingest folder later keeps these working.
    assert dialog._path_for_caller(root / "InsidePack") == "InsidePack"
    # Absolute outside, the only form that still means anything.
    assert Path(dialog._path_for_caller(outside / "CoolPack")) == outside / "CoolPack"


def test_pack_names_come_out_right_for_each_kind() -> None:
    """A folder keeps its name; a .zip and a lone file lose the extension.

    Decided by the extension table rather than "does the name contain a
    dot", because plenty of folders do -- POLY_NaturePack_Godot-001, v1.2
    -- and stripping a suffix off one of those renames the pack wrongly.
    """
    from asset_catalogue.ui.main_window import default_pack_name

    assert default_pack_name("Pack.zip", True) == "Pack"
    assert default_pack_name("prop.glb", False) == "prop"
    assert default_pack_name("hit.wav", False) == "hit"
    assert default_pack_name("FolderPack", False) == "FolderPack"
    assert default_pack_name("POLY_NaturePack_Godot-001", False) == "POLY_NaturePack_Godot-001"
    assert default_pack_name("v1.2", False) == "v1.2"


# -- the picker --------------------------------------------------------


def test_folders_zips_and_asset_files_are_all_listed(qapp, tmp_path: Path) -> None:
    """Anything ingest can take, and nothing it cannot. The listing is
    filtered by the same extension table ingest catalogues from, so the
    picker can never offer a file that would then be skipped.
    """
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "AFolder").mkdir()
    for name in ("b.zip", "c.glb", "d.wav", "readme.txt", "project.unity"):
        (root / name).write_bytes(b"x")

    dialog = StagingBrowserDialog(root, multi_select=True)
    _shown(dialog, qapp)
    assert _rows(dialog) == ["AFolder", "b.zip", "c.glb", "d.wav"]


def test_one_selection_can_span_a_folder_a_zip_and_a_file(qapp, tmp_path: Path) -> None:
    """The reason the hand-built browser existed at all was the belief
    that a file dialog cannot do this. Qt's own dialog can, and returns
    the directory alongside the files.
    """
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "AFolder").mkdir()
    (root / "b.zip").write_bytes(b"PK\x03\x04")
    (root / "c.glb").write_bytes(b"glb")

    dialog = StagingBrowserDialog(root, multi_select=True)
    _shown(dialog, qapp)
    _click_every_row(dialog)
    dialog._record(dialog.selectedFiles())

    assert dialog.selected_items == [
        ("AFolder", False),
        ("b.zip", True),
        ("c.glb", False),
    ]


def test_a_pack_outside_the_ingest_folder_is_recorded_absolutely(
    qapp, tmp_path: Path
) -> None:
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    downloads = tmp_path / "downloads"
    root.mkdir()
    downloads.mkdir()
    (downloads / "far.zip").write_bytes(b"PK\x03\x04")

    dialog = StagingBrowserDialog(root)
    dialog._record([str(downloads / "far.zip")])
    assert Path(dialog.selected_relative_path) == downloads / "far.zip"
    assert dialog.selected_is_zip is True


def test_the_single_pack_case_reports_the_one_thing_picked(qapp, tmp_path: Path) -> None:
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "prop.glb").write_bytes(b"glb")

    dialog = StagingBrowserDialog(root)
    dialog._record([str(root / "prop.glb")])
    assert dialog.selected_relative_path == "prop.glb"
    # A lone asset file is not an archive: saying it was would name the
    # pack "prop.glb" and offer the .zip-only Godot notice.
    assert dialog.selected_is_zip is False


def test_picking_nothing_leaves_the_result_empty(qapp, tmp_path: Path) -> None:
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    dialog = StagingBrowserDialog(root, multi_select=True)
    dialog._record([])
    assert dialog.selected_items == []
    assert dialog.selected_relative_path is None


# -- end to end --------------------------------------------------------


def test_an_out_of_folder_pack_ingests_and_its_files_resolve(tmp_path: Path, monkeypatch) -> None:
    """Catalogue a pack that lives nowhere near the ingest folder, then
    confirm the catalogue can still find its files.
    """
    library, root = tmp_path / "library", tmp_path / "ingest"
    outside = tmp_path / "downloads" / "OutsidePack"
    library.mkdir()
    root.mkdir()
    outside.mkdir(parents=True)
    write_minimal_glb(outside / "prop.glb", {"meshes": [{"name": "prop"}]})

    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(root), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")

    pack_id, _ = ingest.get_or_create_pack(conn, "OutsidePack", str(outside), None, None, None)
    ingest.ingest_pack(conn, outside, pack_id)

    catalogue = Catalogue(conn, root, library / "thumbnails", library / "assets")
    assert [a.filename for a in catalogue.list_assets()] == ["prop.glb"]
    assert catalogue.pack_source_exists(str(outside))

    row = conn.execute(
        "SELECT packs.pack_folder, assets.relative_path FROM assets "
        "JOIN packs ON packs.id = assets.pack_id"
    ).fetchone()
    resolved = root / row["pack_folder"] / row["relative_path"]
    assert resolved == outside / "prop.glb"
    assert resolved.exists(), "the standard join must reach a file outside the ingest folder"
    conn.close()
