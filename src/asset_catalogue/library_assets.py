from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[str], None]


def _sanitize_folder_name(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_")


def pack_library_folder(assets_dir: Path, pack_name: str) -> Path:
    return assets_dir / _sanitize_folder_name(pack_name)


def asset_library_path(assets_dir: Path, pack_name: str, relative_path: str) -> Path:
    return pack_library_folder(assets_dir, pack_name) / relative_path


def archive_asset(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path, asset_id: int
) -> Path | None:
    """Copies an asset's file from staging into the library's assets/ folder,
    if it isn't there already. Returns the library copy's path, or None if
    the asset doesn't exist or its source file can't be found in staging.

    This is what makes a library folder actually self-contained -- without
    it, porting/sharing the library (its own separate feature) only carries
    the catalogue and thumbnails, never the usable files themselves.
    """
    row = conn.execute(
        "SELECT assets.relative_path, packs.pack_folder, packs.name AS pack_name "
        "FROM assets JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        return None

    destination = asset_library_path(assets_dir, row["pack_name"], row["relative_path"])
    if destination.exists():
        return destination

    source = staging_folder / row["pack_folder"] / row["relative_path"]
    if not source.is_file():
        return None

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def archive_pack(
    conn: sqlite3.Connection,
    staging_folder: Path,
    assets_dir: Path,
    pack_id: int,
    on_progress: ProgressCallback | None = None,
) -> int:
    """Archives every asset currently in a pack. Returns how many succeeded
    (an asset already archived counts as a success, not a no-op)."""
    report = on_progress or (lambda _text: None)
    rows = conn.execute(
        "SELECT id, filename FROM assets WHERE pack_id = ?", (pack_id,)
    ).fetchall()
    archived = 0
    for row in rows:
        report(f"Archiving {row['filename']} to library...")
        if archive_asset(conn, staging_folder, assets_dir, row["id"]) is not None:
            archived += 1
    return archived
