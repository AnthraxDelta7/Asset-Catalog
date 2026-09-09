"""Rebinding UI for every command in ui/commands.py.

Edits a working copy and only hands it back on OK, so Cancel genuinely
cancels -- important when a mis-keyed binding could otherwise leave a
command unreachable with no obvious way back.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from asset_catalogue.ui.commands import COMMANDS, is_bare_key

_ID_ROLE = Qt.ItemDataRole.UserRole


class ShortcutsDialog(QDialog):
    def __init__(self, sequences: dict[str, str], parent=None) -> None:
        """`sequences` is the full command id -> key mapping currently in
        effect (defaults already merged with the user's overrides), not
        just the overrides -- the dialog shows what the keys *are*, and
        works out which of them are non-default when handing results back.
        """
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(620, 640)
        self.sequences = dict(sequences)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Select a command and press the key combination you want. "
                "A key with no modifier (F, Space) is ignored while you're "
                "typing in a text box, so it can't fire mid-word."
            )
        )

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Command", "Shortcut"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(12)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree, stretch=1)
        self._populate()
        self.tree.currentItemChanged.connect(self._on_selection_changed)

        capture_row = QHBoxLayout()
        capture_row.addWidget(QLabel("New shortcut:"))
        self.capture = QKeySequenceEdit()
        # One combination, not a chord: Qt's default records up to four
        # in sequence, so pressing Ctrl+E then wandering onto another key
        # would silently bind "Ctrl+E, X".
        self.capture.setMaximumSequenceLength(1)
        self.capture.setEnabled(False)
        capture_row.addWidget(self.capture, stretch=1)
        self.assign_button = QPushButton("Assign")
        self.assign_button.clicked.connect(self._assign)
        self.assign_button.setEnabled(False)
        capture_row.addWidget(self.assign_button)
        self.clear_button = QPushButton("Unbind")
        self.clear_button.clicked.connect(self._unbind)
        self.clear_button.setEnabled(False)
        capture_row.addWidget(self.clear_button)
        layout.addLayout(capture_row)

        bottom = QHBoxLayout()
        reset_all = QPushButton("Reset All to Defaults")
        reset_all.clicked.connect(self._reset_all)
        bottom.addWidget(reset_all)
        bottom.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

    def _populate(self) -> None:
        self.tree.clear()
        by_category: dict[str, QTreeWidgetItem] = {}
        for command in COMMANDS:
            if command.category not in by_category:
                header = QTreeWidgetItem([command.category, ""])
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)
                font = header.font(0)
                font.setBold(True)
                header.setFont(0, font)
                self.tree.addTopLevelItem(header)
                header.setExpanded(True)
                by_category[command.category] = header
            item = QTreeWidgetItem([command.label, self._display(command.id)])
            item.setData(0, _ID_ROLE, command.id)
            by_category[command.category].addChild(item)

    def _display(self, command_id: str) -> str:
        sequence = self.sequences.get(command_id, "")
        return QKeySequence(sequence).toString() if sequence else "—"

    def _current_id(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, _ID_ROLE) if item is not None else None

    def _on_selection_changed(self) -> None:
        command_id = self._current_id()
        enabled = command_id is not None
        self.capture.setEnabled(enabled)
        self.assign_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        if enabled:
            self.capture.setKeySequence(QKeySequence(self.sequences.get(command_id, "")))

    def _refresh_row(self, command_id: str) -> None:
        for index in range(self.tree.topLevelItemCount()):
            header = self.tree.topLevelItem(index)
            for child_index in range(header.childCount()):
                child = header.child(child_index)
                if child.data(0, _ID_ROLE) == command_id:
                    child.setText(1, self._display(command_id))
                    return

    def _assign(self) -> None:
        command_id = self._current_id()
        if command_id is None:
            return
        sequence = self.capture.keySequence().toString()
        if not sequence:
            self._unbind()
            return

        clash = next(
            (
                other
                for other, existing in self.sequences.items()
                if other != command_id and existing and QKeySequence(existing).toString() == sequence
            ),
            None,
        )
        if clash is not None:
            label = next((c.label for c in COMMANDS if c.id == clash), clash)
            answer = QMessageBox.question(
                self,
                "Shortcut Already Used",
                f'"{sequence}" is already assigned to "{label}".\n\n'
                "Reassign it to this command instead? The other command will "
                "be left unbound.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            # Never leave two commands on one key: Qt picks between them
            # unpredictably, which shows up as a key that works only
            # sometimes -- far more confusing than one being unbound.
            self.sequences[clash] = ""
            self._refresh_row(clash)

        self.sequences[command_id] = sequence
        self._refresh_row(command_id)

    def _unbind(self) -> None:
        command_id = self._current_id()
        if command_id is None:
            return
        self.sequences[command_id] = ""
        self.capture.clear()
        self._refresh_row(command_id)

    def _reset_all(self) -> None:
        if QMessageBox.question(
            self,
            "Reset Shortcuts",
            "Put every shortcut back to its default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.sequences = {command.id: command.default_shortcut for command in COMMANDS}
        self._populate()

    def bare_key_count(self) -> int:
        """How many bindings are modifier-less, and so inactive while
        typing. Surfaced for tests and for anyone wondering why a key
        does nothing in the search box.
        """
        return sum(1 for sequence in self.sequences.values() if is_bare_key(sequence))
