from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

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


class MainWindow(QMainWindow):
    def __init__(self, catalogue: Catalogue) -> None:
        super().__init__()
        self._catalogue = catalogue
        self._current_assets: list[AssetSummary] = []
        self.setWindowTitle("Asset Catalogue")
        self.resize(1100, 700)

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
        self._selected_asset_id: int | None = None
        self._refresh_grid()

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
    except RuntimeError as exc:
        QMessageBox.critical(None, "Asset Catalogue", str(exc))
        sys.exit(1)
    window = MainWindow(catalogue)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
