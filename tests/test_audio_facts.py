"""Length and format for sounds, read from headers.

The library holds 37 GB of audio, so nothing decodes a file to learn how
long it is. A WAV states it in the data chunk size, an MP3 in its VBR
header or its bitrate.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from asset_catalogue import audio_facts, db, ingest, library_assets


def _write_wav(path: Path, seconds: float, rate: int = 44100, channels: int = 2) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds) * channels)


def test_a_wav_reports_its_length_and_format(tmp_path: Path) -> None:
    path = tmp_path / "hit.wav"
    _write_wav(path, 1.5, rate=22050, channels=1)

    facts = audio_facts.read(path)
    assert facts is not None
    assert facts.duration_ms == pytest.approx(1500, abs=20)
    assert (facts.sample_rate, facts.channels) == (22050, 1)


def test_length_comes_from_the_header_not_the_samples(tmp_path: Path) -> None:
    """Reading a 200MB data chunk to learn a duration the header already
    states is the thing this exists to avoid, so it must not depend on
    the samples being readable.
    """
    path = tmp_path / "long.wav"
    _write_wav(path, 3.0)
    original = path.read_bytes()

    # Keep the RIFF/fmt/data headers, drop most of the payload. The
    # declared data size still says three seconds.
    truncated = original[:200]
    path.write_bytes(truncated)

    facts = audio_facts.read(path)
    assert facts is not None
    assert facts.duration_ms == pytest.approx(3000, abs=50)


def test_unreadable_audio_is_unknown_not_zero(tmp_path: Path) -> None:
    """None means "not read". Zero would claim a silent file."""
    junk = tmp_path / "broken.wav"
    junk.write_bytes(b"not a wav at all")
    assert audio_facts.read(junk) is None

    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    assert audio_facts.read(empty) is None

    # Formats without a parser yet say nothing rather than guessing.
    assert audio_facts.read(tmp_path / "x.ogg") is None


def test_durations_are_formatted_for_reading(tmp_path: Path) -> None:
    assert audio_facts.format_duration(None) == ""
    assert audio_facts.format_duration(800) == "0.8s"
    assert audio_facts.format_duration(1500) == "1.5s"
    assert audio_facts.format_duration(62800) == "1:02"
    assert audio_facts.format_duration(525500) == "8:45"


def test_the_bands_cover_every_length_without_overlapping() -> None:
    """A sound must land in exactly one band, or the filter lies about
    how many there are.
    """
    bands = audio_facts.DURATION_BANDS
    assert bands, "no bands defined"

    # Contiguous: each band starts where the previous one ended.
    assert bands[0][1] is None
    assert bands[-1][2] is None
    for (_, _, high), (_, low, _) in zip(bands, bands[1:]):
        assert high == low, "a gap or overlap between bands"

    def band_for(ms: int) -> list[str]:
        return [
            label
            for label, low, high in bands
            if (low is None or ms >= low) and (high is None or ms < high)
        ]

    for ms in (0, 1, 1999, 2000, 14999, 15000, 59999, 60000, 120000, 600000):
        assert len(band_for(ms)) == 1, f"{ms}ms lands in {band_for(ms)}"


def test_an_unknown_band_label_selects_nothing(tmp_path: Path) -> None:
    """A stale saved filter must not silently widen to the whole
    library.
    """
    low, high = audio_facts.band_bounds_ms("Not A Band")
    assert (low, high) == (None, -1)


def test_ingest_records_length_and_the_filter_uses_it(tmp_path: Path, monkeypatch) -> None:
    from asset_catalogue import settings
    from asset_catalogue.catalogue import Catalogue

    library, staging = tmp_path / "library", tmp_path / "staging"
    pack = staging / "Sounds"
    pack.mkdir(parents=True)
    library.mkdir()
    _write_wav(pack / "blip.wav", 0.5)
    _write_wav(pack / "sting.wav", 8.0)
    _write_wav(pack / "ambience.wav", 90.0)

    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Sounds", "Sounds", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)

    catalogue = Catalogue(conn, staging, library / "thumbnails", library / "assets")
    by_name = {a.filename: a for a in catalogue.list_assets()}
    assert by_name["blip.wav"].duration_ms == pytest.approx(500, abs=20)
    assert by_name["ambience.wav"].channels == 2

    def names(band: str) -> list[str]:
        return sorted(a.filename for a in catalogue.list_assets(duration_band=band))

    assert names("Under 2s") == ["blip.wav"]
    assert names("2-15s") == ["sting.wav"]
    assert names("1-2min") == ["ambience.wav"]
    assert names("Over 2min") == []
    conn.close()


def test_a_sound_with_no_known_length_is_in_no_band(tmp_path: Path, monkeypatch) -> None:
    """Excluded on purpose. A sound nothing has measured has not been
    shown to be short, and putting it in every band would make the
    filter meaningless.
    """
    from asset_catalogue import settings
    from asset_catalogue.catalogue import Catalogue

    library, staging = tmp_path / "library", tmp_path / "staging"
    pack = staging / "Sounds"
    pack.mkdir(parents=True)
    library.mkdir()
    _write_wav(pack / "known.wav", 1.0)
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Sounds", "Sounds", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    conn.execute("UPDATE assets SET duration_ms = NULL")
    conn.commit()

    catalogue = Catalogue(conn, staging, library / "thumbnails", library / "assets")
    assert len(catalogue.list_assets(asset_type="audio")) == 1
    assert catalogue.list_assets(duration_band="Under 2s") == []
    conn.close()


def test_backfill_fills_only_what_is_unknown(tmp_path: Path) -> None:
    library, staging = tmp_path / "library", tmp_path / "staging"
    pack = staging / "Sounds"
    pack.mkdir(parents=True)
    library.mkdir()
    _write_wav(pack / "a.wav", 2.0)
    (pack / "b.wav").write_bytes(b"not a wav")

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Sounds", "Sounds", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    assets_dir = library / "assets"
    library_assets.archive_pack(conn, staging, assets_dir, pack_id)
    conn.execute("UPDATE assets SET duration_ms = NULL, sample_rate = NULL, channels = NULL")
    conn.commit()

    assert audio_facts.backfill(conn, assets_dir, staging) == 1
    rows = {
        r["filename"]: r["duration_ms"]
        for r in conn.execute("SELECT filename, duration_ms FROM assets")
    }
    assert rows["a.wav"] == pytest.approx(2000, abs=50)
    assert rows["b.wav"] is None, "unreadable must stay unknown"

    # Converged: a second run finds nothing left to do.
    assert audio_facts.backfill(conn, assets_dir, staging) == 0
    conn.close()
