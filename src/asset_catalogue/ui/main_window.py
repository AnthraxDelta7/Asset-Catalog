from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from asset_catalogue import blender_render, settings
from asset_catalogue.catalogue import AssetSummary, Catalogue

THUMBNAIL_ICON_SIZE = QSize(128, 128)


class FilterPanel(QWidget):
    def __init__(self, catalogue: Catalogue, on_change) -> None:
        super().__init__()
        self._on_change = on_change

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Type"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types", None)
        for asset_type in catalogue.list_asset_types():
            self.type_combo.addItem(asset_type, asset_type)
        self.type_combo.currentIndexChanged.connect(self._on_change)
        layout.addWidget(self.type_combo)

        layout.addWidget(QLabel("Pack"))
        self.pack_list = QListWidget()
        self.pack_list.addItem("All packs")
        self.pack_list.addItems(catalogue.list_packs())
        self.pack_list.setCurrentRow(0)
        self.pack_list.currentRowChanged.connect(self._on_change)
        layout.addWidget(self.pack_list, stretch=1)

        layout.addWidget(QLabel("Tags"))
        self.tag_list = QListWidget()
        self.tag_list.addItem("All tags")
        for tag in catalogue.list_tags():
            label = f"{tag.name} ({tag.usage_count})"
            if tag.category:
                label = f"{tag.name} [{tag.category}] ({tag.usage_count})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, tag.name)
            self.tag_list.addItem(item)
        self.tag_list.setCurrentRow(0)
        self.tag_list.currentRowChanged.connect(self._on_change)
        layout.addWidget(self.tag_list, stretch=1)

    def selected_type(self) -> str | None:
        return self.type_combo.currentData()

    def selected_pack(self) -> str | None:
        row = self.pack_list.currentRow()
        return None if row <= 0 else self.pack_list.item(row).text()

    def selected_tag(self) -> str | None:
        row = self.tag_list.currentRow()
        if row <= 0:
            return None
        return self.tag_list.item(row).data(Qt.UserRole)

    def refresh_tags(self, catalogue: Catalogue) -> None:
        previous = self.selected_tag()
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        self.tag_list.addItem("All tags")
        restore_row = 0
        for tag in catalogue.list_tags():
            label = f"{tag.name} ({tag.usage_count})"
            if tag.category:
                label = f"{tag.name} [{tag.category}] ({tag.usage_count})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, tag.name)
            self.tag_list.addItem(item)
            if tag.name == previous:
                restore_row = self.tag_list.count() - 1
        self.tag_list.setCurrentRow(restore_row)
        self.tag_list.blockSignals(False)


class ThumbnailGrid(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(THUMBNAIL_ICON_SIZE)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSpacing(8)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setGridSize(
            QSize(THUMBNAIL_ICON_SIZE.width() + 16, THUMBNAIL_ICON_SIZE.height() + 48)
        )
        # Ctrl/Shift-click and Ctrl+A ("select all") for bulk removal.
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def set_assets(self, assets: list[AssetSummary], catalogue: Catalogue) -> None:
        # Rebuilding the item list can make Qt pick its own new "current"
        # item as items are cleared/added; block signals so that transient
        # churn doesn't reach the selection handler, and restore the real
        # selection explicitly afterward (see select_asset_id).
        self.blockSignals(True)
        self.clear()
        for asset in assets:
            item = QListWidgetItem(asset.filename)
            item.setData(Qt.UserRole, asset.id)
            item.setIcon(QIcon(self._load_thumbnail(asset, catalogue)))
            item.setToolTip(f"{asset.pack_name} / {asset.filename}\n{asset.asset_type}")
            self.addItem(item)
        self.blockSignals(False)

    def select_asset_id(self, asset_id: int | None) -> None:
        if asset_id is not None:
            for row in range(self.count()):
                item = self.item(row)
                if item.data(Qt.UserRole) == asset_id:
                    self.setCurrentItem(item)
                    return
        self.setCurrentItem(None)

    def _load_thumbnail(self, asset: AssetSummary, catalogue: Catalogue) -> QPixmap:
        path = catalogue.thumbnail_path_for(asset.content_hash)
        if path is not None:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap.scaled(
                    THUMBNAIL_ICON_SIZE,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
        placeholder = QPixmap(THUMBNAIL_ICON_SIZE)
        placeholder.fill(Qt.darkGray)
        return placeholder


class DetailPanel(QWidget):
    """Single-asset mode (exactly one grid selection): full tag editing plus
    a link to the asset's archived copy in the library. Multi-select mode
    (2+ selected): tag list and "show in library" are disabled (ambiguous
    for a set of different assets), but adding a tag still works -- it's
    applied to every selected asset at once. All actual catalogue writes are
    delegated to MainWindow via callbacks rather than done here directly,
    since MainWindow decides whether a write needs to run in the background
    (a bulk tag can trigger many file copies) or can stay fast/synchronous
    (a single tag write, archived quietly afterward).
    """

    def __init__(
        self,
        catalogue: Catalogue,
        on_tag_asset,
        on_untag_asset,
        on_bulk_tag_assets,
        on_show_in_library,
    ) -> None:
        super().__init__()
        self._catalogue = catalogue
        self._on_tag_asset = on_tag_asset
        self._on_untag_asset = on_untag_asset
        self._on_bulk_tag_assets = on_bulk_tag_assets
        self._on_show_in_library = on_show_in_library
        self._asset_id: int | None = None
        self._current_asset: AssetSummary | None = None
        self._multi_asset_ids: list[int] = []

        layout = QVBoxLayout(self)
        self.title_label = QLabel("No asset selected")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.meta_label = QLabel("")
        layout.addWidget(self.meta_label)

        self.show_in_library_button = QPushButton("Show in Library Folder")
        self.show_in_library_button.clicked.connect(self._show_in_library)
        layout.addWidget(self.show_in_library_button)

        layout.addWidget(QLabel("Tags"))
        self.tag_list = QListWidget()
        layout.addWidget(self.tag_list, stretch=1)

        remove_row = QHBoxLayout()
        self.remove_button = QPushButton("Remove selected tag")
        self.remove_button.clicked.connect(self._remove_selected_tag)
        remove_row.addWidget(self.remove_button)
        layout.addLayout(remove_row)

        add_row = QHBoxLayout()
        self.new_tag_input = QLineEdit()
        self.new_tag_input.setPlaceholderText("New tag name")
        self.new_tag_input.returnPressed.connect(self._add_tag)
        self.add_button = QPushButton("Add tag")
        self.add_button.clicked.connect(self._add_tag)
        add_row.addWidget(self.new_tag_input)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)

        self._set_idle_state()

    def set_catalogue(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue

    def _set_idle_state(self) -> None:
        self.tag_list.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.new_tag_input.setEnabled(False)
        self.add_button.setEnabled(False)
        self.show_in_library_button.setEnabled(False)

    def clear_selection(self) -> None:
        self._asset_id = None
        self._current_asset = None
        self._multi_asset_ids = []
        self.title_label.setText("No asset selected")
        self.meta_label.setText("")
        self.tag_list.clear()
        self._set_idle_state()

    def show_multi_selection(self, asset_ids: list[int]) -> None:
        self._asset_id = None
        self._current_asset = None
        self._multi_asset_ids = asset_ids
        self.title_label.setText(f"{len(asset_ids)} assets selected")
        self.meta_label.setText("Add a tag to apply it to all selected assets.")
        self.tag_list.clear()
        self.tag_list.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.new_tag_input.setEnabled(True)
        self.add_button.setEnabled(True)
        self.show_in_library_button.setEnabled(False)

    def show_asset(self, asset: AssetSummary) -> None:
        self._asset_id = asset.id
        self._current_asset = asset
        self._multi_asset_ids = []
        self.title_label.setText(f"{asset.pack_name} / {asset.filename}")
        self.meta_label.setText(f"type: {asset.asset_type}   thumbnail: {asset.thumbnail_status}")
        self.tag_list.clear()
        self.tag_list.addItems(asset.tags)
        self.tag_list.setEnabled(True)
        self.remove_button.setEnabled(True)
        self.new_tag_input.setEnabled(True)
        self.add_button.setEnabled(True)
        archived = self._catalogue.library_asset_path_if_archived(
            asset.pack_name, asset.relative_path
        )
        self.show_in_library_button.setEnabled(archived is not None)

    def _add_tag(self) -> None:
        name = self.new_tag_input.text().strip()
        if not name:
            return
        self.new_tag_input.clear()
        if self._asset_id is not None:
            self._on_tag_asset(self._asset_id, name)
        elif self._multi_asset_ids:
            self._on_bulk_tag_assets(list(self._multi_asset_ids), name)

    def _remove_selected_tag(self) -> None:
        item = self.tag_list.currentItem()
        if item is None or self._asset_id is None:
            return
        self._on_untag_asset(self._asset_id, item.text())

    def _show_in_library(self) -> None:
        if self._current_asset is None:
            return
        self._on_show_in_library(self._current_asset.pack_name, self._current_asset.relative_path)


def _browse_row(edit: QLineEdit, on_browse) -> QHBoxLayout:
    row = QHBoxLayout()
    button = QPushButton("Browse...")
    button.clicked.connect(on_browse)
    row.addWidget(edit)
    row.addWidget(button)
    return row


class SettingsDialog(QDialog):
    """Shown on first launch (no library folder configured yet) and from
    File > Settings afterward. Talks to blender_render.find_blender directly
    rather than through Catalogue -- there may be no valid Catalogue yet
    (that's the whole point of this dialog), and detecting a Blender install
    touches the environment, not catalogue data.
    """

    def __init__(self, parent: QWidget | None = None, error_message: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(520, 260)
        s = settings.load()

        layout = QVBoxLayout(self)

        if error_message:
            error_label = QLabel(f"Could not open the library: {error_message}")
            error_label.setWordWrap(True)
            error_label.setStyleSheet("color: #d64545; font-weight: bold;")
            layout.addWidget(error_label)

        form = QFormLayout()

        self.staging_edit = QLineEdit(s.staging_folder or "")
        form.addRow("Staging folder:", _browse_row(self.staging_edit, self._browse_staging))

        self.library_edit = QLineEdit(s.library_folder or "")
        form.addRow("Library folder:", _browse_row(self.library_edit, self._browse_library))

        self.blender_edit = QLineEdit(s.blender_path or "")
        blender_row = QHBoxLayout()
        blender_browse = QPushButton("Browse...")
        blender_browse.clicked.connect(self._browse_blender)
        blender_auto = QPushButton("Auto-detect")
        blender_auto.clicked.connect(self._auto_detect_blender)
        blender_row.addWidget(self.blender_edit)
        blender_row.addWidget(blender_browse)
        blender_row.addWidget(blender_auto)
        form.addRow("Blender path:", blender_row)

        layout.addLayout(form)

        hint = QLabel(
            "Staging folder: where unprocessed packs sit before ingest.\n"
            "Library folder: portable -- holds catalogue.db and thumbnails/. Point at an "
            "existing one (copied from another machine, a shared drive) to pick it up as-is."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_staging(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Select staging folder", self.staging_edit.text()
        )
        if chosen:
            self.staging_edit.setText(chosen)

    def _browse_library(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Select library folder", self.library_edit.text()
        )
        if chosen:
            self.library_edit.setText(chosen)

    def _browse_blender(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select blender.exe", self.blender_edit.text(), "Blender (blender.exe);;All files (*)"
        )
        if chosen:
            self.blender_edit.setText(chosen)

    def _auto_detect_blender(self) -> None:
        found = blender_render.find_blender(None)
        if found is None:
            QMessageBox.warning(self, "Settings", "Could not auto-detect Blender.")
            return
        self.blender_edit.setText(str(found))

    def _on_accept(self) -> None:
        if not self.staging_edit.text().strip() or not self.library_edit.text().strip():
            QMessageBox.warning(self, "Settings", "Staging folder and library folder are both required.")
            return
        s = settings.load()
        s.staging_folder = self.staging_edit.text().strip()
        s.library_folder = self.library_edit.text().strip()
        s.blender_path = self.blender_edit.text().strip() or None
        settings.save(s)
        self.accept()


class StagingBrowserDialog(QDialog):
    """A small custom folder browser scoped to the staging folder, showing
    subfolders AND .zip files side by side as selectable pack sources.

    This exists because a native QFileDialog can't do this: it's either a
    folder picker or a file picker, never both -- Directory mode's own
    selection model treats files as inert even when they're shown in the
    listing (verified rather than assumed: selectFile() on a .zip in
    Directory mode reports it as "selected" programmatically, but that's
    not the same as a real double-click doing anything sane in the actual
    widget, which is the documented, known-flaky part). Building a small
    dedicated browser sidesteps relying on that quirky, undocumented corner
    of the native widget's behavior.
    """

    def __init__(self, staging_folder: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Pack")
        self.resize(480, 420)
        self._staging_folder = staging_folder
        self._current_dir = staging_folder

        self.selected_relative_path: str | None = None
        self.selected_is_zip: bool = False

        layout = QVBoxLayout(self)

        nav_row = QHBoxLayout()
        self.up_button = QPushButton("Up")
        self.up_button.clicked.connect(self._go_up)
        self.location_label = QLabel()
        self.location_label.setWordWrap(True)
        nav_row.addWidget(self.up_button)
        nav_row.addWidget(self.location_label, stretch=1)
        layout.addLayout(nav_row)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, stretch=1)

        hint = QLabel(
            "Double-click a folder to open it, or a .zip to select it directly. "
            'Use "Select This Folder" to pick the folder you\'re currently browsing.'
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        button_row = QHBoxLayout()
        select_folder_button = QPushButton("Select This Folder")
        select_folder_button.clicked.connect(self._select_current_folder)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(select_folder_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        self._refresh_listing()

    def _refresh_listing(self) -> None:
        self.list_widget.clear()
        relative = self._current_dir.relative_to(self._staging_folder)
        self.location_label.setText(
            "Staging (root)" if str(relative) == "." else f"Staging / {relative}"
        )
        self.up_button.setEnabled(self._current_dir != self._staging_folder)

        style = self.style()
        try:
            entries = sorted(
                self._current_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except OSError:
            entries = []

        for entry in entries:
            if entry.is_dir():
                item = QListWidgetItem(style.standardIcon(QStyle.SP_DirIcon), entry.name)
                item.setData(Qt.UserRole, ("folder", entry))
                self.list_widget.addItem(item)
            elif entry.is_file() and entry.suffix.lower() == ".zip":
                item = QListWidgetItem(style.standardIcon(QStyle.SP_FileIcon), entry.name)
                item.setData(Qt.UserRole, ("zip", entry))
                self.list_widget.addItem(item)

    def _go_up(self) -> None:
        if self._current_dir == self._staging_folder:
            return
        self._current_dir = self._current_dir.parent
        self._refresh_listing()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        kind, path = item.data(Qt.UserRole)
        if kind == "folder":
            self._current_dir = path
            self._refresh_listing()
        else:
            self.selected_relative_path = str(path.relative_to(self._staging_folder))
            self.selected_is_zip = True
            self.accept()

    def _select_current_folder(self) -> None:
        self.selected_relative_path = str(self._current_dir.relative_to(self._staging_folder))
        self.selected_is_zip = False
        self.accept()


class IngestDialog(QDialog):
    """One dialog for both pack sources. "Browse Staging..." opens the
    custom StagingBrowserDialog above, which shows folders and .zip files
    together (a zip found there is auto-extracted at ingest time -- see
    ingest.py's ingest_pack). "Browse Zip..." is for a zip that lives
    *outside* the staging folder (e.g. still in Downloads) and needs to be
    brought in -- a genuinely different case, since there's no
    staging-relative path to derive a destination folder name from, so it
    stays a native file picker with an editable destination field.
    """

    def __init__(self, catalogue: Catalogue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalogue = catalogue
        self.setWindowTitle("Ingest Pack")
        self.resize(480, 300)

        self._source_kind: str | None = None  # "folder" or "zip"
        self._zip_path: Path | None = None

        self.pack_folder_name: str = ""
        self.pack_name: str = ""
        self.creator: str | None = None
        self.licence: str | None = None
        self.source_url: str | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        source_row = QHBoxLayout()
        browse_folder_button = QPushButton("Browse Staging...")
        browse_folder_button.clicked.connect(self._browse_staging)
        browse_zip_button = QPushButton("Browse Zip...")
        browse_zip_button.clicked.connect(self._browse_zip)
        source_row.addWidget(self.source_edit)
        source_row.addWidget(browse_folder_button)
        source_row.addWidget(browse_zip_button)
        form.addRow("Pack source:", source_row)

        self.dest_folder_edit = QLineEdit()
        self.dest_folder_edit.setEnabled(False)
        form.addRow("Extract to (folder name):", self.dest_folder_edit)

        self.pack_name_edit = QLineEdit()
        form.addRow("Pack name:", self.pack_name_edit)
        self.creator_edit = QLineEdit()
        form.addRow("Creator:", self.creator_edit)
        self.licence_edit = QLineEdit()
        form.addRow("Licence:", self.licence_edit)
        self.source_url_edit = QLineEdit()
        form.addRow("Source URL:", self.source_url_edit)

        layout.addLayout(form)

        hint = QLabel(
            "Browse Staging: pick a folder or a .zip from inside the staging folder -- "
            "either one, side by side.\n"
            "Browse Zip: pick a .zip anywhere else on disk -- it's extracted into the "
            "staging folder first, using the folder name above."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Ingest")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def is_zip_source(self) -> bool:
        return self._source_kind == "zip"

    @property
    def zip_path(self) -> Path | None:
        return self._zip_path

    def _browse_staging(self) -> None:
        staging_folder = self._catalogue.staging_folder()
        if staging_folder is None:
            QMessageBox.warning(self, "Ingest Pack", "No staging folder configured.")
            return
        browser = StagingBrowserDialog(staging_folder, self)
        if browser.exec() != QDialog.Accepted or browser.selected_relative_path is None:
            return

        relative_path = browser.selected_relative_path
        # A .zip picked here is passed through as-is (e.g. "PackB.zip") --
        # ingest_pack_bg already auto-detects and extracts a pack_folder_name
        # that turns out to be a zip sitting in staging, the same mechanism
        # that already backs plain `ingest` in the CLI. No special-casing
        # needed here; this path is identical to a folder selection.
        self._source_kind = "folder"
        self._zip_path = None
        self.source_edit.setText(relative_path)
        self.dest_folder_edit.setText(relative_path)
        self.dest_folder_edit.setEnabled(False)
        if not self.pack_name_edit.text():
            name = Path(relative_path)
            self.pack_name_edit.setText(name.stem if browser.selected_is_zip else name.name)

    def _browse_zip(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Select zip file", "", "Zip archives (*.zip)")
        if not chosen:
            return
        path = Path(chosen)
        self._source_kind = "zip"
        self._zip_path = path
        self.source_edit.setText(str(path))
        self.dest_folder_edit.setEnabled(True)
        if not self.dest_folder_edit.text():
            self.dest_folder_edit.setText(path.stem)
        if not self.pack_name_edit.text():
            self.pack_name_edit.setText(path.stem)

    def _on_accept(self) -> None:
        pack_name = self.pack_name_edit.text().strip()
        dest_folder = self.dest_folder_edit.text().strip()
        if self._source_kind is None or not dest_folder or not pack_name:
            QMessageBox.warning(
                self, "Ingest Pack", "Pick a pack folder or zip file, and enter a pack name."
            )
            return
        self.pack_folder_name = dest_folder
        self.pack_name = pack_name
        self.creator = self.creator_edit.text().strip() or None
        self.licence = self.licence_edit.text().strip() or None
        self.source_url = self.source_url_edit.text().strip() or None
        self.accept()


class TagPackDialog(QDialog):
    def __init__(
        self, catalogue: Catalogue, initial_pack: str | None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tag Pack")
        self.resize(380, 200)

        self.pack_name: str = ""
        self.tag_name: str = ""
        self.category: str | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.pack_combo = QComboBox()
        packs = catalogue.list_packs()
        self.pack_combo.addItems(packs)
        if initial_pack and initial_pack in packs:
            self.pack_combo.setCurrentText(initial_pack)
        form.addRow("Pack:", self.pack_combo)

        self.tag_name_edit = QLineEdit()
        form.addRow("Tag name:", self.tag_name_edit)
        self.category_edit = QLineEdit()
        form.addRow("Category (optional):", self.category_edit)

        layout.addLayout(form)

        hint = QLabel(
            "Applies the tag to every asset currently in the pack. Safe to re-run "
            "later -- never touches assets that were tagged explicitly."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Tag Pack")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        pack_name = self.pack_combo.currentText().strip()
        tag_name = self.tag_name_edit.text().strip()
        if not pack_name or not tag_name:
            QMessageBox.warning(self, "Tag Pack", "Pick a pack and enter a tag name.")
            return
        self.pack_name = pack_name
        self.tag_name = tag_name
        self.category = self.category_edit.text().strip() or None
        self.accept()


class _BackgroundWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 -- reported to the UI, not swallowed
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(result)


class MainWindow(QMainWindow):
    def __init__(self, catalogue: Catalogue) -> None:
        super().__init__()
        self._catalogue = catalogue
        self._current_assets: list[AssetSummary] = []
        self._selected_asset_id: int | None = None
        self._active_worker: _BackgroundWorker | None = None
        self.resize(1100, 700)
        self._update_window_title()

        self._build_menu()
        self._build_toolbar()

        self.filter_panel = FilterPanel(catalogue, self._refresh_grid)
        self.grid = ThumbnailGrid()
        self.grid.itemSelectionChanged.connect(self._on_grid_selection_changed)
        self.grid.customContextMenuRequested.connect(self._show_grid_context_menu)
        self.detail_panel = DetailPanel(
            catalogue,
            self._handle_tag_asset,
            self._handle_untag_asset,
            self._handle_bulk_tag_assets,
            self._show_in_library_folder,
        )

        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.addWidget(self.grid)
        right_splitter.addWidget(self.detail_panel)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(self.filter_panel)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 3)

        self.setCentralWidget(main_splitter)
        self._refresh_grid()

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        settings_action = file_menu.addAction("Settings...")
        settings_action.triggered.connect(self._open_settings_dialog)
        switch_library_action = file_menu.addAction("Switch Library...")
        switch_library_action.triggered.connect(self._switch_library)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("Exit")
        exit_action.triggered.connect(self.close)

        edit_menu = menu_bar.addMenu("&Edit")
        select_all_action = edit_menu.addAction("Select All")
        select_all_action.setShortcut(QKeySequence.SelectAll)
        # self.grid doesn't exist yet at this point in __init__ -- a lambda
        # defers the lookup to trigger-time instead of connect-time.
        select_all_action.triggered.connect(lambda: self.grid.selectAll())
        remove_action = edit_menu.addAction("Remove Selected...")
        remove_action.setShortcut(QKeySequence.Delete)
        remove_action.triggered.connect(self._remove_selected_assets)
        edit_menu.addSeparator()
        tag_pack_action = edit_menu.addAction("Tag Pack...")
        tag_pack_action.triggered.connect(self._open_tag_pack_dialog)

        thumbnails_menu = menu_bar.addMenu("&Thumbnails")
        gen_2d_action = thumbnails_menu.addAction("Generate 2D Thumbnails (current pack filter)")
        gen_2d_action.triggered.connect(self._generate_2d_thumbnails)
        gen_3d_action = thumbnails_menu.addAction(
            "Generate 3D Thumbnails via Blender (current pack filter)"
        )
        gen_3d_action.triggered.connect(self._generate_model_thumbnails)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Ingest")
        toolbar.setMovable(False)

        # A QAction on a toolbar renders as a flat QToolButton with barely
        # any chrome until hovered -- easy to miss as a clickable button.
        # A real QPushButton always shows a proper raised border/background.
        ingest_button = QPushButton("Ingest Pack...")
        ingest_button.setCursor(Qt.PointingHandCursor)
        ingest_button.setStyleSheet(
            "QPushButton {"
            "  padding: 6px 18px;"
            "  font-weight: 600;"
            "  background-color: #2d6cdf;"
            "  color: white;"
            "  border: 1px solid #1f4fb0;"
            "  border-radius: 4px;"
            "}"
            "QPushButton:hover { background-color: #3d7ae8; }"
            "QPushButton:pressed { background-color: #1f4fb0; }"
        )
        ingest_button.clicked.connect(self._open_ingest_dialog)
        toolbar.addWidget(ingest_button)

    def _update_window_title(self) -> None:
        library_folder = settings.load().library_folder or "no library configured"
        self.setWindowTitle(f"Asset Catalogue -- {library_folder}")

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self._reload_catalogue()

    def _switch_library(self) -> None:
        current = settings.load().library_folder or ""
        chosen = QFileDialog.getExistingDirectory(self, "Select library folder", current)
        if not chosen:
            return
        chosen_path = Path(chosen)

        if (chosen_path / "catalogue.db").exists():
            message = f"Switch to the existing library at:\n{chosen_path}"
        else:
            message = (
                f"No catalogue.db found at:\n{chosen_path}\n\n"
                "Switching here will create a new, empty library. Continue?"
            )
        confirm = QMessageBox.question(
            self, "Switch Library", message, QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        s = settings.load()
        s.library_folder = str(chosen_path)
        settings.save(s)
        self._reload_catalogue()

    def _reload_catalogue(self) -> None:
        try:
            new_catalogue = Catalogue.open()
        except (RuntimeError, OSError) as exc:
            QMessageBox.critical(self, "Asset Catalogue", str(exc))
            return
        self._catalogue.close()
        self._catalogue = new_catalogue
        self._update_window_title()
        self.detail_panel.set_catalogue(self._catalogue)
        self.detail_panel.clear_selection()
        self._rebuild_filter_panel()

    def _rebuild_filter_panel(self) -> None:
        old_filter_panel = self.filter_panel
        self.filter_panel = FilterPanel(self._catalogue, self._refresh_grid)
        main_splitter = self.centralWidget()
        main_splitter.replaceWidget(0, self.filter_panel)
        old_filter_panel.setParent(None)
        old_filter_panel.deleteLater()
        self._selected_asset_id = None
        self._refresh_grid()

    def _open_ingest_dialog(self) -> None:
        if self._catalogue.staging_folder() is None:
            QMessageBox.warning(
                self, "Asset Catalogue", "Configure a staging folder in Settings first."
            )
            return
        dialog = IngestDialog(self._catalogue, self)
        if dialog.exec() != QDialog.Accepted:
            return

        if dialog.is_zip_source():
            job = lambda: self._catalogue.extract_and_ingest_pack_bg(
                dialog.zip_path,
                dialog.pack_folder_name,
                dialog.pack_name,
                dialog.creator,
                dialog.licence,
                dialog.source_url,
            )
        else:
            job = lambda: self._catalogue.ingest_pack_bg(
                dialog.pack_folder_name,
                dialog.pack_name,
                dialog.creator,
                dialog.licence,
                dialog.source_url,
            )

        def format_result(result: tuple) -> str:
            stats, updated_fields = result
            message = (
                f"Ingested '{dialog.pack_name}': {stats.new} new, "
                f"{stats.duplicate} duplicate, {stats.total} scanned"
            )
            if stats.nested_zips_extracted:
                message += (
                    f"\nUnpacked {stats.nested_zips_extracted} nested zip file(s) "
                    "found inside the pack"
                )
            if stats.skipped_engine_files or stats.skipped_engine_folders:
                message += (
                    f"\nSkipped {stats.skipped_engine_files} Unity/Unreal project "
                    f"file(s) and {stats.skipped_engine_folders} project folder(s) "
                    "-- not asset content"
                )
            if updated_fields:
                message += f"\nUpdated pack metadata: {', '.join(updated_fields)}"
            message += f"\nArchived {stats.archived} asset(s) to the library"
            message += (
                f"\nGenerated {stats.thumbnails_generated} thumbnail(s), "
                f"{stats.thumbnails_failed} failed"
            )
            if stats.blender_unavailable_reason:
                message += f"\n3D thumbnails skipped: {stats.blender_unavailable_reason}"
            return message

        self._run_background_job(
            job,
            f"Ingesting '{dialog.pack_name}'...",
            format_result,
            self._rebuild_filter_panel,
        )

    def _generate_2d_thumbnails(self) -> None:
        pack = self.filter_panel.selected_pack()
        self._run_background_job(
            lambda: self._catalogue.generate_2d_thumbnails_bg(pack=pack),
            "Generating 2D thumbnails...",
            lambda stats: (
                f"Thumbnails: {stats.generated} generated, "
                f"{stats.already_done} already done, {stats.failed} failed"
            ),
            self._refresh_grid,
        )

    def _generate_model_thumbnails(self) -> None:
        try:
            blender_exe = self._catalogue.resolve_blender()
        except RuntimeError as exc:
            QMessageBox.critical(self, "Asset Catalogue", str(exc))
            return
        pack = self.filter_panel.selected_pack()
        self._run_background_job(
            lambda: self._catalogue.generate_model_thumbnails_bg(blender_exe, pack=pack),
            "Generating 3D thumbnails via Blender... this can take a while.",
            lambda stats: (
                f"Model thumbnails: {stats.generated} generated, "
                f"{stats.already_done} already done, {stats.failed} failed"
            ),
            self._refresh_grid,
        )

    def _open_tag_pack_dialog(self) -> None:
        if not self._catalogue.list_packs():
            QMessageBox.information(self, "Asset Catalogue", "No packs to tag yet -- ingest one first.")
            return
        dialog = TagPackDialog(self._catalogue, self.filter_panel.selected_pack(), self)
        if dialog.exec() != QDialog.Accepted:
            return

        self._run_background_job(
            lambda: self._catalogue.tag_pack_bg(dialog.pack_name, dialog.tag_name, dialog.category),
            f"Tagging pack '{dialog.pack_name}'...",
            lambda applied: (
                f"Applied '{dialog.tag_name}' to {applied} asset(s) in '{dialog.pack_name}' "
                "(already-tagged assets left untouched)"
            ),
            self._on_tags_changed,
        )

    def _handle_tag_asset(self, asset_id: int, tag_name: str) -> None:
        self._catalogue.tag_asset(asset_id, tag_name)
        self._on_tags_changed()

    def _handle_untag_asset(self, asset_id: int, tag_name: str) -> None:
        self._catalogue.untag_asset(asset_id, tag_name)
        self._on_tags_changed()

    def _handle_bulk_tag_assets(self, asset_ids: list[int], tag_name: str) -> None:
        self._run_background_job(
            lambda: self._catalogue.bulk_tag_assets_bg(asset_ids, tag_name),
            f"Tagging {len(asset_ids)} asset(s)...",
            lambda tagged: f"Tagged {tagged} asset(s) with '{tag_name}'",
            self._on_tags_changed,
        )

    def _show_in_library_folder(self, pack_name: str, relative_path: str) -> None:
        path = self._catalogue.library_asset_path_if_archived(pack_name, relative_path)
        if path is None:
            QMessageBox.information(
                self,
                "Asset Catalogue",
                "This asset hasn't been archived to the library yet -- that "
                "normally happens automatically as part of ingest.",
            )
            return
        if sys.platform == "win32":
            # Passing ["explorer", f"/select,{path}"] as an argv list is the
            # well-known broken version of this: explorer's own command-line
            # parsing for /select expects the path to be independently
            # quoted (/select,"C:\path") when it contains spaces, not
            # bundled as one already-quoted argv element the way Python's
            # list-based Popen would build it. Passing a single pre-built
            # command-line string instead (Windows-only Popen behavior) gets
            # the quoting right.
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            QMessageBox.information(self, "Asset Catalogue", f"Library copy is at:\n{path}")

    def _run_background_job(self, fn, progress_text: str, format_result, on_success_refresh) -> None:
        progress = QProgressDialog(progress_text, None, 0, 0, self)
        progress.setWindowTitle("Asset Catalogue")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()

        worker = _BackgroundWorker(fn)

        def on_ok(result) -> None:
            progress.close()
            QMessageBox.information(self, "Asset Catalogue", format_result(result))
            on_success_refresh()

        def on_fail(message: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Asset Catalogue", message)

        worker.finished_ok.connect(on_ok, Qt.QueuedConnection)
        worker.failed.connect(on_fail, Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self._active_worker = worker
        worker.start()

    def _refresh_grid(self) -> None:
        self._current_assets = self._catalogue.list_assets(
            pack=self.filter_panel.selected_pack(),
            asset_type=self.filter_panel.selected_type(),
            tag=self.filter_panel.selected_tag(),
        )
        self.grid.set_assets(self._current_assets, self._catalogue)
        self.grid.select_asset_id(self._selected_asset_id)
        self.statusBar().showMessage(f"{len(self._current_assets)} asset(s)")

    def _on_grid_selection_changed(self) -> None:
        selected = self.grid.selectedItems()
        if len(selected) == 1:
            asset_id = selected[0].data(Qt.UserRole)
            asset = next((a for a in self._current_assets if a.id == asset_id), None)
            if asset is not None:
                self._selected_asset_id = asset_id
                self.detail_panel.show_asset(asset)
            return
        self._selected_asset_id = None
        if selected:
            asset_ids = [item.data(Qt.UserRole) for item in selected]
            self.detail_panel.show_multi_selection(asset_ids)
        else:
            self.detail_panel.clear_selection()

    def _show_grid_context_menu(self, pos) -> None:
        item = self.grid.itemAt(pos)
        # Right-clicking something outside the current selection replaces
        # it with just that item first, matching the usual file-manager
        # convention, rather than acting on a stale selection.
        if item is not None and item not in self.grid.selectedItems():
            self.grid.setCurrentItem(item)

        menu = self._build_grid_context_menu()
        if menu is not None:
            menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _build_grid_context_menu(self) -> QMenu | None:
        """Split out from _show_grid_context_menu so the menu's contents can
        be tested without invoking exec() (which blocks on real user input)."""
        selected = self.grid.selectedItems()
        if not selected:
            return None

        menu = QMenu(self)
        if len(selected) == 1:
            asset_id = selected[0].data(Qt.UserRole)
            asset = next((a for a in self._current_assets if a.id == asset_id), None)
            if asset is not None:
                show_action = menu.addAction("Show in Library Folder")
                show_action.triggered.connect(
                    lambda: self._show_in_library_folder(asset.pack_name, asset.relative_path)
                )
                show_action.setEnabled(
                    self._catalogue.library_asset_path_if_archived(
                        asset.pack_name, asset.relative_path
                    )
                    is not None
                )
                menu.addSeparator()

        remove_label = "Delete from Library" if len(selected) == 1 else f"Delete {len(selected)} from Library"
        remove_action = menu.addAction(remove_label)
        remove_action.triggered.connect(self._remove_selected_assets)

        return menu

    def _remove_selected_assets(self) -> None:
        selected_ids = [item.data(Qt.UserRole) for item in self.grid.selectedItems()]
        if not selected_ids:
            QMessageBox.information(self, "Asset Catalogue", "No assets selected.")
            return
        confirm = QMessageBox.question(
            self,
            "Remove Assets",
            f"Remove {len(selected_ids)} asset(s) from the catalogue?\n\n"
            "This removes them from the catalogue database and deletes their "
            "thumbnails and any archived library copy -- the original files in "
            "your staging folder are untouched.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._run_background_job(
            lambda: self._catalogue.remove_assets_bg(selected_ids),
            f"Removing {len(selected_ids)} asset(s)...",
            lambda stats: f"Removed {stats.removed} asset(s) from the catalogue.",
            self._rebuild_filter_panel,
        )

    def _on_tags_changed(self) -> None:
        self.filter_panel.refresh_tags(self._catalogue)
        self._refresh_grid()


def _try_open_catalogue() -> tuple[Catalogue | None, str | None]:
    try:
        return Catalogue.open(), None
    except (RuntimeError, OSError) as exc:
        return None, str(exc)


def main() -> None:
    app = QApplication(sys.argv)

    # A library folder is required above everything else -- nothing else in
    # the app can run without one, so this loops until Catalogue.open()
    # actually succeeds or the user explicitly quits, rather than letting a
    # bad path (permissions, a typo, anything beyond "not configured yet")
    # crash past this gate with a raw traceback.
    catalogue, _ = _try_open_catalogue()
    error_message: str | None = None
    while catalogue is None:
        dialog = SettingsDialog(error_message=error_message)
        if dialog.exec() != QDialog.Accepted:
            sys.exit(0)
        catalogue, error_message = _try_open_catalogue()

    window = MainWindow(catalogue)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
