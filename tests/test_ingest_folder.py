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
    # The label names the folder you are actually in, which is the only
    # thing saying you have left the ingest folder at all.
    assert dialog.location_label.text() == root.parent.name


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


def test_single_select_double_click_still_picks_a_zip_outright(qapp, tmp_path: Path) -> None:
    """The single-pack dialog's behaviour must not have moved."""
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "A.zip").write_bytes(b"PK\x03\x04")

    dialog = StagingBrowserDialog(root)
    dialog._on_item_double_clicked(dialog.list_widget.item(0))
    assert (dialog.selected_relative_path, dialog.selected_is_zip) == ("A.zip", True)


def test_go_to_folder_accepts_a_zip_and_lands_on_it(qapp, tmp_path: Path, monkeypatch) -> None:
    """Windows' shell folder picker shows folders and nothing else, so a
    downloads folder holding a hundred .zip files and no subfolders
    rendered as completely empty -- the honest conclusion from looking at
    it being that the app could not see zips at all.

    Qt's own dialog lists files and returns one, so a picked .zip means
    "go to its folder, with that zip ready to choose".
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QFileDialog

    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    downloads = tmp_path / "downloads"
    root.mkdir()
    downloads.mkdir()
    for name in ("alpha.zip", "beta.zip", "gamma.zip"):
        (downloads / name).write_bytes(b"PK\x03\x04")

    monkeypatch.setattr(QFileDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        QFileDialog, "selectedFiles", lambda self: [str(downloads / "beta.zip")]
    )

    dialog = StagingBrowserDialog(root, multi_select=True)
    dialog._browse_elsewhere()

    assert dialog._current_dir == downloads
    listed = [
        dialog.list_widget.item(i).text() for i in range(dialog.list_widget.count())
    ]
    assert listed == ["alpha.zip", "beta.zip", "gamma.zip"]
    # Landed on the one that was picked, rather than leaving it to be
    # hunted for in a long listing.
    assert dialog.list_widget.currentItem().text() == "beta.zip"

    dialog._select_current_folder()
    assert dialog.selected_items == [(str(downloads / "beta.zip"), True)]


def test_go_to_folder_still_just_navigates_when_a_folder_is_picked(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QDialog, QFileDialog

    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    (elsewhere / "Inner").mkdir(parents=True)

    monkeypatch.setattr(QFileDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(elsewhere)])

    dialog = StagingBrowserDialog(root)
    dialog._browse_elsewhere()
    assert dialog._current_dir == elsewhere
    assert dialog.list_widget.currentItem() is None


def test_cancelling_go_to_folder_changes_nothing(qapp, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog, QFileDialog

    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    monkeypatch.setattr(QFileDialog, "exec", lambda self: QDialog.Rejected)

    dialog = StagingBrowserDialog(root)
    dialog._browse_elsewhere()
    assert dialog._current_dir == root


def _browser_at_downloads(tmp_path: Path, monkeypatch, landed_on: str | None = None):
    """A batch browser showing four zips, optionally arrived at via
    Go to Folder with one of them picked.
    """
    from PySide6.QtWidgets import QDialog, QFileDialog

    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    downloads = tmp_path / "downloads"
    root.mkdir()
    downloads.mkdir()
    for name in ("alpha.zip", "beta.zip", "delta.zip", "gamma.zip"):
        (downloads / name).write_bytes(b"PK\x03\x04")

    dialog = StagingBrowserDialog(root, multi_select=True)
    target = downloads / landed_on if landed_on is not None else downloads
    monkeypatch.setattr(QFileDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(target)])
    dialog._browse_elsewhere()
    return dialog, downloads


def _picked(dialog) -> list[str]:
    dialog._select_current_folder()
    return [Path(path).name for path, _is_zip in dialog.selected_items]


def test_landing_on_a_zip_does_not_swallow_the_rest_of_the_selection(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    """Go to Folder highlights the zip it landed on. It must not do
    anything stickier than that: an earlier version ticked it, and
    selection preferred ticks over highlights, so that one tick silently
    discarded every row Ctrl/Shift-clicked afterwards -- the list looked
    right on screen and came back as a single item.
    """
    dialog, _downloads = _browser_at_downloads(tmp_path, monkeypatch, landed_on="alpha.zip")
    widget = dialog.list_widget
    assert widget.currentItem().text() == "alpha.zip"

    for row in (1, 2, 3):
        widget.item(row).setSelected(True)
    widget.item(0).setSelected(False)
    assert _picked(dialog) == ["beta.zip", "delta.zip", "gamma.zip"]


def test_highlighting_alone_still_selects_several(qapp, tmp_path: Path, monkeypatch) -> None:
    dialog, _downloads = _browser_at_downloads(tmp_path, monkeypatch)
    for row in (0, 1, 3):
        dialog.list_widget.item(row).setSelected(True)
    assert _picked(dialog) == ["alpha.zip", "beta.zip", "gamma.zip"]


def _plain_click(widget, row, modifier=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(
        widget.viewport(),
        Qt.LeftButton,
        modifier or Qt.NoModifier,
        widget.visualItemRect(widget.item(row)).center(),
    )


def _double_click(widget, row):
    """A faithful double-click: press, release, dbl-click, release.

    QTest.mouseDClick does not make QListWidget emit itemDoubleClicked at
    all, in any selection mode -- so a test built on it reports drilling
    into a folder as broken when it works perfectly.
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    point = QPointF(widget.visualItemRect(widget.item(row)).center())
    for kind in (
        QEvent.MouseButtonPress,
        QEvent.MouseButtonRelease,
        QEvent.MouseButtonDblClick,
        QEvent.MouseButtonRelease,
    ):
        QApplication.sendEvent(
            widget.viewport(),
            QMouseEvent(kind, point, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier),
        )
        QApplication.processEvents()


def test_plain_clicks_pick_several_packs(qapp, tmp_path: Path) -> None:
    """The batch browser used ExtendedSelection, where a plain click
    *replaces* the selection and adding requires holding Ctrl -- so
    clicking three packs in a row left one selected, which is exactly
    "multi-select does not work". Measured: three plain clicks give one
    item under Extended and three under MultiSelection.

    Driven with real mouse events on purpose. Setting isSelected() in a
    test passes under either mode and proves nothing about clicking.
    """
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    for name in ("a.zip", "b.zip", "c.zip", "d.zip"):
        (root / name).write_bytes(b"PK\x03\x04")

    dialog = StagingBrowserDialog(root, multi_select=True)
    dialog.show()
    widget = dialog.list_widget

    _plain_click(widget, 0)
    _plain_click(widget, 1)
    _plain_click(widget, 2)
    assert sorted(i.text() for i in widget.selectedItems()) == ["a.zip", "b.zip", "c.zip"]

    # Clicking a picked row again drops it.
    _plain_click(widget, 1)
    assert sorted(i.text() for i in widget.selectedItems()) == ["a.zip", "c.zip"]

    dialog._select_current_folder()
    assert [name for name, _is_zip in dialog.selected_items] == ["a.zip", "c.zip"]


def test_double_clicking_a_folder_still_opens_it(qapp, tmp_path: Path) -> None:
    """The risk of click-to-toggle: a double-click is two toggles. It must
    still drill in, and must not leave the folder selected behind it.
    """
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    (root / "Outer" / "Inner").mkdir(parents=True)
    (root / "Outer" / "pack.zip").write_bytes(b"PK\x03\x04")

    dialog = StagingBrowserDialog(root, multi_select=True)
    dialog.show()
    _double_click(dialog.list_widget, 0)

    assert dialog._current_dir == root / "Outer"
    assert [
        dialog.list_widget.item(i).text() for i in range(dialog.list_widget.count())
    ] == ["Inner", "pack.zip"]
    assert dialog.location_label.text() == "Outer"
    assert dialog.list_widget.selectedItems() == []


def test_the_picker_lists_files_folders_and_zips(qapp, tmp_path: Path) -> None:
    """ingest has treated a lone file as a pack of one since drag-and-drop
    landed. Only the picker would not show one, so a .glb sitting in a
    folder was invisible to the one dialog whose job is finding things to
    catalogue.
    """
    from PySide6.QtCore import Qt

    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "SomeFolder").mkdir()
    for name in (
        "Pack.zip", "prop.glb", "chair.fbx", "atlas.png", "hit.wav",
        "readme.txt", "notes.md", "project.unity",
    ):
        (root / name).write_bytes(b"x")

    dialog = StagingBrowserDialog(root, multi_select=True)
    listed = {
        dialog.list_widget.item(i).text(): dialog.list_widget.item(i).data(Qt.UserRole)[0]
        for i in range(dialog.list_widget.count())
    }
    assert listed == {
        "SomeFolder": "folder",
        "Pack.zip": "zip",
        "prop.glb": "file",
        "chair.fbx": "file",
        "atlas.png": "file",
        "hit.wav": "file",
    }
    # Filtered by the table ingest catalogues from, so the listing can
    # never offer something that would then be skipped as unrecognised.
    assert "readme.txt" not in listed
    assert "project.unity" not in listed


def test_a_folder_a_zip_and_a_file_can_be_picked_together(qapp, tmp_path: Path) -> None:
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "AFolder").mkdir()
    (root / "BPack.zip").write_bytes(b"PK\x03\x04")
    (root / "Cprop.glb").write_bytes(b"glb")

    dialog = StagingBrowserDialog(root, multi_select=True)
    dialog.show()
    for row in range(dialog.list_widget.count()):
        _plain_click(dialog.list_widget, row)

    dialog._select_current_folder()
    assert dialog.selected_items == [
        ("AFolder", False),
        ("BPack.zip", True),
        ("Cprop.glb", False),
    ]


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


def test_double_clicking_a_lone_file_picks_it_as_a_file_not_an_archive(
    qapp, tmp_path: Path
) -> None:
    """Saying a .glb was a zip would name the pack after the whole
    filename and offer the .zip-only Godot notice.
    """
    from asset_catalogue.ui.main_window import StagingBrowserDialog

    root = tmp_path / "ingest"
    root.mkdir()
    (root / "prop.glb").write_bytes(b"glb")

    dialog = StagingBrowserDialog(root)
    dialog._on_item_double_clicked(dialog.list_widget.item(0))
    assert dialog.selected_relative_path == "prop.glb"
    assert dialog.selected_is_zip is False
