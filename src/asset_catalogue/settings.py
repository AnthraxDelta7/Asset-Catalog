from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_settings_path() -> Path:
    """Running from source, settings.json sits at the repo root next to the
    code -- convenient for development, and REPO_ROOT (derived from
    __file__) is a real, correct path in that case. A frozen PyInstaller
    build has no repo root at all (__file__ resolves somewhere inside the
    bundle), so it uses the standard per-user app-data location instead --
    stable regardless of where the .exe happens to live, survives a rebuild
    or reinstall (unlike anywhere under the bundle's own folder, which
    PyInstaller deletes and recreates on every build), and doesn't need
    admin rights the way writing next to the .exe would in Program Files.
    """
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "AssetCatalogue" / "settings.json"
    return REPO_ROOT / "settings.json"


SETTINGS_PATH = _default_settings_path()


@dataclass
class Settings:
    staging_folder: str | None = None
    library_folder: str | None = None
    blender_path: str | None = None
    # Most-recently-used project folders for "Export to Project", newest
    # first, capped at a handful of entries (see main_window.py's
    # _remember_export_project). Powers the DetailPanel's Export button --
    # a one-click re-export to the last project, plus a dropdown of others
    # -- without needing a separate opt-in toggle the way the retired
    # Godot-specific remembering used to.
    recent_export_projects: list[str] = field(default_factory=list)
    # A release version the user explicitly dismissed via "Skip This
    # Version" in the update-available notice -- the automatic background
    # check won't nag about that exact version again, but a manual "Check
    # for Updates" always reports the real current state regardless.
    skipped_update_version: str | None = None

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

    # One-time migration from the retired Godot-specific export toggle: its
    # remembered project path (if any) becomes the first entry in the new
    # generic recent-projects list; the enabled flag is simply dropped,
    # since there's no longer a separate opt-in. Both keys are popped
    # before the dataclass unpack below (along with any other unrecognized
    # key) so an old settings.json never fails to load with an
    # unexpected-keyword-argument error.
    legacy_project = data.pop("godot_project_path", None)
    data.pop("godot_export_enabled", None)
    known_fields = set(Settings.__dataclass_fields__)
    data = {key: value for key, value in data.items() if key in known_fields}

    settings = Settings(**data)
    if legacy_project and legacy_project not in settings.recent_export_projects:
        settings.recent_export_projects.insert(0, legacy_project)
    return settings


def save(settings: Settings) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(asdict(settings), indent=2))
