from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

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
    assert dialog.table.item(0, 5).text() == ""  # the "In list" column
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


def test_remove_hands_over_something_the_catalogue_can_actually_remove(
    qapp, catalogue_with_packs: Catalogue, monkeypatch
) -> None:
    """Removal is by pack id. An earlier version handed the callback pack
    *names*, which went straight into remove_pack_bg's pack_id parameter,
    matched no row, and returned "nothing removed" -- so deleting a pack
    silently did nothing, with no error raised and none logged.

    Asserting on the pack actually being gone rather than on the callback
    arguments: the bug was that the two ends disagreed about the type, so
    a test that mirrored either end's assumption would have passed.
    """
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    dialog = PackManagerDialog(catalogue_with_packs)

    removed_ids: list[int] = []

    def on_remove(packs) -> None:
        for pack in packs:
            stats = catalogue_with_packs.remove_pack_bg(pack["id"])
            assert stats.pack_removed, f"remove_pack_bg could not resolve {pack!r}"
            removed_ids.append(pack["id"])

    dialog._on_remove_packs = on_remove
    dialog.table.selectRow(0)
    dialog._remove_selected()

    assert len(removed_ids) == 1
    assert [pack["name"] for pack in catalogue_with_packs.list_pack_summaries()] == ["Beta"]


def test_remove_reports_packs_it_could_not_find(qapp, catalogue_with_packs: Catalogue) -> None:
    """The silent no-op is the failure mode worth guarding: removing a
    pack that isn't there returns empty stats rather than raising, so the
    only way anyone finds out is if the caller checks and says so.
    """
    stats = catalogue_with_packs.remove_pack_bg(999999)
    assert not stats.pack_removed
    assert stats.removed_assets == 0


def _catalogue_with_many_packs(tmp_path: Path, monkeypatch, count: int) -> Catalogue:
    library, staging = tmp_path / "library", tmp_path / "staging"
    library.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")
    for index in range(count):
        name = f"Pack{index:02d}"
        ingest.get_or_create_pack(conn, name, name, None, None, None)
    return Catalogue(conn, staging, library / "thumbnails", library / "assets")


def test_the_pack_list_shows_only_the_most_recent_handful(qapp, tmp_path: Path, monkeypatch) -> None:
    """A library of dozens turned the filter panel's list into a scroll
    box nobody read. It is a recents list now; everything else stays
    reachable through the search and the pack manager.
    """
    from asset_catalogue.ui.main_window import RECENT_PACK_LIMIT, FilterPanel

    catalogue = _catalogue_with_many_packs(tmp_path, monkeypatch, 30)
    panel = FilterPanel(catalogue, lambda: None, *[lambda *a: None] * 5)

    # Row 0 is "All packs".
    assert panel.pack_list.count() == RECENT_PACK_LIMIT + 1
    assert panel.pack_list.item(0).text() == "All packs"


def test_selecting_a_pack_brings_it_to_the_front_of_the_recents(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    from asset_catalogue.ui.main_window import RECENT_PACK_LIMIT, FilterPanel

    catalogue = _catalogue_with_many_packs(tmp_path, monkeypatch, 30)
    panel = FilterPanel(catalogue, lambda: None, *[lambda *a: None] * 5)

    stale = catalogue.list_packs()[0]
    assert stale not in catalogue.list_recent_packs(RECENT_PACK_LIMIT)
    catalogue.touch_pack(stale)
    assert catalogue.list_recent_packs(RECENT_PACK_LIMIT)[0] == stale


def test_searching_looks_past_the_cap(qapp, tmp_path: Path, monkeypatch) -> None:
    """The cap must never hide a pack from the tool meant to find it.
    Hiding rows was fine when the list held everything; with ten rows it
    would mean search could only find what was already on screen.
    """
    from asset_catalogue.ui.main_window import RECENT_PACK_LIMIT, FilterPanel

    catalogue = _catalogue_with_many_packs(tmp_path, monkeypatch, 30)
    panel = FilterPanel(catalogue, lambda: None, *[lambda *a: None] * 5)

    hidden_from_list = "Pack00"
    assert hidden_from_list not in catalogue.list_recent_packs(RECENT_PACK_LIMIT)
    assert not panel.pack_list.findItems(hidden_from_list, Qt.MatchExactly)

    panel.pack_search_edit.setText(hidden_from_list)
    assert panel.pack_list.findItems(hidden_from_list, Qt.MatchExactly)

    # Clearing puts the recents back.
    panel.pack_search_edit.setText("")
    assert panel.pack_list.count() == RECENT_PACK_LIMIT + 1


def test_the_selected_pack_is_never_dropped_from_the_list(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    """A list that dropped the pack currently filtering the grid would
    leave the user looking at a filtered view with nothing on screen
    explaining what filtered it.
    """
    from asset_catalogue.ui.main_window import FilterPanel

    catalogue = _catalogue_with_many_packs(tmp_path, monkeypatch, 30)
    panel = FilterPanel(catalogue, lambda: None, *[lambda *a: None] * 5)

    panel._populate_packs("Pack00")
    assert panel.selected_pack() == "Pack00"
    assert panel.pack_list.findItems("Pack00", Qt.MatchExactly)


def test_show_in_list_puts_a_pack_back_even_when_it_was_never_hidden(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    """Unhiding alone was not enough once the list became a recents list:
    a pack could have its hidden flag cleared and still not appear,
    because it was simply too old. "Show in List" marks it just-used too.
    """
    from asset_catalogue.ui.main_window import RECENT_PACK_LIMIT

    catalogue = _catalogue_with_many_packs(tmp_path, monkeypatch, 30)
    stale = "Pack00"
    assert stale not in catalogue.list_recent_packs(RECENT_PACK_LIMIT)

    dialog = PackManagerDialog(catalogue)
    row = next(
        index
        for index in range(dialog.table.rowCount())
        if dialog.table.item(index, 0).text() == stale
    )
    dialog.table.selectRow(row)
    dialog._set_hidden(False)

    assert catalogue.list_recent_packs(RECENT_PACK_LIMIT)[0] == stale
