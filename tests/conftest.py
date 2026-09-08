from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from asset_catalogue import db, ingest, settings


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


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch) -> tuple[sqlite3.Connection, Path]:
    """cli.py's commands never take a connection/staging folder as a
    parameter -- they all call _connect(), which reads settings.load()
    itself, unlike every lower-level module's functions (which take conn
    explicitly and are already exercised via the plain `conn`/
    `staging_folder` fixtures above). Points real settings at a real,
    on-disk tmp library/staging pair so cli.py's own _connect() and a
    test's setup code operate on the exact same database, then returns
    (conn, staging_folder) for the test to seed data with directly.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    connection = db.connect(library / "catalogue.db")
    yield connection, staging
    connection.close()


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


def write_wav(
    staging_folder: Path, pack_name: str, filename: str = "sound.wav", tone: bytes = b"\x00\x01"
) -> Path:
    import wave

    path = staging_folder / pack_name / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(tone * 800)
    return path


def write_minimal_glb(path, document=None):
    """A real, structurally valid .glb holding nothing but a single
    static mesh -- what an export test needs in order to exercise the
    flattening path at all.

    Placeholder bytes won't do any more: anything the glTF inspector
    can't read is now deliberately preserved untouched rather than
    rewritten (see gltf_metadata.preservation_reasons), so a test using
    fake content would silently take the "preserve" branch and stop
    testing what it meant to.
    """
    import json
    import struct

    payload = json.dumps(document if document is not None else {"meshes": [{}]}).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)  # glTF chunks are 4-byte aligned
    chunk = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(chunk)) + chunk)
