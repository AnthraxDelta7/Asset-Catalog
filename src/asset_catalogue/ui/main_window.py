from __future__ import annotations

import html
import subprocess
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QStringListModel, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QSurfaceFormat
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
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
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from asset_catalogue import blender_render, library_health, library_stats, paths, settings, updater
from asset_catalogue.version import __version__
from asset_catalogue.catalogue import AssetSummary, Catalogue, PackDetail, TagSummary

THUMBNAIL_ICON_SIZE = QSize(128, 128)
# Extra width/height beyond the icon itself is where the (word-wrapped)
# filename label renders. This has to be set on each item's own sizeHint,
# not just the grid's setGridSize -- the grid size only controls layout
# spacing between cells, while text wrapping is computed against the
# item's own sizeHint, so setGridSize alone left long filenames eliding
# with "..." regardless of how much extra grid space was added around them.
GRID_CELL_SIZE = QSize(THUMBNAIL_ICON_SIZE.width() + 32, THUMBNAIL_ICON_SIZE.height() + 72)

# asset_type values that actually have a thumbnail generator -- 'other'
# doesn't, so the detail panel's "Generate Thumbnail" button never offers
# to do something that would just silently do nothing.
THUMBNAIL_CAPABLE_TYPES = {"texture", "audio", "model"}


class FilterPanel(QWidget):
    def __init__(
        self,
        catalogue: Catalogue,
        on_change,
        on_edit_pack,
        on_remove_pack,
        on_rename_tag,
        on_delete_tag,
        on_render_pack_previews,
    ) -> None:
        super().__init__()
        self._catalogue = catalogue
        self._on_change = on_change
        self._on_edit_pack = on_edit_pack
        self._on_remove_pack = on_remove_pack
        self._on_rename_tag = on_rename_tag
        self._on_delete_tag = on_delete_tag
        self._on_render_pack_previews = on_render_pack_previews

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filename contains...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_change)
        layout.addWidget(self.search_edit)

        self.favorites_checkbox = QCheckBox("★ Favorites only")
        self.favorites_checkbox.toggled.connect(self._on_change)
        layout.addWidget(self.favorites_checkbox)

        layout.addWidget(QLabel("Type"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types", None)
        for asset_type in catalogue.list_asset_types():
            self.type_combo.addItem(asset_type, asset_type)
        self.type_combo.currentIndexChanged.connect(self._on_change)
        layout.addWidget(self.type_combo)

        layout.addWidget(QLabel("Format"))
        self.format_combo = QComboBox()
        self._populate_format_combo(catalogue)
        self.format_combo.currentIndexChanged.connect(self._on_change)
        layout.addWidget(self.format_combo)

        layout.addWidget(QLabel("Pack"))
        self.pack_list = QListWidget()
        self.pack_list.addItem("All packs")
        self.pack_list.addItems(catalogue.list_packs())
        self.pack_list.setCurrentRow(0)
        self.pack_list.currentRowChanged.connect(self._on_change)
        self.pack_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pack_list.customContextMenuRequested.connect(self._show_pack_context_menu)
        layout.addWidget(self.pack_list, stretch=1)

        layout.addWidget(QLabel("Tags"))
        self.tag_list = QListWidget()
        self.tag_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tag_list.customContextMenuRequested.connect(self._show_tag_context_menu)
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

    def _populate_format_combo(self, catalogue: Catalogue) -> None:
        self.format_combo.addItem("All formats", None)
        for extension in catalogue.list_asset_extensions():
            self.format_combo.addItem(extension.lstrip(".").upper(), extension)

    def selected_search(self) -> str | None:
        return self.search_edit.text().strip() or None

    def favorites_only(self) -> bool:
        return self.favorites_checkbox.isChecked()

    def selected_type(self) -> str | None:
        return self.type_combo.currentData()

    def selected_format(self) -> str | None:
        return self.format_combo.currentData()

    def refresh_formats(self, catalogue: Catalogue) -> None:
        previous = self.selected_format()
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        self._populate_format_combo(catalogue)
        restore_index = self.format_combo.findData(previous)
        self.format_combo.setCurrentIndex(restore_index if restore_index >= 0 else 0)
        self.format_combo.blockSignals(False)

    def selected_pack(self) -> str | None:
        row = self.pack_list.currentRow()
        return None if row <= 0 else self.pack_list.item(row).text()

    def refresh_packs(self, catalogue: Catalogue, select: str | None = None) -> None:
        self._catalogue = catalogue
        target = select if select is not None else self.selected_pack()
        self.pack_list.blockSignals(True)
        self.pack_list.clear()
        self.pack_list.addItem("All packs")
        self.pack_list.addItems(catalogue.list_packs())
        restore_row = 0
        if target is not None:
            match = self.pack_list.findItems(target, Qt.MatchExactly)
            if match:
                restore_row = self.pack_list.row(match[0])
        self.pack_list.setCurrentRow(restore_row)
        self.pack_list.blockSignals(False)

    def _show_pack_context_menu(self, pos) -> None:
        menu = self._build_pack_context_menu(pos)
        if menu is not None:
            menu.exec(self.pack_list.viewport().mapToGlobal(pos))

    def _build_pack_context_menu(self, pos) -> QMenu | None:
        """Split from _show_pack_context_menu so its contents can be tested
        without invoking exec() -- see the grid context menu's same split
        in MainWindow for why (QMenu.exec is a C++-bound method that's
        unreliable to mock directly)."""
        item = self.pack_list.itemAt(pos)
        if item is None or self.pack_list.row(item) <= 0:  # "All packs" row
            return None
        pack_name = item.text()

        menu = QMenu(self)
        edit_action = menu.addAction("Edit Pack Metadata...")
        edit_action.triggered.connect(lambda: self._on_edit_pack(pack_name))
        model_ids = [a.id for a in self._catalogue.list_assets(pack=pack_name, asset_type="model")]
        if model_ids:
            render_previews_action = menu.addAction(f"Render 3D Previews for Pack ({len(model_ids)})")
            render_previews_action.triggered.connect(
                lambda: self._on_render_pack_previews(model_ids)
            )
        menu.addSeparator()
        remove_action = menu.addAction(f"Remove Pack '{pack_name}'...")
        remove_action.triggered.connect(lambda: self._on_remove_pack(pack_name))
        return menu

    def _show_tag_context_menu(self, pos) -> None:
        menu = self._build_tag_context_menu(pos)
        if menu is not None:
            menu.exec(self.tag_list.viewport().mapToGlobal(pos))

    def _build_tag_context_menu(self, pos) -> QMenu | None:
        item = self.tag_list.itemAt(pos)
        if item is None or self.tag_list.row(item) <= 0:  # "All tags" row
            return None
        tag_name = item.data(Qt.UserRole)

        menu = QMenu(self)
        edit_action = menu.addAction("Edit Tag...")
        edit_action.triggered.connect(lambda: self._on_rename_tag(tag_name))
        menu.addSeparator()
        delete_action = menu.addAction(f"Delete Tag '{tag_name}'...")
        delete_action.triggered.connect(lambda: self._on_delete_tag(tag_name))
        return menu

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
        self.setGridSize(GRID_CELL_SIZE)
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
            label = f"★ {asset.filename}" if asset.favorite else asset.filename
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, asset.id)
            item.setIcon(QIcon(self._load_thumbnail(asset, catalogue)))
            item.setToolTip(f"{asset.pack_name} / {asset.filename}\n{asset.asset_type}")
            item.setSizeHint(GRID_CELL_SIZE)
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
        # Scale to the icon's PHYSICAL pixel size (logical size * the
        # screen's device pixel ratio), then tag the result with that same
        # ratio via setDevicePixelRatio -- without this, a 128x128-pixel
        # image gets stretched by Qt to fill a 128-*logical*-pixel icon
        # slot on any scaled/HiDPI display (e.g. Windows' common 125%/150%
        # scaling), which is what actually causes visible blur: the source
        # thumbnails are rendered at 512x512, plenty of real resolution for
        # this, the blur was purely from Qt not being told about the ratio.
        dpr = self.devicePixelRatioF()
        physical_size = QSize(
            round(THUMBNAIL_ICON_SIZE.width() * dpr), round(THUMBNAIL_ICON_SIZE.height() * dpr)
        )
        path = catalogue.thumbnail_path_for(asset.content_hash)
        if path is not None:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    physical_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                scaled.setDevicePixelRatio(dpr)
                return scaled
        placeholder = QPixmap(physical_size)
        placeholder.setDevicePixelRatio(dpr)
        placeholder.fill(Qt.darkGray)
        return placeholder


class ThumbnailPreviewDialog(QDialog):
    """A larger view of an asset's already-rendered thumbnail -- the 128px
    grid icon doesn't show much detail for a busy texture or a complex
    model render. Just the existing thumbnail image scaled up, not a live
    re-render -- for a model asset with a cached interactive preview, a
    "View in 3D" button hands off to that instead of duplicating it here.
    """

    PREVIEW_SIZE = 512

    def __init__(
        self,
        asset: AssetSummary,
        catalogue: Catalogue,
        on_view_3d=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(asset.filename)

        layout = QVBoxLayout(self)
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setMinimumSize(self.PREVIEW_SIZE, self.PREVIEW_SIZE)

        path = catalogue.thumbnail_path_for(asset.content_hash)
        pixmap = QPixmap(str(path)) if path is not None else QPixmap()
        if not pixmap.isNull():
            dpr = self.devicePixelRatioF()
            physical_size = QSize(round(self.PREVIEW_SIZE * dpr), round(self.PREVIEW_SIZE * dpr))
            scaled = pixmap.scaled(physical_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            image_label.setPixmap(scaled)
        else:
            image_label.setText("(no thumbnail rendered yet)")
        layout.addWidget(image_label, stretch=1)

        info_label = QLabel(f"{asset.pack_name} / {asset.filename}   ({asset.asset_type})")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)

        button_row = QHBoxLayout()
        if asset.asset_type == "model" and on_view_3d is not None:
            view_3d_button = QPushButton("View in 3D (Orbit/Zoom)...")
            view_3d_button.clicked.connect(
                lambda: (self.accept(), on_view_3d(asset.filename, asset.id, asset.content_hash))
            )
            button_row.addWidget(view_3d_button)
        button_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self.resize(self.PREVIEW_SIZE + 40, self.PREVIEW_SIZE + 100)


class PlayButton(QPushButton):
    """A push button that fills left-to-right with playback progress, like
    a small progress bar built into the button itself -- feedback that
    audio is actually playing (and roughly how far through the clip)
    without a separate widget alongside it. Draws the button completely
    normally first (same style/theme/hover/disabled look as every other
    QPushButton in the app), then overlays just a semi-transparent progress
    wash on top -- an earlier version hand-painted the whole button from
    scratch instead, which looked visibly flat/grayed-out next to the
    platform-styled buttons around it.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._progress = 0.0

    def set_progress(self, progress: float) -> None:
        progress = max(0.0, min(1.0, progress))
        if progress != self._progress:
            self._progress = progress
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._progress <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        fill_rect = QRectF(0, 0, rect.width() * self._progress, rect.height())
        painter.fillRect(fill_rect, QColor(90, 150, 220, 90))
        painter.end()


class DetailPanel(QWidget):
    """Single-asset mode (exactly one grid selection): full tag editing plus
    a link to the asset's archived copy in the library. Multi-select mode
    (2+ selected): "show in library" is disabled (ambiguous for a set of
    different assets), but tag editing works both ways -- adding applies a
    tag to every selected asset at once, and the tag list shows only tags
    common to *all* selected assets (the intersection, not the union) so
    removing one is unambiguous: it comes off of every selected asset,
    never just some of them silently. All actual catalogue writes are
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
        on_bulk_untag_assets,
        on_show_in_library,
        on_revert_conversion,
        on_cleanup_conversion,
        on_filter_by_pack,
        on_export_browse,
        on_quick_export,
        on_generate_thumbnail,
        on_toggle_favorite,
    ) -> None:
        super().__init__()
        self._catalogue = catalogue
        self._on_tag_asset = on_tag_asset
        self._on_untag_asset = on_untag_asset
        self._on_bulk_tag_assets = on_bulk_tag_assets
        self._on_bulk_untag_assets = on_bulk_untag_assets
        self._on_show_in_library = on_show_in_library
        self._on_revert_conversion = on_revert_conversion
        self._on_cleanup_conversion = on_cleanup_conversion
        self._on_filter_by_pack = on_filter_by_pack
        self._on_export_browse = on_export_browse
        self._on_quick_export = on_quick_export
        self._on_generate_thumbnail = on_generate_thumbnail
        self._on_toggle_favorite = on_toggle_favorite
        self._asset_id: int | None = None
        self._current_asset: AssetSummary | None = None
        self._multi_asset_ids: list[int] = []

        layout = QVBoxLayout(self)
        self.title_label = QLabel("No asset selected")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        # A quick personal flag independent of tags -- only shown for a
        # single-selected asset (multi-select favoriting goes through the
        # grid's right-click menu, see MainWindow._build_grid_context_menu).
        self.favorite_button = QPushButton("☆ Add to Favorites")
        self.favorite_button.clicked.connect(self._toggle_favorite)
        self.favorite_button.setVisible(False)
        layout.addWidget(self.favorite_button)

        # A small clickable "Pack: <name>" line -- clicking it filters the
        # grid down to just that pack, a quick way to jump from "this one
        # asset looks off" to "let me see the whole pack it came from".
        self.pack_label = QLabel("")
        self.pack_label.setWordWrap(True)
        self.pack_label.setToolTip("Click to filter the grid to this pack")
        self.pack_label.linkActivated.connect(self._on_pack_link_clicked)
        layout.addWidget(self.pack_label)

        self.meta_label = QLabel("")
        layout.addWidget(self.meta_label)

        # Only shown for a single-selected asset whose thumbnail isn't
        # 'done' yet, and whose asset_type actually has a thumbnail
        # generator at all ("other" doesn't -- see THUMBNAIL_CAPABLE_TYPES).
        self.generate_thumbnail_button = QPushButton("Generate Thumbnail")
        self.generate_thumbnail_button.clicked.connect(self._generate_thumbnail)
        self.generate_thumbnail_button.setVisible(False)
        layout.addWidget(self.generate_thumbnail_button)

        self.show_in_library_button = QPushButton("Show in Library Folder")
        self.show_in_library_button.clicked.connect(self._show_in_library)
        layout.addWidget(self.show_in_library_button)

        # Only shown for a single-selected audio asset that's actually been
        # archived (same enablement condition as Show in Library Folder --
        # that's the reliable "there's a real file to play" signal). Plays
        # straight from the archived library copy, not staging, so it's
        # unaffected by whatever's currently selected in the staging
        # browser and always available once ingested.
        self._playable_audio_path: Path | None = None
        self._media_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._media_player.positionChanged.connect(self._on_playback_position_changed)
        self.play_button = PlayButton("▶ Play")
        self.play_button.clicked.connect(self.toggle_playback)
        self.play_button.setVisible(False)
        layout.addWidget(self.play_button)

        # Only shown for a single-selected asset with a pending conversion
        # (see conversion.py) -- lets the user review a converted .glb next
        # to its regenerated thumbnail and decide whether to keep it.
        conversion_row = QHBoxLayout()
        self.revert_conversion_button = QPushButton("Revert Conversion")
        self.revert_conversion_button.clicked.connect(self._revert_conversion)
        self.cleanup_conversion_button = QPushButton("Delete Pre-Conversion Original")
        self.cleanup_conversion_button.clicked.connect(self._cleanup_conversion)
        conversion_row.addWidget(self.revert_conversion_button)
        conversion_row.addWidget(self.cleanup_conversion_button)
        layout.addLayout(conversion_row)
        self.revert_conversion_button.setVisible(False)
        self.cleanup_conversion_button.setVisible(False)

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
        # Autocompletes against the existing tag vocabulary -- without this,
        # a typo'd re-tagging attempt silently creates a near-duplicate tag
        # (e.g. "Si-FI" and "SiFi Guns" both ending up applied to a whole
        # pack) instead of reusing the tag that's already there.
        self._tag_completer = QCompleter([], self)
        self._tag_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._tag_completer.setFilterMode(Qt.MatchContains)
        self.new_tag_input.setCompleter(self._tag_completer)
        self.refresh_tag_completer()
        self.add_button = QPushButton("Add tag")
        self.add_button.clicked.connect(self._add_tag)
        add_row.addWidget(self.new_tag_input)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)

        # A "smart" export button, right-aligned at the bottom of the panel:
        # a plain click exports the current selection straight to the most
        # recently used project (no dialog), and its own dropdown arrow
        # lists every other recent project plus a "Browse for Project..."
        # entry for picking a new one -- see _update_export_button, which
        # rebuilds the label/menu from Settings.recent_export_projects.
        export_row = QHBoxLayout()
        export_row.addStretch(1)
        self.export_button = QToolButton()
        self.export_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.export_button.clicked.connect(self._on_export_button_clicked)
        self.export_menu = QMenu(self.export_button)
        self.export_button.setMenu(self.export_menu)
        export_row.addWidget(self.export_button)
        layout.addLayout(export_row)

        self._set_idle_state()

    def refresh_tag_completer(self) -> None:
        tag_names = [tag.name for tag in self._catalogue.list_tags()]
        self._tag_completer.setModel(QStringListModel(tag_names, self._tag_completer))

    def set_catalogue(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue
        self.refresh_tag_completer()

    def refresh_export_button(self) -> None:
        """Called by MainWindow after any successful export (however it was
        triggered) so the button's label/menu reflect the latest recent-
        projects list immediately, without waiting for the selection to
        change.
        """
        self._update_export_button(self.export_button.isEnabled())

    def _update_export_button(self, enabled: bool) -> None:
        self.export_button.setEnabled(enabled)
        recent_projects = settings.load().recent_export_projects
        self.export_menu.clear()
        if recent_projects:
            for path in recent_projects:
                label = Path(path).name or path
                action = self.export_menu.addAction(f"Export to {label}")
                action.setToolTip(path)
                action.triggered.connect(lambda checked=False, p=path: self._on_quick_export(p))
            self.export_menu.addSeparator()
        browse_action = self.export_menu.addAction("Browse for Project...")
        browse_action.triggered.connect(lambda checked=False: self._on_export_browse())

        if recent_projects:
            self.export_button.setText(f"Export to {Path(recent_projects[0]).name}")
        else:
            self.export_button.setText("Export to Project...")

    def _on_export_button_clicked(self) -> None:
        recent_projects = settings.load().recent_export_projects
        if recent_projects:
            self._on_quick_export(recent_projects[0])
        else:
            self._on_export_browse()

    def _set_idle_state(self) -> None:
        self.tag_list.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.new_tag_input.setEnabled(False)
        self.add_button.setEnabled(False)
        self.show_in_library_button.setEnabled(False)
        self.favorite_button.setVisible(False)
        self.generate_thumbnail_button.setVisible(False)
        self.revert_conversion_button.setVisible(False)
        self.cleanup_conversion_button.setVisible(False)
        self._update_export_button(False)
        self._stop_playback_and_hide()

    def clear_selection(self) -> None:
        self._asset_id = None
        self._current_asset = None
        self._multi_asset_ids = []
        self.title_label.setText("No asset selected")
        self.pack_label.setText("")
        self.meta_label.setText("")
        self.tag_list.clear()
        self._set_idle_state()

    def _pack_link_html(self, pack_name: str, rating: int | None = None) -> str:
        escaped = html.escape(pack_name)
        stars = f" {'★' * rating}{'☆' * (5 - rating)}" if rating else ""
        return f'Pack: <a href="{escaped}">{escaped}</a>{stars}'

    def show_multi_selection(self, assets: list[AssetSummary]) -> None:
        self._asset_id = None
        self._current_asset = None
        self._multi_asset_ids = [asset.id for asset in assets]
        self.title_label.setText(f"{len(assets)} assets selected")

        pack_names = {asset.pack_name for asset in assets}
        if len(pack_names) == 1:
            self.pack_label.setText(self._pack_link_html(assets[0].pack_name, assets[0].pack_rating))
            self.pack_label.setToolTip(assets[0].pack_notes or "Click to filter the grid to this pack")
        else:
            self.pack_label.setText("")
            self.pack_label.setToolTip("Click to filter the grid to this pack")

        common_tags = sorted(set.intersection(*(set(asset.tags) for asset in assets))) if assets else []
        self.tag_list.clear()
        self.tag_list.addItems(common_tags)
        if common_tags:
            self.meta_label.setText(
                "Add a tag to apply it to all selected assets. The list below shows "
                "only tags common to all of them -- removing one untags all of them."
            )
        else:
            self.meta_label.setText(
                "Add a tag to apply it to all selected assets. No tag is common to "
                "all of them yet, so there's nothing to remove in bulk."
            )
        self.tag_list.setEnabled(bool(common_tags))
        self.remove_button.setEnabled(bool(common_tags))
        self.new_tag_input.setEnabled(True)
        self.add_button.setEnabled(True)
        self.show_in_library_button.setEnabled(False)
        self.favorite_button.setVisible(False)
        self.generate_thumbnail_button.setVisible(False)
        self.revert_conversion_button.setVisible(False)
        self.cleanup_conversion_button.setVisible(False)
        self._update_export_button(True)
        self._stop_playback_and_hide()

    def show_asset(self, asset: AssetSummary) -> None:
        self._asset_id = asset.id
        self._current_asset = asset
        self._multi_asset_ids = []
        self.title_label.setText(Path(asset.filename).stem)
        self.pack_label.setText(self._pack_link_html(asset.pack_name, asset.pack_rating))
        self.pack_label.setToolTip(asset.pack_notes or "Click to filter the grid to this pack")
        file_format = Path(asset.filename).suffix.lstrip(".").upper() or "(none)"
        self.meta_label.setText(f"type: {asset.asset_type}   format: {file_format}")
        self.favorite_button.setVisible(True)
        self.favorite_button.setText("★ Favorited" if asset.favorite else "☆ Add to Favorites")
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
        self.generate_thumbnail_button.setVisible(
            asset.thumbnail_status != "done" and asset.asset_type in THUMBNAIL_CAPABLE_TYPES
        )
        pending = self._catalogue.has_pending_conversion(asset.id)
        self.revert_conversion_button.setVisible(pending)
        self.cleanup_conversion_button.setVisible(pending)
        self._update_export_button(True)

        self._media_player.stop()
        self._playable_audio_path = archived if asset.asset_type == "audio" else None
        self.play_button.setVisible(self._playable_audio_path is not None)
        self.play_button.setText("▶ Play")

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
        if item is None:
            return
        if self._asset_id is not None:
            self._on_untag_asset(self._asset_id, item.text())
        elif self._multi_asset_ids:
            self._on_bulk_untag_assets(list(self._multi_asset_ids), item.text())

    def _show_in_library(self) -> None:
        if self._current_asset is None:
            return
        self._on_show_in_library(self._current_asset.pack_name, self._current_asset.relative_path)

    def _generate_thumbnail(self) -> None:
        if self._current_asset is None:
            return
        self._on_generate_thumbnail(self._current_asset.id, self._current_asset.asset_type)

    def _toggle_favorite(self) -> None:
        if self._current_asset is None:
            return
        self._on_toggle_favorite(self._current_asset.id, not self._current_asset.favorite)

    def _stop_playback_and_hide(self) -> None:
        self._media_player.stop()
        self._playable_audio_path = None
        self.play_button.setVisible(False)
        self.play_button.setText("▶ Play")
        self.play_button.set_progress(0.0)

    def is_audio_playable(self) -> bool:
        return self._playable_audio_path is not None

    def is_playing(self) -> bool:
        return self._media_player.playbackState() == QMediaPlayer.PlayingState

    def toggle_playback(self) -> None:
        if self.is_playing():
            self._media_player.stop()
            return
        if self._playable_audio_path is None:
            return
        self._media_player.setSource(QUrl.fromLocalFile(str(self._playable_audio_path)))
        self._media_player.play()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText("Stop" if state == QMediaPlayer.PlayingState else "▶ Play")
        if state != QMediaPlayer.PlayingState:
            self.play_button.set_progress(0.0)

    def _on_playback_position_changed(self, position: int) -> None:
        duration = self._media_player.duration()
        if duration > 0:
            self.play_button.set_progress(position / duration)

    def _on_pack_link_clicked(self, pack_name: str) -> None:
        self._on_filter_by_pack(html.unescape(pack_name))

    def _revert_conversion(self) -> None:
        if self._asset_id is not None:
            self._on_revert_conversion(self._asset_id)

    def _cleanup_conversion(self) -> None:
        if self._asset_id is not None:
            self._on_cleanup_conversion(self._asset_id)


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
        self.resize(520, 340)
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
        if not s.blender_path:
            # Leaving this blank is a valid, working choice -- Blender is
            # re-detected on demand every time it's actually needed, not
            # cached here -- but a blank field with no explanation looks
            # unconfigured even when it's already working. Show what
            # auto-detection would find so that's clear at a glance.
            auto_found = blender_render.find_blender(None)
            if auto_found is not None:
                self.blender_edit.setPlaceholderText(f"(auto-detected) {auto_found}")
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
            "existing one (copied from another machine, a shared drive) to pick it up as-is.\n"
            "Export to Project remembers your recently used project folders on its own -- "
            "nothing to configure here."
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
            "Single-click an entry and press Select to pick it without entering it "
            "(a folder this way, not its contents); with nothing highlighted, Select "
            "picks the folder you're currently browsing."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        button_row = QHBoxLayout()
        select_button = QPushButton("Select")
        select_button.clicked.connect(self._select_current_folder)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(select_button)
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
        # A single-clicked-but-not-entered list item (the natural first
        # instinct in most file pickers -- highlight, then press a button)
        # takes priority over "the folder currently being browsed": without
        # this, single-clicking a .zip and pressing Select silently picked
        # the current directory instead, ignoring the highlighted zip
        # entirely -- the real cause of "the name doesn't auto-fill for a
        # zip" (the zip was never actually selected in the first place).
        item = self.list_widget.currentItem()
        if item is not None:
            kind, path = item.data(Qt.UserRole)
            self.selected_relative_path = str(path.relative_to(self._staging_folder))
            self.selected_is_zip = kind == "zip"
            self.accept()
            return
        self.selected_relative_path = str(self._current_dir.relative_to(self._staging_folder))
        self.selected_is_zip = False
        self.accept()


class IngestDialog(QDialog):
    """One "Browse Folder/Zip..." button opens the custom StagingBrowserDialog
    above, which shows subfolders and .zip files inside the staging folder
    together -- either one selectable directly, a zip picked this way
    auto-extracted at ingest time (see ingest.py's ingest_pack). There used
    to be a second "Browse Zip..." button for a zip living *outside* the
    staging folder (e.g. still in Downloads); removed since it was a rarely
    needed extra option in practice (the CLI's `ingest-zip` still covers
    that case) and its separate "Extract to" field was easy to mix up with
    this one. Picking a new source always refreshes the suggested pack
    name to match it, unless you've typed your own -- see _pack_name_auto.
    """

    def __init__(self, catalogue: Catalogue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalogue = catalogue
        self.setWindowTitle("Ingest Pack")
        self.resize(480, 260)

        self._selected_relative_path: str | None = None
        # True until the user actually types into Pack name themselves --
        # while True, picking a new source keeps the suggested name in sync
        # with it; once the user edits it manually, later source picks stop
        # overwriting their choice. Previously this only ever checked "is
        # the field currently empty", so re-picking a different source
        # after an earlier pick (which had already filled it in) silently
        # left the old name in place -- the reported "doesn't copy the name
        # into the pack field" bug.
        self._pack_name_auto = True

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
        browse_button = QPushButton("Browse Folder/Zip...")
        browse_button.clicked.connect(self._browse_staging)
        source_row.addWidget(self.source_edit)
        source_row.addWidget(browse_button)
        form.addRow("Pack source:", source_row)

        self.pack_name_edit = QLineEdit()
        self.pack_name_edit.textEdited.connect(self._on_pack_name_edited)
        form.addRow("Pack name:", self.pack_name_edit)
        self.creator_edit = QLineEdit()
        form.addRow("Creator:", self.creator_edit)
        self.licence_edit = QLineEdit()
        form.addRow("Licence:", self.licence_edit)
        self.source_url_edit = QLineEdit()
        form.addRow("Source URL:", self.source_url_edit)

        layout.addLayout(form)

        hint = QLabel(
            "Pick a folder or a .zip from inside the staging folder -- either one, "
            "side by side. A .zip is extracted automatically at ingest time."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Ingest")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_pack_name_edited(self, _text: str) -> None:
        self._pack_name_auto = False

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
        self._selected_relative_path = relative_path
        self.source_edit.setText(relative_path)
        if self._pack_name_auto:
            name = Path(relative_path)
            self.pack_name_edit.setText(name.stem if browser.selected_is_zip else name.name)

    def _on_accept(self) -> None:
        pack_name = self.pack_name_edit.text().strip()
        if self._selected_relative_path is None or not pack_name:
            QMessageBox.warning(
                self, "Ingest Pack", "Pick a pack folder or zip file, and enter a pack name."
            )
            return
        self.pack_folder_name = self._selected_relative_path
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


class CorrectionsFormWidget(QWidget):
    """The up_axis/scale/material_fallback render-correction fields, shared
    by PackEditDialog and the post-ingest calibration review dialog so both
    edit the exact same fields the same way.
    """

    def __init__(self, initial: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        self.up_axis_combo = QComboBox()
        self.up_axis_combo.addItem("(default)", None)
        self.up_axis_combo.addItem("Y_UP", "Y_UP")
        self.up_axis_combo.addItem("Z_UP", "Z_UP")
        index = self.up_axis_combo.findData(initial.get("up_axis"))
        self.up_axis_combo.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Up axis:", self.up_axis_combo)

        self.scale_edit = QLineEdit()
        if "scale" in initial:
            self.scale_edit.setText(str(initial["scale"]))
        self.scale_edit.setPlaceholderText("(default) e.g. 1.0")
        form.addRow("Scale:", self.scale_edit)

        self.material_fallback_check = QCheckBox("Replace materials with a flat gray fallback")
        self.material_fallback_check.setChecked(bool(initial.get("material_fallback")))
        form.addRow("", self.material_fallback_check)

    def read(self) -> tuple[dict | None, str | None]:
        """Returns (corrections, error) -- corrections is None and error is
        a human-readable message if the scale field doesn't parse.
        """
        scale_text = self.scale_edit.text().strip()
        scale_value: float | None = None
        if scale_text:
            try:
                scale_value = float(scale_text)
            except ValueError:
                return None, "Scale must be a number."

        corrections: dict = {}
        up_axis = self.up_axis_combo.currentData()
        if up_axis:
            corrections["up_axis"] = up_axis
        if scale_value is not None:
            corrections["scale"] = scale_value
        corrections["material_fallback"] = self.material_fallback_check.isChecked()
        return corrections, None


class PackEditDialog(QDialog):
    """Everything about a pack that's stored in the database and editable
    after the fact -- name, creator/licence/source URL, and render
    corrections -- in one form, rather than a separate dialog per field.
    Renaming moves the pack's archived library folder to match (see
    packs.rename_pack); corrections here fully replace whatever was set via
    the CLI's 'pack set-corrections', same "edit form, not a delta patch"
    semantics as the other fields.
    """

    def __init__(self, detail: PackDetail, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Pack -- {detail.name}")
        self.resize(420, 320)
        self._pack_id = detail.id

        self.new_name: str = detail.name
        self.creator: str | None = detail.creator
        self.licence: str | None = detail.licence
        self.source_url: str | None = detail.source_url
        self.corrections: dict = {}
        self.notes: str | None = detail.notes
        self.rating: int | None = detail.rating

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(detail.name)
        form.addRow("Name:", self.name_edit)
        self.creator_edit = QLineEdit(detail.creator or "")
        form.addRow("Creator:", self.creator_edit)
        self.licence_edit = QLineEdit(detail.licence or "")
        form.addRow("Licence:", self.licence_edit)
        self.source_url_edit = QLineEdit(detail.source_url or "")
        form.addRow("Source URL:", self.source_url_edit)

        self.rating_spin = QSpinBox()
        self.rating_spin.setRange(0, 5)
        self.rating_spin.setValue(detail.rating or 0)
        self.rating_spin.setSpecialValueText("Unrated")
        self.rating_spin.setSuffix(" / 5")
        form.addRow("Rating:", self.rating_spin)

        layout.addLayout(form)

        layout.addWidget(QLabel("Notes (personal, not shown to anyone else):"))
        self.notes_edit = QPlainTextEdit(detail.notes or "")
        self.notes_edit.setPlaceholderText("e.g. \"great for sci-fi corridors\", \"textures are low-res\"")
        self.notes_edit.setMaximumHeight(70)
        layout.addWidget(self.notes_edit)

        layout.addWidget(QLabel(f"{detail.asset_count} asset(s) in this pack."))

        corrections_label = QLabel("Render corrections (applied to Blender thumbnails/conversions):")
        corrections_label.setWordWrap(True)
        layout.addWidget(corrections_label)

        self.corrections_widget = CorrectionsFormWidget(detail.corrections)
        layout.addWidget(self.corrections_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def pack_id(self) -> int:
        return self._pack_id

    def _on_accept(self) -> None:
        new_name = self.name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Edit Pack", "Pack name can't be blank.")
            return

        corrections, error = self.corrections_widget.read()
        if error:
            QMessageBox.warning(self, "Edit Pack", error)
            return

        self.new_name = new_name
        self.creator = self.creator_edit.text().strip() or None
        self.licence = self.licence_edit.text().strip() or None
        self.source_url = self.source_url_edit.text().strip() or None
        self.corrections = corrections
        self.notes = self.notes_edit.toPlainText().strip() or None
        self.rating = self.rating_spin.value() or None
        self.accept()


class TagEditDialog(QDialog):
    def __init__(self, tag: TagSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Tag -- {tag.name}")
        self.resize(360, 140)
        self._tag_id = tag.id

        self.new_name: str = tag.name
        self.new_category: str | None = tag.category

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(tag.name)
        form.addRow("Name:", self.name_edit)
        self.category_edit = QLineEdit(tag.category or "")
        form.addRow("Category (optional):", self.category_edit)
        layout.addLayout(form)

        layout.addWidget(QLabel(f"Used by {tag.usage_count} asset(s) -- renaming keeps them tagged."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def tag_id(self) -> int:
        return self._tag_id

    def _on_accept(self) -> None:
        new_name = self.name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Edit Tag", "Tag name can't be blank.")
            return
        self.new_name = new_name
        self.new_category = self.category_edit.text().strip() or None
        self.accept()


class ExportDialog(QDialog):
    """Copies the current grid selection out to a target project. Pre-fills
    the project folder with the most recently used one, if any (see
    Settings.recent_export_projects) -- always editable, never locked;
    unlike the retired Godot-specific version of this dialog, there's no
    opt-in toggle and no read-only trap. The DetailPanel's Export button
    (see below) is the fast path for one-click re-use of a recent project
    without opening this dialog at all; this dialog is for picking a
    different one, or setting a non-default destination subfolder.
    """

    def __init__(self, asset_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export to Project")
        self.resize(460, 180)
        self.project_root: Path | None = None
        self.dest_subfolder: str = "exported_assets"

        recent_projects = settings.load().recent_export_projects

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Export {asset_count} asset(s) into:"))

        form = QFormLayout()
        self.project_edit = QLineEdit(recent_projects[0] if recent_projects else "")
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self._browse_project)
        project_row = QHBoxLayout()
        project_row.addWidget(self.project_edit)
        project_row.addWidget(self.browse_button)
        form.addRow("Project folder:", project_row)

        self.dest_subfolder_edit = QLineEdit("exported_assets")
        form.addRow("Destination subfolder:", self.dest_subfolder_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Export")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_project(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Select project folder", self.project_edit.text()
        )
        if chosen:
            self.project_edit.setText(chosen)

    def _on_accept(self) -> None:
        text = self.project_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "Export to Project", "Pick a project folder.")
            return
        project_root = Path(text)
        if not project_root.is_dir():
            QMessageBox.warning(self, "Export to Project", f"Folder not found: {project_root}")
            return

        self.project_root = project_root
        self.dest_subfolder = self.dest_subfolder_edit.text().strip() or "exported_assets"
        self.accept()


class CreditsReportDialog(QDialog):
    """Generates a plain-text attribution report (creator/licence/source
    URL per pack) -- for the whole catalogue by default, or narrowed to
    just the packs actually exported into one project. A read-only preview
    plus Save As, rather than writing straight to a file, so it's easy to
    check before it ends up in a real credits screen.
    """

    def __init__(self, catalogue: Catalogue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalogue = catalogue
        self.setWindowTitle("Credits Report")
        self.resize(560, 480)

        layout = QVBoxLayout(self)

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Project folder:"))
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("(leave blank for the whole catalogue)")
        scope_row.addWidget(self.project_edit, stretch=1)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_project)
        scope_row.addWidget(browse_button)
        generate_button = QPushButton("Generate")
        generate_button.clicked.connect(self._generate)
        scope_row.addWidget(generate_button)
        layout.addLayout(scope_row)

        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        font = self.report_view.font()
        font.setFamily("Consolas")
        self.report_view.setFont(font)
        layout.addWidget(self.report_view, stretch=1)

        button_row = QHBoxLayout()
        save_button = QPushButton("Save As...")
        save_button.clicked.connect(self._save_as)
        button_row.addWidget(save_button)
        button_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self._generate()

    def _browse_project(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Select project folder", self.project_edit.text()
        )
        if chosen:
            self.project_edit.setText(chosen)
            self._generate()

    def _generate(self) -> None:
        text = self.project_edit.text().strip()
        project_root = Path(text) if text else None
        if project_root is not None and not project_root.is_dir():
            QMessageBox.warning(self, "Credits Report", f"Folder not found: {project_root}")
            return
        report = self._catalogue.generate_credits_report(project_root)
        self.report_view.setPlainText(report)

    def _save_as(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save Credits Report", "credits.txt", "Text files (*.txt);;All files (*)"
        )
        if not chosen:
            return
        Path(chosen).write_text(self.report_view.toPlainText(), encoding="utf-8")


class LibraryStatsDialog(QDialog):
    """A read-only snapshot of the library's size and composition -- total
    assets/size, breakdowns by type and thumbnail status, and the largest
    packs by size. Purely informational (no editing here), so it's just a
    formatted report rather than an interactive table.
    """

    def __init__(self, catalogue: Catalogue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Library Statistics")
        self.resize(480, 420)

        layout = QVBoxLayout(self)
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        font = self.report_view.font()
        font.setFamily("Consolas")
        self.report_view.setFont(font)
        layout.addWidget(self.report_view, stretch=1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self.report_view.setPlainText(library_stats.format_report(catalogue.get_library_stats()))


class _BackgroundWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit)
        except Exception as exc:  # noqa: BLE001 -- reported to the UI, not swallowed
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(result)


class ProgressLogDialog(QDialog):
    """Modal dialog shown during a background job: an indeterminate progress
    bar plus a live-appending text feed of what's happening, file by file --
    replaces a bare spinner so a long ingest/thumbnail/import/removal run
    isn't just a frozen-looking window.
    """

    def __init__(self, title: str, initial_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(520, 320)

        layout = QVBoxLayout(self)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        self._log = QPlainTextEdit(self)
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        if initial_text:
            self.append(initial_text)

    def append(self, text: str) -> None:
        self._log.appendPlainText(text)
        scrollbar = self._log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class CalibrationReviewDialog(QDialog):
    """Shown right after a pack's first-ever model ingest, when exactly one
    model was rendered as a calibration preview (see
    blender_render.generate_pack_thumbnails) and the rest were deliberately
    left un-rendered. Lets the user check the preview, adjust render
    corrections and re-render it in place as many times as needed, then
    choose how to proceed with the remaining models -- rather than just
    reporting the situation in a text message and requiring a separate trip
    through Edit Pack Metadata plus a menu action to act on it.

    self.result_action is one of "render_all", "skip", or "cancelled" once
    the dialog closes -- callers should treat a rejected/closed-via-X dialog
    the same as "skip" (nothing further needs undoing; the pack was already
    fully ingested before this dialog ever opened).
    """

    def __init__(
        self,
        catalogue: Catalogue,
        pack_id: int,
        pack_name: str,
        preview_asset_id: int,
        models_pending: int,
        corrections: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalogue = catalogue
        self._pack_id = pack_id
        self._pack_name = pack_name
        self._preview_asset_id = preview_asset_id
        self._models_pending = models_pending
        self._worker: _BackgroundWorker | None = None
        self.result_action = "skip"
        self.corrections = dict(corrections)

        self.setWindowTitle(f"Calibration Preview -- {pack_name}")
        self.resize(440, 620)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "This pack's models haven't been rendered before, so only one was "
            "rendered as a preview. Check it below -- if the orientation, scale, "
            "or materials look wrong, adjust the corrections and re-render before "
            f"rendering the remaining {models_pending} model(s)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setMinimumHeight(256)
        self._preview_label.setStyleSheet("background: #202020;")
        layout.addWidget(self._preview_label)
        self._reload_preview()

        self.corrections_widget = CorrectionsFormWidget(corrections)
        layout.addWidget(self.corrections_widget)

        self._rerender_button = QPushButton("Re-render Preview")
        self._rerender_button.clicked.connect(self._on_rerender)
        layout.addWidget(self._rerender_button)

        proceed_row = QHBoxLayout()
        self._render_all_button = QPushButton(f"Render Remaining {models_pending} Model(s)")
        self._render_all_button.clicked.connect(self._on_render_all)
        proceed_row.addWidget(self._render_all_button)

        skip_button = QPushButton("Skip for Now")
        skip_button.clicked.connect(self._on_skip)
        proceed_row.addWidget(skip_button)
        layout.addLayout(proceed_row)

        cancel_button = QPushButton("Cancel Import (Remove This Pack)")
        cancel_button.clicked.connect(self._on_cancel_import)
        layout.addWidget(cancel_button)

    def _reload_preview(self) -> None:
        asset = self._catalogue.get_asset(self._preview_asset_id)
        path = self._catalogue.thumbnail_path_for(asset.content_hash) if asset else None
        if path is not None:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                dpr = self.devicePixelRatioF()
                physical_size = QSize(round(256 * dpr), round(256 * dpr))
                scaled = pixmap.scaled(physical_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                self._preview_label.setPixmap(scaled)
                return
        self._preview_label.setText("(no preview available)")

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self._rerender_button.setEnabled(enabled)
        self._render_all_button.setEnabled(enabled)

    def _run_job(self, fn, progress_text: str, on_ok) -> None:
        self._set_buttons_enabled(False)
        progress = ProgressLogDialog("Asset Catalogue", progress_text, self)
        progress.show()

        worker = _BackgroundWorker(fn)

        def handle_ok(result) -> None:
            progress.close()
            self._set_buttons_enabled(True)
            on_ok(result)

        def handle_fail(message: str) -> None:
            progress.close()
            self._set_buttons_enabled(True)
            QMessageBox.critical(self, "Asset Catalogue", message)

        worker.progress.connect(progress.append, Qt.QueuedConnection)
        worker.finished_ok.connect(handle_ok, Qt.QueuedConnection)
        worker.failed.connect(handle_fail, Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_rerender(self) -> None:
        corrections, error = self.corrections_widget.read()
        if error:
            QMessageBox.warning(self, "Asset Catalogue", error)
            return

        def job(report):
            self._catalogue.set_pack_corrections_bg(self._pack_id, corrections)
            return self._catalogue.regenerate_model_thumbnail_bg(
                self._preview_asset_id, on_progress=report
            )

        def on_ok(stats) -> None:
            self.corrections = corrections
            self._reload_preview()
            if stats.failed:
                QMessageBox.warning(
                    self, "Asset Catalogue", "Re-render failed -- check the pack's source files."
                )

        self._run_job(job, "Re-rendering preview...", on_ok)

    def _on_render_all(self) -> None:
        def job(report):
            report("Checking Blender installation...")
            blender_exe = self._catalogue.resolve_blender()
            return self._catalogue.generate_model_thumbnails_bg(
                blender_exe, pack=self._pack_name, on_progress=report
            )

        def on_ok(stats) -> None:
            QMessageBox.information(
                self,
                "Asset Catalogue",
                f"Model thumbnails: {stats.generated} generated, "
                f"{stats.already_done} already done, {stats.failed} failed",
            )
            self.result_action = "render_all"
            self.accept()

        self._run_job(job, f"Rendering {self._models_pending} model thumbnail(s)...", on_ok)

    def _on_skip(self) -> None:
        self.result_action = "skip"
        self.accept()

    def _on_cancel_import(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Cancel Import",
            f"Remove '{self._pack_name}' and everything just ingested? This deletes its "
            "catalogue entries, thumbnails, and archived library copies. Files in the "
            "staging folder are never touched.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        def job(report):
            return self._catalogue.remove_pack_bg(self._pack_id, on_progress=report)

        def on_ok(_stats) -> None:
            self.result_action = "cancelled"
            self.accept()

        self._run_job(job, f"Removing '{self._pack_name}'...", on_ok)


class PendingConversionsDialog(QDialog):
    """Real review for pending glTF conversions -- what the status bar's
    pending-conversions badge opens now, replacing what used to be a bare
    "delete N originals?" confirmation (the same information the badge
    already gave) with an actual list of which assets, which packs, and
    when they were converted, so a revert/keep decision can be made with
    real context instead of just a count. Supports acting on a subset
    (select rows, Revert or Keep just those) as well as the original
    all-at-once bulk action.
    """

    def __init__(self, catalogue: Catalogue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalogue = catalogue
        self._worker: _BackgroundWorker | None = None
        self._asset_ids: list[int] = []
        self.setWindowTitle("Pending Conversions")
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "These model assets were converted to .glb, but the pre-conversion "
            "original hasn't been reverted or cleaned up yet. Select one or more "
            "rows to act on just those, or use Keep All to confirm every "
            "conversion listed here at once."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Pack", "Original File", "Converted To", "Converted At"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

        selection_row = QHBoxLayout()
        self.revert_button = QPushButton("Revert Selected")
        self.revert_button.clicked.connect(self._revert_selected)
        self.keep_button = QPushButton("Keep Selected (Delete Originals)")
        self.keep_button.clicked.connect(self._keep_selected)
        selection_row.addWidget(self.revert_button)
        selection_row.addWidget(self.keep_button)
        layout.addLayout(selection_row)

        bulk_row = QHBoxLayout()
        self.keep_all_button = QPushButton("Keep All (Delete All Originals)")
        self.keep_all_button.clicked.connect(self._keep_all)
        bulk_row.addWidget(self.keep_all_button)
        bulk_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        bulk_row.addWidget(close_button)
        layout.addLayout(bulk_row)

        self._refresh()

    def _refresh(self) -> None:
        rows = self._catalogue.list_pending_conversions()
        self._asset_ids = [row["asset_id"] for row in rows]
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(row["pack_name"]))
            self.table.setItem(i, 1, QTableWidgetItem(row["original_filename"]))
            self.table.setItem(i, 2, QTableWidgetItem(row["converted_filename"]))
            self.table.setItem(i, 3, QTableWidgetItem(row["converted_at"]))
        self.table.resizeColumnsToContents()
        has_rows = bool(rows)
        self.revert_button.setEnabled(has_rows)
        self.keep_button.setEnabled(has_rows)
        self.keep_all_button.setEnabled(has_rows)
        if not rows:
            # Nothing left to review -- close rather than leave an empty
            # table sitting open with every action already disabled.
            self.accept()

    def _selected_asset_ids(self) -> list[int]:
        selected_rows = {index.row() for index in self.table.selectedIndexes()}
        return [self._asset_ids[row] for row in selected_rows]

    def _run_job(self, fn, progress_text: str, on_ok) -> None:
        progress = ProgressLogDialog("Asset Catalogue", progress_text, self)
        progress.show()
        worker = _BackgroundWorker(fn)

        def handle_ok(result) -> None:
            progress.close()
            on_ok(result)

        def handle_fail(message: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Asset Catalogue", message)

        worker.progress.connect(progress.append, Qt.QueuedConnection)
        worker.finished_ok.connect(handle_ok, Qt.QueuedConnection)
        worker.failed.connect(handle_fail, Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _revert_selected(self) -> None:
        asset_ids = self._selected_asset_ids()
        if not asset_ids:
            QMessageBox.information(self, "Asset Catalogue", "Select at least one row first.")
            return
        confirm = QMessageBox.question(
            self,
            "Revert Conversions",
            f"Restore the pre-conversion original for {len(asset_ids)} asset(s) and "
            "discard the converted .glb?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        def job(report):
            reverted = 0
            for i, asset_id in enumerate(asset_ids, 1):
                report(f"Reverting asset {i}/{len(asset_ids)}...")
                if self._catalogue.revert_conversion_bg(asset_id):
                    reverted += 1
            return reverted

        def on_ok(reverted) -> None:
            QMessageBox.information(self, "Asset Catalogue", f"Reverted {reverted} asset(s).")
            self._refresh()

        self._run_job(job, f"Reverting {len(asset_ids)} asset(s)...", on_ok)

    def _keep_selected(self) -> None:
        asset_ids = self._selected_asset_ids()
        if not asset_ids:
            QMessageBox.information(self, "Asset Catalogue", "Select at least one row first.")
            return
        confirm = QMessageBox.question(
            self,
            "Delete Pre-Conversion Originals",
            f"Permanently delete the pre-conversion original for {len(asset_ids)} "
            "asset(s)? The converted .glb files are kept.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        def job(report):
            cleaned = 0
            for i, asset_id in enumerate(asset_ids, 1):
                report(f"Cleaning up asset {i}/{len(asset_ids)}...")
                if self._catalogue.cleanup_pending_conversion_bg(asset_id):
                    cleaned += 1
            return cleaned

        def on_ok(cleaned) -> None:
            QMessageBox.information(self, "Asset Catalogue", f"Deleted {cleaned} pre-conversion original(s).")
            self._refresh()

        self._run_job(job, f"Cleaning up {len(asset_ids)} asset(s)...", on_ok)

    def _keep_all(self) -> None:
        count = len(self._asset_ids)
        if count == 0:
            return
        confirm = QMessageBox.question(
            self,
            "Delete All Pre-Conversion Originals",
            f"Permanently delete the pre-conversion original for all {count} "
            "pending asset(s)? The converted .glb files are kept.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        def job(report):
            report(f"Cleaning up {count} asset(s)...")
            return self._catalogue.cleanup_all_pending_conversions_bg()

        def on_ok(cleaned) -> None:
            QMessageBox.information(self, "Asset Catalogue", f"Deleted {cleaned} pre-conversion original(s).")
            self._refresh()

        self._run_job(job, f"Cleaning up {count} asset(s)...", on_ok)


class TrashDialog(QDialog):
    """What "Move to Trash"/"Move N to Trash" (the grid's delete action --
    see MainWindow._remove_selected_assets) actually feeds: assets are
    hidden from the normal grid but nothing is deleted until explicitly
    confirmed here, either per-row/selection (Delete Permanently Selected)
    or all at once (Empty Trash). Restoring is instant and needs no
    confirmation -- it's just clearing a flag, no files are touched either
    way until an actual delete happens.
    """

    def __init__(self, catalogue: Catalogue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalogue = catalogue
        self._worker: _BackgroundWorker | None = None
        self._asset_ids: list[int] = []
        self.setWindowTitle("Trash")
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Assets moved to Trash stay in the catalogue -- thumbnails and archived "
            "library copies are untouched until you actually delete them here. "
            "Select one or more rows to act on just those, or use the buttons below "
            "for everything at once."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Pack", "File", "Trashed At"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

        selection_row = QHBoxLayout()
        self.restore_button = QPushButton("Restore Selected")
        self.restore_button.clicked.connect(self._restore_selected)
        self.delete_button = QPushButton("Delete Selected Permanently")
        self.delete_button.clicked.connect(self._delete_selected_permanently)
        selection_row.addWidget(self.restore_button)
        selection_row.addWidget(self.delete_button)
        layout.addLayout(selection_row)

        bulk_row = QHBoxLayout()
        self.empty_button = QPushButton("Empty Trash (Delete All Permanently)")
        self.empty_button.clicked.connect(self._empty_trash)
        bulk_row.addWidget(self.empty_button)
        bulk_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        bulk_row.addWidget(close_button)
        layout.addLayout(bulk_row)

        self._refresh()

    def _refresh(self) -> None:
        assets = self._catalogue.list_trashed_assets()
        self._asset_ids = [asset.id for asset in assets]
        self.table.setRowCount(len(assets))
        for i, asset in enumerate(assets):
            self.table.setItem(i, 0, QTableWidgetItem(asset.pack_name))
            self.table.setItem(i, 1, QTableWidgetItem(asset.filename))
            self.table.setItem(i, 2, QTableWidgetItem(asset.deleted_at or ""))
        self.table.resizeColumnsToContents()
        has_rows = bool(assets)
        self.restore_button.setEnabled(has_rows)
        self.delete_button.setEnabled(has_rows)
        self.empty_button.setEnabled(has_rows)
        if not assets:
            self.accept()

    def _selected_asset_ids(self) -> list[int]:
        selected_rows = {index.row() for index in self.table.selectedIndexes()}
        return [self._asset_ids[row] for row in selected_rows]

    def _run_job(self, fn, progress_text: str, on_ok) -> None:
        progress = ProgressLogDialog("Asset Catalogue", progress_text, self)
        progress.show()
        worker = _BackgroundWorker(fn)

        def handle_ok(result) -> None:
            progress.close()
            on_ok(result)

        def handle_fail(message: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Asset Catalogue", message)

        worker.progress.connect(progress.append, Qt.QueuedConnection)
        worker.finished_ok.connect(handle_ok, Qt.QueuedConnection)
        worker.failed.connect(handle_fail, Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _restore_selected(self) -> None:
        asset_ids = self._selected_asset_ids()
        if not asset_ids:
            QMessageBox.information(self, "Asset Catalogue", "Select at least one row first.")
            return
        count = self._catalogue.restore_assets(asset_ids)
        QMessageBox.information(self, "Asset Catalogue", f"Restored {count} asset(s).")
        self._refresh()

    def _delete_selected_permanently(self) -> None:
        asset_ids = self._selected_asset_ids()
        if not asset_ids:
            QMessageBox.information(self, "Asset Catalogue", "Select at least one row first.")
            return
        confirm = QMessageBox.question(
            self,
            "Delete Permanently",
            f"Permanently delete {len(asset_ids)} asset(s)? This deletes the catalogue "
            "entries, thumbnails, and any archived library copy -- the original files "
            "in your staging folder are untouched, but this cannot be undone here.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        def job(report):
            return self._catalogue.remove_assets_bg(asset_ids, on_progress=report)

        def on_ok(stats) -> None:
            QMessageBox.information(self, "Asset Catalogue", f"Permanently deleted {stats.removed} asset(s).")
            self._refresh()

        self._run_job(job, f"Deleting {len(asset_ids)} asset(s)...", on_ok)

    def _empty_trash(self) -> None:
        count = len(self._asset_ids)
        if count == 0:
            return
        confirm = QMessageBox.question(
            self,
            "Empty Trash",
            f"Permanently delete all {count} trashed asset(s)? This deletes the "
            "catalogue entries, thumbnails, and any archived library copy -- the "
            "original files in your staging folder are untouched, but this cannot "
            "be undone here.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        asset_ids = list(self._asset_ids)

        def job(report):
            report(f"Deleting {count} asset(s)...")
            return self._catalogue.remove_assets_bg(asset_ids, on_progress=report)

        def on_ok(stats) -> None:
            QMessageBox.information(self, "Asset Catalogue", f"Permanently deleted {stats.removed} asset(s).")
            self._refresh()

        self._run_job(job, f"Emptying Trash ({count} asset(s))...", on_ok)


class LibraryHealthDialog(QDialog):
    """A read-only scan for the ways the catalogue's records can drift from
    reality on disk -- a missing archived copy, a thumbnail file gone
    despite thumbnail_status saying 'done', or a staging source that's
    disappeared since ingest. Surfaced as an actionable list with two
    one-click fixes rather than a raw dump, but a genuinely broken asset
    (both staging and library copy gone) has no automatic fix -- Trash or
    Remove is the honest next step for those, done manually via the grid.
    """

    def __init__(self, catalogue: Catalogue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalogue = catalogue
        self._worker: _BackgroundWorker | None = None
        self._issue_asset_ids: list[int] = []
        self._issue_types: list[str] = []
        self.setWindowTitle("Library Health Check")
        self.resize(680, 440)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Pack", "File", "Issue", "Detail"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

        fix_row = QHBoxLayout()
        self.reset_thumb_button = QPushButton("Reset Selected Thumbnail Status")
        self.reset_thumb_button.setToolTip(
            "For 'missing thumbnail file' rows -- marks them pending again so the "
            "next thumbnail pass re-renders them."
        )
        self.reset_thumb_button.clicked.connect(self._reset_selected_thumbnails)
        fix_row.addWidget(self.reset_thumb_button)
        self.rearchive_button = QPushButton("Re-archive Selected from Staging")
        self.rearchive_button.setToolTip(
            "For 'missing library copy' rows -- re-copies from the staging source, "
            "if it's still there."
        )
        self.rearchive_button.clicked.connect(self._rearchive_selected)
        fix_row.addWidget(self.rearchive_button)
        layout.addLayout(fix_row)

        bottom_row = QHBoxLayout()
        rescan_button = QPushButton("Re-scan")
        rescan_button.clicked.connect(self._refresh)
        bottom_row.addWidget(rescan_button)
        bottom_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        bottom_row.addWidget(close_button)
        layout.addLayout(bottom_row)

        self._refresh()

    def _refresh(self) -> None:
        report = self._catalogue.check_library_health()
        self._issue_asset_ids = [issue.asset_id for issue in report.issues]
        self._issue_types = [issue.issue_type for issue in report.issues]
        self.table.setRowCount(len(report.issues))
        for i, issue in enumerate(report.issues):
            self.table.setItem(i, 0, QTableWidgetItem(issue.pack_name))
            self.table.setItem(i, 1, QTableWidgetItem(issue.filename))
            self.table.setItem(i, 2, QTableWidgetItem(issue.issue_type.replace("_", " ")))
            self.table.setItem(i, 3, QTableWidgetItem(issue.detail))
        self.table.resizeColumnsToContents()
        if report.issues:
            self.summary_label.setText(
                f"{len(report.issues)} issue(s) found across {report.checked_count} asset(s) checked."
            )
        else:
            self.summary_label.setText(f"No issues found -- {report.checked_count} asset(s) checked.")
        has_issues = bool(report.issues)
        self.reset_thumb_button.setEnabled(has_issues)
        self.rearchive_button.setEnabled(has_issues)

    def _selected_ids_for_issue(self, issue_type: str) -> list[int]:
        selected_rows = {index.row() for index in self.table.selectedIndexes()}
        return [
            self._issue_asset_ids[row]
            for row in selected_rows
            if self._issue_types[row] == issue_type
        ]

    def _reset_selected_thumbnails(self) -> None:
        asset_ids = self._selected_ids_for_issue(library_health.MISSING_THUMBNAIL_FILE)
        if not asset_ids:
            QMessageBox.information(
                self, "Asset Catalogue",
                "Select one or more 'missing thumbnail file' rows first.",
            )
            return
        count = self._catalogue.reset_broken_thumbnails(asset_ids)
        QMessageBox.information(
            self, "Asset Catalogue",
            f"Reset {count} asset(s) to pending -- the next thumbnail pass will re-render them.",
        )
        self._refresh()

    def _rearchive_selected(self) -> None:
        asset_ids = self._selected_ids_for_issue(library_health.MISSING_LIBRARY_COPY)
        if not asset_ids:
            QMessageBox.information(
                self, "Asset Catalogue",
                "Select one or more 'missing library copy' rows first.",
            )
            return

        progress = ProgressLogDialog("Asset Catalogue", f"Re-archiving {len(asset_ids)} asset(s)...", self)
        progress.show()
        worker = _BackgroundWorker(lambda report: self._catalogue.rearchive_assets_bg(asset_ids))

        def handle_ok(count) -> None:
            progress.close()
            QMessageBox.information(
                self, "Asset Catalogue",
                f"Re-archived {count} of {len(asset_ids)} asset(s) "
                f"({len(asset_ids) - count} had no staging source left to copy from).",
            )
            self._refresh()

        def handle_fail(message: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Asset Catalogue", message)

        worker.finished_ok.connect(handle_ok, Qt.QueuedConnection)
        worker.failed.connect(handle_fail, Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()


class MainWindow(QMainWindow):
    def __init__(self, catalogue: Catalogue) -> None:
        super().__init__()
        self._catalogue = catalogue
        self._current_assets: list[AssetSummary] = []
        self._selected_asset_id: int | None = None
        self._active_worker: _BackgroundWorker | None = None
        self._update_check_worker: _BackgroundWorker | None = None
        self.resize(1100, 700)
        self._update_window_title()

        self._build_menu()
        self._build_toolbar()

        self.filter_panel = FilterPanel(
            catalogue,
            self._refresh_grid,
            self._edit_pack,
            self._remove_pack,
            self._rename_tag,
            self._delete_tag,
            self._render_model_previews_for_selection,
        )
        self.grid = ThumbnailGrid()
        self.grid.itemSelectionChanged.connect(self._on_grid_selection_changed)
        self.grid.customContextMenuRequested.connect(self._show_grid_context_menu)
        self.grid.itemDoubleClicked.connect(self._on_grid_item_double_clicked)
        self.detail_panel = DetailPanel(
            catalogue,
            self._handle_tag_asset,
            self._handle_untag_asset,
            self._handle_bulk_tag_assets,
            self._handle_bulk_untag_assets,
            self._show_in_library_folder,
            self._handle_revert_conversion,
            self._handle_cleanup_conversion,
            self._filter_by_pack,
            self._export_selected_to_project,
            self._quick_export,
            self._handle_generate_thumbnail,
            self._handle_toggle_favorite,
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

        # A persistent, click-to-act reminder for pending glTF conversions
        # left unresolved (never reverted or cleaned up) -- easy to forget
        # about since nothing else in the UI surfaces it unless you're
        # looking at the specific asset. Lives in the status bar's
        # permanent-widget area so it survives every _refresh_grid() call
        # without competing with the transient "N asset(s)" message.
        self.pending_conversions_button = QPushButton()
        self.pending_conversions_button.setFlat(True)
        self.pending_conversions_button.setCursor(Qt.PointingHandCursor)
        self.pending_conversions_button.setStyleSheet(
            "QPushButton { color: #d9a441; border: none; padding: 2px 8px; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        self.pending_conversions_button.setToolTip(
            "Click to review and clean up pending glTF conversions"
        )
        self.pending_conversions_button.clicked.connect(self._open_pending_conversions_dialog)
        self.pending_conversions_button.setVisible(False)
        self.statusBar().addPermanentWidget(self.pending_conversions_button)

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
        remove_action = edit_menu.addAction("Move Selected to Trash")
        remove_action.setShortcut(QKeySequence.Delete)
        remove_action.triggered.connect(self._remove_selected_assets)

        # Selection-scoped actions that transform/export rather than edit
        # the catalogue directly, plus pack- and library-wide maintenance --
        # kept out of Edit so it doesn't become a junk drawer of every bulk
        # feature that's landed here over time.
        tools_menu = menu_bar.addMenu("&Tools")
        convert_action = tools_menu.addAction("Convert Selected to glTF (.glb)...")
        convert_action.triggered.connect(self._convert_selected_to_gltf)
        export_action = tools_menu.addAction("Export Selected to Project...")
        export_action.triggered.connect(self._export_selected_to_project)
        tools_menu.addSeparator()
        tag_pack_action = tools_menu.addAction("Tag Pack...")
        tag_pack_action.triggered.connect(self._open_tag_pack_dialog)
        tools_menu.addSeparator()
        cleanup_conversions_action = tools_menu.addAction("Clean Up Pre-Conversion Assets...")
        cleanup_conversions_action.triggered.connect(self._cleanup_all_pending_conversions)
        tools_menu.addSeparator()
        credits_action = tools_menu.addAction("Generate Credits Report...")
        credits_action.triggered.connect(self._open_credits_dialog)
        stats_action = tools_menu.addAction("Library Statistics...")
        stats_action.triggered.connect(self._open_library_stats_dialog)
        trash_action = tools_menu.addAction("View Trash...")
        trash_action.triggered.connect(self._open_trash_dialog)
        health_action = tools_menu.addAction("Check Library Integrity...")
        health_action.triggered.connect(self._open_library_health_dialog)

        thumbnails_menu = menu_bar.addMenu("&Thumbnails")
        gen_2d_action = thumbnails_menu.addAction("Generate 2D Thumbnails (current pack filter)")
        gen_2d_action.triggered.connect(self._generate_2d_thumbnails)
        gen_3d_action = thumbnails_menu.addAction(
            "Generate 3D Thumbnails via Blender (current pack filter)"
        )
        gen_3d_action.triggered.connect(self._generate_model_thumbnails)
        gen_audio_action = thumbnails_menu.addAction("Generate Audio Thumbnails (current pack filter)")
        gen_audio_action.triggered.connect(self._generate_audio_thumbnails)

        help_menu = menu_bar.addMenu("&Help")
        check_updates_action = help_menu.addAction("Check for Updates...")
        check_updates_action.triggered.connect(lambda: self._check_for_updates(silent=False))
        about_action = help_menu.addAction("About Asset Catalogue")
        about_action.triggered.connect(self._show_about_dialog)

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
        self.filter_panel = FilterPanel(
            self._catalogue,
            self._refresh_grid,
            self._edit_pack,
            self._remove_pack,
            self._rename_tag,
            self._delete_tag,
            self._render_model_previews_for_selection,
        )
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

        job = lambda report: self._catalogue.ingest_pack_bg(
            dialog.pack_folder_name,
            dialog.pack_name,
            dialog.creator,
            dialog.licence,
            dialog.source_url,
            on_progress=report,
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
            if stats.skipped_unrecognized_files or stats.skipped_engine_folders:
                message += (
                    f"\nSkipped {stats.skipped_unrecognized_files} unrecognized "
                    f"file(s) and {stats.skipped_engine_folders} project folder(s) "
                    "-- not a supported asset type"
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

        def on_complete(result: tuple) -> None:
            stats, _updated_fields = result
            if stats.calibration_preview and stats.preview_asset_id is not None:
                self._show_calibration_review(dialog.pack_name, stats)
            else:
                QMessageBox.information(self, "Asset Catalogue", format_result(result))
            self._rebuild_filter_panel()

        self._run_background_job(
            job,
            f"Ingesting '{dialog.pack_name}'...",
            format_result,
            self._rebuild_filter_panel,
            on_complete=on_complete,
        )

    def _show_calibration_review(self, pack_name: str, stats) -> None:
        detail = self._catalogue.get_pack_detail(pack_name)
        if detail is None:
            return
        dialog = CalibrationReviewDialog(
            self._catalogue,
            detail.id,
            pack_name,
            stats.preview_asset_id,
            stats.models_pending,
            detail.corrections,
            self,
        )
        dialog.exec()

    def _generate_2d_thumbnails(self) -> None:
        pack = self.filter_panel.selected_pack()
        self._run_background_job(
            lambda report: self._catalogue.generate_2d_thumbnails_bg(pack=pack, on_progress=report),
            "Generating 2D thumbnails...",
            lambda stats: (
                f"Thumbnails: {stats.generated} generated, "
                f"{stats.already_done} already done, {stats.failed} failed"
            ),
            self._refresh_grid,
        )

    def _generate_audio_thumbnails(self) -> None:
        pack = self.filter_panel.selected_pack()
        self._run_background_job(
            lambda report: self._catalogue.generate_audio_thumbnails_bg(pack=pack, on_progress=report),
            "Generating audio thumbnails...",
            lambda stats: (
                f"Audio thumbnails: {stats.generated} generated, "
                f"{stats.already_done} already done, {stats.failed} failed"
            ),
            self._refresh_grid,
        )

    def _generate_model_thumbnails(self) -> None:
        pack = self.filter_panel.selected_pack()

        def job(report):
            report("Checking Blender installation...")
            blender_exe = self._catalogue.resolve_blender()
            return self._catalogue.generate_model_thumbnails_bg(
                blender_exe, pack=pack, on_progress=report
            )

        self._run_background_job(
            job,
            "Generating 3D thumbnails via Blender... this can take a while.",
            lambda stats: (
                f"Model thumbnails: {stats.generated} generated, "
                f"{stats.already_done} already done, {stats.failed} failed"
            ),
            self._refresh_grid,
        )

    def _open_credits_dialog(self) -> None:
        dialog = CreditsReportDialog(self._catalogue, self)
        dialog.exec()

    def _open_library_stats_dialog(self) -> None:
        dialog = LibraryStatsDialog(self._catalogue, self)
        dialog.exec()

    def _open_trash_dialog(self) -> None:
        dialog = TrashDialog(self._catalogue, self)
        dialog.exec()
        self._refresh_grid()

    def _open_library_health_dialog(self) -> None:
        dialog = LibraryHealthDialog(self._catalogue, self)
        dialog.exec()
        self._refresh_grid()

    def _check_for_updates(self, silent: bool) -> None:
        """silent=True is the automatic on-launch check -- stays quiet if
        already up to date or if the check itself fails (no network, etc.),
        only ever speaking up when there's actually something to report.
        silent=False is the manual Help > Check for Updates... action,
        which always reports a real outcome either way.
        """

        def job(_report):
            return updater.check_for_update()

        worker = _BackgroundWorker(job)

        def on_ok(update_info) -> None:
            if update_info is not None:
                self._show_update_available(update_info)
            elif not silent:
                QMessageBox.information(
                    self, "Check for Updates", f"You're up to date (v{__version__})."
                )

        def on_fail(message: str) -> None:
            if not silent:
                QMessageBox.warning(
                    self, "Check for Updates", f"Couldn't check for updates: {message}"
                )

        worker.finished_ok.connect(on_ok, Qt.QueuedConnection)
        worker.failed.connect(on_fail, Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self._update_check_worker = worker
        worker.start()

    def _show_update_available(self, info: updater.UpdateInfo) -> None:
        s = settings.load()
        if s.skipped_update_version == info.latest_version:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Update Available")
        box.setIcon(QMessageBox.Information)
        box.setText(
            f"A new version is available: v{info.latest_version} "
            f"(you have v{info.current_version})."
        )
        if info.release_notes:
            box.setDetailedText(info.release_notes)
        open_button = box.addButton("Open Release Page", QMessageBox.AcceptRole)
        skip_button = box.addButton("Skip This Version", QMessageBox.DestructiveRole)
        box.addButton("Remind Me Later", QMessageBox.RejectRole)
        box.exec()

        if box.clickedButton() is open_button:
            webbrowser.open(info.release_url)
        elif box.clickedButton() is skip_button:
            s.skipped_update_version = info.latest_version
            settings.save(s)

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About Asset Catalogue",
            f"<b>Asset Catalogue</b> v{__version__}<br><br>"
            "Cataloguing, tagging, previewing and exporting game assets.<br><br>"
            "Licensed under the GNU GPLv3.<br><br>"
            "App icon by Gajah Mada "
            '(<a href="https://www.flaticon.com/authors/gajah-mada">flaticon.com</a>).',
        )

    def _open_tag_pack_dialog(self) -> None:
        if not self._catalogue.list_packs():
            QMessageBox.information(self, "Asset Catalogue", "No packs to tag yet -- ingest one first.")
            return
        dialog = TagPackDialog(self._catalogue, self.filter_panel.selected_pack(), self)
        if dialog.exec() != QDialog.Accepted:
            return

        self._run_background_job(
            lambda report: self._catalogue.tag_pack_bg(dialog.pack_name, dialog.tag_name, dialog.category),
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
            lambda report: self._catalogue.bulk_tag_assets_bg(asset_ids, tag_name),
            f"Tagging {len(asset_ids)} asset(s)...",
            lambda tagged: f"Tagged {tagged} asset(s) with '{tag_name}'",
            self._on_tags_changed,
        )

    def _handle_bulk_untag_assets(self, asset_ids: list[int], tag_name: str) -> None:
        self._run_background_job(
            lambda report: self._catalogue.bulk_untag_assets_bg(asset_ids, tag_name),
            f"Removing '{tag_name}' from {len(asset_ids)} asset(s)...",
            lambda removed: f"Removed '{tag_name}' from {removed} asset(s)",
            self._on_tags_changed,
        )

    def _convert_selected_to_gltf(self) -> None:
        """Edit > Tools menu entry point -- same eligibility logic as the
        grid's right-click Convert action (single-asset vs batch dispatch
        included), just reachable without right-clicking anything.
        """
        selected = self.grid.selectedItems()
        if not selected:
            QMessageBox.information(self, "Asset Catalogue", "No assets selected.")
            return
        selected_ids = {item.data(Qt.UserRole) for item in selected}
        eligible_ids = [
            asset.id
            for asset in self._current_assets
            if asset.id in selected_ids
            and asset.asset_type == "model"
            and not asset.relative_path.lower().endswith(".glb")
        ]
        if not eligible_ids:
            QMessageBox.information(
                self,
                "Asset Catalogue",
                "None of the selected assets can be converted (already .glb, or not a model).",
            )
            return
        if len(eligible_ids) == 1:
            self._convert_asset_to_gltf(eligible_ids[0])
        else:
            self._convert_assets_to_gltf(eligible_ids)

    def _convert_asset_to_gltf(self, asset_id: int) -> None:
        def format_result(result) -> str:
            if not result.ok:
                return f"Conversion failed: {result.error}"
            return (
                "Converted to .glb and regenerated its thumbnail. The pre-conversion "
                "original is kept until you Revert or Delete it in the detail panel."
            )

        self._run_background_job(
            lambda report: self._catalogue.convert_asset_to_gltf_bg(asset_id, on_progress=report),
            "Converting to .glb via Blender...",
            format_result,
            self._on_conversion_changed,
        )

    def _convert_assets_to_gltf(self, asset_ids: list[int]) -> None:
        def format_result(result) -> str:
            message = f"Converted {result.converted} to .glb (thumbnails regenerated)."
            if result.skipped:
                message += f"\nSkipped {result.skipped} (not a model asset, or already .glb)."
            if result.failed:
                message += f"\nFailed {result.failed}:"
                for error_message in result.errors:
                    message += f"\n  {error_message}"
            return message

        self._run_background_job(
            lambda report: self._catalogue.convert_assets_to_gltf_bg(asset_ids, on_progress=report),
            f"Converting {len(asset_ids)} asset(s) to .glb via Blender...",
            format_result,
            self._on_conversion_changed,
        )

    def _handle_revert_conversion(self, asset_id: int) -> None:
        confirm = QMessageBox.question(
            self,
            "Revert Conversion",
            "Restore the pre-conversion original and discard the converted .glb?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._run_background_job(
            lambda report: self._catalogue.revert_conversion_bg(asset_id, on_progress=report),
            "Reverting conversion...",
            lambda reverted: "Reverted to the pre-conversion original." if reverted else "Nothing to revert.",
            self._on_conversion_changed,
        )

    def _handle_cleanup_conversion(self, asset_id: int) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete Pre-Conversion Original",
            "Permanently delete the pre-conversion original? The converted .glb is kept.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._run_background_job(
            lambda report: self._catalogue.cleanup_pending_conversion_bg(asset_id),
            "Deleting pre-conversion original...",
            lambda cleaned: "Deleted the pre-conversion original." if cleaned else "Nothing to clean up.",
            self._refresh_grid,
        )

    def _handle_generate_thumbnail(self, asset_id: int, asset_type: str) -> None:
        if asset_type == "texture":
            job = lambda report: self._catalogue.generate_2d_thumbnails_bg(asset_id=asset_id, on_progress=report)
        elif asset_type == "audio":
            job = lambda report: self._catalogue.generate_audio_thumbnails_bg(asset_id=asset_id, on_progress=report)
        elif asset_type == "model":
            job = lambda report: self._catalogue.regenerate_model_thumbnail_bg(asset_id, on_progress=report)
        else:
            return

        self._run_background_job(
            job,
            "Generating thumbnail...",
            lambda stats: (
                f"Thumbnail: {stats.generated} generated, "
                f"{stats.already_done} already done, {stats.failed} failed"
            ),
            self._refresh_grid,
        )

    def _regenerate_thumbnails(self, asset_ids: list[int]) -> None:
        """Force-regenerates thumbnails for an arbitrary selection -- unlike
        the detail panel's conditional "Generate Thumbnail" button, this
        always re-renders regardless of current thumbnail_status (e.g. to
        freshen already-'done' thumbnails after a resolution change). Assets
        not in THUMBNAIL_CAPABLE_TYPES are expected to already be filtered
        out by the caller (see _build_grid_context_menu).
        """
        by_type: dict[str, list[int]] = {"texture": [], "audio": [], "model": []}
        for asset in self._current_assets:
            if asset.id in asset_ids and asset.asset_type in by_type:
                by_type[asset.asset_type].append(asset.id)

        def job(report):
            generated = already_done = failed = 0
            if by_type["texture"]:
                stats = self._catalogue.generate_2d_thumbnails_bg(
                    asset_ids=by_type["texture"], on_progress=report
                )
                generated += stats.generated
                already_done += stats.already_done
                failed += stats.failed
            if by_type["audio"]:
                stats = self._catalogue.generate_audio_thumbnails_bg(
                    asset_ids=by_type["audio"], on_progress=report
                )
                generated += stats.generated
                already_done += stats.already_done
                failed += stats.failed
            if by_type["model"]:
                stats = self._catalogue.regenerate_model_thumbnail_bg(
                    asset_ids=by_type["model"], on_progress=report
                )
                generated += stats.generated
                already_done += stats.already_done
                failed += stats.failed
            return generated, already_done, failed

        self._run_background_job(
            job,
            "Regenerating thumbnails...",
            lambda result: (
                f"Thumbnails: {result[0]} generated, {result[1]} already done, {result[2]} failed"
            ),
            self._refresh_grid,
        )

    def _handle_toggle_favorite(self, asset_id: int, favorite: bool) -> None:
        self._catalogue.set_favorite([asset_id], favorite)
        self._refresh_grid()

    def _set_favorite_for_selection(self, asset_ids: list[int], favorite: bool) -> None:
        self._catalogue.set_favorite(asset_ids, favorite)
        self._refresh_grid()

    def _cleanup_all_pending_conversions(self) -> None:
        pending_count = self._catalogue.count_pending_conversions()
        if pending_count == 0:
            QMessageBox.information(self, "Asset Catalogue", "No pending conversions to clean up.")
            return
        confirm = QMessageBox.question(
            self,
            "Clean Up Pre-Conversion Assets",
            f"Permanently delete the pre-conversion original for {pending_count} asset(s)? "
            "The converted .glb files are kept.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._run_background_job(
            lambda report: self._catalogue.cleanup_all_pending_conversions_bg(),
            "Cleaning up pre-conversion originals...",
            lambda count: f"Deleted {count} pre-conversion original(s).",
            self._refresh_grid,
        )

    def _open_model_preview(self, filename: str, asset_id: int, content_hash: str) -> None:
        """The preview .glb is generated on demand, not automatically at
        ingest/thumbnail time (see Catalogue.render_model_previews_bg) --
        rendering it for every model up front made ingesting/bulk-rendering
        a large pack noticeably slower for previews most people would
        never open. If this asset doesn't have one cached yet, this
        renders just that one asset first (same background job, same
        progress dialog), then opens the viewer once it's ready.
        """

        def job(report):
            preview_path = self._catalogue.model_preview_path_for(content_hash)
            if preview_path is None:
                report("Rendering 3D preview (first time for this asset)...")
                self._catalogue.render_model_previews_bg([asset_id], on_progress=report)
                preview_path = self._catalogue.model_preview_path_for(content_hash)
            if preview_path is None:
                raise RuntimeError("Couldn't render a 3D preview for this asset.")

            report("Loading 3D preview...")
            # Imported lazily, on this background thread -- pyqtgraph/
            # PyOpenGL/trimesh are only ever loaded into memory if someone
            # actually opens a 3D preview, not on every app launch. The
            # import itself (and the file parsing/color-baking below) is
            # pure CPU work with no Qt/OpenGL calls, so it's safe here --
            # this used to run synchronously on the GUI thread with zero
            # feedback, which is what made the first 3D preview in a
            # session look like the whole app had frozen or crashed (a
            # cold import of these libraries alone took over a second in
            # testing, done invisibly).
            from asset_catalogue.ui.model_preview_dialog import load_preview_parts

            return load_preview_parts(preview_path)

        def on_complete(parts) -> None:
            from asset_catalogue.ui.model_preview_dialog import Model3DPreviewDialog

            dialog = Model3DPreviewDialog(filename, parts, self)
            dialog.exec()

        self._run_background_job(job, "Rendering 3D preview...", None, None, on_complete=on_complete)

    def _render_model_previews_for_selection(self, asset_ids: list[int]) -> None:
        """Proactively pre-generates the interactive 3D preview cache for a
        grid selection or a whole pack, without opening the viewer --
        assets that already have one cached are skipped. The deliberate,
        explicit alternative to generating previews automatically for
        every model (see render_model_previews_bg).
        """
        self._run_background_job(
            lambda report: self._catalogue.render_model_previews_bg(asset_ids, on_progress=report),
            f"Rendering 3D preview(s) for {len(asset_ids)} asset(s)...",
            lambda stats: (
                f"3D previews: {stats.generated} rendered, "
                f"{stats.already_done} already cached, {stats.failed} failed"
            ),
            self._refresh_grid,
        )

    def _open_pending_conversions_dialog(self) -> None:
        dialog = PendingConversionsDialog(self._catalogue, self)
        dialog.exec()
        self._refresh_grid()

    def _filter_by_pack(self, pack_name: str) -> None:
        match = self.filter_panel.pack_list.findItems(pack_name, Qt.MatchExactly)
        if match:
            self.filter_panel.pack_list.setCurrentItem(match[0])

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

    def _run_background_job(
        self, fn, progress_text: str, format_result, on_success_refresh, on_complete=None
    ) -> None:
        """Runs fn (which must accept a `report` callback as its only
        argument) on a background thread with a live progress feed. On
        success, either calls on_complete(result) if given -- for a caller
        that needs to do something other than "show a message box, then
        refresh" (see the ingest call site's calibration-preview handling)
        -- or the default: show format_result(result) in a message box,
        then call on_success_refresh().
        """
        # self._active_worker is a single shared slot -- starting a second
        # job while one is still running would drop the only Python
        # reference to its QThread while the underlying thread is still
        # alive natively, a real crash risk ("QThread: Destroyed while
        # thread is still running"). ProgressLogDialog is modal, so real
        # mouse/keyboard input can't normally trigger this, but guard it
        # directly rather than relying on that alone.
        if self._active_worker is not None and self._active_worker.isRunning():
            QMessageBox.information(
                self, "Asset Catalogue", "Another background job is already running -- please wait for it to finish."
            )
            return

        progress = ProgressLogDialog("Asset Catalogue", progress_text, self)
        progress.show()

        worker = _BackgroundWorker(fn)

        def on_progress(text: str) -> None:
            progress.append(text)

        def on_ok(result) -> None:
            progress.close()
            if on_complete is not None:
                on_complete(result)
            else:
                QMessageBox.information(self, "Asset Catalogue", format_result(result))
                on_success_refresh()

        def on_fail(message: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Asset Catalogue", message)

        def on_thread_finished() -> None:
            # Runs after deleteLater() is scheduled but before the C++
            # object is actually destroyed -- clearing the reference here
            # (rather than leaving self._active_worker pointing at a
            # worker that's about to become invalid) is what makes the
            # "already running" guard above safe to check at any time,
            # instead of risking a shiboken "already deleted" error on a
            # stale reference.
            if self._active_worker is worker:
                self._active_worker = None
            worker.deleteLater()

        worker.progress.connect(on_progress, Qt.QueuedConnection)
        worker.finished_ok.connect(on_ok, Qt.QueuedConnection)
        worker.failed.connect(on_fail, Qt.QueuedConnection)
        worker.finished.connect(on_thread_finished)
        self._active_worker = worker
        worker.start()

    def _refresh_grid(self) -> None:
        self._current_assets = self._catalogue.list_assets(
            pack=self.filter_panel.selected_pack(),
            asset_type=self.filter_panel.selected_type(),
            tag=self.filter_panel.selected_tag(),
            extension=self.filter_panel.selected_format(),
            search=self.filter_panel.selected_search(),
            favorites_only=self.filter_panel.favorites_only(),
        )
        self.grid.set_assets(self._current_assets, self._catalogue)
        self.grid.select_asset_id(self._selected_asset_id)
        self.statusBar().showMessage(f"{len(self._current_assets)} asset(s)")
        self._update_pending_conversions_badge()

    def _update_pending_conversions_badge(self) -> None:
        count = self._catalogue.count_pending_conversions()
        if count:
            noun = "conversion" if count == 1 else "conversions"
            self.pending_conversions_button.setText(f"⚠ {count} pending {noun} -- click to review")
            self.pending_conversions_button.setVisible(True)
        else:
            self.pending_conversions_button.setVisible(False)

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
            selected_ids = {item.data(Qt.UserRole) for item in selected}
            assets = [a for a in self._current_assets if a.id in selected_ids]
            self.detail_panel.show_multi_selection(assets)
        else:
            self.detail_panel.clear_selection()

    def _on_grid_item_double_clicked(self, item) -> None:
        asset_id = item.data(Qt.UserRole)
        asset = next((a for a in self._current_assets if a.id == asset_id), None)
        if asset is None:
            return
        dialog = ThumbnailPreviewDialog(asset, self._catalogue, self._open_model_preview, self)
        dialog.exec()

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

        selected_ids = {item.data(Qt.UserRole) for item in selected}
        menu = QMenu(self)
        if len(selected) == 1:
            asset_id = selected[0].data(Qt.UserRole)
            asset = next((a for a in self._current_assets if a.id == asset_id), None)
            if asset is not None:
                fav_label = "★ Remove from Favorites" if asset.favorite else "☆ Add to Favorites"
                fav_action = menu.addAction(fav_label)
                fav_action.triggered.connect(
                    lambda: self._set_favorite_for_selection([asset.id], not asset.favorite)
                )
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
                if asset.asset_type in THUMBNAIL_CAPABLE_TYPES:
                    regen_action = menu.addAction("Regenerate Thumbnail")
                    regen_action.triggered.connect(
                        lambda: self._regenerate_thumbnails([asset.id])
                    )
                if asset.asset_type == "model":
                    has_preview = self._catalogue.model_preview_path_for(asset.content_hash) is not None
                    preview_label = "3D Preview (Orbit/Zoom)..." if has_preview else "3D Preview (renders on open)..."
                    preview_action = menu.addAction(preview_label)
                    preview_action.triggered.connect(
                        lambda: self._open_model_preview(asset.filename, asset.id, asset.content_hash)
                    )
                if asset.asset_type == "model" and not asset.relative_path.lower().endswith(".glb"):
                    convert_action = menu.addAction("Convert to glTF (.glb)...")
                    convert_action.triggered.connect(
                        lambda: self._convert_asset_to_gltf(asset.id)
                    )
                    convert_action.setEnabled(
                        not self._catalogue.has_pending_conversion(asset.id)
                    )
                if asset.asset_type == "audio":
                    play_label = "Stop" if self.detail_panel.is_playing() else "▶ Play"
                    play_action = menu.addAction(play_label)
                    play_action.triggered.connect(self.detail_panel.toggle_playback)
                    play_action.setEnabled(self.detail_panel.is_audio_playable())
                menu.addSeparator()
        else:
            add_fav_action = menu.addAction(f"★ Add {len(selected)} to Favorites")
            add_fav_action.triggered.connect(
                lambda: self._set_favorite_for_selection(list(selected_ids), True)
            )
            remove_fav_action = menu.addAction(f"☆ Remove {len(selected)} from Favorites")
            remove_fav_action.triggered.connect(
                lambda: self._set_favorite_for_selection(list(selected_ids), False)
            )
            thumbnail_eligible_ids = [
                asset.id
                for asset in self._current_assets
                if asset.id in selected_ids and asset.asset_type in THUMBNAIL_CAPABLE_TYPES
            ]
            if thumbnail_eligible_ids:
                regen_action = menu.addAction(
                    f"Regenerate {len(thumbnail_eligible_ids)} Thumbnail(s)"
                )
                regen_action.triggered.connect(
                    lambda: self._regenerate_thumbnails(thumbnail_eligible_ids)
                )
            model_ids = [
                asset.id
                for asset in self._current_assets
                if asset.id in selected_ids and asset.asset_type == "model"
            ]
            if model_ids:
                render_preview_action = menu.addAction(f"Render {len(model_ids)} 3D Preview(s)")
                render_preview_action.triggered.connect(
                    lambda: self._render_model_previews_for_selection(model_ids)
                )
            eligible_ids = [
                asset.id
                for asset in self._current_assets
                if asset.id in selected_ids
                and asset.asset_type == "model"
                and not asset.relative_path.lower().endswith(".glb")
            ]
            if eligible_ids:
                convert_action = menu.addAction(f"Convert {len(eligible_ids)} to glTF (.glb)...")
                convert_action.triggered.connect(
                    lambda: self._convert_assets_to_gltf(eligible_ids)
                )
            menu.addSeparator()

        export_label = "Export to Project..." if len(selected) == 1 else f"Export {len(selected)} to Project..."
        export_action = menu.addAction(export_label)
        export_action.triggered.connect(self._export_selected_to_project)
        menu.addSeparator()

        remove_label = "Move to Trash" if len(selected) == 1 else f"Move {len(selected)} to Trash"
        remove_action = menu.addAction(remove_label)
        remove_action.triggered.connect(self._remove_selected_assets)

        return menu

    def _export_selected_to_project(self) -> None:
        selected_ids = [item.data(Qt.UserRole) for item in self.grid.selectedItems()]
        if not selected_ids:
            QMessageBox.information(self, "Asset Catalogue", "No assets selected.")
            return
        if self._catalogue.staging_folder() is None:
            QMessageBox.warning(
                self, "Asset Catalogue", "Configure a staging folder in Settings first."
            )
            return

        dialog = ExportDialog(len(selected_ids), self)
        if dialog.exec() != QDialog.Accepted:
            return

        self._run_export_job(selected_ids, str(dialog.project_root), dialog.dest_subfolder)

    def _quick_export(self, project_root: str) -> None:
        """One-click export of the current selection to a specific,
        already-known project folder (a recent project picked from the
        DetailPanel's Export button, or the button itself when it already
        has a last-used project) -- skips ExportDialog entirely, always
        using the default destination subfolder.
        """
        selected_ids = [item.data(Qt.UserRole) for item in self.grid.selectedItems()]
        if not selected_ids:
            QMessageBox.information(self, "Asset Catalogue", "No assets selected.")
            return
        if self._catalogue.staging_folder() is None:
            QMessageBox.warning(
                self, "Asset Catalogue", "Configure a staging folder in Settings first."
            )
            return
        self._run_export_job(selected_ids, project_root, "exported_assets")

    def _run_export_job(self, selected_ids: list[int], project_root: str, dest_subfolder: str) -> None:
        self._run_background_job(
            lambda report: self._catalogue.export_assets_bg(
                selected_ids, project_root, dest_subfolder, on_progress=report
            ),
            f"Exporting {len(selected_ids)} asset(s)...",
            lambda stats: f"Exported {stats.copied} asset(s) into {project_root}",
            lambda: self._remember_export_project(project_root),
        )

    def _remember_export_project(self, project_root: str) -> None:
        s = settings.load()
        resolved = str(Path(project_root).resolve())
        recents = [path for path in s.recent_export_projects if path != resolved]
        recents.insert(0, resolved)
        s.recent_export_projects = recents[:5]
        settings.save(s)
        self.detail_panel.refresh_export_button()

    def _remove_selected_assets(self) -> None:
        """Moves the selection to Trash rather than deleting outright --
        catalogue rows, thumbnails, and the archived library copy all stay
        exactly as they are, just hidden from the normal grid (see
        removal.trash_assets). Reversible via Tools > View Trash..., so no
        confirmation prompt is needed -- unlike a real, file-touching
        delete (Remove Pack, or the Trash dialog's own Delete Permanently).
        """
        selected_ids = [item.data(Qt.UserRole) for item in self.grid.selectedItems()]
        if not selected_ids:
            QMessageBox.information(self, "Asset Catalogue", "No assets selected.")
            return
        count = self._catalogue.trash_assets(selected_ids)
        self._refresh_grid()
        self.statusBar().showMessage(
            f"Moved {count} asset(s) to Trash (Tools > View Trash... to restore)."
        )

    def _on_tags_changed(self) -> None:
        self.filter_panel.refresh_tags(self._catalogue)
        self.detail_panel.refresh_tag_completer()
        self._refresh_grid()

    def _on_conversion_changed(self) -> None:
        # Conversion (and reverting one) changes an asset's extension --
        # keep the Format filter's choices in sync (e.g. a first .glb
        # appearing) without resetting the pack/type/tag filters the way a
        # full _rebuild_filter_panel would.
        self.filter_panel.refresh_formats(self._catalogue)
        self._refresh_grid()

    def _on_pack_changed(self, select_pack: str | None = None) -> None:
        # Renaming or removing a pack can change the pack list itself, tag
        # usage counts, and which formats still exist -- refresh all three
        # in place rather than a full _rebuild_filter_panel (which would
        # also reset the type/format/tag filters the user may still want).
        # select_pack keeps a rename's new name selected instead of falling
        # back to "All packs" just because the old name vanished.
        self.filter_panel.refresh_packs(self._catalogue, select=select_pack)
        self.filter_panel.refresh_tags(self._catalogue)
        self.filter_panel.refresh_formats(self._catalogue)
        self._refresh_grid()

    def _edit_pack(self, pack_name: str) -> None:
        detail = self._catalogue.get_pack_detail(pack_name)
        if detail is None:
            return
        dialog = PackEditDialog(detail, self)
        if dialog.exec() != QDialog.Accepted:
            return

        def format_result(_result) -> str:
            return f"Updated '{dialog.new_name}'."

        self._run_background_job(
            lambda report: self._catalogue.update_pack_bg(
                dialog.pack_id, dialog.new_name, dialog.creator, dialog.licence,
                dialog.source_url, dialog.corrections, dialog.notes, dialog.rating,
            ),
            "Saving pack...",
            format_result,
            lambda: self._on_pack_changed(select_pack=dialog.new_name),
        )

    def _remove_pack(self, pack_name: str) -> None:
        detail = self._catalogue.get_pack_detail(pack_name)
        if detail is None:
            return
        confirm = QMessageBox.question(
            self,
            "Remove Pack",
            f"Remove '{pack_name}' and all {detail.asset_count} of its asset(s) from the "
            "catalogue?\n\nThis deletes the catalogue entries, thumbnails, and the pack's "
            "entire archived library copy. The original files in your staging folder are "
            "untouched.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._run_background_job(
            lambda report: self._catalogue.remove_pack_bg(detail.id, on_progress=report),
            f"Removing '{pack_name}'...",
            lambda stats: f"Removed '{pack_name}' ({stats.removed_assets} asset(s)).",
            self._on_pack_changed,
        )

    def _rename_tag(self, tag_name: str) -> None:
        tag = next((t for t in self._catalogue.list_tags() if t.name == tag_name), None)
        if tag is None:
            return
        dialog = TagEditDialog(tag, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self._catalogue.rename_tag(dialog.tag_id, dialog.new_name, dialog.new_category)
        except ValueError as exc:
            QMessageBox.critical(self, "Edit Tag", str(exc))
            return
        self._on_tags_changed()

    def _delete_tag(self, tag_name: str) -> None:
        tag = next((t for t in self._catalogue.list_tags() if t.name == tag_name), None)
        if tag is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete Tag",
            f"Delete '{tag_name}' from the tag vocabulary? It will be removed from all "
            f"{tag.usage_count} asset(s) currently carrying it.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._catalogue.delete_tag(tag.id)
        self._on_tags_changed()


def _try_open_catalogue() -> tuple[Catalogue | None, str | None]:
    try:
        return Catalogue.open(), None
    except (RuntimeError, OSError) as exc:
        return None, str(exc)


class _StartupSplash(QWidget):
    """Icon, name, credit, version -- shown while the app is doing the two
    things that take long enough to be worth covering for: opening the
    catalogue database, and the OpenGL pre-warm below (a cold
    pyqtgraph/PyOpenGL import measured over a second on its own). Frameless
    and always-on-top (Qt.SplashScreen is the purpose-built window type for
    exactly this), closed via finish() once the real main window is ready
    to take over.
    """

    def __init__(self) -> None:
        super().__init__(None, Qt.SplashScreen | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(320, 220)
        self.setStyleSheet(
            "QWidget { background-color: #202225; border: 1px solid #3a3d42; }"
            "QLabel { color: #e8e8e8; background: transparent; border: none; }"
        )

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(6)

        icon_label = QLabel()
        icon_pixmap = QPixmap(str(paths.package_dir() / "app_icon.png"))
        if not icon_pixmap.isNull():
            icon_label.setPixmap(
                icon_pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        name_label = QLabel("Asset Catalogue")
        name_font = name_label.font()
        name_font.setPointSize(15)
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(name_label)

        credit_label = QLabel("by AnthraxDelta7")
        credit_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(credit_label)

        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet("color: #9a9a9a;")
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)

        screen = QApplication.primaryScreen()
        if screen is not None:
            center = screen.geometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)


def _prewarm_opengl(window: "MainWindow") -> None:
    """Pays -- once, at launch, before the main window's first paint --
    the one-time cost that otherwise showed up as a visible flicker the
    first time someone opened a 3D preview. QSurfaceFormat.setDefaultFormat()
    in main() (see the comment there) turned out not to be the actual
    mechanism: GLViewWidget never calls setFormat() itself, it only reads
    whatever format Qt handed it -- confirmed by reading pyqtgraph's own
    source. The real cause is a Windows/Qt behavior that has nothing to
    do with GL capabilities: the first time any QOpenGLWidget-based
    widget is realized inside a top-level window, Windows' compositor has
    to switch that window's whole backing surface to one that can host
    GPU-composited OpenGL content, and that switch is visible as a flash,
    regardless of which depth/stencil/profile bits were requested. It
    happens exactly once per process, on whichever window hosts the first
    such widget.

    Called with `window` not yet shown: a child widget's .show() is
    deferred by Qt until its top-level parent actually becomes visible,
    so building the dummy GL widget here and calling window.show()
    afterward realizes both together in a single step -- the window comes
    into existence already GL-capable, with no separate "before" state
    for the flicker to show up against, rather than just moving the
    flicker earlier (which building it right after window.show() would
    still do).

    Blocks briefly (the cold pyqtgraph/PyOpenGL import, ~1s+ measured) --
    acceptable here specifically because a splash screen is already
    covering for it; this is not meant to be called once the app is
    actually running. Silent on failure (e.g. no OpenGL driver at all) --
    worst case, the flicker just reappears on first real use, exactly
    like before this existed.
    """
    try:
        import pyqtgraph.opengl as gl

        warm = gl.GLViewWidget(window)
        warm.setGeometry(0, 0, 1, 1)
        warm.show()
        QTimer.singleShot(200, warm.deleteLater)
    except Exception:  # noqa: BLE001 -- purely a nice-to-have, never worth crashing startup over
        pass


def main() -> None:
    # Both must happen before QApplication is constructed to take effect.
    #
    # AA_ShareOpenGLContexts: each new pyqtgraph GLViewWidget (the 3D
    # preview -- see model_preview_dialog.py) otherwise gets its own
    # separate OpenGL context, but pyqtgraph's compiled shader programs
    # are cached globally by name, not per-context -- opening a second 3D
    # preview in the same session reused a program handle that belonged
    # to the first (now-different) context, raising GL_INVALID_VALUE on
    # every draw call and, in the packaged .exe, taking the whole app
    # down with it. Sharing one context across every OpenGL-backed widget
    # in the app fixes both.
    #
    # QSurfaceFormat.setDefaultFormat: reasonable GL capabilities (a real
    # depth buffer) for every window up front. Doesn't fix the startup
    # flicker on its own (see _prewarm_opengl's docstring for the actual
    # mechanism and fix) but there's no reason not to have it set anyway.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    surface_format = QSurfaceFormat()
    surface_format.setDepthBufferSize(24)
    QSurfaceFormat.setDefaultFormat(surface_format)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(paths.package_dir() / "app_icon.png")))

    splash = _StartupSplash()
    splash.show()
    app.processEvents()

    # A library folder is required above everything else -- nothing else in
    # the app can run without one, so this loops until Catalogue.open()
    # actually succeeds or the user explicitly quits, rather than letting a
    # bad path (permissions, a typo, anything beyond "not configured yet")
    # crash past this gate with a raw traceback.
    catalogue, _ = _try_open_catalogue()
    error_message: str | None = None
    if catalogue is None:
        # A modal settings dialog needs the user's attention, not a splash
        # screen competing for it -- brought back afterward, below.
        splash.hide()
    while catalogue is None:
        dialog = SettingsDialog(error_message=error_message)
        if dialog.exec() != QDialog.Accepted:
            sys.exit(0)
        catalogue, error_message = _try_open_catalogue()

    window = MainWindow(catalogue)
    splash.show()
    app.processEvents()
    # Before window.show(), deliberately -- see _prewarm_opengl's
    # docstring for why the ordering is what makes this actually work
    # rather than just moving the flicker a little earlier.
    _prewarm_opengl(window)
    window.show()
    splash.close()
    window._check_for_updates(silent=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
