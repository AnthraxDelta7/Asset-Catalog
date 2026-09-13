"""The ingest folder is a starting point, not a boundary.

A pack can be ingested from anywhere on the machine. What makes that work
without touching the ~180 places that resolve `ingest_folder /
pack_folder / relative_path` is pathlib: joining an absolute right-hand
side returns it unchanged, so an out-of-folder pack flows through exactly
the same resolution as every other one.
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


def test_an_absolute_pack_folder_resolves_through_the_normal_join() -> None:
    """The property the whole design rests on. If this ever stopped being
    true, every out-of-folder pack would silently resolve to a path under
    the ingest folder that does not exist.
    """
    root = Path("D:/GameDev/Ingest")
    assert root / "InsidePack" == Path("D:/GameDev/Ingest/InsidePack")
    assert root / "D:/Elsewhere/Pack" == Path("D:/Elsewhere/Pack")
    assert root / "C:/Users/someone/Downloads/Pack" == Path("C:/Users/someone/Downloads/Pack")


def test_a_pack_outside_the_ingest_folder_is_recorded_absolutely(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    outside = tmp_path / "somewhere_else" / "CoolPack"
    root.mkdir()
    outside.mkdir(parents=True)

    dialog = StagingBrowserDialog(root)
    # Inside the root: short and relative, so moving the root later keeps
    # these packs working.
    assert dialog._path_for_caller(root / "InsidePack") == "InsidePack"
    # Outside it: absolute, the only form that still means anything.
    assert Path(dialog._path_for_caller(outside)) == outside


def test_browsing_is_not_trapped_at_the_ingest_folder(qapp, tmp_path: Path) -> None:
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "a" / "b" / "ingest"
    root.mkdir(parents=True)
    dialog = StagingBrowserDialog(root)

    assert dialog._current_dir == root
    assert dialog.up_button.isEnabled()
    dialog._go_up()
    assert dialog._current_dir == root.parent, "Up must leave the ingest folder"
    dialog._go_home()
    assert dialog._current_dir == root


def test_an_out_of_folder_pack_ingests_and_its_files_resolve(tmp_path: Path, monkeypatch) -> None:
    """End to end: catalogue a pack that lives nowhere near the ingest
    folder, then confirm the catalogue can still find its files.
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

    # An absolute pack_folder is what the browser hands back for this.
    pack_id, _ = ingest.get_or_create_pack(conn, "OutsidePack", str(outside), None, None, None)
    ingest.ingest_pack(conn, outside, pack_id)

    catalogue = Catalogue(conn, root, library / "thumbnails", library / "assets")
    assets = catalogue.list_assets()
    assert [a.filename for a in assets] == ["prop.glb"]
    assert catalogue.pack_source_exists(str(outside))

    row = conn.execute(
        "SELECT packs.pack_folder, assets.relative_path FROM assets "
        "JOIN packs ON packs.id = assets.pack_id"
    ).fetchone()
    resolved = root / row["pack_folder"] / row["relative_path"]
    assert resolved == outside / "prop.glb"
    assert resolved.exists(), "the standard join must reach a file outside the ingest folder"
    conn.close()


def test_batch_mode_picks_zips_with_checkboxes(qapp, tmp_path: Path) -> None:
    """Batch ingest could not take zips in practice.

    Selection was Ctrl/Shift-click, which is invisible unless you already
    know it is there -- and double-clicking a .zip, the gesture that picks
    one in single-select mode, was deliberately inert here, so the obvious
    action did nothing at all and the dialog read as refusing zips.
    """
    from PySide6.QtCore import Qt

    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "FolderPack").mkdir()
    (root / "A.zip").write_bytes(b"PK\x03\x04")
    (root / "B.zip").write_bytes(b"PK\x03\x04")

    dialog = StagingBrowserDialog(root, multi_select=True)
    rows = {
        dialog.list_widget.item(i).text(): dialog.list_widget.item(i)
        for i in range(dialog.list_widget.count())
    }
    assert all(item.flags() & Qt.ItemIsUserCheckable for item in rows.values())

    # The gesture that used to do nothing now ticks the row.
    dialog._on_item_double_clicked(rows["A.zip"])
    assert rows["A.zip"].checkState() == Qt.Checked
    rows["B.zip"].setCheckState(Qt.Checked)

    dialog._select_current_folder()
    assert dialog.selected_items == [("A.zip", True), ("B.zip", True)]


def test_batch_mode_still_honours_a_highlighted_row(qapp, tmp_path: Path) -> None:
    """Ticking is the new way, not the only way -- anyone who already had
    the Ctrl/Shift habit keeps it.
    """
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "A.zip").write_bytes(b"PK\x03\x04")

    dialog = StagingBrowserDialog(root, multi_select=True)
    dialog.list_widget.item(0).setSelected(True)
    dialog._select_current_folder()
    assert dialog.selected_items == [("A.zip", True)]


def test_single_select_double_click_still_picks_a_zip_outright(qapp, tmp_path: Path) -> None:
    """The single-pack dialog's behaviour must not have moved."""
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "A.zip").write_bytes(b"PK\x03\x04")

    dialog = StagingBrowserDialog(root)
    dialog._on_item_double_clicked(dialog.list_widget.item(0))
    assert (dialog.selected_relative_path, dialog.selected_is_zip) == ("A.zip", True)
