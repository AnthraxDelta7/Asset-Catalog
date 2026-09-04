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
