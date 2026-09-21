"""Rig and animation counts, kept on the asset row.

model_metadata can answer "what does this model contain" for one asset,
but it costs a file read every time -- a glTF header parse, or a JSON
cache lookup. The grid needs the answer for every visible asset at once,
and the tag panel needs it for the whole library, so the answer is
written onto the row when it is learned and read back with the rest of
the asset.

NULL means "not inspected yet", which is deliberately different from 0
("inspected, and it has none"). A badge must not claim a model has no
rig when nothing has looked.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import library_assets, model_metadata


def record(
    conn: sqlite3.Connection, asset_id: int, joint_count: int, animation_count: int
) -> None:
    conn.execute(
        "UPDATE assets SET joint_count = ?, animation_count = ? WHERE id = ?",
        (joint_count, animation_count, asset_id),
    )


def record_by_hash(
    conn: sqlite3.Connection, content_hash: str, joint_count: int, animation_count: int
) -> int:
    """By content hash rather than id, because that is the identity a
    render reports back -- and because two assets with identical bytes
    are the same model and deserve the same answer without a second
    inspection. Returns rows updated.
    """
    cursor = conn.execute(
        "UPDATE assets SET joint_count = ?, animation_count = ? WHERE content_hash = ?",
        (joint_count, animation_count, content_hash),
    )
    return cursor.rowcount


def backfill(
    conn: sqlite3.Connection,
    assets_dir: Path,
    ingest_folder: Path | None,
    preview_dir: Path,
    limit: int | None = None,
) -> int:
    """Fills in models that predate these columns. Returns rows filled.

    Only touches rows that are still NULL, so it is safe to re-run and
    cheap once the library has caught up. Anything still unreadable --
    an .fbx never rendered, a file whose source is gone -- is left NULL
    rather than recorded as zero, since "unknown" is the honest answer
    and the next render will supply a real one.
    """
    query = (
        "SELECT assets.id, assets.relative_path, assets.content_hash, packs.name AS pack_name, "
        "packs.pack_folder FROM assets JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.asset_type = 'model' AND assets.joint_count IS NULL"
    )
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    filled = 0
    for row in conn.execute(query).fetchall():
        source = library_assets.source_file(
            assets_dir, ingest_folder, row["pack_name"], row["pack_folder"], row["relative_path"]
        )
        if not source.is_file():
            continue
        metadata = model_metadata.read(source, preview_dir, row["content_hash"])
        if metadata is None:
            continue
        record(conn, row["id"], metadata.joint_count, len(metadata.animation_names))
        filled += 1
    conn.commit()
    return filled
