"""Pure name-matching logic for linking a material to a texture file by
naming convention -- no bpy dependency, unlike blender_common.py, so this
can be (and is) unit-tested directly. blender_common.py imports this
module the same way it's imported here: as a plain top-level module
sitting directly in src/asset_catalogue/ (see paths.py's package_dir()
docstring for why it can't live in a subpackage), added to Blender's own
sys.path by the calling script.

Built after finding, on a real downloaded pack (a low-poly sci-fi kit),
that most of its material names corresponded exactly to a texture file
sitting elsewhere in the same pack -- the FBX exporter just never wired
the two together, and a handful of materials had a texture reference at
all but baked in as a dead absolute path from the original author's own
machine. Rather than leave either case rendering wrong (a flat default
color, or Blender's own bright-pink "missing image" placeholder) when the
real texture is sitting right there, this searches the rest of the pack
by name and wires it in automatically.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# Map-type suffix words this recognizes when stripping a texture filename
# down to its "key" (e.g. "SciFiTextures_RecessA_albedo.png" -> key
# "scifitextures_recessa", suffix "albedo"). Color-like suffixes are what
# a Base Color slot actually wants, in preference order when more than one
# exists for the same matched key; a normal/ao/roughness/etc.-only match
# is never used as a Base Color texture even if it's the only file that
# matches the material name -- assigning a normal map as a base color
# would look actively wrong, not just imperfect.
#
# "bakedalbedo" is deliberately NOT in this usable set, even though it's
# unmistakably a color map: it has AO/shadow baked in for one specific
# mesh's own UV unwrap (confirmed on the real Sci-Fi pack: each bake has a
# matching "<Name>_low.fbx" reference mesh it was baked from), but the
# *material name* it's keyed to is shared by many structurally different
# meshes across a modular kit -- "TiledPanelsA" alone is the material on 9
# unrelated pieces (a wedge, a cuboid, bevelled blocks, etc.) besides its
# own low-poly reference mesh. find_texture_match is a blind guess by
# material name across the whole pack, so injecting that bake onto a
# mesh it wasn't baked for pastes someone else's baked shadow/seam lines
# onto geometry they don't correspond to -- confirmed by rendering it: a
# baked seam with no relation to any real edge on the receiving mesh. A
# broken-link *relink* (see find_file_by_basename) doesn't have this
# problem and still uses a bakedalbedo file freely -- relinking restores
# the exact file that mesh's own material already named, so there's no
# cross-mesh guessing involved.
#
# It gets its own set, separate from _NON_COLOR_MAP_SUFFIXES, because its
# *presence* changes how a same-key "albedo" candidate is treated too (see
# find_texture_match): on the real pack, every one of the 9 material keys
# that has a "_BakedAlbedo" file also has a same-named plain "_albedo"
# file, and every one of those plain files checked turned out to be a
# leftover red/green UV-checker debug image, not finished art -- the
# checker and the bake share the exact same UV silhouette, meaning the
# checker is a byproduct of that same one-mesh bake, not independent
# tileable art. So a bakedalbedo sibling isn't just "don't use this file"
# -- it's a signal that this whole material key's "generic" texture is
# unreliable too, and nothing for this name should be guessed at all.
_MESH_SPECIFIC_BAKE_SUFFIXES = frozenset({"bakedalbedo"})
_COLOR_MAP_SUFFIXES = ("albedo", "basecolor", "diffuse", "color", "colour")
_NON_COLOR_MAP_SUFFIXES = frozenset(
    {
        "ao",
        "occlusion",
        "normal",
        "roughness",
        "metallic",
        "metalness",
        "height",
        "displacement",
        "spec",
        "specular",
        "gloss",
        "glossiness",
        "matid",
        "curve",
        "mask",
        "emissive",
        "emission",
        "opacity",
        "alpha",
    }
)
_ALL_MAP_SUFFIXES = frozenset(_COLOR_MAP_SUFFIXES) | _NON_COLOR_MAP_SUFFIXES | _MESH_SPECIFIC_BAKE_SUFFIXES

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff"})


def tokenize(name: str) -> list[str]:
    """Splits on any run of non-alphanumeric characters, lowercased --
    "SciFiTextures_RecessA_albedo" -> ["scifitextures", "recessa",
    "albedo"]. Matching is always done on whole tokens, never a raw
    substring search, so a material named "Recess" can't collide with a
    texture keyed "RecessA" just for containing "recess".
    """
    return [token.lower() for token in re.split(r"[^A-Za-z0-9]+", name) if token]


def _classify_suffix(tokens: list[str]) -> tuple[list[str], str | None]:
    """Returns (remaining_tokens, suffix). suffix is the recognized
    trailing map-type token if the filename ends with one, else None -- a
    filename with no recognized suffix at all (e.g. just "RecessA.png")
    is treated as a color map by default, the common case for a pack that
    ships one plain texture per material with no PBR-map naming scheme.
    """
    if tokens and tokens[-1] in _ALL_MAP_SUFFIXES:
        return tokens[:-1], tokens[-1]
    return tokens, None


def _contains_subsequence(haystack: list[str], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    return any(tuple(haystack[i : i + n]) == needle for i in range(len(haystack) - n + 1))


def find_texture_match(material_name: str, texture_paths: Iterable[Path]) -> Path | None:
    """The best texture file whose name corresponds to material_name by
    naming convention, or None if nothing confidently matches.

    A texture "matches" when material_name's own tokens appear as a
    contiguous, exact (case-insensitive) subsequence among the filename's
    tokens once a trailing map-type word is stripped off. Among matches,
    the most color-like map type wins; a match that's only available as a
    normal/ao/roughness/etc. map is skipped entirely rather than used.

    A match against a "bakedalbedo" file returns None outright, for the
    whole name -- not just skipping that one file -- since its presence
    means any same-key "albedo" sibling is unreliable too (see the
    _MESH_SPECIFIC_BAKE_SUFFIXES comment); guessing between two untrustworthy
    files is worse than leaving the material unmatched.
    """
    material_tokens = tuple(tokenize(material_name))
    if not material_tokens:
        return None

    best: tuple[int, Path] | None = None
    for path in texture_paths:
        tokens, suffix = _classify_suffix(tokenize(path.stem))
        if not _contains_subsequence(tokens, material_tokens):
            continue
        if suffix in _MESH_SPECIFIC_BAKE_SUFFIXES:
            return None
        if suffix in _NON_COLOR_MAP_SUFFIXES:
            continue
        priority = _COLOR_MAP_SUFFIXES.index(suffix) if suffix in _COLOR_MAP_SUFFIXES else len(_COLOR_MAP_SUFFIXES)
        if best is None or priority < best[0]:
            best = (priority, path)

    return best[1] if best is not None else None


def find_file_by_basename(basename: str, search_root: Path) -> Path | None:
    """The first file under search_root (recursive) whose name matches
    basename case-insensitively -- used to relink a broken absolute-path
    image reference to wherever the same file actually lives in the
    downloaded pack, when it's present but just not at the path baked
    into the source file.
    """
    lowered = basename.lower()
    for candidate in search_root.rglob("*"):
        if candidate.is_file() and candidate.name.lower() == lowered:
            return candidate
    return None


def find_image_files(search_root: Path) -> list[Path]:
    return [p for p in search_root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
