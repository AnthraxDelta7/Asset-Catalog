from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import audio_thumbnails, ingest

from conftest import write_wav


def test_generate_audio_thumbnails_renders_waveform_for_wav(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    write_wav(staging_folder, "Pack", "sound.wav")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)

    messages: list[str] = []
    stats = audio_thumbnails.generate_audio_thumbnails(conn, staging_folder, thumbnail_dir, on_progress=messages.append)
    assert stats.generated == 1
    content_hash = conn.execute("SELECT content_hash FROM assets").fetchone()["content_hash"]
    assert audio_thumbnails.thumbnail_path(thumbnail_dir, content_hash).is_file()
    assert any("Rendering thumbnail for sound.wav" in m for m in messages)


def test_generate_audio_thumbnails_placeholder_for_undecodable_format(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "song.mp3").write_bytes(b"fake mp3 bytes, never decoded")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack_root, pack_id)

    stats = audio_thumbnails.generate_audio_thumbnails(conn, staging_folder, thumbnail_dir)
    assert stats.generated == 1  # placeholder counts as generated, not failed
    content_hash = conn.execute("SELECT content_hash FROM assets").fetchone()["content_hash"]
    assert audio_thumbnails.thumbnail_path(thumbnail_dir, content_hash).is_file()
