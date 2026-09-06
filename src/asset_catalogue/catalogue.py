from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from asset_catalogue import (
    archives,
    audio_thumbnails,
    blender_render,
    conversion,
    credits,
    db,
    exporting,
    godot_export,
    ingest,
    library_assets,
    library_health,
    library_stats,
    model_preview,
    packs,
    removal,
    settings,
    tagging,
    thumbnails,
)


@dataclass
class AssetSummary:
    id: int
    filename: str
    pack_name: str
    asset_type: str
    thumbnail_status: str
    content_hash: str
    relative_path: str
    favorite: bool = False
    pack_rating: int | None = None
    pack_notes: str | None = None
    deleted_at: str | None = None
    needs_glb_conversion: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class TagSummary:
    id: int
    name: str
    category: str | None
    usage_count: int


@dataclass
class PackDetail:
    id: int
    name: str
    pack_folder: str
    creator: str | None
    licence: str | None
    source_url: str | None
    corrections: dict
    asset_count: int
    notes: str | None = None
    rating: int | None = None


class Catalogue:
    """The only thing the UI is allowed to talk to -- never the filesystem
    or raw SQL directly. See asset-catalogue-seed.md section 3.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        staging_folder: Path | None,
        thumbnail_dir: Path,
        assets_dir: Path,
        preview_dir: Path | None = None,
    ) -> None:
        self._conn = conn
        self._staging_folder = staging_folder
        self._thumbnail_dir = thumbnail_dir
        self._assets_dir = assets_dir
        # Sibling of thumbnail_dir by default (matches settings.preview_dir()
        # / thumbnail_dir() both living directly under library_folder) --
        # callers that don't care about the interactive 3D preview (most
        # tests) never need to pass this explicitly.
        self._preview_dir = preview_dir if preview_dir is not None else thumbnail_dir.parent / "previews"

    @classmethod
    def open(cls) -> Catalogue:
        s = settings.load()
        if not s.library_folder:
            raise RuntimeError(
                "No library folder configured. Run: "
                "asset-catalogue settings set --library-folder <path>"
            )
        Path(s.library_folder).mkdir(parents=True, exist_ok=True)
        conn = db.connect(s.db_path())
        staging_folder = Path(s.staging_folder) if s.staging_folder else None
        return cls(conn, staging_folder, s.thumbnail_dir(), s.assets_dir(), s.preview_dir())

    def close(self) -> None:
        self._conn.close()

    def staging_folder(self) -> Path | None:
        return self._staging_folder

    def list_packs(self) -> list[str]:
        rows = self._conn.execute("SELECT name FROM packs ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def list_asset_types(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT asset_type FROM assets ORDER BY asset_type"
        ).fetchall()
        return [row["asset_type"] for row in rows]

    def list_asset_extensions(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT extension FROM assets ORDER BY extension"
        ).fetchall()
        return [row["extension"] for row in rows]

    def list_tags(self) -> list[TagSummary]:
        rows = self._conn.execute(
            "SELECT tags.id, tags.name, tags.category, COUNT(asset_tags.asset_id) AS usage_count "
            "FROM tags LEFT JOIN asset_tags ON asset_tags.tag_id = tags.id "
            "GROUP BY tags.id ORDER BY tags.name"
        ).fetchall()
        return [
            TagSummary(row["id"], row["name"], row["category"], row["usage_count"])
            for row in rows
        ]

    def rename_tag(self, tag_id: int, new_name: str, new_category: str | None = None) -> None:
        tagging.rename_tag(self._conn, tag_id, new_name, new_category)

    def delete_tag(self, tag_id: int) -> int:
        return tagging.delete_tag(self._conn, tag_id)

    def get_pack_detail(self, pack_name: str) -> PackDetail | None:
        row = self._conn.execute(
            "SELECT id, name, pack_folder, creator, licence, source_url, corrections, notes, rating "
            "FROM packs WHERE name = ?",
            (pack_name,),
        ).fetchone()
        if row is None:
            return None
        asset_count = self._conn.execute(
            "SELECT COUNT(*) AS c FROM assets WHERE pack_id = ?", (row["id"],)
        ).fetchone()["c"]
        corrections = packs.get_corrections(self._conn, row["id"])
        return PackDetail(
            id=row["id"],
            name=row["name"],
            pack_folder=row["pack_folder"],
            creator=row["creator"],
            licence=row["licence"],
            source_url=row["source_url"],
            corrections=corrections,
            asset_count=asset_count,
            notes=row["notes"],
            rating=row["rating"],
        )

    _ASSET_SUMMARY_COLUMNS = (
        "assets.id, assets.filename, assets.asset_type, "
        "assets.thumbnail_status, assets.content_hash, assets.relative_path, "
        "assets.favorite, assets.deleted_at, assets.needs_glb_conversion, "
        "packs.name AS pack_name, packs.rating AS pack_rating, packs.notes AS pack_notes"
    )

    def _row_to_asset_summary(self, row: sqlite3.Row) -> AssetSummary:
        return AssetSummary(
            id=row["id"],
            filename=row["filename"],
            pack_name=row["pack_name"],
            asset_type=row["asset_type"],
            thumbnail_status=row["thumbnail_status"],
            content_hash=row["content_hash"],
            relative_path=row["relative_path"],
            favorite=bool(row["favorite"]),
            pack_rating=row["pack_rating"],
            pack_notes=row["pack_notes"],
            deleted_at=row["deleted_at"],
            needs_glb_conversion=bool(row["needs_glb_conversion"]),
            tags=self.get_asset_tags(row["id"]),
        )

    def list_assets(
        self,
        pack: str | None = None,
        asset_type: str | None = None,
        tag: str | None = None,
        extension: str | None = None,
        search: str | None = None,
        favorites_only: bool = False,
        needs_conversion_only: bool = False,
    ) -> list[AssetSummary]:
        query = (
            f"SELECT {self._ASSET_SUMMARY_COLUMNS} "
            "FROM assets JOIN packs ON packs.id = assets.pack_id"
        )
        # Soft-deleted (trashed) assets never show up in the normal grid --
        # see list_trashed_assets for the dedicated trash view.
        clauses: list[str] = ["assets.deleted_at IS NULL"]
        params: list[str] = []
        if tag:
            query += (
                " JOIN asset_tags ON asset_tags.asset_id = assets.id"
                " JOIN tags ON tags.id = asset_tags.tag_id"
            )
            clauses.append("tags.name = ?")
            params.append(tag)
        if pack:
            clauses.append("packs.name = ?")
            params.append(pack)
        if asset_type:
            clauses.append("assets.asset_type = ?")
            params.append(asset_type)
        if extension:
            clauses.append("assets.extension = ?")
            params.append(extension)
        if favorites_only:
            clauses.append("assets.favorite = 1")
        if needs_conversion_only:
            clauses.append("assets.needs_glb_conversion = 1")
        if search:
            # Escape LIKE's own wildcards so a filename that happens to
            # contain a literal "%" or "_" is matched literally, not
            # interpreted as a pattern.
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("assets.filename LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY packs.name, assets.relative_path"

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_asset_summary(row) for row in rows]

    def get_asset(self, asset_id: int) -> AssetSummary | None:
        row = self._conn.execute(
            f"SELECT {self._ASSET_SUMMARY_COLUMNS} "
            "FROM assets JOIN packs ON packs.id = assets.pack_id WHERE assets.id = ?",
            (asset_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_asset_summary(row)

    def get_asset_tags(self, asset_id: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT tags.name FROM tags JOIN asset_tags ON asset_tags.tag_id = tags.id "
            "WHERE asset_tags.asset_id = ? ORDER BY tags.name",
            (asset_id,),
        ).fetchall()
        return [row["name"] for row in rows]

    def next_pending_model_asset_id(self, pack_id: int, exclude_asset_id: int) -> int | None:
        """Another model asset in this pack still thumbnail_status='pending',
        not counting exclude_asset_id (the one currently shown as the
        calibration preview) -- what CalibrationReviewDialog's "Skip and
        Render Next" button steps to when the current preview isn't a good
        representative of the pack. None once every model has already been
        rendered (or attempted), meaning there's nothing left to step to.
        """
        row = self._conn.execute(
            "SELECT id FROM assets WHERE pack_id = ? AND asset_type = 'model' "
            "AND thumbnail_status = 'pending' AND id != ? AND deleted_at IS NULL "
            "ORDER BY id LIMIT 1",
            (pack_id, exclude_asset_id),
        ).fetchone()
        return row["id"] if row is not None else None

    def count_pending_model_assets(self, pack_id: int) -> int:
        """How many model assets in this pack still have thumbnail_status
        ='pending' -- what CalibrationReviewDialog's "Render Remaining N
        Model(s)" button's own count reflects, recomputed (not just
        decremented) after each "Skip and Render Next" so it can't drift.
        """
        return self._conn.execute(
            "SELECT COUNT(*) AS c FROM assets WHERE pack_id = ? AND asset_type = 'model' "
            "AND thumbnail_status = 'pending' AND deleted_at IS NULL",
            (pack_id,),
        ).fetchone()["c"]

    def thumbnail_path_for(self, content_hash: str) -> Path | None:
        path = thumbnails.thumbnail_path(self._thumbnail_dir, content_hash)
        return path if path.exists() else None

    def model_preview_path_for(self, content_hash: str) -> Path | None:
        path = model_preview.preview_path(self._preview_dir, content_hash)
        return path if path.exists() else None

    def model_preview_colors_path_for(self, content_hash: str) -> Path | None:
        """The Blender-resolved material-color metadata sidecar for a
        preview .glb, if one was exported alongside it -- see
        model_preview.colors_path. None for a preview rendered before this
        existed; model_preview_dialog.load_preview_parts falls back to its
        own derivation in that case, so callers can pass None through
        unconditionally rather than checking first.
        """
        path = model_preview.colors_path(self._preview_dir, content_hash)
        return path if path.exists() else None

    def library_asset_path_if_archived(self, pack_name: str, relative_path: str) -> Path | None:
        path = library_assets.asset_library_path(self._assets_dir, pack_name, relative_path)
        return path if path.exists() else None

    def has_pending_conversion(self, asset_id: int) -> bool:
        return conversion.has_pending_conversion(self._conn, asset_id)

    def count_pending_conversions(self) -> int:
        return len(conversion.list_pending_conversion_asset_ids(self._conn))

    def list_pending_conversions(self) -> list[sqlite3.Row]:
        return conversion.list_pending_conversions(self._conn)

    def generate_credits_report(self, project_root: Path | str | None = None) -> str:
        return credits.generate_report(self._conn, project_root)

    def get_library_stats(self) -> library_stats.LibraryStats:
        return library_stats.compute_stats(self._conn)

    def check_library_health(self) -> library_health.HealthReport:
        return library_health.check_integrity(
            self._conn, self._staging_folder, self._assets_dir, self._thumbnail_dir
        )

    def reset_broken_thumbnails(self, asset_ids: list[int]) -> int:
        return library_health.reset_broken_thumbnails(self._conn, asset_ids)

    def rearchive_assets_bg(self, asset_ids: list[int]) -> int:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            return library_health.rearchive_assets(
                conn, self._staging_folder, self._assets_dir, asset_ids
            )
        finally:
            conn.close()

    def tag_asset(self, asset_id: int, tag_name: str, category: str | None = None) -> None:
        tag_id = tagging.get_or_create_tag(self._conn, tag_name, category)
        tagging.tag_asset(self._conn, asset_id, tag_id)

    def untag_asset(self, asset_id: int, tag_name: str) -> None:
        row = self._conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
        if row is None:
            return
        tagging.untag_asset(self._conn, asset_id, row["id"])

    def trash_assets(self, asset_ids: list[int]) -> int:
        """Soft-delete -- see removal.trash_assets. A single fast UPDATE
        touching no files, so (like set_favorite) this runs on self._conn
        directly rather than as a background job.
        """
        return removal.trash_assets(self._conn, asset_ids)

    def restore_assets(self, asset_ids: list[int]) -> int:
        return removal.restore_assets(self._conn, asset_ids)

    def list_trashed_assets(self) -> list[AssetSummary]:
        rows = self._conn.execute(
            f"SELECT {self._ASSET_SUMMARY_COLUMNS} "
            "FROM assets JOIN packs ON packs.id = assets.pack_id "
            "WHERE assets.deleted_at IS NOT NULL ORDER BY assets.deleted_at DESC"
        ).fetchall()
        return [self._row_to_asset_summary(row) for row in rows]

    def count_trashed_assets(self) -> int:
        return len(removal.list_trashed_asset_ids(self._conn))

    def set_favorite(self, asset_ids: list[int], favorite: bool) -> None:
        """A quick personal flag independent of tags -- tags are for
        categorization, favorite is for "I liked this specific one" out of
        a big purchased-pack backlog. A single fast UPDATE, so (like
        tag_asset) this runs on self._conn directly rather than as a
        background job.
        """
        self._conn.executemany(
            "UPDATE assets SET favorite = ? WHERE id = ?",
            [(1 if favorite else 0, asset_id) for asset_id in asset_ids],
        )
        self._conn.commit()

    # -- Background-safe operations -----------------------------------
    #
    # Each of these opens and closes its own SQLite connection rather than
    # using self._conn, so it's safe to call from a worker thread (SQLite
    # connections aren't shared safely across threads) -- used by the UI to
    # run ingest/thumbnail generation without freezing the window.

    def resolve_blender(self) -> Path:
        blender_exe, error = blender_render.resolve_blender(settings.load().blender_path)
        if blender_exe is None:
            raise RuntimeError(error)
        return blender_exe

    def resolve_godot(self) -> Path:
        godot_exe, error = godot_export.resolve_godot(settings.load().godot_path)
        if godot_exe is None:
            raise RuntimeError(error)
        return godot_exe

    def find_godot_projects(self, staged_folder_name: str) -> list[str]:
        """Relative-to-staging-folder paths of every Godot project
        (anything with its own project.godot) found inside a staged
        folder, itself included -- a staged pack occasionally bundles more
        than one Godot project.
        """
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        search_root = self._staging_folder / staged_folder_name
        roots = godot_export.find_godot_project_roots(search_root)
        return [str(root.relative_to(self._staging_folder)) for root in roots]

    def extract_godot_scenes_batch_bg(
        self,
        project_folder_names: list[str],
        include_colliders: bool = True,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[tuple[str, godot_export.GodotExportStats]]:
        """Runs godot_export.export_scenes_to_glb once per selected Godot
        project (each project root gets its own headless Godot process,
        since res:// is fixed per run), writing .glb files into the staged
        pack folder itself -- ingest_pack's own recursive walk then picks
        them up as ordinary, already-recognized model assets, so nothing
        about ingest.py needs to know Godot was involved.
        """
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        godot_exe = self.resolve_godot()
        results = []
        for project_folder_name in project_folder_names:
            project_root = self._staging_folder / project_folder_name
            scenes = godot_export.find_scenes(project_root)
            stats = godot_export.export_scenes_to_glb(
                godot_exe, project_root, scenes, include_colliders, on_progress=on_progress
            )
            results.append((project_folder_name, stats))
        return results

    def _resolve_pack_root(self, pack_folder_name: str) -> tuple[Path, str]:
        """A pack source that turns out to be a .zip sitting directly in
        staging (rather than an already-extracted folder) is extracted
        transparently here -- shared by ingest_pack_bg and
        scan_format_duplicates so a duplicate-format scan sees exactly the
        same files the real ingest is about to. An earlier version of
        scan_format_duplicates skipped extraction "just to preview" and
        returned empty for a zip source instead -- a real bug: the format-
        choice prompt (Ingest Pack / Batch Ingest) silently never appeared
        at all for any pack ingested straight from a .zip, only for one
        already extracted to a folder first.

        Idempotent, so calling this once for the scan and again moments
        later for the real ingest -- or on a later re-ingest of an
        already-extracted pack -- never re-extracts: only happens if the
        destination folder isn't already there. Returns (pack_root,
        pack_folder_name) since a .zip source's real folder name (the
        zip's own stem) isn't known until after this resolves it.
        """
        pack_root = self._staging_folder / pack_folder_name
        if pack_root.is_file() and pack_root.suffix.lower() == ".zip":
            pack_folder_name = pack_root.stem
            zip_path = pack_root
            pack_root = self._staging_folder / pack_folder_name
            if not (pack_root.exists() and any(pack_root.iterdir())):
                archives.extract_zip(zip_path, pack_root)
        return pack_root, pack_folder_name

    def scan_format_duplicates(self, pack_folder_name: str) -> set[str]:
        """Every extension involved in a same-name, multiple-format group
        somewhere in this staged pack source (Model.fbx next to Model.glb,
        say) -- empty when there's nothing to choose between, which is the
        common case, so a caller (the Ingest Pack / Batch Ingest dialogs)
        can skip prompting entirely. See _resolve_pack_root for how a .zip
        source is handled.
        """
        if self._staging_folder is None:
            return set()
        pack_root, _resolved_name = self._resolve_pack_root(pack_folder_name)
        if not pack_root.is_dir():
            return set()
        return ingest.scan_format_duplicates(pack_root)

    def ingest_pack_bg(
        self,
        pack_folder_name: str,
        pack_name: str,
        creator: str | None,
        licence: str | None,
        source_url: str | None,
        on_progress: Callable[[str], None] | None = None,
        format_selection: set[str] | None = None,
    ) -> tuple[ingest.IngestStats, list[str]]:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        pack_root, pack_folder_name = self._resolve_pack_root(pack_folder_name)
        if not pack_root.is_dir():
            raise RuntimeError(f"Pack folder not found: {pack_root}")

        conn = db.connect(settings.load().db_path())
        try:
            pack_id, updated_fields = ingest.get_or_create_pack(
                conn, pack_name, pack_folder_name, creator, licence, source_url
            )
            stats = ingest.ingest_pack(
                conn, pack_root, pack_id, on_progress=on_progress, format_selection=format_selection
            )
            stats.archived = library_assets.archive_pack(
                conn, self._staging_folder, self._assets_dir, pack_id, on_progress=on_progress
            )
            self._auto_generate_thumbnails(conn, stats, pack_id, pack_name, on_progress)
            return stats, updated_fields
        finally:
            conn.close()

    def ingest_packs_batch_bg(
        self,
        items: list[tuple[str, str, str | None, str | None, str | None]],
        on_progress: Callable[[str], None] | None = None,
        format_selections: dict[str, set[str]] | None = None,
    ) -> list[tuple[str, ingest.IngestStats, list[str]]]:
        """Ingests multiple packs in one background job -- each pack still
        goes through the exact same ingest_pack_bg used for a single pack
        (hash, archive, auto-thumbnail), just looped with a header line
        between packs so the progress log reads as one pack at a time
        rather than an interleaved mess. Each (pack_folder_name, pack_name,
        creator, licence, source_url) tuple in items is independent, so one
        pack failing partway (e.g. a missing folder) still raises and aborts
        the whole batch -- same all-or-nothing semantics as a single ingest,
        just scoped to the batch rather than silently skipping the rest.

        format_selections, keyed by pack_folder_name, carries a per-pack
        format_selection (see scan_format_duplicates / ingest_pack_bg) --
        a separate dict rather than extending the items tuples, since most
        packs in a batch won't have any duplicates to choose between at
        all and shouldn't need a placeholder None threaded through for
        every one of them.
        """
        results = []
        total = len(items)
        for index, (pack_folder_name, pack_name, creator, licence, source_url) in enumerate(
            items, start=1
        ):
            if on_progress:
                on_progress(f"=== Pack {index}/{total}: {pack_name} ===")
            format_selection = (format_selections or {}).get(pack_folder_name)
            stats, updated_fields = self.ingest_pack_bg(
                pack_folder_name, pack_name, creator, licence, source_url,
                on_progress=on_progress, format_selection=format_selection,
            )
            results.append((pack_name, stats, updated_fields))
        return results

    def _auto_generate_thumbnails(
        self,
        conn: sqlite3.Connection,
        stats: ingest.IngestStats,
        pack_id: int,
        pack_name: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        thumb_stats = blender_render.generate_pack_thumbnails(
            conn,
            self._staging_folder,
            self._thumbnail_dir,
            settings.load().blender_path,
            pack_id,
            pack_name,
            on_progress=on_progress,
        )
        stats.thumbnails_generated = thumb_stats.generated
        stats.thumbnails_failed = thumb_stats.failed
        stats.blender_unavailable_reason = thumb_stats.blender_unavailable_reason
        stats.calibration_preview = thumb_stats.calibration_preview
        stats.models_pending = thumb_stats.models_pending
        stats.preview_asset_id = thumb_stats.preview_asset_id
        stats.broken_texture_filenames = thumb_stats.broken_texture_filenames
        stats.smart_texture_notes = thumb_stats.smart_texture_notes

    def remove_assets_bg(
        self, asset_ids: list[int], on_progress: Callable[[str], None] | None = None
    ) -> removal.RemoveStats:
        conn = db.connect(settings.load().db_path())
        try:
            return removal.remove_assets(
                conn, self._thumbnail_dir, self._assets_dir, asset_ids, on_progress=on_progress
            )
        finally:
            conn.close()

    def remove_pack_bg(
        self, pack_id: int, on_progress: Callable[[str], None] | None = None
    ) -> removal.RemovePackStats:
        conn = db.connect(settings.load().db_path())
        try:
            return removal.remove_pack(
                conn, self._thumbnail_dir, self._assets_dir, pack_id, on_progress=on_progress
            )
        finally:
            conn.close()

    def update_pack_bg(
        self,
        pack_id: int,
        name: str,
        creator: str | None,
        licence: str | None,
        source_url: str | None,
        corrections: dict,
        notes: str | None = None,
        rating: int | None = None,
    ) -> None:
        """Renames (if changed, moving the archived library folder to
        match), updates creator/licence/source_url, replaces the render
        corrections, and updates notes/rating -- one call for the whole
        "Edit Pack" dialog's fields.
        """
        conn = db.connect(settings.load().db_path())
        try:
            packs.rename_pack(conn, self._assets_dir, pack_id, name)
            packs.set_metadata(conn, pack_id, creator, licence, source_url)
            packs.set_corrections(conn, pack_id, corrections)
            packs.set_notes_and_rating(conn, pack_id, notes, rating)
        finally:
            conn.close()

    def set_pack_corrections_bg(self, pack_id: int, corrections: dict) -> None:
        conn = db.connect(settings.load().db_path())
        try:
            packs.set_corrections(conn, pack_id, corrections)
        finally:
            conn.close()

    def regenerate_model_thumbnail_bg(
        self,
        asset_id: int | None = None,
        asset_ids: list[int] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> blender_render.ModelThumbnailStats:
        """Re-renders one or more model assets' thumbnails regardless of
        their current thumbnail_status -- used to preview render corrections
        (up_axis/scale/material_fallback) against one asset at a time, e.g.
        the post-ingest calibration review, and by the grid's "Regenerate
        Thumbnail(s)" context menu action for an arbitrary selection.
        """
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        report = on_progress or (lambda _text: None)
        report("Checking Blender installation...")
        blender_exe = self.resolve_blender()
        conn = db.connect(settings.load().db_path())
        try:
            return blender_render.generate_model_thumbnails(
                conn,
                self._staging_folder,
                self._thumbnail_dir,
                blender_exe,
                asset_id=asset_id,
                asset_ids=asset_ids,
                on_progress=on_progress,
            )
        finally:
            conn.close()

    @staticmethod
    def _model_asset_ids_missing_preview(
        conn: sqlite3.Connection, preview_dir: Path, asset_ids: list[int]
    ) -> list[int]:
        """Filters a candidate list down to just the model assets that don't
        already have a cached interactive-preview .glb -- so a bulk "render
        previews for this selection/pack" action skips ones already done
        instead of re-paying Blender's import cost for nothing. Takes conn
        explicitly (rather than using self._conn) since the only caller,
        render_model_previews_bg, runs on a background thread with its own
        connection -- SQLite connections aren't safe to share across
        threads, same reasoning as every other _bg method in this class.
        """
        if not asset_ids:
            return []
        placeholders = ",".join("?" for _ in asset_ids)
        rows = conn.execute(
            f"SELECT id, content_hash FROM assets "
            f"WHERE id IN ({placeholders}) AND asset_type = 'model'",
            asset_ids,
        ).fetchall()
        return [
            row["id"]
            for row in rows
            if not model_preview.preview_path(preview_dir, row["content_hash"]).exists()
        ]

    def render_model_previews_bg(
        self, asset_ids: list[int], on_progress: Callable[[str], None] | None = None
    ) -> blender_render.ModelThumbnailStats:
        """The one and only place the interactive 3D preview .glb actually
        gets generated -- deliberately on demand, never as a side effect of
        ingest or of any other thumbnail-generation path (regenerate,
        convert-to-glTF, revert). Generating it for every model at import
        time made ingesting/bulk-rendering a large pack noticeably slower
        for previews most people would never actually open -- see the
        grid's "Render 3D Preview(s)" context menu action and the "3D
        Preview" viewer's own on-demand render for a single missing one.
        Assets that already have a cached preview are skipped (counted as
        already_done) rather than re-rendered.
        """
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            missing_ids = self._model_asset_ids_missing_preview(conn, self._preview_dir, asset_ids)
            already_done = len(asset_ids) - len(missing_ids)
            if not missing_ids:
                return blender_render.ModelThumbnailStats(already_done=already_done)
            report = on_progress or (lambda _text: None)
            report("Checking Blender installation...")
            blender_exe = self.resolve_blender()
            stats = blender_render.generate_model_thumbnails(
                conn,
                self._staging_folder,
                self._thumbnail_dir,
                blender_exe,
                asset_ids=missing_ids,
                preview_dir=self._preview_dir,
                on_progress=on_progress,
            )
            stats.already_done += already_done
            return stats
        finally:
            conn.close()

    def bulk_tag_assets_bg(
        self, asset_ids: list[int], tag_name: str, category: str | None = None
    ) -> int:
        """Tags every given asset with the same tag. Returns how many were
        tagged. Assets are archived to the library at ingest time, not here
        -- see ingest_pack_bg.
        """
        conn = db.connect(settings.load().db_path())
        try:
            tag_id = tagging.get_or_create_tag(conn, tag_name, category)
            for asset_id in asset_ids:
                tagging.tag_asset(conn, asset_id, tag_id)
            return len(asset_ids)
        finally:
            conn.close()

    def bulk_untag_assets_bg(self, asset_ids: list[int], tag_name: str) -> int:
        """Removes the same tag from every given asset. Returns how many
        actually had it (an asset that never had it isn't an error, just
        not counted) -- mirrors bulk_tag_assets_bg's shape for the reverse
        operation.
        """
        conn = db.connect(settings.load().db_path())
        try:
            row = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
            if row is None:
                return 0
            return sum(
                1 for asset_id in asset_ids if tagging.untag_asset(conn, asset_id, row["id"])
            )
        finally:
            conn.close()

    def export_assets_bg(
        self,
        asset_ids: list[int],
        project_root: Path,
        dest_subfolder: str = "exported_assets",
        on_progress: Callable[[str], None] | None = None,
    ) -> exporting.ExportStats:
        """Copies the given assets out to a target project (rebuilding each
        asset's relative path under a per-pack subfolder, not flattened)
        and records every copy in the exports table. project_root is
        resolved to an absolute path for the recorded project_identifier,
        matching the CLI's export command exactly.
        """
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            assets = exporting.select_assets(conn, asset_ids=asset_ids)
            project_identifier = str(Path(project_root).resolve())
            return exporting.export_assets(
                conn,
                self._staging_folder,
                Path(project_root),
                project_identifier,
                dest_subfolder,
                assets,
                on_progress=on_progress,
            )
        finally:
            conn.close()

    def tag_pack_bg(self, pack_name: str, tag_name: str, category: str | None = None) -> int:
        conn = db.connect(settings.load().db_path())
        try:
            pack_row = conn.execute("SELECT id FROM packs WHERE name = ?", (pack_name,)).fetchone()
            if pack_row is None:
                raise RuntimeError(f"No such pack: {pack_name}")
            tag_id = tagging.get_or_create_tag(conn, tag_name, category)
            return tagging.tag_pack(conn, pack_row["id"], tag_id)
        finally:
            conn.close()

    def generate_2d_thumbnails_bg(
        self,
        pack: str | None = None,
        force: bool = False,
        asset_id: int | None = None,
        asset_ids: list[int] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> thumbnails.ThumbnailStats:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            return thumbnails.generate_texture_thumbnails(
                conn,
                self._staging_folder,
                self._thumbnail_dir,
                pack_name=pack,
                force=force,
                asset_id=asset_id,
                asset_ids=asset_ids,
                on_progress=on_progress,
            )
        finally:
            conn.close()

    def generate_audio_thumbnails_bg(
        self,
        pack: str | None = None,
        force: bool = False,
        asset_id: int | None = None,
        asset_ids: list[int] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> thumbnails.ThumbnailStats:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            return audio_thumbnails.generate_audio_thumbnails(
                conn,
                self._staging_folder,
                self._thumbnail_dir,
                pack_name=pack,
                force=force,
                asset_id=asset_id,
                asset_ids=asset_ids,
                on_progress=on_progress,
            )
        finally:
            conn.close()

    def convert_asset_to_gltf_bg(
        self, asset_id: int, on_progress: Callable[[str], None] | None = None
    ) -> conversion.ConversionResult:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        report = on_progress or (lambda _text: None)
        report("Checking Blender installation...")
        blender_exe = self.resolve_blender()
        conn = db.connect(settings.load().db_path())
        try:
            result = conversion.convert_asset_to_gltf(
                conn,
                self._staging_folder,
                self._assets_dir,
                blender_exe,
                asset_id,
                on_progress=on_progress,
            )
            if result.ok:
                blender_render.generate_model_thumbnails(
                    conn,
                    self._staging_folder,
                    self._thumbnail_dir,
                    blender_exe,
                    asset_id=asset_id,
                    on_progress=on_progress,
                )
            return result
        finally:
            conn.close()

    def convert_assets_to_gltf_bg(
        self, asset_ids: list[int], on_progress: Callable[[str], None] | None = None
    ) -> conversion.ConversionBatchResult:
        """Batch counterpart to convert_asset_to_gltf_bg -- non-model assets
        and ones already .glb are silently skipped (result.skipped), so a
        caller can pass a raw multi-selection straight through.
        """
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        report = on_progress or (lambda _text: None)
        report("Checking Blender installation...")
        blender_exe = self.resolve_blender()
        conn = db.connect(settings.load().db_path())
        try:
            result = conversion.convert_assets_to_gltf(
                conn,
                self._staging_folder,
                self._assets_dir,
                blender_exe,
                asset_ids,
                on_progress=on_progress,
            )
            if result.converted_asset_ids:
                blender_render.generate_model_thumbnails(
                    conn,
                    self._staging_folder,
                    self._thumbnail_dir,
                    blender_exe,
                    asset_ids=result.converted_asset_ids,
                    on_progress=on_progress,
                )
            return result
        finally:
            conn.close()

    def revert_conversion_bg(
        self, asset_id: int, on_progress: Callable[[str], None] | None = None
    ) -> bool:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            reverted = conversion.revert_conversion(conn, self._staging_folder, self._assets_dir, asset_id)
            if reverted:
                row = conn.execute("SELECT asset_type FROM assets WHERE id = ?", (asset_id,)).fetchone()
                if row and row["asset_type"] == "model":
                    try:
                        blender_exe = self.resolve_blender()
                        blender_render.generate_model_thumbnails(
                            conn,
                            self._staging_folder,
                            self._thumbnail_dir,
                            blender_exe,
                            asset_id=asset_id,
                            on_progress=on_progress,
                        )
                    except RuntimeError:
                        pass  # blender unavailable -- thumbnail just stays 'pending'
            return reverted
        finally:
            conn.close()

    def cleanup_pending_conversion_bg(self, asset_id: int) -> bool:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            return conversion.cleanup_pending_conversion(conn, self._staging_folder, self._assets_dir, asset_id)
        finally:
            conn.close()

    def cleanup_all_pending_conversions_bg(self) -> int:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            return conversion.cleanup_all_pending_conversions(conn, self._staging_folder, self._assets_dir)
        finally:
            conn.close()

    def generate_model_thumbnails_bg(
        self,
        blender_exe: Path,
        pack: str | None = None,
        force: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> blender_render.ModelThumbnailStats:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            return blender_render.generate_model_thumbnails(
                conn,
                self._staging_folder,
                self._thumbnail_dir,
                blender_exe,
                pack_name=pack,
                force=force,
                on_progress=on_progress,
            )
        finally:
            conn.close()
