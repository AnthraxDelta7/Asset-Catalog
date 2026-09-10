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


def test_hint_renders_in_the_menu_shortcut_column(qapp) -> None:
    """A context-menu entry advertises its key by putting it after a tab,
    which Qt right-aligns exactly where a real shortcut appears. It must
    NOT set a real shortcut on that second action -- two actions on one
    key is the ambiguity this whole design avoids.
    """
    from PySide6.QtWidgets import QWidget

    registry = commands.CommandRegistry(QWidget())
    registry.build()

    assert registry.hint("asset.favorite") == "\tF"
    assert registry.hint("export.dialog") == "\tCtrl+E"
    # A command with no key contributes nothing rather than a stray tab.
    assert registry.hint("tools.credits") == ""


def test_every_action_is_armed_on_its_parent_widget(qapp) -> None:
    """Parenting a QAction does not activate its shortcut.

    Qt matches a WindowShortcut against the widgets an action has been
    *added* to; the constructor's parent argument is ownership only. A
    command that appeared in a menu got added there and worked, so for a
    long time exactly the menu-bar commands responded to their key and
    every command reachable only from a context menu or a panel button
    silently did nothing -- 12 of the 23 with a default binding.
    """
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    registry = commands.CommandRegistry(parent)
    registry.build()

    armed = set(parent.actions())
    unarmed = [
        command.id
        for command in commands.COMMANDS
        if registry.action(command.id) not in armed
    ]
    assert unarmed == []


def test_every_default_shortcut_actually_fires_in_a_real_window(qapp, tmp_path, monkeypatch) -> None:
    """The structural check above can't see a shortcut Qt refuses to
    deliver, so this presses the keys.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    from PySide6.QtTest import QTest

    library, staging = tmp_path / "library", tmp_path / "staging"
    library.mkdir()
    staging.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")

    from asset_catalogue.ui.main_window import MainWindow

    window = MainWindow(Catalogue(conn, staging, library / "thumbnails", library / "assets"))
    window.show()
    window.activateWindow()
    if not window.isActiveWindow():
        conn.close()
        pytest.skip("window never became active; shortcuts cannot be delivered")

    fired: list[str] = []
    for command_id, action in window.commands.actions.items():
        # Several handlers open modal dialogs, which would block the test
        # rather than tell it anything.
        try:
            action.triggered.disconnect()
        except RuntimeError:
            pass
        action.triggered.connect(lambda _checked=False, c=command_id: fired.append(c))

    silent = []
    for command_id in window.commands.actions:
        sequence = window.commands.shortcut_of(command_id)
        if not sequence:
            continue
        combination = QKeySequence(sequence)[0]
        fired.clear()
        window.grid.setFocus()
        qapp.processEvents()
        QTest.keyClick(
            window,
            Qt.Key(combination.key().value),
            Qt.KeyboardModifier(combination.keyboardModifiers().value),
        )
        qapp.processEvents()
        if command_id not in fired:
            silent.append((command_id, sequence))

    conn.close()
    assert silent == []
