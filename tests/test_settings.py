from __future__ import annotations

import json
from pathlib import Path

from asset_catalogue import settings


def test_load_returns_defaults_when_no_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    loaded = settings.load()
    assert loaded.staging_folder is None
    assert loaded.recent_export_projects == []


def test_save_load_round_trip(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    s = settings.Settings(staging_folder="A", library_folder="B", recent_export_projects=["C"])
    settings.save(s)
    loaded = settings.load()
    assert loaded == s


def test_load_migrates_legacy_godot_keys(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "staging_folder": "A",
        "library_folder": "B",
        "blender_path": None,
        "godot_export_enabled": False,
        "godot_project_path": "D:/Godot/proj",
    }))
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    loaded = settings.load()
    assert loaded.recent_export_projects == ["D:/Godot/proj"]
    assert not hasattr(loaded, "godot_export_enabled")
    assert not hasattr(loaded, "godot_project_path")

    # Saving afterward must clean the legacy keys out of the file.
    settings.save(loaded)
    raw = json.loads(path.read_text())
    assert "godot_export_enabled" not in raw
    assert "godot_project_path" not in raw


def test_load_ignores_unknown_keys(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"staging_folder": "A", "some_future_field": "x"}))
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    loaded = settings.load()
    assert loaded.staging_folder == "A"


def test_db_path_thumbnail_dir_assets_dir_derive_from_library_folder() -> None:
    s = settings.Settings(library_folder="D:/Lib")
    assert s.db_path() == Path("D:/Lib") / "catalogue.db"
    assert s.thumbnail_dir() == Path("D:/Lib") / "thumbnails"
    assert s.assets_dir() == Path("D:/Lib") / "assets"
