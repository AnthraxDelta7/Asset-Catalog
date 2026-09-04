from __future__ import annotations

import sqlite3
import struct
import wave
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from asset_catalogue.thumbnails import THUMBNAIL_SIZE, ThumbnailStats, thumbnail_path

ProgressCallback = Callable[[str], None]

BACKGROUND_COLOR = (24, 24, 28)
WAVEFORM_COLOR = (100, 180, 255)
PLACEHOLDER_COLOR = (140, 140, 150)

# .wav decodes with the stdlib `wave` module, so it gets a real rendered
# waveform. .mp3/.ogg/.flac have no stdlib decoder -- rather than add a
# decoding dependency (and, for mp3, likely an external ffmpeg install),
# they get a plain "audio file" placeholder icon instead: still visually
# distinct from other asset types, without pretending to show real data.
WAVEFORM_EXTENSIONS = {".wav"}

# WAV's own format-tag values, from the fmt chunk -- `wave` gives us
# channels/sample-width/frame-rate but never this, and it's needed to tell
# 32-bit integer PCM apart from 32-bit IEEE float (same sample_width,
# completely different byte layout and value range).
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def _parse_wav(source_path: Path) -> tuple[int, int, int, bytes]:
    """Manually parses the WAV file's RIFF chunks and returns
    (format_tag, n_channels, sample_width_bytes, raw_data_bytes).

    Bypasses the stdlib `wave` module entirely, rather than just reading
    the format tag ourselves and letting `wave` handle the rest -- verified
    that `wave.open()` outright refuses to open a WAVE_FORMAT_EXTENSIBLE
    file whose SubFormat isn't its own hardcoded PCM GUID, raising
    `wave.Error: unknown extended format` for a perfectly valid 32-bit
    float WAVE_FORMAT_EXTENSIBLE file -- a format real audio tools commonly
    export. Chunk-walking (not fixed offsets) since real files often carry
    extra chunks (LIST/INFO metadata, etc.) before "fmt "/"data". For
    WAVE_FORMAT_EXTENSIBLE, the real format tag is buried in the first two
    bytes of the 16-byte SubFormat GUID rather than the base tag field.
    """
    try:
        with source_path.open("rb") as f:
            header = f.read(12)
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                raise ValueError(f"Not a valid WAV file: {source_path.name}")

            format_tag: int | None = None
            n_channels: int | None = None
            sample_width: int | None = None
            raw_data: bytes | None = None

            while True:
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    break
                chunk_id = chunk_header[:4]
                chunk_size = struct.unpack("<I", chunk_header[4:8])[0]

                if chunk_id == b"fmt ":
                    fmt_data = f.read(chunk_size)
                    format_tag, n_channels = struct.unpack("<HH", fmt_data[:4])
                    bits_per_sample = struct.unpack("<H", fmt_data[14:16])[0]
                    sample_width = bits_per_sample // 8
                    if format_tag == WAVE_FORMAT_EXTENSIBLE and len(fmt_data) >= 26:
                        format_tag = struct.unpack("<H", fmt_data[24:26])[0]
                elif chunk_id == b"data":
                    raw_data = f.read(chunk_size)
                else:
                    f.seek(chunk_size, 1)
                # Chunks are word-aligned; an odd-sized chunk has a padding
                # byte after it that isn't part of chunk_size.
                if chunk_size % 2:
                    f.read(1)

            if format_tag is None or n_channels is None or sample_width is None:
                raise ValueError(f"WAV file has no fmt chunk: {source_path.name}")
            if raw_data is None:
                raise ValueError(f"WAV file has no data chunk: {source_path.name}")
            return format_tag, n_channels, sample_width, raw_data
    except struct.error as exc:
        raise ValueError(f"Malformed WAV header: {source_path.name}") from exc


def _decode_pcm_samples(raw: bytes, sample_width: int) -> tuple[list[int], int]:
    """Returns (signed samples, max possible amplitude for that width).
    WAV's 8-bit PCM is stored unsigned (centered at 128) -- every other
    integer width is signed -- so 8-bit needs re-centering before it's
    comparable to the others. 24-bit has no native `struct` format code,
    so it's unpacked manually as little-endian byte triplets with sign
    extension.
    """
    if sample_width == 1:
        return [b - 128 for b in raw], 128
    if sample_width == 2:
        count = len(raw) // 2
        return list(struct.unpack(f"<{count}h", raw[: count * 2])), 1 << 15
    if sample_width == 3:
        count = len(raw) // 3
        samples = []
        for i in range(count):
            b0, b1, b2 = raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]
            value = b0 | (b1 << 8) | (b2 << 16)
            if value >= 1 << 23:
                value -= 1 << 24
            samples.append(value)
        return samples, 1 << 23
    if sample_width == 4:
        count = len(raw) // 4
        return list(struct.unpack(f"<{count}i", raw[: count * 4])), 1 << 31
    raise ValueError(f"Unsupported WAV sample width: {sample_width * 8}-bit")


def _decode_float_samples(raw: bytes, sample_width: int) -> tuple[list[float], float]:
    """IEEE float WAV samples are already normalized to roughly -1.0..1.0."""
    if sample_width == 4:
        count = len(raw) // 4
        return list(struct.unpack(f"<{count}f", raw[: count * 4])), 1.0
    if sample_width == 8:
        count = len(raw) // 8
        return list(struct.unpack(f"<{count}d", raw[: count * 8])), 1.0
    raise ValueError(f"Unsupported floating-point WAV sample width: {sample_width * 8}-bit")


def render_waveform_thumbnail(source_path: Path, dest_path: Path) -> None:
    format_tag, n_channels, sample_width, raw = _parse_wav(source_path)

    if format_tag == WAVE_FORMAT_IEEE_FLOAT:
        samples, max_amplitude = _decode_float_samples(raw, sample_width)
    elif format_tag == WAVE_FORMAT_PCM:
        samples, max_amplitude = _decode_pcm_samples(raw, sample_width)
    else:
        raise ValueError(f"Unsupported WAV format tag: {format_tag}")

    if n_channels > 1:
        samples = samples[::n_channels]  # first channel only -- plenty for a preview

    width, height = THUMBNAIL_SIZE
    image = Image.new("RGB", (width, height), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    if samples:
        chunk_size = max(1, len(samples) // width)
        mid_y = height / 2
        scale = (height / 2 - 4) / max_amplitude
        for x in range(width):
            chunk = samples[x * chunk_size : (x + 1) * chunk_size]
            if not chunk:
                continue
            peak = max(abs(s) for s in chunk)
            bar_height = max(1, int(peak * scale))
            draw.line([(x, mid_y - bar_height), (x, mid_y + bar_height)], fill=WAVEFORM_COLOR)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest_path, "PNG")


def render_placeholder_thumbnail(dest_path: Path, label: str) -> None:
    width, height = THUMBNAIL_SIZE
    image = Image.new("RGB", (width, height), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    # A simple musical-note glyph built from primitive shapes -- there's no
    # waveform data to show here, so this only needs to read as "audio" at
    # a glance, not attempt to look like a real decoded file.
    head_w, head_h = 34, 24
    head_x, head_y = width / 2 - 30, height / 2 + 10
    draw.ellipse([head_x, head_y, head_x + head_w, head_y + head_h], fill=PLACEHOLDER_COLOR)
    stem_x = head_x + head_w - 4
    draw.line([(stem_x, head_y + head_h / 2), (stem_x, head_y - 90)], fill=PLACEHOLDER_COLOR, width=6)
    draw.polygon(
        [(stem_x, head_y - 90), (stem_x + 26, head_y - 70), (stem_x, head_y - 55)],
        fill=PLACEHOLDER_COLOR,
    )

    text = label.lstrip(".").upper()
    text_bbox = draw.textbbox((0, 0), text)
    text_w = text_bbox[2] - text_bbox[0]
    draw.text((width / 2 - text_w / 2, height - 40), text, fill=PLACEHOLDER_COLOR)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest_path, "PNG")


def generate_audio_thumbnails(
    conn: sqlite3.Connection,
    staging_folder: Path,
    thumbnail_dir: Path,
    pack_name: str | None = None,
    force: bool = False,
    asset_id: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> ThumbnailStats:
    # Targeting one asset directly (e.g. the detail panel's "Generate
    # Thumbnail" button) always renders it regardless of prior status, same
    # as --force -- mirrors blender_render.build_job_list's asset_id
    # handling for models.
    effective_force = force or asset_id is not None
    report = on_progress or (lambda _text: None)
    query = (
        "SELECT assets.id, assets.filename, assets.relative_path, assets.content_hash, "
        "assets.extension, packs.pack_folder "
        "FROM assets JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.asset_type = 'audio'"
    )
    params: list[str] = []
    if not effective_force:
        query += " AND assets.thumbnail_status != 'done'"
    if pack_name:
        query += " AND packs.name = ?"
        params.append(pack_name)
    if asset_id is not None:
        query += " AND assets.id = ?"
        params.append(asset_id)

    rows = conn.execute(query, params).fetchall()
    stats = ThumbnailStats()
    for row in rows:
        dest = thumbnail_path(thumbnail_dir, row["content_hash"])

        # Thumbnail identity is the hash, not the file -- if it's already on
        # disk (from a previous run) there's nothing to render, just record it.
        if dest.exists() and not effective_force:
            conn.execute(
                "UPDATE assets SET thumbnail_status = 'done' WHERE id = ?",
                (row["id"],),
            )
            stats.already_done += 1
            continue

        report(f"Rendering thumbnail for {row['filename']}...")
        extension = row["extension"].lower()
        source = staging_folder / row["pack_folder"] / row["relative_path"]
        try:
            if extension in WAVEFORM_EXTENSIONS:
                render_waveform_thumbnail(source, dest)
            else:
                render_placeholder_thumbnail(dest, extension)
        except (wave.Error, OSError, ValueError, EOFError):
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
