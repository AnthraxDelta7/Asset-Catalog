from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from asset_catalogue import (
    archives,
    audio_thumbnails,
    blender_render,
    conversion,
    db,
    ingest,
    library_assets,
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
    creator: str | None
    licence: str | None
    source_url: str | None
    corrections: dict
    asset_count: int


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
    ) -> None:
        self._conn = conn
        self._staging_folder = staging_folder
        self._thumbnail_dir = thumbnail_dir
        self._assets_dir = assets_dir

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
        return cls(conn, staging_folder, s.thumbnail_dir(), s.assets_dir())

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
            "SELECT id, name, creator, licence, source_url, corrections "
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
            creator=row["creator"],
            licence=row["licence"],
            source_url=row["source_url"],
            corrections=corrections,
            asset_count=asset_count,
        )

    def list_assets(
        self,
        pack: str | None = None,
        asset_type: str | None = None,
        tag: str | None = None,
        extension: str | None = None,
    ) -> list[AssetSummary]:
        query = (
            "SELECT assets.id, assets.filename, assets.asset_type, "
            "assets.thumbnail_status, assets.content_hash, assets.relative_path, "
            "packs.name AS pack_name "
            "FROM assets JOIN packs ON packs.id = assets.pack_id"
        )
        clauses: list[str] = []
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
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY packs.name, assets.relative_path"

        rows = self._conn.execute(query, params).fetchall()
        return [
            AssetSummary(
                id=row["id"],
                filename=row["filename"],
                pack_name=row["pack_name"],
                asset_type=row["asset_type"],
                thumbnail_status=row["thumbnail_status"],
                content_hash=row["content_hash"],
                relative_path=row["relative_path"],
                tags=self.get_asset_tags(row["id"]),
            )
            for row in rows
        ]

    def get_asset_tags(self, asset_id: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT tags.name FROM tags JOIN asset_tags ON asset_tags.tag_id = tags.id "
            "WHERE asset_tags.asset_id = ? ORDER BY tags.name",
            (asset_id,),
        ).fetchall()
        return [row["name"] for row in rows]

    def thumbnail_path_for(self, content_hash: str) -> Path | None:
        path = thumbnails.thumbnail_path(self._thumbnail_dir, content_hash)
        return path if path.exists() else None

    def library_asset_path_if_archived(self, pack_name: str, relative_path: str) -> Path | None:
        path = library_assets.asset_library_path(self._assets_dir, pack_name, relative_path)
        return path if path.exists() else None

    def has_pending_conversion(self, asset_id: int) -> bool:
        return conversion.has_pending_conversion(self._conn, asset_id)

    def count_pending_conversions(self) -> int:
        return len(conversion.list_pending_conversion_asset_ids(self._conn))

    def tag_asset(self, asset_id: int, tag_name: str, category: str | None = None) -> None:
        tag_id = tagging.get_or_create_tag(self._conn, tag_name, category)
        tagging.tag_asset(self._conn, asset_id, tag_id)

    def untag_asset(self, asset_id: int, tag_name: str) -> None:
        row = self._conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
        if row is None:
            return
        tagging.untag_asset(self._conn, asset_id, row["id"])

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

    def ingest_pack_bg(
        self,
        pack_folder_name: str,
        pack_name: str,
        creator: str | None,
        licence: str | None,
        source_url: str | None,
    ) -> tuple[ingest.IngestStats, list[str]]:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        pack_root = self._staging_folder / pack_folder_name

        # A folder pick that turns out to be a .zip (e.g. one sitting
        # directly in staging) is handled transparently rather than failing.
        if pack_root.is_file() and pack_root.suffix.lower() == ".zip":
            pack_folder_name = pack_root.stem
            zip_path = pack_root
            pack_root = self._staging_folder / pack_folder_name
            # Re-selecting the same zip after an earlier ingest already
            # extracted it (e.g. re-running ingest to pick up new files) is
            # a normal, expected case, not a clobber attempt -- only
            # extract if the destination isn't already there, and ingest
            # from whatever's on disk either way (same idempotent-merge
            # behavior as picking the already-extracted folder directly).
            if not (pack_root.exists() and any(pack_root.iterdir())):
                archives.extract_zip(zip_path, pack_root)
        elif not pack_root.is_dir():
            raise RuntimeError(f"Pack folder not found: {pack_root}")

        conn = db.connect(settings.load().db_path())
        try:
            pack_id, updated_fields = ingest.get_or_create_pack(
                conn, pack_name, pack_folder_name, creator, licence, source_url
            )
            stats = ingest.ingest_pack(conn, pack_root, pack_id)
            stats.archived = library_assets.archive_pack(
                conn, self._staging_folder, self._assets_dir, pack_id
            )
            self._auto_generate_thumbnails(conn, stats, pack_id, pack_name)
            return stats, updated_fields
        finally:
            conn.close()

    def _auto_generate_thumbnails(
        self, conn: sqlite3.Connection, stats: ingest.IngestStats, pack_id: int, pack_name: str
    ) -> None:
        thumb_stats = blender_render.generate_pack_thumbnails(
            conn,
            self._staging_folder,
            self._thumbnail_dir,
            settings.load().blender_path,
            pack_id,
            pack_name,
        )
        stats.thumbnails_generated = thumb_stats.generated
        stats.thumbnails_failed = thumb_stats.failed
        stats.blender_unavailable_reason = thumb_stats.blender_unavailable_reason
        stats.calibration_preview = thumb_stats.calibration_preview
        stats.models_pending = thumb_stats.models_pending

    def remove_assets_bg(self, asset_ids: list[int]) -> removal.RemoveStats:
        conn = db.connect(settings.load().db_path())
        try:
            return removal.remove_assets(conn, self._thumbnail_dir, self._assets_dir, asset_ids)
        finally:
            conn.close()

    def remove_pack_bg(self, pack_id: int) -> removal.RemovePackStats:
        conn = db.connect(settings.load().db_path())
        try:
            return removal.remove_pack(conn, self._thumbnail_dir, self._assets_dir, pack_id)
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
    ) -> None:
        """Renames (if changed, moving the archived library folder to
        match), updates creator/licence/source_url, and replaces the render
        corrections -- one call for the whole "Edit Pack" dialog's fields.
        """
        conn = db.connect(settings.load().db_path())
        try:
            packs.rename_pack(conn, self._assets_dir, pack_id, name)
            packs.set_metadata(conn, pack_id, creator, licence, source_url)
            packs.set_corrections(conn, pack_id, corrections)
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
        self, pack: str | None = None, force: bool = False
    ) -> thumbnails.ThumbnailStats:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            return thumbnails.generate_texture_thumbnails(
                conn, self._staging_folder, self._thumbnail_dir, pack_name=pack, force=force
            )
        finally:
            conn.close()

    def generate_audio_thumbnails_bg(
        self, pack: str | None = None, force: bool = False
    ) -> thumbnails.ThumbnailStats:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        conn = db.connect(settings.load().db_path())
        try:
            return audio_thumbnails.generate_audio_thumbnails(
                conn, self._staging_folder, self._thumbnail_dir, pack_name=pack, force=force
            )
        finally:
            conn.close()

    def convert_asset_to_gltf_bg(self, asset_id: int) -> conversion.ConversionResult:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        blender_exe = self.resolve_blender()
        conn = db.connect(settings.load().db_path())
        try:
            result = conversion.convert_asset_to_gltf(
                conn, self._staging_folder, self._assets_dir, blender_exe, asset_id
            )
            if result.ok:
                blender_render.generate_model_thumbnails(
                    conn, self._staging_folder, self._thumbnail_dir, blender_exe, asset_id=asset_id
                )
            return result
        finally:
            conn.close()

    def convert_assets_to_gltf_bg(self, asset_ids: list[int]) -> conversion.ConversionBatchResult:
        """Batch counterpart to convert_asset_to_gltf_bg -- non-model assets
        and ones already .glb are silently skipped (result.skipped), so a
        caller can pass a raw multi-selection straight through.
        """
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        blender_exe = self.resolve_blender()
        conn = db.connect(settings.load().db_path())
        try:
            result = conversion.convert_assets_to_gltf(
                conn, self._staging_folder, self._assets_dir, blender_exe, asset_ids
            )
            if result.converted_asset_ids:
                blender_render.generate_model_thumbnails(
                    conn,
                    self._staging_folder,
                    self._thumbnail_dir,
                    blender_exe,
                    asset_ids=result.converted_asset_ids,
                )
            return result
        finally:
            conn.close()

    def revert_conversion_bg(self, asset_id: int) -> bool:
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
                            conn, self._staging_folder, self._thumbnail_dir, blender_exe, asset_id=asset_id
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
        self, blender_exe: Path, pack: str | None = None, force: bool = False
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
            )
        finally:
            conn.close()
