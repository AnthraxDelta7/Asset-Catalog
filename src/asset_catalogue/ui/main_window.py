from __future__ import annotations

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
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
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
    def __init__(self, catalogue: Catalogue, on_tags_changed) -> None:
        super().__init__()
        self._catalogue = catalogue
        self._on_tags_changed = on_tags_changed
        self._asset_id: int | None = None

        layout = QVBoxLayout(self)
        self.title_label = QLabel("No asset selected")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.meta_label = QLabel("")
        layout.addWidget(self.meta_label)

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

        self.setEnabled(False)

    def set_catalogue(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue

    def clear_selection(self) -> None:
        self._asset_id = None
        self.title_label.setText("No asset selected")
        self.meta_label.setText("")
        self.tag_list.clear()
        self.setEnabled(False)

    def show_multi_selection(self, count: int) -> None:
        self._asset_id = None
        self.title_label.setText(f"{count} assets selected")
        self.meta_label.setText("Select exactly one asset to view or edit its tags.")
        self.tag_list.clear()
        self.setEnabled(False)

    def show_asset(self, asset: AssetSummary) -> None:
        self._asset_id = asset.id
        self.title_label.setText(f"{asset.pack_name} / {asset.filename}")
        self.meta_label.setText(f"type: {asset.asset_type}   thumbnail: {asset.thumbnail_status}")
        self.tag_list.clear()
        self.tag_list.addItems(asset.tags)
        self.setEnabled(True)

    def _add_tag(self) -> None:
        name = self.new_tag_input.text().strip()
        if not name or self._asset_id is None:
            return
        self._catalogue.tag_asset(self._asset_id, name)
        self.new_tag_input.clear()
        self._on_tags_changed()

    def _remove_selected_tag(self) -> None:
        item = self.tag_list.currentItem()
        if item is None or self._asset_id is None:
            return
        self._catalogue.untag_asset(self._asset_id, item.text())
        self._on_tags_changed()


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


class IngestDialog(QDialog):
    """One dialog for both pack sources -- an already-extracted folder inside
    the staging folder, or a .zip anywhere on disk. Two browse buttons rather
    than one is a real Qt/OS limitation, not a design choice: a native file
    picker is either a folder picker or a file picker, never both at once.
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
        browse_folder_button = QPushButton("Browse Folder...")
        browse_folder_button.clicked.connect(self._browse_folder)
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
            "Browse Folder: pick an already-extracted pack inside the staging folder.\n"
            "Browse Zip: pick a .zip anywhere on disk -- it's extracted into the staging "
            "folder first, using the folder name above."
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

    def _browse_folder(self) -> None:
        staging_folder = self._catalogue.staging_folder()
        if staging_folder is None:
            QMessageBox.warning(self, "Ingest Pack", "No staging folder configured.")
            return
        chosen = QFileDialog.getExistingDirectory(self, "Select pack folder", str(staging_folder))
        if not chosen:
            return
        chosen_path = Path(chosen)
        try:
            relative = chosen_path.relative_to(staging_folder)
        except ValueError:
            QMessageBox.warning(
                self, "Ingest Pack", "The selected folder must be inside the staging folder."
            )
            return
        self._source_kind = "folder"
        self._zip_path = None
        self.source_edit.setText(str(relative))
        self.dest_folder_edit.setText(str(relative))
        self.dest_folder_edit.setEnabled(False)
        if not self.pack_name_edit.text():
            self.pack_name_edit.setText(chosen_path.name)

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
        self.detail_panel = DetailPanel(catalogue, self._on_tags_changed)

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
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)

        ingest_action = toolbar.addAction("Ingest Pack...")
        ingest_action.triggered.connect(self._open_ingest_dialog)

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
            if updated_fields:
                message += f"\nUpdated pack metadata: {', '.join(updated_fields)}"
            return message

        self._run_background_job(
            job,
            f"Ingesting '{dialog.pack_name}'...",
            format_result,
            rebuild_filters=True,
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
            rebuild_filters=False,
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
            rebuild_filters=False,
        )

    def _run_background_job(self, fn, progress_text: str, format_result, rebuild_filters: bool) -> None:
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
            if rebuild_filters:
                self._rebuild_filter_panel()
            else:
                self._refresh_grid()

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
            self.detail_panel.show_multi_selection(len(selected))
        else:
            self.detail_panel.clear_selection()

    def _remove_selected_assets(self) -> None:
        selected_ids = [item.data(Qt.UserRole) for item in self.grid.selectedItems()]
        if not selected_ids:
            QMessageBox.information(self, "Asset Catalogue", "No assets selected.")
            return
        confirm = QMessageBox.question(
            self,
            "Remove Assets",
            f"Remove {len(selected_ids)} asset(s) from the catalogue?\n\n"
            "This only removes them from the catalogue database and deletes their "
            "thumbnails -- the original files in your staging folder are untouched.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._run_background_job(
            lambda: self._catalogue.remove_assets_bg(selected_ids),
            f"Removing {len(selected_ids)} asset(s)...",
            lambda stats: f"Removed {stats.removed} asset(s) from the catalogue.",
            rebuild_filters=True,
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
