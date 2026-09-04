from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from asset_catalogue import db, ingest


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = db.connect(tmp_path / "catalogue.db")
    yield connection
    connection.close()


@pytest.fixture
def staging_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "staging"
    folder.mkdir()
    return folder


@pytest.fixture
def thumbnail_dir(tmp_path: Path) -> Path:
    return tmp_path / "thumbnails"


@pytest.fixture
def assets_dir(tmp_path: Path) -> Path:
    return tmp_path / "assets"


def make_pack(conn: sqlite3.Connection, staging_folder: Path, name: str = "Pack") -> int:
    """Creates a pack row and its matching staging folder, returns pack_id."""
    (staging_folder / name).mkdir(parents=True, exist_ok=True)
    pack_id, _ = ingest.get_or_create_pack(conn, name, name, None, None, None)
    return pack_id


def write_texture(
    staging_folder: Path, pack_name: str, filename: str = "tex.png", color: tuple[int, int, int] = (200, 50, 50)
) -> Path:
    from PIL import Image

    path = staging_folder / pack_name / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(path)
    return path


def write_wav(staging_folder: Path, pack_name: str, filename: str = "sound.wav") -> Path:
    import wave

    path = staging_folder / pack_name / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x01" * 800)
    return path
