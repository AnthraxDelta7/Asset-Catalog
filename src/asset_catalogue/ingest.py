from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from asset_catalogue import archives

ProgressCallback = Callable[[str], None]

HASH_CHUNK_SIZE = 1024 * 1024

# Guards against a maliciously or accidentally self-referential chain of
# nested zips (a "zip bomb" via unbounded recursive extraction). Only counts
# zip-extraction depth, never ordinary folder nesting, which stays unlimited.
MAX_NESTED_ZIP_DEPTH = 10

# Quality control: this catalogue is for raw 3D/2D/audio content, and this
# is now the whitelist of what actually gets catalogued -- a file whose
# extension isn't a key here is skipped entirely during ingest, not
# catalogued as a generic "other" asset. This is deliberately a whitelist
# rather than a blacklist of known-bad extensions (Unity/Unreal project
# files, etc.): a blacklist only excludes what it explicitly knows about,
# so anything unanticipated (a new engine, a random stray file) still got
# catalogued as noise; a whitelist only *includes* what it explicitly
# knows how to handle, so nothing unrecognized slips through either way.
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

# Whole folders belonging to an engine project's build/cache machinery
# (never real content) -- skipped entirely, not walked into. Matched
# case-insensitively against the folder name only, not the full path. Kept
# even under the whitelist model below: these folders can genuinely contain
# files with whitelisted extensions (e.g. Unity's Library/ caching its own
# internal preview textures), which aren't real pack content just because
# their extension looks like one.
ENGINE_PROJECT_FOLDER_NAMES: set[str] = {
    name.lower()
    for name in (
        # Unity
        "Library", "Temp", "Obj", "Logs", "UserSettings", "ProjectSettings",
        # Unreal
        "Binaries", "Intermediate", "Saved", "DerivedDataCache",
        # Godot -- .godot (4.x) / .import (3.x) hold the editor's own
        # re-import cache (compressed textures, baked shaders, etc.), not
        # source content; can be sizable and is regenerated on demand.
        ".godot", ".import",
        # General VCS noise, commonly bundled by accident
        ".git", ".svn", ".vs",
    )
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
    nested_zips_extracted: int = 0
    skipped_unrecognized_files: int = 0
    skipped_engine_folders: int = 0
    archived: int = 0
    thumbnails_generated: int = 0
    thumbnails_failed: int = 0
    blender_unavailable_reason: str | None = None
    calibration_preview: bool = False
    models_pending: int = 0
    preview_asset_id: int | None = None
    broken_texture_filenames: list[str] = field(default_factory=list)
    smart_texture_notes: list[str] = field(default_factory=list)


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


def ingest_pack(
    conn: sqlite3.Connection,
    pack_root: Path,
    pack_id: int,
    on_progress: ProgressCallback | None = None,
) -> IngestStats:
    """Walks pack_root and catalogues every file as an asset.

    A .zip found anywhere in the walk -- not just at pack_root itself -- is
    extracted in place into a sibling folder named after it (stripping the
    .zip suffix) rather than being catalogued as an opaque asset, and that
    folder's contents are walked too, recursively. This can't be a plain
    rglob(): files created by an extraction mid-walk need to be picked up
    within the same pass, so this uses an explicit work queue instead.
    """
    report = on_progress or (lambda _text: None)
    stats = IngestStats()
    work_items: list[tuple[Path, int]] = [(pack_root, 0)]

    while work_items:
        current_dir, zip_depth = work_items.pop()
        entries = sorted(current_dir.iterdir())
        # A directory that's the extraction destination of a sibling .zip
        # (from an earlier ingest pass) shows up here as an ordinary
        # pre-existing folder too -- skip it on the plain "it's a folder"
        # path below so it's only queued once, via the zip branch, not
        # walked twice (which otherwise compounds at every nesting level).
        zip_stems = {entry.stem for entry in entries if entry.is_file() and entry.suffix.lower() == ".zip"}

        for entry in entries:
            if entry.is_dir():
                if entry.name in zip_stems:
                    continue
                if entry.name.lower() in ENGINE_PROJECT_FOLDER_NAMES:
                    stats.skipped_engine_folders += 1
                    continue
                work_items.append((entry, zip_depth))
                continue
            if not entry.is_file():
                continue

            if entry.suffix.lower() == ".zip":
                if zip_depth >= MAX_NESTED_ZIP_DEPTH:
                    raise ValueError(
                        f"Refusing to extract more than {MAX_NESTED_ZIP_DEPTH} levels "
                        f"of nested zips: {entry}"
                    )
                extracted_dir = entry.with_suffix("")
                if extracted_dir.exists() and any(extracted_dir.iterdir()):
                    # Already unpacked on an earlier ingest pass -- just
                    # re-walk it for anything new, don't re-extract.
                    pass
                else:
                    report(f"Extracting {entry.name}...")
                    archives.extract_zip(entry, extracted_dir)
                    stats.nested_zips_extracted += 1
                work_items.append((extracted_dir, zip_depth + 1))
                continue

            if entry.suffix.lower() not in ASSET_TYPE_BY_EXTENSION:
                stats.skipped_unrecognized_files += 1
                continue

            stats.total += 1
            relative_path = entry.relative_to(pack_root).as_posix()
            report(f"Hashing {entry.name}...")
            content_hash = hash_file(entry)
            extension = entry.suffix.lower()
            cursor = conn.execute(
                "INSERT OR IGNORE INTO assets "
                "(pack_id, relative_path, filename, extension, file_size, "
                " content_hash, asset_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    pack_id,
                    relative_path,
                    entry.name,
                    extension,
                    entry.stat().st_size,
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
