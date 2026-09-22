"""How long a sound is, and what shape it's in.

Reads headers only. The library holds 37 GB of audio, so anything that
decodes a file to find out how long it is would take hours and would have
to do it again every time. A WAV says its own length in the `data` chunk
size; an MP3 says it in a VBR header, or can be worked out from its
bitrate.

No new dependencies. Adding a decoder to read a number that's already
written in the file would be a poor trade.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

WAVE_FORMAT_EXTENSIBLE = 0xFFFE

# MPEG audio, indexed [version_bit][layer] and [version][bitrate_index].
_SAMPLE_RATES = {
    3: (44100, 48000, 32000),  # MPEG 1
    2: (22050, 24000, 16000),  # MPEG 2
    0: (11025, 12000, 8000),   # MPEG 2.5
}
_BITRATES_V1_L3 = (
    0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0
)
_BITRATES_V2_L3 = (
    0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0
)
# Samples per frame differs between MPEG 1 and MPEG 2/2.5 for Layer III.
_SAMPLES_PER_FRAME = {3: 1152, 2: 576, 0: 576}


@dataclass
class AudioFacts:
    duration_ms: int
    sample_rate: int
    channels: int


def _read_wav(path: Path) -> AudioFacts | None:
    """Duration from the data chunk's declared size, not its contents.

    Walks chunks rather than assuming offsets, since real files carry
    LIST/INFO metadata before the parts that matter. Handles
    WAVE_FORMAT_EXTENSIBLE, which the stdlib wave module refuses outright
    for perfectly valid 32-bit float files (see audio_thumbnails).
    """
    with path.open("rb") as handle:
        header = handle.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return None

        channels = sample_rate = byte_rate = data_bytes = None
        while True:
            chunk_header = handle.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id = chunk_header[:4]
            chunk_size = struct.unpack("<I", chunk_header[4:8])[0]

            if chunk_id == b"fmt ":
                fmt = handle.read(chunk_size)
                if len(fmt) < 16:
                    return None
                channels, sample_rate, byte_rate = struct.unpack("<HII", fmt[2:12])
            elif chunk_id == b"data":
                # Size only. Reading 200MB of samples to learn a duration
                # that the header already states would be absurd.
                data_bytes = chunk_size
                handle.seek(chunk_size, 1)
            else:
                handle.seek(chunk_size, 1)
            if chunk_size % 2:
                handle.seek(1, 1)

        if not (channels and sample_rate and byte_rate and data_bytes):
            return None
        return AudioFacts(round(data_bytes / byte_rate * 1000), sample_rate, channels)


def _id3_size(handle) -> int:
    """Bytes to skip past an ID3v2 tag, or 0 if there isn't one.

    The size is stored "syncsafe": seven bits per byte, so the high bit
    can never produce a false frame sync.
    """
    head = handle.read(10)
    if len(head) < 10 or head[:3] != b"ID3":
        handle.seek(0)
        return 0
    size = 0
    for byte in head[6:10]:
        size = (size << 7) | (byte & 0x7F)
    return size + 10


def _read_mp3(path: Path) -> AudioFacts | None:
    """Duration from the Xing/Info frame count where present, otherwise
    from the bitrate.

    The VBR header is the accurate one and most encoders write it. The
    bitrate fallback is exact for constant-bitrate files and an estimate
    for variable ones without a header, which is a reasonable answer for
    a file that declined to say.
    """
    total_size = path.stat().st_size
    with path.open("rb") as handle:
        start = _id3_size(handle)
        handle.seek(start)
        block = handle.read(8192)
        if len(block) < 4:
            return None

        for offset in range(len(block) - 4):
            if block[offset] != 0xFF or (block[offset + 1] & 0xE0) != 0xE0:
                continue
            b1, b2, b3 = block[offset + 1], block[offset + 2], block[offset + 3]
            version = (b1 >> 3) & 0x03
            layer = (b1 >> 1) & 0x03
            if version == 1 or layer == 0:
                continue  # reserved, so this sync was a coincidence
            rate_index = (b2 >> 2) & 0x03
            bitrate_index = (b2 >> 4) & 0x0F
            if rate_index == 3 or bitrate_index in (0, 15):
                continue
            sample_rate = _SAMPLE_RATES[version][rate_index]
            table = _BITRATES_V1_L3 if version == 3 else _BITRATES_V2_L3
            bitrate = table[bitrate_index] * 1000
            channels = 1 if ((b3 >> 6) & 0x03) == 3 else 2
            samples = _SAMPLES_PER_FRAME[version]

            # Xing (VBR) or Info (CBR) sits inside the first frame, past a
            # side-information block whose length depends on the layout.
            side_info = (
                (17 if channels == 1 else 32) if version == 3
                else (9 if channels == 1 else 17)
            )
            tag_at = offset + 4 + side_info
            if block[tag_at:tag_at + 4] in (b"Xing", b"Info"):
                flags = struct.unpack(">I", block[tag_at + 4:tag_at + 8])[0]
                if flags & 0x01:
                    frames = struct.unpack(">I", block[tag_at + 8:tag_at + 12])[0]
                    return AudioFacts(
                        round(frames * samples / sample_rate * 1000), sample_rate, channels
                    )
            if not bitrate:
                return None
            audio_bytes = total_size - start
            return AudioFacts(round(audio_bytes * 8 / bitrate * 1000), sample_rate, channels)
    return None


def read(path: Path) -> AudioFacts | None:
    """Facts about a sound file, or None if they can't be determined.

    None means "unknown", never "zero length". Nothing displays a
    duration it didn't actually read.
    """
    try:
        suffix = path.suffix.lower()
        if suffix == ".wav":
            return _read_wav(path)
        if suffix == ".mp3":
            return _read_mp3(path)
    except (OSError, struct.error, ValueError, IndexError, KeyError):
        return None
    # .ogg and .flac carry their duration too, but in formats that need
    # their own parsers. Left unknown until there are enough of them here
    # to be worth writing.
    return None


def format_duration(duration_ms: int | None) -> str:
    """m:ss for anything over a minute, otherwise seconds with one
    decimal -- a sound effect's length is usually the interesting part
    below a second, and minutes matter above it.
    """
    if duration_ms is None:
        return ""
    seconds = duration_ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def record(conn, content_hash: str, facts: AudioFacts) -> int:
    """By content hash: identical bytes are the same sound, and two packs
    shipping the same effect shouldn't be read twice. Returns rows
    updated.
    """
    cursor = conn.execute(
        "UPDATE assets SET duration_ms = ?, sample_rate = ?, channels = ? "
        "WHERE content_hash = ?",
        (facts.duration_ms, facts.sample_rate, facts.channels, content_hash),
    )
    return cursor.rowcount


def backfill(conn, assets_dir, ingest_folder, limit: int | None = None) -> int:
    """Fills in sounds that predate these columns. Returns rows filled.

    NULL-only, so it is safe to re-run and costs one indexed query once
    the library has caught up. A file that can't be read is left NULL
    rather than recorded as zero.
    """
    from asset_catalogue import library_assets

    query = (
        "SELECT assets.content_hash, assets.relative_path, packs.name AS pack_name, "
        "packs.pack_folder FROM assets JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.asset_type = 'audio' AND assets.duration_ms IS NULL"
    )
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    filled = 0
    for row in conn.execute(query).fetchall():
        source = library_assets.source_file(
            assets_dir, ingest_folder, row["pack_name"], row["pack_folder"],
            row["relative_path"],
        )
        if not source.is_file():
            continue
        facts = read(source)
        if facts is None:
            continue
        filled += record(conn, row["content_hash"], facts)
    conn.commit()
    return filled


# The filter panel's length bands, in order. Each entry is
# (label, low_ms_inclusive, high_ms_exclusive); None means unbounded.
#
# Chosen against the real library rather than from habit. The usual
# short-effect split (under 1s / 1-5s / 5-30s) would be wrong here: the
# median sound is 63 seconds and only 13% is under five, so that split
# puts 87% of everything in one bucket.
#
# The cutoffs separate jobs rather than making the groups equal. Under
# two seconds is a one-shot -- an impact, a UI blip. Up to fifteen is an
# effect or a sting. Up to a minute is a loop or a short ambience. Past
# that it is a piece of music, split once more because "two minutes or
# more" is a third of this library on its own.
#
# Resulting spread: 6% / 17% / 20% / 23% / 34%.
DURATION_BANDS: list[tuple[str, int | None, int | None]] = [
    ("Under 2s", None, 2_000),
    ("2-15s", 2_000, 15_000),
    ("15s-1min", 15_000, 60_000),
    ("1-2min", 60_000, 120_000),
    ("Over 2min", 120_000, None),
]


def band_bounds_ms(label: str) -> tuple[int | None, int | None]:
    """The (low, high) millisecond bounds for a band label.

    Unknown labels select nothing rather than everything, so a stale
    saved filter can't silently widen to the whole library.
    """
    for name, low, high in DURATION_BANDS:
        if name == label:
            return low, high
    return (None, -1)
