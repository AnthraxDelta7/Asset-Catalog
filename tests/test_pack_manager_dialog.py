from __future__ import annotations

from pathlib import Path

import pytest

from asset_catalogue import db, ingest, library_assets, settings
from asset_catalogue.catalogue import Catalogue
from asset_catalogue.ui.pack_manager_dialog import PackManagerDialog

from conftest import write_minimal_glb


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def catalogue_with_packs(tmp_path: Path, monkeypatch) -> Catalogue:
    library, staging = tmp_path / "library", tmp_path / "staging"
    library.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")
    for index, name in enumerate(("Alpha", "Beta")):
        pack = staging / name
        pack.mkdir(parents=True)
        # Models only, with distinct content: assets.content_hash is
        # UNIQUE, so identical bytes would dedupe across packs and the
        # counts under test would silently be wrong.
        for asset in range(index + 1):
            write_minimal_glb(pack / f"m{index}_{asset}.glb", {"meshes": [{"name": f"{index}{asset}"}]})
        pack_id, _ = ingest.get_or_create_pack(conn, name, name, f"Creator{index}", "CC0", None)
        ingest.ingest_pack(conn, pack, pack_id)
        library_assets.archive_pack(conn, staging, library / "assets", pack_id)
    catalogue = Catalogue(conn, staging, library / "thumbnails", library / "assets")
    yield catalogue
    conn.close()


def test_lists_every_pack_with_its_counts(qapp, catalogue_with_packs: Catalogue) -> None:
    dialog = PackManagerDialog(catalogue_with_packs)

    assert dialog.table.rowCount() == 2
    names = [dialog.table.item(row, 0).text() for row in range(2)]
    assert names == ["Alpha", "Beta"]
    assert dialog.table.item(0, 1).text() == "1"
    assert dialog.table.item(1, 1).text() == "2"
    assert dialog.table.item(0, 2).text() == "1"
    assert dialog.table.item(1, 2).text() == "2"


def test_hiding_a_pack_drops_it_from_the_filter_list_but_not_from_here(
    qapp, catalogue_with_packs: Catalogue
) -> None:
    """Hiding is a listing concern, not a delete -- the assets stay
    catalogued and searchable, and this dialog is the only place a hidden
    pack can be found again.
    """
    dialog = PackManagerDialog(catalogue_with_packs)
    dialog.table.selectRow(0)
    dialog._set_hidden(True)

    assert catalogue_with_packs.list_packs() == ["Beta"]
    assert catalogue_with_packs.list_packs(include_hidden=True) == ["Alpha", "Beta"]
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 5).text() == "Hidden"
    assert dialog.changed is True

    dialog.table.selectRow(0)
    dialog._set_hidden(False)
    assert catalogue_with_packs.list_packs() == ["Alpha", "Beta"]


def test_selecting_one_pack_shows_its_contents(qapp, catalogue_with_packs: Catalogue) -> None:
    dialog = PackManagerDialog(catalogue_with_packs)

    dialog.table.selectRow(1)
    assert "Beta" in dialog.contents_label.text()
    assert dialog.contents_list.count() == 2
    assert dialog.edit_button.isEnabled()

    # Metadata is per-pack, so a multi-selection can't edit it -- but the
    # bulk actions stay available, which is the reason for multi-select.
    dialog.table.selectAll()
    assert not dialog.edit_button.isEnabled()
    assert dialog.remove_button.isEnabled()
    assert dialog.contents_list.count() == 0


def test_warns_when_a_packs_original_source_is_gone(qapp, catalogue_with_packs: Catalogue) -> None:
    """Re-ingest re-walks the pack's staged folder, so it can't run once
    that's been cleaned up. Said before the attempt, since the answer is
    "go find the file" rather than anything the app can do.
    """
    import shutil

    shutil.rmtree(catalogue_with_packs.staging_folder() / "Alpha")
    dialog = PackManagerDialog(catalogue_with_packs)

    dialog.table.selectRow(0)
    assert not dialog.reingest_notice.isHidden()
    assert "Alpha" in dialog.reingest_notice.text()

    dialog.table.selectRow(1)
    assert dialog.reingest_notice.isHidden()


def test_repointing_a_pack_updates_where_it_reads_from(catalogue_with_packs: Catalogue) -> None:
    """Asset paths resolve as staging/pack_folder/relative_path, so a
    re-ingest against a new source has to persist the new folder rather
    than use it once.
    """
    pack = catalogue_with_packs.list_pack_summaries()[0]
    assert catalogue_with_packs.pack_source_exists(pack["pack_folder"])

    catalogue_with_packs.update_pack_source_folder(pack["id"], "Beta")
    refreshed = {p["name"]: p for p in catalogue_with_packs.list_pack_summaries()}
    assert refreshed["Alpha"]["pack_folder"] == "Beta"
