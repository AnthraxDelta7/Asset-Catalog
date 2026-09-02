from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(520, 260)
        s = settings.load()

        layout = QVBoxLayout(self)
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
    def __init__(self, catalogue: Catalogue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalogue = catalogue
        self.setWindowTitle("Ingest Pack")
        self.resize(440, 240)

        self.pack_folder_name: str | None = None
        self.pack_name: str = ""
        self.creator: str | None = None
        self.licence: str | None = None
        self.source_url: str | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.pack_folder_edit = QLineEdit()
        self.pack_folder_edit.setReadOnly(True)
        form.addRow("Pack folder:", _browse_row(self.pack_folder_edit, self._browse))

        self.pack_name_edit = QLineEdit()
        form.addRow("Pack name:", self.pack_name_edit)
        self.creator_edit = QLineEdit()
        form.addRow("Creator:", self.creator_edit)
        self.licence_edit = QLineEdit()
        form.addRow("Licence:", self.licence_edit)
        self.source_url_edit = QLineEdit()
        form.addRow("Source URL:", self.source_url_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Ingest")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
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
        self.pack_folder_edit.setText(str(relative))
        if not self.pack_name_edit.text():
            self.pack_name_edit.setText(chosen_path.name)

    def _on_accept(self) -> None:
        pack_folder = self.pack_folder_edit.text().strip()
        pack_name = self.pack_name_edit.text().strip()
        if not pack_folder or not pack_name:
            QMessageBox.warning(self, "Ingest Pack", "Pick a pack folder and enter a pack name.")
            return
        self.pack_folder_name = pack_folder
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
        self.setWindowTitle("Asset Catalogue")
        self.resize(1100, 700)

        self._build_menu()

        self.filter_panel = FilterPanel(catalogue, self._refresh_grid)
        self.grid = ThumbnailGrid()
        self.grid.currentItemChanged.connect(self._on_selection_changed)
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
        ingest_action = file_menu.addAction("Ingest Pack...")
        ingest_action.triggered.connect(self._open_ingest_dialog)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("Exit")
        exit_action.triggered.connect(self.close)

        thumbnails_menu = menu_bar.addMenu("&Thumbnails")
        gen_2d_action = thumbnails_menu.addAction("Generate 2D Thumbnails (current pack filter)")
        gen_2d_action.triggered.connect(self._generate_2d_thumbnails)
        gen_3d_action = thumbnails_menu.addAction(
            "Generate 3D Thumbnails via Blender (current pack filter)"
        )
        gen_3d_action.triggered.connect(self._generate_model_thumbnails)

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self._reload_catalogue()

    def _reload_catalogue(self) -> None:
        try:
            new_catalogue = Catalogue.open()
        except RuntimeError as exc:
            QMessageBox.critical(self, "Asset Catalogue", str(exc))
            return
        self._catalogue.close()
        self._catalogue = new_catalogue
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
        self._run_background_job(
            lambda: self._catalogue.ingest_pack_bg(
                dialog.pack_folder_name,
                dialog.pack_name,
                dialog.creator,
                dialog.licence,
                dialog.source_url,
            ),
            f"Ingesting '{dialog.pack_name}'...",
            lambda stats: (
                f"Ingested '{dialog.pack_name}': {stats.new} new, "
                f"{stats.duplicate} duplicate, {stats.total} scanned"
            ),
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

    def _on_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        if current is None:
            self._selected_asset_id = None
            self.detail_panel.clear_selection()
            return
        asset_id = current.data(Qt.UserRole)
        asset = next((a for a in self._current_assets if a.id == asset_id), None)
        if asset is not None:
            self._selected_asset_id = asset_id
            self.detail_panel.show_asset(asset)

    def _on_tags_changed(self) -> None:
        self.filter_panel.refresh_tags(self._catalogue)
        self._refresh_grid()


def main() -> None:
    app = QApplication(sys.argv)

    try:
        catalogue = Catalogue.open()
    except RuntimeError:
        dialog = SettingsDialog()
        if dialog.exec() != QDialog.Accepted:
            sys.exit(0)
        try:
            catalogue = Catalogue.open()
        except RuntimeError as exc:
            QMessageBox.critical(None, "Asset Catalogue", str(exc))
            sys.exit(1)

    window = MainWindow(catalogue)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
