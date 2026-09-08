"""Runs inside Blender's own Python interpreter via `blender --background --python`.

Renders one named animation clip of one model to a numbered PNG sequence.
Not part of the asset_catalogue package's normal import graph -- bpy only
exists inside Blender. See animation_preview.py for the host-side half.

Deliberately one clip per launch, unlike blender_thumbnail_script.py's
batch: this only ever runs because someone pressed Play on a specific
clip, so there's no batch to amortize Blender's startup over, and doing
the work lazily is the entire point -- rendering every clip of every
rigged asset up front would cost minutes per asset for frames nobody may
ever look at.
"""

import json
import math
import sys
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blender_common import IMPORTERS, apply_corrections, get_job_list_path

RESOLUTION = 320  # smaller than the 512 static thumbnail: this is a moving preview
WORLD_COLOR = (0.05, 0.05, 0.06)
CAMERA_DIRECTION = mathutils.Vector((1, -1, 0.7)).normalized()
CAMERA_DISTANCE_FACTOR = 3.2
# Bounds are sampled at a handful of frames rather than every one: a
# walk cycle's arm swing can reach well outside the bind pose, and
# framing on frame 1 alone would clip it, but sampling all 24+ frames
# just to measure them doubles the scene evaluation for no visible gain.
BOUNDS_SAMPLES = 6


def setup_scene() -> None:
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    camera_data = bpy.data.cameras.new("Camera")
    camera = bpy.data.objects.new("Camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera

    light_data = bpy.data.lights.new("Light", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("Light", light_data)
    scene.collection.objects.link(light)

    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (*WORLD_COLOR, 1.0)
    scene.world = world

    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False


def find_action(clip_name: str):
    """The glTF importer names each action after its clip, but a second
    model in the same session (or a name Blender had to uniquify) can end
    up as "@walk.001" -- so fall back to a prefix match before giving up.
    """
    action = bpy.data.actions.get(clip_name)
    if action is not None:
        return action
    for candidate in bpy.data.actions:
        if candidate.name.rsplit(".", 1)[0] == clip_name:
            return candidate
    return None


def bind_action(action) -> None:
    """Assigns the clip to every object that can play it. Which object
    actually drives a glTF character varies (the armature normally, but a
    rigidly-animated prop is keyed on the object itself), so this binds
    whatever matches rather than assuming an armature is present.
    """
    for obj in bpy.data.objects:
        if obj.animation_data is None:
            obj.animation_data_create()
        obj.animation_data.action = action


def sampled_bounds(mesh_objects: list, frames: list[int]):
    min_corner = mathutils.Vector((math.inf, math.inf, math.inf))
    max_corner = mathutils.Vector((-math.inf, -math.inf, -math.inf))
    step = max(1, len(frames) // BOUNDS_SAMPLES)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for frame in frames[::step]:
        bpy.context.scene.frame_set(frame)
        depsgraph.update()
        for obj in mesh_objects:
            evaluated = obj.evaluated_get(depsgraph)
            for corner in evaluated.bound_box:
                world_corner = evaluated.matrix_world @ mathutils.Vector(corner)
                min_corner.x = min(min_corner.x, world_corner.x)
                min_corner.y = min(min_corner.y, world_corner.y)
                min_corner.z = min(min_corner.z, world_corner.z)
                max_corner.x = max(max_corner.x, world_corner.x)
                max_corner.y = max(max_corner.y, world_corner.y)
                max_corner.z = max(max_corner.z, world_corner.z)
    return min_corner, max_corner


def aim_camera(min_corner, max_corner) -> None:
    center = (min_corner + max_corner) / 2
    size = max_corner - min_corner
    radius = max(size.length / 2, 0.001)
    distance = radius * CAMERA_DISTANCE_FACTOR

    camera = bpy.data.objects["Camera"]
    camera.location = center + CAMERA_DIRECTION * distance
    look_direction = (center - camera.location).normalized()
    camera.rotation_euler = look_direction.to_track_quat("-Z", "Y").to_euler()
    camera.data.clip_end = max(distance * 4, camera.data.clip_end)

    light = bpy.data.objects["Light"]
    light.location = center + CAMERA_DIRECTION * distance * 1.5
    light.rotation_euler = look_direction.to_track_quat("-Z", "Y").to_euler()


def pick_frames(action, max_frames: int) -> list[int]:
    """Evenly-spaced frames across the clip, capped -- a 300-frame clip
    rendered in full would take minutes and produce a preview nobody
    needs that much detail from. Playback speed is adjusted host-side to
    keep the clip's real duration.
    """
    start, end = (int(round(v)) for v in action.frame_range)
    total = max(1, end - start + 1)
    if total <= max_frames:
        return list(range(start, end + 1))
    stride = total / max_frames
    return [start + int(index * stride) for index in range(max_frames)]


def main() -> None:
    with open(get_job_list_path(), "r", encoding="utf-8") as f:
        job = json.load(f)

    output_dir = Path(job["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_scene()

    try:
        importer = IMPORTERS.get(job["extension"].lower())
        if importer is None:
            raise RuntimeError(f"no importer for {job['extension']}")
        importer(job["source_path"])
        pack_root = job.get("pack_root")
        mesh_objects, _broken, _notes, _materials = apply_corrections(
            job.get("corrections") or {},
            Path(pack_root) if pack_root else None,
        )
        if not mesh_objects:
            raise RuntimeError("no mesh content to render")

        action = find_action(job["clip_name"])
        if action is None:
            raise RuntimeError(f"no animation named {job['clip_name']!r}")
        bind_action(action)

        frames = pick_frames(action, int(job.get("max_frames", 24)))
        aim_camera(*sampled_bounds(mesh_objects, frames))

        for index, frame in enumerate(frames):
            bpy.context.scene.frame_set(frame)
            bpy.context.scene.render.filepath = str(output_dir / f"frame_{index:04d}")
            bpy.ops.render.render(write_still=True)
            print(f"ASSET_CATALOGUE_ANIM_FRAME|{index + 1}|{len(frames)}", flush=True)

        print(f"ASSET_CATALOGUE_ANIM_RESULT|ok|{len(frames)}", flush=True)
    except Exception as exc:  # noqa: BLE001 - reported to the host, not raised
        print(f"ASSET_CATALOGUE_ANIM_RESULT|fail|{exc}", flush=True)


main()
