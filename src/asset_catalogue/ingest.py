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
    creator: str | None,
    licence: str | None,
    source_url: str | None,
) -> int:
    row = conn.execute("SELECT id FROM packs WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO packs (name, creator, licence, source_url, date_added) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, creator, licence, source_url, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


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
