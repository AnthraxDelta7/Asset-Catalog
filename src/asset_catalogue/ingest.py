from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HASH_CHUNK_SIZE = 1024 * 1024

ASSET_TYPE_BY_EXTENSION: dict[str, str] = {
    ".obj": "model",
    ".fbx": "model",
    ".gltf": "model",
    ".glb": "model",
    ".stl": "model",
    ".blend": "model",
    ".png": "texture",
    ".jpg": "texture",
    ".jpeg": "texture",
    ".tga": "texture",
    ".bmp": "texture",
    ".tiff": "texture",
    ".webp": "texture",
    ".wav": "audio",
    ".mp3": "audio",
    ".ogg": "audio",
    ".flac": "audio",
}


def classify(extension: str) -> str:
    return ASSET_TYPE_BY_EXTENSION.get(extension.lower(), "other")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class IngestStats:
    new: int = 0
    duplicate: int = 0
    total: int = 0


def get_or_create_pack(
    conn: sqlite3.Connection,
    name: str,
    pack_folder: str,
    creator: str | None,
    licence: str | None,
    source_url: str | None,
) -> tuple[int, list[str]]:
    """Returns (pack_id, updated_fields).

    Re-ingesting an existing pack (matched by name) only overwrites fields
    that were actually supplied this time AND differ from what's stored --
    the delta, not a blind overwrite. Omitting --creator on a re-ingest
    (creator=None) must never erase a creator recorded on an earlier run.
    """
    row = conn.execute(
        "SELECT id, pack_folder, creator, licence, source_url FROM packs WHERE name = ?",
        (name,),
    ).fetchone()
    if row is not None:
        updates: dict[str, str | None] = {}
        if pack_folder != row["pack_folder"]:
            updates["pack_folder"] = pack_folder
        if creator is not None and creator != row["creator"]:
            updates["creator"] = creator
        if licence is not None and licence != row["licence"]:
            updates["licence"] = licence
        if source_url is not None and source_url != row["source_url"]:
            updates["source_url"] = source_url
        if updates:
            set_clause = ", ".join(f"{column} = ?" for column in updates)
            conn.execute(
                f"UPDATE packs SET {set_clause} WHERE id = ?",
                (*updates.values(), row["id"]),
            )
            conn.commit()
        return row["id"], list(updates.keys())
    cursor = conn.execute(
        "INSERT INTO packs (name, pack_folder, creator, licence, source_url, date_added) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            name,
            pack_folder,
            creator,
            licence,
            source_url,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return cursor.lastrowid, []


def ingest_pack(conn: sqlite3.Connection, pack_root: Path, pack_id: int) -> IngestStats:
    stats = IngestStats()
    for path in sorted(pack_root.rglob("*")):
        if not path.is_file():
            continue
        stats.total += 1
        relative_path = path.relative_to(pack_root).as_posix()
        content_hash = hash_file(path)
        extension = path.suffix.lower()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO assets "
            "(pack_id, relative_path, filename, extension, file_size, "
            " content_hash, asset_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                pack_id,
                relative_path,
                path.name,
                extension,
                path.stat().st_size,
                content_hash,
                classify(extension),
            ),
        )
        if cursor.rowcount == 0:
            stats.duplicate += 1
        else:
            stats.new += 1
    conn.commit()
    return stats
