from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMessageBox, QWidget

from asset_catalogue.ui import commands
from asset_catalogue.ui.shortcuts_dialog import _ID_ROLE, ShortcutsDialog


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp) -> ShortcutsDialog:
    defaults = {command.id: command.default_shortcut for command in commands.COMMANDS}
    return ShortcutsDialog(defaults)


def _select(dialog: ShortcutsDialog, command_id: str) -> None:
    for index in range(dialog.tree.topLevelItemCount()):
        header = dialog.tree.topLevelItem(index)
        for child_index in range(header.childCount()):
            child = header.child(child_index)
            if child.data(0, _ID_ROLE) == command_id:
                dialog.tree.setCurrentItem(child)
                return
    raise AssertionError(f"no row for {command_id}")


def test_assigning_a_free_key_rebinds_only_that_command(dialog: ShortcutsDialog) -> None:
    _select(dialog, "asset.favorite")
    dialog.capture.setKeySequence(QKeySequence("Ctrl+Alt+V"))
    dialog._assign()

    assert dialog.sequences["asset.favorite"] == "Ctrl+Alt+V"
    assert dialog.sequences["export.dialog"] == "Ctrl+E"


def test_declining_a_conflict_changes_nothing(dialog: ShortcutsDialog) -> None:
    _select(dialog, "asset.favorite")
    dialog.capture.setKeySequence(QKeySequence("Ctrl+E"))
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        dialog._assign()

    assert dialog.sequences["asset.favorite"] == "F"
    assert dialog.sequences["export.dialog"] == "Ctrl+E"


def test_accepting_a_conflict_unbinds_the_other_command(dialog: ShortcutsDialog) -> None:
    """Two commands must never share a key: Qt resolves that
    unpredictably, so it surfaces as a key that only sometimes works --
    worse than the displaced command simply being unbound.
    """
    _select(dialog, "asset.favorite")
    dialog.capture.setKeySequence(QKeySequence("Ctrl+E"))
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog._assign()

    assert dialog.sequences["asset.favorite"] == "Ctrl+E"
    assert dialog.sequences["export.dialog"] == ""


def test_unbinding_leaves_the_command_without_a_key(dialog: ShortcutsDialog) -> None:
    _select(dialog, "asset.favorite")
    dialog._unbind()
    assert dialog.sequences["asset.favorite"] == ""


def test_reset_all_restores_every_default(dialog: ShortcutsDialog) -> None:
    _select(dialog, "asset.favorite")
    dialog.capture.setKeySequence(QKeySequence("Ctrl+Alt+V"))
    dialog._assign()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog._reset_all()

    assert dialog.sequences == {c.id: c.default_shortcut for c in commands.COMMANDS}


def test_only_changed_bindings_are_stored(qapp) -> None:
    """Storing the full mapping would freeze today's defaults into every
    user's settings, so a later improvement to a default would never
    reach anyone who had opened this dialog once.
    """
    registry = commands.CommandRegistry(QWidget())
    registry.build()
    sequences = {c.id: c.default_shortcut for c in commands.COMMANDS}
    sequences["asset.favorite"] = "Ctrl+Alt+V"

    assert registry.overrides_from(sequences) == {"asset.favorite": "Ctrl+Alt+V"}


def test_applying_overrides_updates_the_live_action(qapp) -> None:
    """Rebinding must take effect without a restart, and without
    rebuilding the actions -- that would drop every signal connection and
    every menu's reference to them.
    """
    registry = commands.CommandRegistry(QWidget())
    registry.build()
    action = registry.action("asset.favorite")
    fired = []
    registry.bind("asset.favorite", lambda: fired.append(1))

    registry.apply_shortcuts({"asset.favorite": "Ctrl+Alt+V"})

    assert action.shortcut().toString() == "Ctrl+Alt+V"
    assert registry.action("asset.favorite") is action
    action.trigger()
    assert fired == [1]
