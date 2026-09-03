from __future__ import annotations

import sqlite3
import struct
import wave
from pathlib import Path

from PIL import Image, ImageDraw

from asset_catalogue.thumbnails import THUMBNAIL_SIZE, ThumbnailStats, thumbnail_path

BACKGROUND_COLOR = (24, 24, 28)
WAVEFORM_COLOR = (100, 180, 255)
PLACEHOLDER_COLOR = (140, 140, 150)

# .wav decodes with the stdlib `wave` module, so it gets a real rendered
# waveform. .mp3/.ogg/.flac have no stdlib decoder -- rather than add a
# decoding dependency (and, for mp3, likely an external ffmpeg install),
# they get a plain "audio file" placeholder icon instead: still visually
# distinct from other asset types, without pretending to show real data.
WAVEFORM_EXTENSIONS = {".wav"}


def render_waveform_thumbnail(source_path: Path, dest_path: Path) -> None:
    with wave.open(str(source_path), "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        raw = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        # 8-bit/24-bit/32-bit-or-float PCM isn't handled by the 16-bit
        # unpack below -- treated as a decode failure (retried later like
        # any other failed thumbnail) rather than misinterpreting bytes.
        raise ValueError(f"Unsupported WAV sample width: {sample_width * 8}-bit")

    total_samples = len(raw) // 2
    samples = struct.unpack(f"<{total_samples}h", raw[: total_samples * 2])
    if n_channels > 1:
        samples = samples[::n_channels]  # first channel only -- plenty for a preview

    width, height = THUMBNAIL_SIZE
    image = Image.new("RGB", (width, height), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    if samples:
        chunk_size = max(1, len(samples) // width)
        mid_y = height / 2
        scale = (height / 2 - 4) / 32768
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
) -> ThumbnailStats:
    query = (
        "SELECT assets.id, assets.relative_path, assets.content_hash, assets.extension, "
        "packs.pack_folder FROM assets JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.asset_type = 'audio'"
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
