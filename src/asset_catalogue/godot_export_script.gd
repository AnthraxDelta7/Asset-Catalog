extends SceneTree
# Run headlessly via:
#   godot --headless --path <project_root> -s godot_export_script.gd -- <job_list_json_path>
# job_list_json_path points at a JSON object: {"include_colliders": bool,
# "jobs": [{"scene_path": "res://...", "output_path": "C:/.../out.glb"}, ...]}
# -- one Godot process handles every scene in the project in one run rather
# than relaunching per scene, since Godot's own startup cost dominates for
# anything this quick (the same "one process, many files" tradeoff
# blender_thumbnail_script.py makes on the Blender side).
#
# Each job's outcome is reported on stdout as a single delimited line so
# the calling Python process can parse progress without depending on
# Godot's own log formatting, which varies by version:
#   GODOT_EXPORT_RESULT|<scene_path>|ok|<output_path>
#   GODOT_EXPORT_RESULT|<scene_path>|error|<reason>
#
# GLTFDocument.append_from_scene bakes whatever material is actually
# resolved on each MeshInstance3D in the scene tree -- inherited from the
# mesh's own file, or overridden in this .tscn via surface/material
# overrides -- so this covers the common Godot pack pattern of a bare mesh
# file textured only through a scene-level material assignment, which a
# direct Blender import of the mesh file alone would never see.
#
# Collision shapes (CollisionShape3D) have no visual representation, so
# append_from_scene never sees them -- optionally (include_colliders),
# _inject_collider_meshes adds a temporary sibling MeshInstance3D built
# from Shape3D.get_debug_mesh() (the same low-poly wireframe the Godot
# editor itself uses to visualize collision shapes) next to each one
# before exporting, so the collider survives as real, visible geometry in
# the glb instead of being silently dropped.

func _inject_collider_meshes(node: Node) -> void:
	# Deliberately uses local .transform, not .global_transform -- the
	# instantiated scene is never added to the SceneTree (append_from_scene
	# doesn't need that, and adding it just to compute a world transform
	# turned out to make global_transform reads fail outright, verified
	# against a real collider scene before settling on this). Parenting the
	# new mesh as a sibling under the same parent means its LOCAL transform
	# is exactly the collider's own -- the parent chain above that is
	# unchanged, so append_from_scene bakes the correct world position on
	# its own, the same as it does for every other node in the tree.
	if node is CollisionShape3D and node.shape != null:
		var mesh_instance := MeshInstance3D.new()
		mesh_instance.mesh = node.shape.get_debug_mesh()
		mesh_instance.name = node.name + "_collider"
		mesh_instance.transform = node.transform
		node.get_parent().add_child(mesh_instance)
	for child in node.get_children():
		_inject_collider_meshes(child)


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 1:
		push_error("Usage: -- <job_list_json_path>")
		quit(1)
		return

	var job_list_path: String = args[0]
	var file := FileAccess.open(job_list_path, FileAccess.READ)
	if file == null:
		push_error("Could not open job list: %s" % job_list_path)
		quit(1)
		return
	var payload = JSON.parse_string(file.get_as_text())
	file.close()
	if payload == null:
		push_error("Job list is not valid JSON: %s" % job_list_path)
		quit(1)
		return

	var include_colliders: bool = payload.get("include_colliders", false)
	for job in payload["jobs"]:
		_export_one(job["scene_path"], job["output_path"], include_colliders)

	quit(0)


func _export_one(scene_path: String, output_path: String, include_colliders: bool) -> void:
	var packed_scene = load(scene_path)
	if packed_scene == null or not (packed_scene is PackedScene):
		print("GODOT_EXPORT_RESULT|%s|error|could not load scene" % scene_path)
		return

	var scene_root: Node = packed_scene.instantiate()
	if include_colliders:
		_inject_collider_meshes(scene_root)

	var gltf_document := GLTFDocument.new()
	var gltf_state := GLTFState.new()
	var err := gltf_document.append_from_scene(scene_root, gltf_state)
	if err != OK:
		print("GODOT_EXPORT_RESULT|%s|error|append_from_scene failed (%s)" % [scene_path, err])
		scene_root.queue_free()
		return

	err = gltf_document.write_to_filesystem(gltf_state, output_path)
	scene_root.queue_free()
	if err != OK:
		print("GODOT_EXPORT_RESULT|%s|error|write_to_filesystem failed (%s)" % [scene_path, err])
		return

	print("GODOT_EXPORT_RESULT|%s|ok|%s" % [scene_path, output_path])
