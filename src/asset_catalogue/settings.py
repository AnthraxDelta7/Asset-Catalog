from __future__ import annotations

import json
import os
import sys
from dataclasses import MISSING, asdict, dataclass, field
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
    godot_path: str | None = None
    # Most-recently-used project folders for "Export to Project", newest
    # first, capped at a handful of entries (see main_window.py's
    # _remember_export_project). Powers the DetailPanel's Export button --
    # a one-click re-export to the last project, plus a dropdown of others
    # -- without needing a separate opt-in toggle the way the retired
    # Godot-specific remembering used to.
    recent_export_projects: list[str] = field(default_factory=list)
    # The last export mode the user *deliberately* chose ("standard" or
    # "godot" -- see main_window.py's _effective_export_mode), remembered
    # as the DetailPanel export button's preferred default so repeat
    # exports don't need re-picking it every time. Never overwritten by an
    # automatic fallback: selecting a non-model asset temporarily forces
    # "standard" for that one export (Godot's MeshInstance3D wrapper makes
    # no sense for it), but the next model-asset export goes right back to
    # whatever was last chosen on purpose, not the fallback.
    last_export_mode: str = "standard"
    # command id -> key sequence, layered over the defaults declared in
    # ui/commands.py. Only ever holds what the user actually changed, so
    # a new command's default takes effect without touching this.
    shortcuts: dict = field(default_factory=dict)
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

    def preview_dir(self) -> Path:
        return Path(self.library_folder) / "previews"


#: Set when the last load() had to fall back to defaults, so the UI can
#: say so. A silent reset looks exactly like the app forgetting every
#: setting for no reason, which is the more alarming of the two.
last_load_error: str | None = None


def _is_expected_shape(key: str, value) -> bool:
    """Whether a loaded value is usable for this field.

    Derived from the field's own default rather than a second list of
    types kept alongside the dataclass, which would drift the first time
    a field was added. A field defaulting to None is an optional string --
    true of every one here, and the reason that assumption is written
    down rather than inferred at the call site.
    """
    field_def = Settings.__dataclass_fields__[key]
    if field_def.default_factory is not MISSING:
        return isinstance(value, type(field_def.default_factory()))
    if field_def.default is None:
        return value is None or isinstance(value, str)
    return isinstance(value, type(field_def.default))


def load() -> Settings:
    """Never raises. A settings file that can't be read yields defaults.

    Everything in the app calls this, including startup, so an exception
    here is not a bad setting -- it's an app that won't launch, with a
    raw traceback and no way back short of finding and deleting the file
    by hand. Losing preferences is recoverable; losing the app is not.
    """
    global last_load_error
    last_load_error = None
    if not SETTINGS_PATH.exists():
        return Settings()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        last_load_error = f"{SETTINGS_PATH} could not be read ({exc}); using defaults."
        return Settings()
    if not isinstance(data, dict):
        last_load_error = f"{SETTINGS_PATH} is not a JSON object; using defaults."
        return Settings()

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

    # A dataclass does not check types, so a file with the right keys and
    # wrong values (hand-edited, or a list where a string belongs) is
    # accepted here and fails much later -- Path(library_folder) on a
    # dict, or .insert() on an int -- somewhere with no clue left about
    # the cause. Each bad value is dropped back to its default instead.
    rejected = [key for key, value in data.items() if not _is_expected_shape(key, value)]
    for key in rejected:
        del data[key]
    if rejected:
        last_load_error = (
            f"{SETTINGS_PATH}: ignoring unusable value(s) for {', '.join(sorted(rejected))}."
        )

    try:
        settings = Settings(**data)
    except TypeError as exc:
        # A field present but of the wrong shape -- a hand-edited file, or
        # one written by a much newer build.
        last_load_error = f"{SETTINGS_PATH} has unusable values ({exc}); using defaults."
        return Settings()
    if legacy_project and legacy_project not in settings.recent_export_projects:
        settings.recent_export_projects.insert(0, legacy_project)
    return settings


def save(settings: Settings) -> None:
    """Writes via a temporary file and one atomic replace.

    A plain write truncates the real file first, so a crash or a power
    loss during it leaves a half-written settings.json -- and this file is
    read on every launch. Paired with a load() that used to raise, that
    turned an unlucky moment into an app that would not start again.
    os.replace is atomic on both Windows and POSIX, so a reader sees
    either the old file or the new one, never a partial one.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
    temp_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(temp_path, SETTINGS_PATH)
