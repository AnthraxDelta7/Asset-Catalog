"""Shared helpers for the Blender-side scripts (blender_thumbnail_script.py,
blender_convert_script.py). Runs inside Blender's own Python interpreter --
bpy only exists there. Blender adds a --python script's own directory to
sys.path automatically, which is what lets both scripts `import
blender_common` despite this not being part of the normal package.
"""

import math
import os
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import texture_matching

FALLBACK_MATERIAL_NAME = "AssetCatalogueFallback"


def _long_path(path: str) -> str:
    r"""Windows' classic file APIs cap a path at 260 characters (MAX_PATH).
    Python's own file access is long-path-aware and doesn't hit this, but
    Blender's importers (STL confirmed; likely the others too, just not yet
    hit by a deep enough pack) call straight into the OS APIs that do --
    "Cannot open file" for a perfectly real, readable file once its full
    staging path creeps past 260 chars (easy with a nested zip extraction
    plus a long pack/creator name). Prefixing with \\?\ (\\?\UNC\ for a
    network path) opts into Windows' extended-length path handling, which
    lifts the limit to ~32,767 chars -- the standard fix, not Blender- or
    asset-type-specific, so it's applied once here for every importer
    rather than per file type.
    """
    normalized = os.path.abspath(path)
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    return "\\\\?\\" + normalized


IMPORTERS = {
    ".obj": lambda path: bpy.ops.wm.obj_import(filepath=_long_path(path)),
    ".fbx": lambda path: bpy.ops.import_scene.fbx(filepath=_long_path(path)),
    ".gltf": lambda path: bpy.ops.import_scene.gltf(filepath=_long_path(path)),
    ".glb": lambda path: bpy.ops.import_scene.gltf(filepath=_long_path(path)),
    ".stl": lambda path: bpy.ops.wm.stl_import(filepath=_long_path(path)),
    ".blend": lambda path: _import_blend(_long_path(path)),
}


def _import_blend(path: str) -> None:
    with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
        data_to.objects = data_from.objects
    for obj in data_to.objects:
        if obj is not None:
            bpy.context.collection.objects.link(obj)


def get_job_list_path() -> str:
    argv = sys.argv
    return argv[argv.index("--") + 1]


def clear_imported_objects(keep: tuple[str, ...] = ()) -> None:
    for obj in list(bpy.data.objects):
        if obj.name not in keep:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


def find_broken_texture_images() -> list:
    """Every loaded image whose pixel data never actually resolved (size
    (0, 0)) -- Blender's own reliable signal that an image failed to load,
    regardless of why. In practice the common real-world cause isn't a
    typo'd relative path but an absolute one baked into the file at export
    time from the original author's own machine (e.g. "L:\\UNITY\\...",
    confirmed against a real downloaded pack) -- meaningless on any other
    machine, and there's nothing this app can do to recover the actual
    image data if the pack simply didn't include it. Checked directly
    against the loaded image's actual pixel size rather than guessing from
    the path string, since a relative path can be just as broken as an
    absolute one, and an absolute path pointing at a real, correct
    location isn't a problem at all.
    """
    return [image for image in bpy.data.images if tuple(image.size) == (0, 0)]


def _mesh_uses_any_image(obj, images: set) -> bool:
    for material in obj.data.materials:
        if material is None or not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image in images:
                return True
    return False


def _get_or_create_fallback_material():
    material = bpy.data.materials.get(FALLBACK_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(FALLBACK_MATERIAL_NAME)
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
    return material


def _mesh_materials(mesh_objects) -> dict:
    """name -> material, one entry per distinct material actually used
    across mesh_objects (a material can be shared by several objects;
    each is only ever processed once).
    """
    materials = {}
    for obj in mesh_objects:
        for material in obj.data.materials:
            if material is not None and material.name not in materials:
                materials[material.name] = material
    return materials


def resolve_material_metadata(mesh_objects) -> dict:
    """material_name -> {"has_texture": bool, "base_color_factor": [r, g, b]}
    for every material actually used across mesh_objects, read straight off
    each material's own Principled BSDF -- the exact same node graph the
    static thumbnail renders from, corrections (relink/injection/overrides)
    already applied by the time this runs.

    Exists so the interactive 3D preview (model_preview_dialog.py, which has
    no bpy access and instead re-parses the exported .glb through trimesh)
    doesn't have to *guess* at the same two facts a second time. It used to:
    whether a part's to_color() output needs a linear->sRGB fix-up (only
    correct for a flat baseColorFactor, not a texture-sampled color -- see
    that module's docstring) was inferred from whether the reloaded glb's
    material happened to carry a baseColorTexture, which is right for the
    common case but silently drops a real, non-white baseColorFactor
    layered on top of a texture (glTF composites factor * texture; trimesh's
    to_color() alone doesn't). Cached alongside the preview .glb (see
    model_preview.colors_path) instead of recomputed by guesswork.

    base_color_factor is Blender's own default_value for the Base Color
    input regardless of whether something is linked to it -- when nothing
    is linked, this *is* the material's flat color (needs the sRGB fix-up
    at display time); when a texture is linked, it's still the correct
    factor to multiply that texture's sampled color by (usually white,
    i.e. leaves the texture unchanged, but not always).
    """
    metadata: dict = {}
    for name, material in _mesh_materials(mesh_objects).items():
        if not material.use_nodes:
            metadata[name] = {"has_texture": False, "base_color_factor": [0.8, 0.8, 0.8]}
            continue
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if bsdf is None:
            metadata[name] = {"has_texture": False, "base_color_factor": [0.8, 0.8, 0.8]}
            continue
        base_color_input = bsdf.inputs["Base Color"]
        metadata[name] = {
            "has_texture": base_color_input.is_linked
            and base_color_input.links[0].from_node.type == "TEX_IMAGE",
            "base_color_factor": list(base_color_input.default_value[:3]),
        }
    return metadata


def _assign_base_color_texture(material, texture_path: Path) -> bool:
    """Wires texture_path into material's Base Color, used by a manual
    texture override (_apply_texture_overrides). If Base Color already
    had an image node linked (typically the original, broken one an
    override exists specifically to replace), that node is removed
    entirely rather than just unlinked -- confirmed as a real bug
    otherwise: an orphaned broken-image node left sitting in the tree,
    disconnected but still present, kept being counted as "this material
    still references a broken image" by has_broken_texture/
    _broken_materials (both scan every TEX_IMAGE node in the material,
    not just whatever Base Color currently points at), so a successfully
    applied override still showed up as unresolved.
    """
    if not material.use_nodes:
        material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        return False
    base_color_input = bsdf.inputs["Base Color"]
    if base_color_input.is_linked:
        previous_node = base_color_input.links[0].from_node
        if previous_node.type == "TEX_IMAGE":
            material.node_tree.nodes.remove(previous_node)
    image = bpy.data.images.load(str(texture_path))
    tex_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    material.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    return True


def _apply_texture_overrides(mesh_objects, pack_root: Path, overrides: dict) -> tuple[list[str], set[str]]:
    """Explicit material_name -> texture-path (relative to pack_root)
    overrides, applied before any automatic matching -- lets a user
    manually point at the right texture when the naming-convention
    matcher can't find one on its own. Returns (notes, handled_material_
    names); the caller skips both automatic passes for any name in the
    second set, whether or not applying it actually succeeded, so a
    materials with an override entry is never also second-guessed by the
    automatic matcher.
    """
    if not overrides:
        return [], set()
    materials = _mesh_materials(mesh_objects)
    notes = []
    handled = set()
    for material_name, relative_path in overrides.items():
        material = materials.get(material_name)
        if material is None:
            continue
        handled.add(material_name)
        if not relative_path:
            continue
        texture_path = pack_root / relative_path
        if not texture_path.is_file():
            continue
        if _assign_base_color_texture(material, texture_path):
            notes.append(f"'{material_name}' -> {texture_path.name} (manual override)")
    return notes, handled


def _apply_texture_extras(mesh_objects, pack_root: Path, extras: dict) -> list[str]:
    """Embeds one supplementary image file per material into the .glb
    export -- for a mask or extra map (see corrections' texture_extras)
    the user wants available later (e.g. to hand-wire the vendor's own
    recolor shader themselves) but that this app has no way to correctly
    auto-apply on its own. Never alters the material's actual rendered
    appearance.

    Blender's glTF exporter only bundles an image that's actually
    reachable from the material's node graph -- confirmed directly: a
    wired-but-unconnected Image Texture node is silently dropped from the
    export entirely, present in the .blend scene but absent from the
    resulting glTF images/textures arrays. So this wires it into
    Emission Color instead, with Emission Strength forced to 0 --
    verified against a real export that a 0-strength emissive texture
    still gets embedded, but produces an omitted (default, meaning
    black/zero-contribution) emissiveFactor, i.e. genuinely no visual
    effect in any spec-compliant viewer.

    Only when Emission isn't already doing something real, though: a
    material already using it for an actual glow effect is left alone
    rather than risking silently overwriting it, and reported as skipped
    rather than failing quietly. Checked via Emission *Color*, not
    Strength -- confirmed against a real pack that Blender's Principled
    BSDF defaults Emission Strength to 1.0, not 0.0, so nearly every
    material that has never touched Emission at all would otherwise look
    "already in use" and the guard would misfire almost everywhere.
    Emission Color defaults to pure black, so strength(1.0) * color(0,0,0)
    is still genuinely zero contribution regardless of the strength
    value -- black-and-unlinked is the actual "never touched" signal,
    not the strength. This is also why only one supplementary file per
    material is supported -- there's no second "free" slot in the core
    glTF material model that's this safe to commandeer without spec-
    extension complexity.
    """
    if not extras:
        return []
    materials = _mesh_materials(mesh_objects)
    notes = []
    for material_name, relative_path in extras.items():
        material = materials.get(material_name)
        if material is None or not relative_path:
            continue
        if not material.use_nodes:
            material.use_nodes = True
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if bsdf is None:
            continue
        emission_strength = bsdf.inputs["Emission Strength"]
        emission_color = bsdf.inputs["Emission Color"]
        already_emissive = emission_color.is_linked or any(c > 0 for c in emission_color.default_value[:3])
        if already_emissive:
            notes.append(f"'{material_name}': supplementary file skipped (Emission already in use)")
            continue
        texture_path = pack_root / relative_path
        if not texture_path.is_file():
            continue
        image = bpy.data.images.load(str(texture_path))
        tex_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        tex_node.label = "AssetCatalogueSupplementaryFile"
        material.node_tree.links.new(tex_node.outputs["Color"], emission_color)
        emission_strength.default_value = 0.0
        notes.append(f"'{material_name}': embedded supplementary file {texture_path.name}")
    return notes


def _relink_broken_images(broken_images, pack_root: Path) -> list[str]:
    """Attempts to fix each broken image by finding a file with the exact
    same basename anywhere else in the pack -- the common real-world
    cause of a broken reference is an absolute path baked in from the
    original author's own machine at export time, and the actual file is
    often still present in the pack, just not at that exact path
    (confirmed against a real downloaded pack). Returns a human-readable
    note per image successfully relinked.
    """
    notes = []
    for image in broken_images:
        basename = Path(image.filepath).name
        found = texture_matching.find_file_by_basename(basename, pack_root)
        if found is None:
            continue
        image.filepath = str(found)
        image.reload()
        if tuple(image.size) != (0, 0):
            notes.append(f"relinked {basename}")
    return notes


def _broken_materials(mesh_objects, broken_images: set, acknowledged: set[str]) -> list[str]:
    """Names (deduped, order-preserving) of every material whose Base
    Color still points at a broken image after relink has already run --
    the actual thing worth surfacing to a human (see the review-dialog
    workflow this feeds), since find_broken_texture_images() alone only
    says *something* in the scene is broken, not which materials.

    Deliberately checks only the Base Color link, not every TEX_IMAGE
    node in the material -- a manual override (see _assign_base_color_
    texture) only ever fixes Base Color, so flagging a material over a
    broken normal/AO/etc. map would offer a "Browse..." fix that can't
    actually address what's broken. This also sidesteps a real bug that
    scanning every node hit: an orphaned, no-longer-linked image node
    left over from a previous fix (now cleaned up by
    _assign_base_color_texture, but a materials-with-more-complex-history
    could still carry one) would otherwise keep counting as broken even
    once Base Color itself points somewhere valid.

    acknowledged filters out a material the user has already explicitly
    said "no texture, that's fine" for (see corrections'
    acknowledged_materials) -- otherwise every future render of every
    asset sharing that material would keep re-flagging a decision that's
    already been made.
    """
    names: list[str] = []
    for name, material in _mesh_materials(mesh_objects).items():
        if name in acknowledged or not material.use_nodes:
            continue
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if bsdf is None:
            continue
        base_color_input = bsdf.inputs["Base Color"]
        if not base_color_input.is_linked:
            continue
        from_node = base_color_input.links[0].from_node
        if from_node.type == "TEX_IMAGE" and from_node.image in broken_images:
            names.append(name)
    return names


def apply_corrections(
    corrections: dict, pack_root: Path | None = None
) -> tuple[list, bool, list[str], list[str]]:
    """Applies pack-level corrections to whatever was just imported (every
    object that isn't Camera/Light -- those only exist in the thumbnail
    script's scene, harmless to exclude by name here regardless). Returns
    (mesh_objects, has_broken_texture, smart_texture_notes, broken_materials):
    - mesh_objects, for the caller's next step (framing for a render, or
      nothing special for an export).
    - has_broken_texture: whether ANY mesh still references an image that
      failed to load, checked unconditionally (not gated behind
      broken_texture_fallback) since a caller needs to know this
      regardless of whether that fallback correction is turned on, to
      report it back to the user.
    - smart_texture_notes: one human-readable line per texture manually
      overridden, embedded as a supplementary file, or auto-relinked --
      see _apply_texture_overrides / _apply_texture_extras /
      _relink_broken_images. Relink is always attempted when pack_root is
      given, unless disable_smart_texture_matching is set (an explicit,
      easy rollback switch) -- manual overrides still apply even then,
      since those are the user's own explicit instruction, not a guess.
      This used to also include a name-based guess at a texture for a
      material that never had one at all ("RecessA" -> a same-named file
      found elsewhere in the pack) -- removed after repeatedly guessing
      wrong on real packs (a shared material name across unrelated
      meshes, or a same-suffixed file that turned out to be something
      other than a usable color texture). Nothing here guesses anymore;
      see broken_materials below for how an actually-broken reference
      gets surfaced to a human instead.
    - broken_materials: names of materials still referencing a broken
      image even after relink -- what the review-dialog "missing
      texture" workflow (main_window.py) actually acts on, letting a
      human browse to the right file or mark it as intentionally
      textureless (see corrections' acknowledged_materials, which
      filters a name back out of this list once they have).
    """
    imported = [obj for obj in bpy.data.objects if obj.name not in ("Camera", "Light")]

    if corrections.get("up_axis") == "Y_UP":
        for obj in imported:
            if obj.parent is None:
                obj.rotation_euler.x += math.radians(90)

    scale = corrections.get("scale")
    if scale and scale != 1.0:
        for obj in imported:
            if obj.parent is None:
                obj.scale = tuple(s * scale for s in obj.scale)

    if corrections.get("material_fallback"):
        material = _get_or_create_fallback_material()
        for obj in imported:
            if obj.type == "MESH":
                obj.data.materials.clear()
                obj.data.materials.append(material)

    mesh_objects = [obj for obj in imported if obj.type == "MESH"]
    smart_texture_notes: list[str] = []

    if pack_root is not None:
        override_notes, _handled_materials = _apply_texture_overrides(
            mesh_objects, pack_root, corrections.get("texture_overrides") or {}
        )
        smart_texture_notes.extend(override_notes)
        smart_texture_notes.extend(
            _apply_texture_extras(mesh_objects, pack_root, corrections.get("texture_extras") or {})
        )

        if not corrections.get("disable_smart_texture_matching"):
            smart_texture_notes.extend(_relink_broken_images(set(find_broken_texture_images()), pack_root))

    broken_images = set(find_broken_texture_images())  # recomputed -- a relink above may have fixed some
    has_broken_texture = any(_mesh_uses_any_image(obj, broken_images) for obj in mesh_objects)
    acknowledged = set(corrections.get("acknowledged_materials") or [])
    broken_materials = _broken_materials(mesh_objects, broken_images, acknowledged)

    if has_broken_texture and corrections.get("broken_texture_fallback"):
        # Surgical, not blanket like material_fallback above: only the
        # mesh(es) actually using a broken image get replaced -- a pack
        # can easily have some assets with working, deliberately-authored
        # materials right alongside others with a genuinely broken texture
        # link (confirmed against a real pack: guns with a dead absolute
        # path next to walls with real flat colors), and blanket-replacing
        # everything would wrongly gray out the ones that already work.
        material = _get_or_create_fallback_material()
        for obj in mesh_objects:
            if _mesh_uses_any_image(obj, broken_images):
                obj.data.materials.clear()
                obj.data.materials.append(material)

    bpy.context.view_layer.update()
    return mesh_objects, has_broken_texture, smart_texture_notes, broken_materials
