from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from asset_catalogue import db, settings, tagging, thumbnails


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

    def __init__(self, conn: sqlite3.Connection, thumbnail_dir: Path) -> None:
        self._conn = conn
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
        return cls(conn, s.thumbnail_dir())

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
