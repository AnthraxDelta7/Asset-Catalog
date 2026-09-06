from __future__ import annotations

import os
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


def test_download_update_reports_zero_total_when_no_content_length() -> None:
    """Documented behavior: total_bytes is 0 if the server doesn't report
    Content-Length, and callers are told to treat that as "show bytes
    downloaded, not a percentage" -- untested until now, only the with-
    Content-Length case had coverage.
    """
    body = [b"a" * 100]
    progress_calls = []

    with patch("urllib.request.urlopen", return_value=_fake_download_response(body, content_length=None)):
        path = self_update.download_update(
            "https://example.com/update.zip",
            on_progress=lambda done, tot: progress_calls.append((done, tot)),
        )

    try:
        assert progress_calls == [(100, 0)]
    finally:
        path.unlink(missing_ok=True)


def test_apply_update_and_exit_launches_powershell_with_correct_args_then_exits(
    tmp_path: Path, monkeypatch
) -> None:
    """The "point of no return" -- a wiring bug here (wrong install_dir,
    wrong extract_root) would only surface at runtime on a real machine,
    since nothing about a wrong argument raises an error on its own.
    subprocess.Popen is mocked (never actually launches PowerShell); the
    real sys.exit(0) is left in place and caught via pytest.raises, since
    "never returns" is exactly the behavior worth pinning down, not
    something to mock away.
    """
    extracted_app_dir = tmp_path / "extracted" / "AssetCatalogue"
    extracted_app_dir.mkdir(parents=True)
    fake_install_dir = tmp_path / "install"
    monkeypatch.setattr(self_update, "install_dir", lambda: fake_install_dir)

    with patch("asset_catalogue.self_update.subprocess.Popen") as mock_popen:
        with pytest.raises(SystemExit) as exc_info:
            self_update.apply_update_and_exit(extracted_app_dir, "AssetCatalogue.exe")

    assert exc_info.value.code == 0
    mock_popen.assert_called_once()
    args = mock_popen.call_args.args[0]
    assert args[0] == "powershell.exe"
    # Named PowerShell params are passed as adjacent (flag, value) pairs --
    # zip the arg list with itself offset by one to read them back as a dict.
    named_args = dict(zip(args, args[1:]))
    assert named_args["-InstallDir"] == str(fake_install_dir)
    assert named_args["-NewVersionDir"] == str(extracted_app_dir)
    assert named_args["-ExeName"] == "AssetCatalogue.exe"
    assert named_args["-ExtractRoot"] == str(extracted_app_dir.parent)
    assert named_args["-ProcessId"] == str(os.getpid())
