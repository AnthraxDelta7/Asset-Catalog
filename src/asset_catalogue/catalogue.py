from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from asset_catalogue import archives, blender_render, db, ingest, settings, tagging, thumbnails


@dataclass
class AssetSummary:
    id: int
    filename: str
    pack_name: str
    asset_type: str
    thumbnail_status: str
    content_hash: str
    tags: list[str] = field(default_factory=list)


@dataclass
class TagSummary:
    name: str
    category: str | None
    usage_count: int


class Catalogue:
    """The only thing the UI is allowed to talk to -- never the filesystem
    or raw SQL directly. See asset-catalogue-seed.md section 3.
    """

    def __init__(
        self, conn: sqlite3.Connection, staging_folder: Path | None, thumbnail_dir: Path
    ) -> None:
        self._conn = conn
        self._staging_folder = staging_folder
        self._thumbnail_dir = thumbnail_dir

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
        return cls(conn, staging_folder, s.thumbnail_dir())

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

    def list_tags(self) -> list[TagSummary]:
        rows = self._conn.execute(
            "SELECT tags.name, tags.category, COUNT(asset_tags.asset_id) AS usage_count "
            "FROM tags LEFT JOIN asset_tags ON asset_tags.tag_id = tags.id "
            "GROUP BY tags.id ORDER BY tags.name"
        ).fetchall()
        return [TagSummary(row["name"], row["category"], row["usage_count"]) for row in rows]

    def list_assets(
        self,
        pack: str | None = None,
        asset_type: str | None = None,
        tag: str | None = None,
    ) -> list[AssetSummary]:
        query = (
            "SELECT assets.id, assets.filename, assets.asset_type, "
            "assets.thumbnail_status, assets.content_hash, packs.name AS pack_name "
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
        s = settings.load()
        blender_exe = blender_render.find_blender(s.blender_path)
        if blender_exe is None:
            raise RuntimeError("Blender not found. Set its path in Settings, or install it.")
        version = blender_render.get_blender_version(blender_exe)
        if version is None:
            raise RuntimeError(f"Could not determine Blender's version from {blender_exe}")
        if version < blender_render.MIN_BLENDER_VERSION:
            min_version = ".".join(str(part) for part in blender_render.MIN_BLENDER_VERSION)
            found_version = ".".join(str(part) for part in version)
            raise RuntimeError(
                f"Blender {found_version} is older than the minimum supported {min_version}"
            )
        return blender_exe

    def ingest_pack_bg(
        self,
        pack_folder_name: str,
        pack_name: str,
        creator: str | None,
        licence: str | None,
        source_url: str | None,
    ) -> ingest.IngestStats:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        pack_root = self._staging_folder / pack_folder_name
        if not pack_root.is_dir():
            raise RuntimeError(f"Pack folder not found: {pack_root}")
        conn = db.connect(settings.load().db_path())
        try:
            pack_id = ingest.get_or_create_pack(
                conn, pack_name, pack_folder_name, creator, licence, source_url
            )
            return ingest.ingest_pack(conn, pack_root, pack_id)
        finally:
            conn.close()

    def extract_and_ingest_pack_bg(
        self,
        zip_path: Path,
        pack_folder_name: str,
        pack_name: str,
        creator: str | None,
        licence: str | None,
        source_url: str | None,
    ) -> ingest.IngestStats:
        if self._staging_folder is None:
            raise RuntimeError("No staging folder configured.")
        pack_root = self._staging_folder / pack_folder_name
        archives.extract_zip(zip_path, pack_root)
        conn = db.connect(settings.load().db_path())
        try:
            pack_id = ingest.get_or_create_pack(
                conn, pack_name, pack_folder_name, creator, licence, source_url
            )
            return ingest.ingest_pack(conn, pack_root, pack_id)
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
