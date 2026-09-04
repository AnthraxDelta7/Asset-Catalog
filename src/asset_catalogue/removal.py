from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from asset_catalogue import library_assets, thumbnails

ProgressCallback = Callable[[str], None]


@dataclass
class RemoveStats:
    removed: int = 0


@dataclass
class RemovePackStats:
    removed_assets: int = 0
    pack_removed: bool = False


def _remove_one_asset(
    conn: sqlite3.Connection,
    thumbnail_dir: Path,
    assets_dir: Path,
    asset_id: int,
    on_progress: ProgressCallback | None = None,
) -> bool:
    report = on_progress or (lambda _text: None)
    row = conn.execute(
        "SELECT assets.content_hash, assets.relative_path, packs.name AS pack_name "
        "FROM assets JOIN packs ON packs.id = assets.pack_id WHERE assets.id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        return False
    report(f"Removing {row['relative_path']}...")
    thumb_path = thumbnails.thumbnail_path(thumbnail_dir, row["content_hash"])
    if thumb_path.exists():
        thumb_path.unlink()
    library_path = library_assets.asset_library_path(
        assets_dir, row["pack_name"], row["relative_path"]
    )
    if library_path.exists():
        library_path.unlink()
    conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
    conn.execute("DELETE FROM exports WHERE asset_id = ?", (asset_id,))
    # pending_conversions.asset_id REFERENCES assets(id) with foreign_keys=ON
    # -- deleting the asset row below without this first raises
    # IntegrityError for any asset with an unresolved conversion pending.
    conn.execute("DELETE FROM pending_conversions WHERE asset_id = ?", (asset_id,))
    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    return True


def remove_assets(
    conn: sqlite3.Connection,
    thumbnail_dir: Path,
    assets_dir: Path,
    asset_ids: list[int],
    on_progress: ProgressCallback | None = None,
) -> RemoveStats:
    """Removes assets from the catalogue -- the database rows, rendered
    thumbnails, and any library-archived copy (see library_assets.py).
    Never touches the original files in the staging folder; those stay
    exactly as shipped, per the catalogue's core design (see
    asset-catalogue-seed.md section 1).
    """
    stats = RemoveStats()
    for asset_id in asset_ids:
        if _remove_one_asset(conn, thumbnail_dir, assets_dir, asset_id, on_progress):
            stats.removed += 1
    conn.commit()
    return stats


def remove_pack(
    conn: sqlite3.Connection,
    thumbnail_dir: Path,
    assets_dir: Path,
    pack_id: int,
    on_progress: ProgressCallback | None = None,
) -> RemovePackStats:
    """Removes an entire pack: every asset in it (same per-asset cleanup as
    remove_assets), the packs row itself, and the pack's whole archived
    library folder (removed wholesale via rmtree, not just the files known
    to individual assets, so nothing orphaned is left behind). Never
    touches the original files in the staging folder.
    """
    report = on_progress or (lambda _text: None)
    stats = RemovePackStats()
    pack_row = conn.execute("SELECT name FROM packs WHERE id = ?", (pack_id,)).fetchone()
    if pack_row is None:
        return stats

    asset_ids = [
        row["id"] for row in conn.execute("SELECT id FROM assets WHERE pack_id = ?", (pack_id,))
    ]
    for asset_id in asset_ids:
        if _remove_one_asset(conn, thumbnail_dir, assets_dir, asset_id, on_progress):
            stats.removed_assets += 1
    conn.execute("DELETE FROM packs WHERE id = ?", (pack_id,))
    conn.commit()
    stats.pack_removed = True

    pack_folder = library_assets.pack_library_folder(assets_dir, pack_row["name"])
    if pack_folder.exists():
        report(f"Deleting library folder for {pack_row['name']}...")
        shutil.rmtree(pack_folder, ignore_errors=True)

    return stats
