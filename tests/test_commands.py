from __future__ import annotations

from pathlib import Path

import pytest

from asset_catalogue import db, settings
from asset_catalogue.catalogue import Catalogue
from asset_catalogue.ui import commands


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_command_ids_are_unique_and_stable() -> None:
    """Ids are what a user's saved keybinding refers to -- a duplicate
    would mean one command silently shadowing another's binding.
    """
    ids = [command.id for command in commands.COMMANDS]
    assert len(ids) == len(set(ids))
    assert all(command.label for command in commands.COMMANDS)


def test_bare_keys_are_told_apart_from_modifier_shortcuts() -> None:
    assert commands.is_bare_key("F") is True
    assert commands.is_bare_key("Space") is True
    assert commands.is_bare_key("Del") is True
    assert commands.is_bare_key("Ctrl+E") is False
    assert commands.is_bare_key("Ctrl+Shift+R") is False
    # Unbound is not a bare key -- it must not be "suppressed" into
    # something, or restoring focus would give it a shortcut it never had.
    assert commands.is_bare_key("") is False


def test_no_two_commands_share_a_shortcut(qapp) -> None:
    """Two actions on one key is silently ambiguous in Qt -- whichever it
    reaches first wins -- so a collision has to fail here rather than be
    discovered as a key that only sometimes works.
    """
    from PySide6.QtWidgets import QWidget

    registry = commands.CommandRegistry(QWidget())
    registry.build()
    assert registry.conflicts() == {}


def test_text_focus_suppresses_only_bare_keys(qapp) -> None:
    from PySide6.QtWidgets import QWidget

    registry = commands.CommandRegistry(QWidget())
    registry.build()
    favorite = registry.action("asset.favorite")
    export = registry.action("export.dialog")
    assert favorite.shortcut().toString() == "F"

    registry.set_text_focus(True)
    # Typing "f" in the search box must reach the box, not toggle favorite.
    assert favorite.shortcut().toString() == ""
    assert export.shortcut().toString() == "Ctrl+E"

    registry.set_text_focus(False)
    assert favorite.shortcut().toString() == "F"


def test_user_overrides_replace_defaults(qapp) -> None:
    from PySide6.QtWidgets import QWidget

    registry = commands.CommandRegistry(QWidget())
    registry.build({"asset.favorite": "Ctrl+D"})

    assert registry.action("asset.favorite").shortcut().toString() == "Ctrl+D"
    # An override that adds a modifier stops it being suppressed by typing.
    registry.set_text_focus(True)
    assert registry.action("asset.favorite").shortcut().toString() == "Ctrl+D"
    # Untouched commands keep their declared default.
    assert registry.action("export.dialog").shortcut().toString() == "Ctrl+E"


def test_every_shortcut_actually_does_something(qapp, tmp_path: Path, monkeypatch) -> None:
    """A key that fires nothing is worse than an unbound one -- it looks
    broken. Every command carrying a shortcut must have a handler
    connected by the time MainWindow is built.
    """
    from asset_catalogue.ui.main_window import MainWindow

    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    library, staging = tmp_path / "library", tmp_path / "staging"
    library.mkdir()
    staging.mkdir()
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")
    window = MainWindow(Catalogue(conn, staging, library / "thumbnails", library / "assets"))

    assert window.commands.unbound_shortcuts() == []
    window.close()
    conn.close()
