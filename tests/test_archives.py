from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from asset_catalogue import archives


def test_extract_zip_happy_path(tmp_path: Path) -> None:
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("model.fbx", b"fake model data")
        zf.writestr("sub/texture.png", b"fake texture data")

    destination = tmp_path / "extracted"
    archives.extract_zip(zip_path, destination)

    assert (destination / "model.fbx").read_bytes() == b"fake model data"
    assert (destination / "sub" / "texture.png").read_bytes() == b"fake texture data"


def test_extract_zip_refuses_nonempty_destination(tmp_path: Path) -> None:
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.txt", b"data")

    destination = tmp_path / "extracted"
    destination.mkdir()
    (destination / "existing.txt").write_text("already here")

    with pytest.raises(FileExistsError):
        archives.extract_zip(zip_path, destination)


def test_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../evil_escape.txt", b"malicious")

    destination = tmp_path / "extracted"
    with pytest.raises(archives.UnsafeZipError):
        archives.extract_zip(zip_path, destination)

    # Nothing should have been extracted, and no partial destination left behind.
    assert not destination.exists()
