from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, UnidentifiedImageError

THUMBNAIL_SIZE = (256, 256)
ProgressCallback = Callable[[str], None]


def thumbnail_path(thumbnail_dir: Path, content_hash: str) -> Path:
    return thumbnail_dir / f"{content_hash}.png"


def render_2d_thumbnail(source_path: Path, dest_path: Path) -> None:
    with Image.open(source_path) as image:
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        image.thumbnail(THUMBNAIL_SIZE)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest_path, "PNG")


@dataclass
class ThumbnailStats:
    generated: int = 0
    already_done: int = 0
    failed: int = 0


def generate_texture_thumbnails(
    conn: sqlite3.Connection,
    staging_folder: Path,
    thumbnail_dir: Path,
    pack_name: str | None = None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> ThumbnailStats:
    report = on_progress or (lambda _text: None)
    query = (
        "SELECT assets.id, assets.filename, assets.relative_path, assets.content_hash, "
        "packs.pack_folder FROM assets JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.asset_type = 'texture'"
    )
    params: list[str] = []
    if not force:
        query += " AND assets.thumbnail_status != 'done'"
    if pack_name:
        query += " AND packs.name = ?"
        params.append(pack_name)

    rows = conn.execute(query, params).fetchall()
    stats = ThumbnailStats()
    for row in rows:
        dest = thumbnail_path(thumbnail_dir, row["content_hash"])

        # Thumbnail identity is the hash, not the file -- if it's already on
        # disk (from a previous run) there's nothing to render, just record it.
        if dest.exists() and not force:
            conn.execute(
                "UPDATE assets SET thumbnail_status = 'done' WHERE id = ?",
                (row["id"],),
            )
            stats.already_done += 1
            continue

        report(f"Rendering thumbnail for {row['filename']}...")
        source = staging_folder / row["pack_folder"] / row["relative_path"]
        try:
            render_2d_thumbnail(source, dest)
        except (UnidentifiedImageError, OSError):
            conn.execute(
                "UPDATE assets SET thumbnail_status = 'failed' WHERE id = ?",
                (row["id"],),
            )
            stats.failed += 1
            continue

        conn.execute(
            "UPDATE assets SET thumbnail_status = 'done' WHERE id = ?",
            (row["id"],),
        )
        stats.generated += 1

    conn.commit()
    return stats
