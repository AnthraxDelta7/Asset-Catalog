from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "settings.json"
DEFAULT_DB_PATH = str(REPO_ROOT / "catalogue.db")
DEFAULT_THUMBNAIL_DIR = str(REPO_ROOT / "thumbnails")


@dataclass
class Settings:
    staging_folder: str | None = None
    db_path: str = DEFAULT_DB_PATH
    thumbnail_dir: str = DEFAULT_THUMBNAIL_DIR
    blender_path: str | None = None


def load() -> Settings:
    if not SETTINGS_PATH.exists():
        return Settings()
    data = json.loads(SETTINGS_PATH.read_text())
    return Settings(**data)


def save(settings: Settings) -> None:
    SETTINGS_PATH.write_text(json.dumps(asdict(settings), indent=2))
