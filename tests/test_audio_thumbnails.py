from __future__ import annotations

import sqlite3
import struct
import wave
from pathlib import Path

import pytest

from asset_catalogue import audio_thumbnails, ingest

from conftest import write_wav


def _write_pcm_wav(path: Path, sample_width: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(sample_width)
        w.setframerate(44100)
        frame = bytes([200]) * sample_width + bytes([50]) * sample_width
        w.writeframes(frame * 800)


def _write_extensible_float32_wav(path: Path, n_frames: int = 1600) -> None:
    """Hand-builds a WAVE_FORMAT_EXTENSIBLE 32-bit float WAV -- the format
    many DAWs/export tools actually use, which the stdlib `wave` module
    can't write (or even open -- see audio_thumbnails._parse_wav).
    """
    n_channels = 1
    sample_rate = 44100
    bits_per_sample = 32
    block_align = n_channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align

    sub_format_guid = struct.pack("<H", 3) + bytes.fromhex("0000000000100080000000aa00389b71")
    fmt_chunk = (
        struct.pack(
            "<HHIIHHHH", 0xFFFE, n_channels, sample_rate, byte_rate, block_align,
            bits_per_sample, 22, bits_per_sample,
        )
        + struct.pack("<I", 0)  # channel mask
        + sub_format_guid
    )
    samples = struct.pack(f"<{n_frames}f", *([0.5, -0.25] * (n_frames // 2)))

    with path.open("wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 0))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", len(fmt_chunk)))
        f.write(fmt_chunk)
        f.write(b"data")
        f.write(struct.pack("<I", len(samples)))
        f.write(samples)
    size = path.stat().st_size - 8
    with path.open("r+b") as f:
        f.seek(4)
        f.write(struct.pack("<I", size))


@pytest.mark.parametrize("sample_width", [1, 2, 3, 4])
def test_render_waveform_thumbnail_handles_all_pcm_bit_depths(tmp_path: Path, sample_width: int) -> None:
    src = tmp_path / f"pcm_{sample_width}.wav"
    _write_pcm_wav(src, sample_width)
    dest = tmp_path / f"pcm_{sample_width}.png"
    audio_thumbnails.render_waveform_thumbnail(src, dest)
    assert dest.is_file()


def test_render_waveform_thumbnail_handles_extensible_float32(tmp_path: Path) -> None:
    src = tmp_path / "extensible_float.wav"
    _write_extensible_float32_wav(src)
    dest = tmp_path / "extensible_float.png"
    audio_thumbnails.render_waveform_thumbnail(src, dest)
    assert dest.is_file()


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


def test_generate_audio_thumbnails_asset_id_targets_only_that_asset(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    write_wav(staging_folder, "Pack", "a.wav", tone=b"\x00\x01")
    write_wav(staging_folder, "Pack", "b.wav", tone=b"\x02\x03")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    rows = {row["filename"]: row["id"] for row in conn.execute("SELECT id, filename FROM assets")}

    stats = audio_thumbnails.generate_audio_thumbnails(conn, staging_folder, thumbnail_dir, asset_id=rows["a.wav"])
    assert stats.generated == 1
    statuses = {
        row["filename"]: row["thumbnail_status"]
        for row in conn.execute("SELECT filename, thumbnail_status FROM assets")
    }
    assert statuses["a.wav"] == "done"
    assert statuses["b.wav"] == "pending"


def test_generate_audio_thumbnails_asset_ids_targets_the_given_set(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    """Backs the grid's multi-select "Regenerate N Thumbnail(s)" context
    menu action -- renders exactly the given assets and re-renders them
    regardless of current status, mirroring asset_id's behavior.
    """
    write_wav(staging_folder, "Pack", "a.wav", tone=b"\x00\x01")
    write_wav(staging_folder, "Pack", "b.wav", tone=b"\x02\x03")
    write_wav(staging_folder, "Pack", "c.wav", tone=b"\x04\x05")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    rows = {row["filename"]: row["id"] for row in conn.execute("SELECT id, filename FROM assets")}

    stats = audio_thumbnails.generate_audio_thumbnails(
        conn, staging_folder, thumbnail_dir, asset_ids=[rows["a.wav"], rows["b.wav"]]
    )
    assert stats.generated == 2
    statuses = {
        row["filename"]: row["thumbnail_status"]
        for row in conn.execute("SELECT filename, thumbnail_status FROM assets")
    }
    assert statuses["a.wav"] == "done"
    assert statuses["b.wav"] == "done"
    assert statuses["c.wav"] == "pending"

    stats2 = audio_thumbnails.generate_audio_thumbnails(conn, staging_folder, thumbnail_dir, asset_ids=[])
    assert stats2.generated == 0
    assert stats2.already_done == 0


def test_generate_audio_thumbnails_marks_malformed_wav_failed(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    """Mirrors thumbnails.py's own corrupt-file regression test
    (test_generate_texture_thumbnails_marks_corrupt_file_failed) -- this
    module has the equivalent try/except (wave.Error, OSError, ValueError,
    EOFError) around render_waveform_thumbnail, but nothing exercised a
    file that's genuinely a .wav by extension (so it goes through
    _parse_wav, not the undecodable-format placeholder path) yet fails
    to parse as one.
    """
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "corrupt.wav").write_bytes(b"not actually a wav file at all")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack_root, pack_id)

    stats = audio_thumbnails.generate_audio_thumbnails(conn, staging_folder, thumbnail_dir)

    assert stats.failed == 1
    assert conn.execute("SELECT thumbnail_status FROM assets").fetchone()["thumbnail_status"] == "failed"


def test_generate_audio_thumbnails_pack_name_filters_to_that_pack(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    write_wav(staging_folder, "PackA", "a.wav", tone=b"\x00\x01")
    write_wav(staging_folder, "PackB", "b.wav", tone=b"\x02\x03")
    pack_a_id, _ = ingest.get_or_create_pack(conn, "PackA", "PackA", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "PackA", pack_a_id)
    pack_b_id, _ = ingest.get_or_create_pack(conn, "PackB", "PackB", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "PackB", pack_b_id)

    stats = audio_thumbnails.generate_audio_thumbnails(conn, staging_folder, thumbnail_dir, pack_name="PackA")

    assert stats.generated == 1
    statuses = {
        row["filename"]: row["thumbnail_status"]
        for row in conn.execute("SELECT filename, thumbnail_status FROM assets")
    }
    assert statuses["a.wav"] == "done"
    assert statuses["b.wav"] == "pending"
