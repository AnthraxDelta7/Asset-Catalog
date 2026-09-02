from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from asset_catalogue import thumbnails


@dataclass
class RemoveStats:
    removed: int = 0


def remove_assets(
    conn: sqlite3.Connection, thumbnail_dir: Path, asset_ids: list[int]
) -> RemoveStats:
    """Removes assets from the catalogue -- the database rows and their
    rendered thumbnails. Never touches the original files in the staging
    folder; those stay exactly as shipped, per the catalogue's core design
    (see asset-catalogue-seed.md section 1).
    """
    stats = RemoveStats()
    for asset_id in asset_ids:
        row = conn.execute(
            "SELECT content_hash FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if row is None:
            continue
        thumb_path = thumbnails.thumbnail_path(thumbnail_dir, row["content_hash"])
        if thumb_path.exists():
            thumb_path.unlink()
        conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
        conn.execute("DELETE FROM imports WHERE asset_id = ?", (asset_id,))
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        stats.removed += 1
    conn.commit()
    return stats
