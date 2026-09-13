from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Callable

from asset_catalogue import dependencies

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
    source = staging_folder / row["pack_folder"] / row["relative_path"]
    pack_root = staging_folder / row["pack_folder"]

    if not destination.exists():
        if not source.is_file():
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    # Whatever this model needs but the catalogue does not list -- a
    # .gltf's .bin, a .obj's .mtl and that .mtl's textures. Ingest is a
    # whitelist of content extensions, so those files are never assets
    # and were therefore never archived: the library held a .gltf with no
    # buffer beside it, which loads only while the original folder
    # survives. Run even when the model itself was already archived, so
    # libraries built before this fill in on the next archive pass.
    _archive_dependencies(source, pack_root, assets_dir, row["pack_name"])
    return destination


def _archive_dependencies(
    source: Path, pack_root: Path, assets_dir: Path, pack_name: str
) -> int:
    """Copies a model's uncatalogued dependencies, preserving their layout.

    Position relative to the pack root is the whole point: a reference
    inside the file is relative, so the copy only resolves if the tree
    around it is reproduced exactly.
    """
    if not source.is_file():
        return 0
    copied = 0
    for dependency in dependencies.referenced_files(source, pack_root):
        try:
            relative = dependency.relative_to(pack_root.resolve())
        except ValueError:
            continue
        target = asset_library_path(assets_dir, pack_name, str(relative))
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dependency, target)
            copied += 1
        except OSError:
            # One unreadable sidecar is not a reason to fail the whole
            # archive; the missing-file case is already reported by
            # library_health.
            continue
    return copied


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
