from __future__ import annotations

from pathlib import Path

from asset_catalogue import texture_matching


def test_find_file_by_basename_finds_a_relocated_file(tmp_path: Path) -> None:
    real_dir = tmp_path / "Textures" / "Nested"
    real_dir.mkdir(parents=True)
    real_file = real_dir / "AtlasColor.png"
    real_file.write_bytes(b"fake png bytes")

    found = texture_matching.find_file_by_basename("AtlasColor.png", tmp_path)
    assert found == real_file


def test_find_file_by_basename_case_insensitive(tmp_path: Path) -> None:
    real_file = tmp_path / "atlascolor.PNG"
    real_file.write_bytes(b"fake png bytes")

    assert texture_matching.find_file_by_basename("AtlasColor.png", tmp_path) == real_file


def test_find_file_by_basename_none_when_not_present(tmp_path: Path) -> None:
    (tmp_path / "unrelated.png").write_bytes(b"x")
    assert texture_matching.find_file_by_basename("AtlasColor.png", tmp_path) is None
