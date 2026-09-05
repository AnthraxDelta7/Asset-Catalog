from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from asset_catalogue import self_update


def test_is_frozen_false_in_tests() -> None:
    # Tests always run from source, never as a packaged .exe.
    assert self_update.is_frozen() is False


def _make_update_zip(tmp_path: Path, top_folder: str, exe_name: str | None) -> Path:
    zip_path = tmp_path / "update.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{top_folder}/_internal/dummy.txt", "dummy bundled file")
        if exe_name is not None:
            zf.writestr(f"{top_folder}/{exe_name}", "pretend exe bytes")
    return zip_path


def test_extract_update_finds_the_app_folder_containing_the_exe(tmp_path: Path) -> None:
    zip_path = _make_update_zip(tmp_path, "AssetCatalogue", "AssetCatalogue.exe")
    extracted = self_update.extract_update(zip_path, "AssetCatalogue.exe")
    assert extracted.name == "AssetCatalogue"
    assert (extracted / "AssetCatalogue.exe").is_file()
    assert (extracted / "_internal" / "dummy.txt").is_file()


def test_extract_update_rejects_zip_missing_the_exe(tmp_path: Path) -> None:
    zip_path = _make_update_zip(tmp_path, "AssetCatalogue", exe_name=None)
    with pytest.raises(self_update.SelfUpdateError, match="AssetCatalogue.exe"):
        self_update.extract_update(zip_path, "AssetCatalogue.exe")


def test_extract_update_rejects_a_corrupt_zip(tmp_path: Path) -> None:
    bad_zip = tmp_path / "corrupt.zip"
    bad_zip.write_bytes(b"not actually a zip file")
    with pytest.raises(self_update.SelfUpdateError, match="not a valid zip"):
        self_update.extract_update(bad_zip, "AssetCatalogue.exe")


def _fake_download_response(chunks: list[bytes], content_length: int | None):
    mock = MagicMock()
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    headers = {"Content-Length": str(content_length)} if content_length is not None else {}
    mock.headers = headers
    remaining = list(chunks) + [b""]
    mock.read.side_effect = remaining
    return mock


def test_download_update_reports_progress_and_writes_the_file() -> None:
    body = [b"a" * 100, b"b" * 50]
    total = sum(len(c) for c in body)
    progress_calls = []

    with patch("urllib.request.urlopen", return_value=_fake_download_response(body, total)):
        path = self_update.download_update(
            "https://example.com/update.zip",
            on_progress=lambda done, tot: progress_calls.append((done, tot)),
        )

    try:
        assert path.read_bytes() == b"a" * 100 + b"b" * 50
        assert progress_calls == [(100, total), (150, total)]
    finally:
        path.unlink(missing_ok=True)


def test_download_update_cleans_up_partial_file_on_failure() -> None:
    with patch("urllib.request.urlopen", side_effect=OSError("connection reset")):
        with pytest.raises(self_update.SelfUpdateError, match="download"):
            self_update.download_update("https://example.com/update.zip")
