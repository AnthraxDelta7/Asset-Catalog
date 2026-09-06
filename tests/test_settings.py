from __future__ import annotations

import json
import sys
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
    assert s.preview_dir() == Path("D:/Lib") / "previews"


def test_load_migrates_legacy_godot_key_without_duplicating_an_existing_entry(
    tmp_path: Path, monkeypatch
) -> None:
    """The legacy project only gets inserted if it isn't already the
    first entry in recent_export_projects -- untested branch: a settings
    file that (somehow) already has both the legacy key and that same
    path in the new list must not end up with it listed twice.
    """
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "staging_folder": "A",
                "library_folder": "B",
                "godot_project_path": "D:/Godot/proj",
                "recent_export_projects": ["D:/Godot/proj"],
            }
        )
    )
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    loaded = settings.load()

    assert loaded.recent_export_projects == ["D:/Godot/proj"]


def test_default_settings_path_uses_appdata_when_frozen(monkeypatch, tmp_path: Path) -> None:
    """A packaged PyInstaller build (sys.frozen = True) has no real repo
    root to write settings.json next to -- must fall back to the
    per-user AppData location instead.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))

    resolved = settings._default_settings_path()

    assert resolved == tmp_path / "AssetCatalogue" / "settings.json"


def test_default_settings_path_frozen_without_appdata_env_falls_back_to_home(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    resolved = settings._default_settings_path()

    assert resolved == tmp_path / "AppData" / "Roaming" / "AssetCatalogue" / "settings.json"


def test_default_settings_path_not_frozen_uses_repo_root() -> None:
    assert settings._default_settings_path() == settings.REPO_ROOT / "settings.json"
