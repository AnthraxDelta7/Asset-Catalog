"""A hand-written corrections file that travels with a pack.

Corrections (texture overrides, up-axis, scale...) normally accumulate in
the database as someone works through a pack's problems in the UI. That
works, but it's invisible and it's per-install: the knowledge that
POLY_ForestVillage's materials all want `Polygon_Texture_vol3.png` lives
only in one machine's SQLite file, and re-ingesting elsewhere rediscovers
nothing.

Dropping an `asset-catalogue.json` in the pack's own folder makes that
knowledge part of the pack. It's read at ingest, before any thumbnail is
rendered, so a pack with a known fix comes in correct the first time
rather than coming in broken and being repaired afterwards.

Vendor packaging errors are the case this exists for. A Unity-converted
Synty pack ships `.gltf` stubs whose baked-in texture URI names a file
from a *different product* -- `Polygon_Camping_Texture_vol2.0.jpg` in a
ForestVillage pack, which ships `Polygon_Texture_vol3.png` and nothing
else. Nothing can infer that mapping safely, but a person can state it
once, and every material in the pack shares two or three material names,
so stating it takes two or three lines.

Deliberately not an auto-remap: guessing which of several atlases a
material wanted is exactly the kind of plausible-but-wrong answer that
is worse than an honest missing texture, because it looks fine until
someone notices the colours are off.
"""

from __future__ import annotations

import json
from pathlib import Path

OVERRIDE_FILENAME = "asset-catalogue.json"

# The corrections vocabulary blender_common.apply_corrections actually
# consumes. Anything outside this is a typo rather than a feature, and
# silently ignoring it in a hand-edited file means someone stares at a
# correction that never applies -- so unknown keys are reported.
_MAPPING_KEYS = {"texture_overrides", "texture_extras"}
_SCALAR_KEYS = {
    "up_axis",
    "scale",
    "material_fallback",
    "disable_smart_texture_matching",
    "broken_texture_fallback",
    "prefer_source_models",
}
_LIST_KEYS = {"acknowledged_materials"}
SUPPORTED_KEYS = _MAPPING_KEYS | _SCALAR_KEYS | _LIST_KEYS


def path_for(pack_root: Path) -> Path:
    """Where the file lives for a pack. A single-file pack is rooted at
    the file itself, so the override sits beside it in the parent.
    """
    base = pack_root.parent if pack_root.is_file() else pack_root
    return base / OVERRIDE_FILENAME


def _validate_relative(value: str, pack_root: Path, label: str, problems: list[str]) -> bool:
    """Texture paths are resolved as pack_root / value at render time, so
    an absolute path or one climbing out with `..` would reach arbitrary
    files on disk. A pack folder is somewhere a downloaded archive was
    unpacked, which makes this file attacker-influenced in exactly the
    way that matters.
    """
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        problems.append(f"{label}: '{value}' must be a relative path inside the pack")
        return False
    if not (pack_root / candidate).is_file():
        problems.append(f"{label}: '{value}' does not exist in the pack")
        return False
    return True


def read(pack_root: Path) -> tuple[dict, list[str]]:
    """Returns (corrections, problems) for a pack's override file.

    An absent file is not a problem -- it's the normal case, and returns
    ({}, []). A malformed one is, and reports rather than raising: a
    typo in a hand-edited file shouldn't abort an ingest that would
    otherwise succeed, it should ingest and say what was ignored.
    """
    source = path_for(pack_root)
    if not source.is_file():
        return {}, []
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, [f"{OVERRIDE_FILENAME} could not be read: {exc}"]
    except ValueError as exc:
        return {}, [f"{OVERRIDE_FILENAME} is not valid JSON: {exc}"]
    if not isinstance(raw, dict):
        return {}, [f"{OVERRIDE_FILENAME} must contain a JSON object"]

    base = pack_root.parent if pack_root.is_file() else pack_root
    corrections: dict = {}
    problems: list[str] = []
    for key, value in raw.items():
        # JSON has no comments, and this file is where someone records
        # *why* a pack needs correcting -- which is the part worth
        # keeping, since the mapping itself looks arbitrary a year later.
        if key.startswith("_"):
            continue
        if key not in SUPPORTED_KEYS:
            problems.append(f"unknown key '{key}' ignored")
            continue
        if key in _MAPPING_KEYS:
            if not isinstance(value, dict):
                problems.append(f"'{key}' must be an object of material name -> path")
                continue
            kept = {
                name: rel
                for name, rel in value.items()
                if isinstance(rel, str)
                and _validate_relative(rel, base, f"{key}['{name}']", problems)
            }
            if kept:
                corrections[key] = kept
        elif key in _LIST_KEYS:
            if not isinstance(value, list):
                problems.append(f"'{key}' must be a list")
                continue
            corrections[key] = [str(item) for item in value]
        else:
            corrections[key] = value
    return corrections, problems


def merge(existing: dict, from_file: dict) -> dict:
    """The file wins for what it names; everything else is preserved.

    The file is the deliberate, auditable statement of how a pack should
    be corrected, so a re-ingest re-asserting it is the point -- that's
    what makes it reproducible. But it's usually partial, and clobbering
    a whole pack's accumulated UI fixes because the file mentions one
    material would make it a trap. So the merge is per-key, and per-entry
    within the two mappings.
    """
    merged = dict(existing)
    for key, value in from_file.items():
        if key in _MAPPING_KEYS and isinstance(merged.get(key), dict):
            combined = dict(merged[key])
            combined.update(value)
            merged[key] = combined
        else:
            merged[key] = value
    return merged
