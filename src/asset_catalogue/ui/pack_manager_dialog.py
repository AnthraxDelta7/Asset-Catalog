"""Manage every pack in one place: metadata, contents, hiding, removal.

The filter panel's pack list is a *filter* -- one click, one pack, get
back to the grid. This is the other job: seeing what a library actually
holds, and acting on packs in bulk. Keeping them separate is what lets
the filter list stay short (hidden packs drop out of it) without hiding
anything from the place you'd go looking for it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_PACK_ID_ROLE = Qt.ItemDataRole.UserRole
CONTENTS_ICON_SIZE = QSize(96, 96)


class PackManagerDialog(QDialog):
    def __init__(self, catalogue, primary_style: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Packs")
        self.resize(1040, 660)
        self._catalogue = catalogue
        self._summaries: list = []
        # Set when something changed that the main window's own pack list
        # and grid need to reflect -- a rename, a removal, a hide.
        self.changed = False

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Everything in the library. Select packs to hide them from the filter "
                "list, edit their credits, re-ingest to pick up changed files, or "
                "remove them entirely."
            )
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Pack", "Assets", "Models", "Creator", "Licence", "Hidden"]
        )
        # Multi-select, because the bulk actions (hide, remove) are the
        # reason to open this rather than the per-pack context menu that
        # already exists on the filter list.
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.table)

        contents_panel = QWidget()
        contents_layout = QVBoxLayout(contents_panel)
        contents_layout.setContentsMargins(0, 0, 0, 0)
        self.contents_label = QLabel("Select a pack to see what's in it")
        contents_layout.addWidget(self.contents_label)
        # Thumbnails come from the render that already happens at ingest,
        # so this is a cache read, not a render trigger -- opening this
        # dialog must never kick off Blender.
        self.contents_list = QListWidget()
        self.contents_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.contents_list.setIconSize(CONTENTS_ICON_SIZE)
        self.contents_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.contents_list.setMovement(QListWidget.Movement.Static)
        self.contents_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        contents_layout.addWidget(self.contents_list, stretch=1)
        splitter.addWidget(contents_panel)
        splitter.setSizes([560, 480])
        layout.addWidget(splitter, stretch=1)

        self.reingest_notice = QLabel("")
        self.reingest_notice.setWordWrap(True)
        self.reingest_notice.setStyleSheet("color: #e0b070;")
        self.reingest_notice.setVisible(False)
        layout.addWidget(self.reingest_notice)

        actions = QHBoxLayout()
        self.remove_button = QPushButton("Remove...")
        self.remove_button.setStyleSheet("QPushButton { color: #d98080; }")
        self.remove_button.clicked.connect(self._remove_selected)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)

        self.hide_button = QPushButton("Hide")
        self.hide_button.clicked.connect(lambda: self._set_hidden(True))
        actions.addWidget(self.hide_button)
        self.unhide_button = QPushButton("Unhide")
        self.unhide_button.clicked.connect(lambda: self._set_hidden(False))
        actions.addWidget(self.unhide_button)

        self.edit_button = QPushButton("Edit Metadata...")
        self.edit_button.clicked.connect(self._edit_selected)
        actions.addWidget(self.edit_button)

        self.reingest_button = QPushButton("Re-ingest")
        self.reingest_button.setStyleSheet(primary_style)
        self.reingest_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reingest_button.clicked.connect(self._reingest_selected)
        actions.addWidget(self.reingest_button)
        layout.addLayout(actions)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

        self.refresh()

    # -- listing -------------------------------------------------------

    def refresh(self) -> None:
        self._summaries = self._catalogue.list_pack_summaries()
        self.table.setRowCount(len(self._summaries))
        for row, pack in enumerate(self._summaries):
            name_item = QTableWidgetItem(pack["name"])
            name_item.setData(_PACK_ID_ROLE, pack["id"])
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(pack["asset_count"] or 0)))
            self.table.setItem(row, 2, QTableWidgetItem(str(pack["model_count"] or 0)))
            self.table.setItem(row, 3, QTableWidgetItem(pack["creator"] or ""))
            self.table.setItem(row, 4, QTableWidgetItem(pack["licence"] or ""))
            self.table.setItem(row, 5, QTableWidgetItem("Hidden" if pack["hidden"] else ""))
        self.table.resizeColumnsToContents()
        self._on_selection_changed()

    def selected_packs(self) -> list:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._summaries[row] for row in rows]

    def _on_selection_changed(self) -> None:
        selected = self.selected_packs()
        one = len(selected) == 1
        any_selected = bool(selected)
        self.remove_button.setEnabled(any_selected)
        self.hide_button.setEnabled(any_selected)
        self.unhide_button.setEnabled(any_selected)
        self.reingest_button.setEnabled(any_selected)
        # Metadata is per-pack: editing several at once would mean
        # deciding what a shared creator/licence even means.
        self.edit_button.setEnabled(one)
        self._show_contents(selected[0] if one else None)
        self._update_reingest_notice(selected)

    def _show_contents(self, pack) -> None:
        self.contents_list.clear()
        if pack is None:
            self.contents_label.setText("Select a pack to see what's in it")
            return
        assets = self._catalogue.list_assets(pack=pack["name"])
        self.contents_label.setText(f"{pack['name']} -- {len(assets)} asset(s)")
        for asset in assets:
            item = QListWidgetItem(asset.filename)
            path = self._catalogue.thumbnail_path_for(asset.content_hash)
            if path is not None:
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    item.setIcon(QIcon(pixmap))
            self.contents_list.addItem(item)

    def _update_reingest_notice(self, selected: list) -> None:
        missing = [
            pack["name"]
            for pack in selected
            if not self._catalogue.pack_source_exists(pack["pack_folder"])
        ]
        if not missing:
            self.reingest_notice.setVisible(False)
            return
        # Re-ingest re-walks the pack's original staged folder, so it
        # can't run at all once that's been cleaned up. Said here rather
        # than after the attempt, since the answer is "go find the file".
        self.reingest_notice.setText(
            "Re-ingest reads the pack's original folder in the staging area, and it's "
            f"no longer there for: {', '.join(missing[:4])}"
            f"{' and others' if len(missing) > 4 else ''}. Put it back, or use "
            "Re-ingest to pick a new source for a single pack."
        )
        self.reingest_notice.setVisible(True)

    # -- actions -------------------------------------------------------

    def _set_hidden(self, hidden: bool) -> None:
        selected = self.selected_packs()
        if not selected:
            return
        self._catalogue.set_packs_hidden([pack["id"] for pack in selected], hidden)
        self.changed = True
        self.refresh()

    def _edit_selected(self) -> None:
        selected = self.selected_packs()
        if len(selected) != 1:
            return
        if self._on_edit_pack is not None:
            self._on_edit_pack(selected[0]["name"])
            self.changed = True
            self.refresh()

    def _remove_selected(self) -> None:
        selected = self.selected_packs()
        if not selected:
            return
        names = ", ".join(pack["name"] for pack in selected[:5])
        more = f" and {len(selected) - 5} more" if len(selected) > 5 else ""
        confirm = QMessageBox.question(
            self,
            "Remove Packs",
            f"Remove {names}{more}?\n\n"
            "This deletes their catalogue entries, thumbnails and archived library "
            "copies. Files in the staging folder are never touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if self._on_remove_packs is not None:
            self._on_remove_packs([pack["name"] for pack in selected])
            self.changed = True
            self.refresh()

    def _reingest_selected(self) -> None:
        selected = self.selected_packs()
        if not selected or self._on_reingest_packs is None:
            return
        self._on_reingest_packs(selected)
        self.changed = True
        self.refresh()

    # Wired by the caller: everything that needs a background job or
    # another dialog stays in MainWindow, the same split every other
    # dialog here uses.
    _on_edit_pack = None
    _on_remove_packs = None
    _on_reingest_packs = None
