"""The files a model needs but that are not themselves catalogued.

Ingest is a whitelist of content extensions (see ingest.ASSET_TYPE_BY_
EXTENSION), which is right for a *catalogue* -- nobody wants a `.bin` in
the grid as though it were an asset. But the library's archived copy is
built from catalogued rows only, so those uncatalogued files never got
copied, and a `.gltf` in the library sat there with no buffer beside it
and a `.obj` with no materials. Loadable only by accident, and only for
as long as the original folder survived.

That is what this closes. A model is asked what it references, and those
files ride along into the library with it -- so the archived copy is
genuinely self-sufficient and the unpacked source can be deleted.

Only text formats are parsed. A `.fbx` or `.blend` is a binary whose only
real reader is Blender, far too expensive to consult per asset; both
commonly embed their textures anyway, and any loose texture beside them
is a whitelisted extension that gets catalogued and archived on its own.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from asset_catalogue import gltf_metadata

# Every `map_*` statement (map_Kd, map_Bump, ...) plus `bump`/`disp`/
# `decal`. The filename is the last whitespace-separated token, since
# options like "-bm 0.5" sit between the keyword and the path.
_MTL_TEXTURE = re.compile(r"^\s*(?:map_\w+|bump|disp|decal)\s+(.*\S)\s*$", re.IGNORECASE)
_OBJ_MTLLIB = re.compile(r"^\s*mtllib\s+(.*\S)\s*$", re.IGNORECASE)

# A .mtl/.obj reference that is really a Windows absolute path from
# whoever authored the pack. Meaningless here, and following one would
# read outside the pack entirely.
_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|[A-Za-z][A-Za-z0-9+.-]*://)")

MAX_TEXT_BYTES = 8 * 1024 * 1024


def _resolve(base: Path, reference: str, pack_root: Path) -> Path | None:
    """A referenced path, or None if it is unusable or escapes the pack.

    Containment is the important half. A reference is arbitrary text from
    a downloaded file, and "../../../../Windows/System32/x" is a valid
    thing for it to say -- following it would copy files from outside the
    pack into the library.
    """
    reference = unquote(reference.strip().strip('"'))
    if not reference or reference.startswith("data:") or _ABSOLUTE.match(reference):
        return None
    candidate = (base / reference.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(pack_root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _gltf_references(path: Path, pack_root: Path) -> set[Path]:
    document = gltf_metadata._read_gltf_json(path)
    if not document:
        return set()
    found: set[Path] = set()
    # Buffers are the load-bearing ones -- a .gltf without its .bin has no
    # geometry at all. Images matter too, but a loose image is usually a
    # whitelisted extension and archived in its own right anyway.
    for section in ("buffers", "images"):
        for entry in document.get(section) or []:
            uri = entry.get("uri") if isinstance(entry, dict) else None
            if not isinstance(uri, str):
                continue
            resolved = _resolve(path.parent, uri, pack_root)
            if resolved is not None:
                found.add(resolved)
    return found


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _strip_mtl_options(value: str) -> str:
    """The filename out of a map_* statement, past any leading options.

    "map_Bump -bm 0.5 textures/n.png" names textures/n.png, not
    "-bm 0.5 textures/n.png". Options always start with "-" and take
    numeric arguments, so dropping a flag plus the numbers after it
    leaves the path -- and leaves it whole, which matters because a
    filename may legitimately contain spaces.
    """
    tokens = value.split()
    index = 0
    while index < len(tokens) and tokens[index].startswith("-"):
        index += 1
        while index < len(tokens):
            try:
                float(tokens[index])
            except ValueError:
                break
            index += 1
    return " ".join(tokens[index:])


def _mtl_references(path: Path, pack_root: Path) -> set[Path]:
    text = _read_text(path)
    if text is None:
        return set()
    found: set[Path] = set()
    for line in text.splitlines():
        match = _MTL_TEXTURE.match(line)
        if match:
            resolved = _resolve(path.parent, _strip_mtl_options(match.group(1)), pack_root)
            if resolved is not None:
                found.add(resolved)
    return found


def _obj_references(path: Path, pack_root: Path) -> set[Path]:
    text = _read_text(path)
    if text is None:
        return set()
    found: set[Path] = set()
    for line in text.splitlines():
        match = _OBJ_MTLLIB.match(line)
        if not match:
            continue
        # One mtllib statement can name several .mtl files.
        for name in match.group(1).split():
            resolved = _resolve(path.parent, name, pack_root)
            if resolved is None:
                continue
            found.add(resolved)
            # And each .mtl names textures of its own.
            found |= _mtl_references(resolved, pack_root)
    return found


def referenced_files(model_path: Path, pack_root: Path) -> set[Path]:
    """Existing files inside pack_root that this model needs to load.

    Never raises and never returns anything outside pack_root. An
    unreadable or unrecognised file yields an empty set, which leaves
    behaviour exactly as it was before this existed.
    """
    try:
        suffix = model_path.suffix.lower()
        if suffix in (".gltf", ".glb"):
            return _gltf_references(model_path, pack_root)
        if suffix == ".obj":
            return _obj_references(model_path, pack_root)
        if suffix == ".mtl":
            return _mtl_references(model_path, pack_root)
    except Exception:  # noqa: BLE001 -- a malformed pack file must not stop an ingest
        return set()
    return set()
