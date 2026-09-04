from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "settings.json"


@dataclass
class Settings:
    staging_folder: str | None = None
    library_folder: str | None = None
    blender_path: str | None = None
    # Godot export is opt-in and fully separable from the rest of the app --
    # someone cataloguing assets for a non-Godot pipeline (raw STLs, a
    # different engine) should never be nudged toward Godot-specific UI.
    # When enabled, godot_project_path is set on first import and reused
    # for every later one, until cleared or redefined here.
    godot_export_enabled: bool = False
    godot_project_path: str | None = None

    def db_path(self) -> Path:
        return Path(self.library_folder) / "catalogue.db"

    def thumbnail_dir(self) -> Path:
        return Path(self.library_folder) / "thumbnails"

    def assets_dir(self) -> Path:
        return Path(self.library_folder) / "assets"


def load() -> Settings:
    if not SETTINGS_PATH.exists():
        return Settings()
    data = json.loads(SETTINGS_PATH.read_text())
    return Settings(**data)


def save(settings: Settings) -> None:
    SETTINGS_PATH.write_text(json.dumps(asdict(settings), indent=2))
