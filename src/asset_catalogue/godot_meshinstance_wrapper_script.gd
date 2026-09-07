extends SceneTree
# Run headlessly via:
#   godot --headless --path <project_root> -s godot_meshinstance_wrapper_script.gd -- <job_list_json_path>
# job_list_json_path points at a JSON object: {"jobs": [{"glb_path":
# "res://...", "output_path": "res://..._meshinstance.tscn"}, ...]}
# Every .glb referenced must already have a .import cache -- the caller
# runs a full headless project import pass first (see godot_export.py's
# _run_godot_import_pass); load() on an unimported resource fails outright
# with no loader found, confirmed directly.
#
# Each job's outcome is reported on stdout as a single delimited line,
# same convention as godot_export_script.gd's own GODOT_EXPORT_RESULT:
#   GODOT_WRAPPER_RESULT|<glb_path>|ok|<output_path>
#   GODOT_WRAPPER_RESULT|<glb_path>|error|<reason>
#
# The point: Godot's own glTF import produces an arbitrary root node type
# (often a plain Node3D, sometimes with an AnimationPlayer/Skeleton3D
# import artifact even for a static prop) wrapping the actual mesh --
# awkward to just "instance and use" compared to a clean MeshInstance3D,
# and not something worth hand-configuring per asset via the per-file
# import "Root Type" override in the editor. This walks the imported
# scene, finds every MeshInstance3D in it, and builds a fresh, minimal
# scene from just those: a single MeshInstance3D at the root if there's
# exactly one, or a Node3D root with one MeshInstance3D child per mesh
# (each at its correct relative position) for anything with more than
# one.
#
# Confirmed directly against a real Godot 4.4 install that
# ResourceSaver.save() on the result fully embeds the mesh geometry and
# material as self-contained sub-resources of the new scene -- not a
# fragile reference back into the source .glb's own import -- while a
# texture stays a proper external reference to the real image file the
# glTF importer already extracted into the project on its own. So the
# saved scene is a genuinely native, editable Godot resource on its own,
# not a thin wrapper that breaks if the source .glb is ever removed.
#
# Anything with no mesh at all (skeletal/animated content, or an empty
# scene) is reported as an error rather than silently skipped -- a single
# MeshInstance3D can't represent an animated character, and it's better
# for the caller to know a job produced nothing than to wonder why an
# expected file is missing.

func _find_mesh_instances(node: Node, out: Array) -> void:
	if node is MeshInstance3D:
		out.append(node)
	for child in node.get_children():
		_find_mesh_instances(child, out)


# Manually composes node's transform relative to root by walking UP the
# parent chain and multiplying top-down -- .global_transform is unreliable
# on a node that's never been added to the live SceneTree (the same
# already-discovered gotcha godot_export_script.gd's own
# _inject_collider_meshes works around the same way, confirmed again here
# directly: a real multi-mesh glb round-tripped through pack/save/reload
# with both meshes intact at their correct positions using this).
func _relative_transform(node: Node3D, root: Node) -> Transform3D:
	var chain: Array = []
	var current: Node = node
	while current != root and current != null:
		chain.append(current)
		current = current.get_parent()
	chain.reverse()
	var result := Transform3D.IDENTITY
	for n in chain:
		result = result * n.transform
	return result


func _copy_surface_overrides(source: MeshInstance3D, dest: MeshInstance3D) -> void:
	for i in range(source.get_surface_override_material_count()):
		var mat = source.get_surface_override_material(i)
		if mat != null:
			dest.set_surface_override_material(i, mat)


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

	for job in payload["jobs"]:
		_generate_one(job["glb_path"], job["output_path"])

	quit(0)


func _generate_one(glb_path: String, output_path: String) -> void:
	var packed_scene = load(glb_path)
	if packed_scene == null or not (packed_scene is PackedScene):
		print("GODOT_WRAPPER_RESULT|%s|error|could not load .glb" % glb_path)
		return

	var scene_root: Node = packed_scene.instantiate()
	var mesh_instances: Array = []
	_find_mesh_instances(scene_root, mesh_instances)

	if mesh_instances.is_empty():
		print("GODOT_WRAPPER_RESULT|%s|error|no mesh content found" % glb_path)
		scene_root.free()
		return

	var new_root: Node3D
	if mesh_instances.size() == 1:
		var source: MeshInstance3D = mesh_instances[0]
		new_root = MeshInstance3D.new()
		new_root.mesh = source.mesh
		_copy_surface_overrides(source, new_root)
	else:
		new_root = Node3D.new()
		for source in mesh_instances:
			var wrapper := MeshInstance3D.new()
			wrapper.name = source.name
			wrapper.mesh = source.mesh
			wrapper.transform = _relative_transform(source, scene_root)
			_copy_surface_overrides(source, wrapper)
			new_root.add_child(wrapper)
			wrapper.owner = new_root
	new_root.name = output_path.get_file().get_basename()

	var new_packed := PackedScene.new()
	var err := new_packed.pack(new_root)
	if err != OK:
		print("GODOT_WRAPPER_RESULT|%s|error|pack failed (%s)" % [glb_path, err])
		scene_root.free()
		new_root.free()
		return

	err = ResourceSaver.save(new_packed, output_path)
	# .free() (immediate), not .queue_free() -- see
	# godot_export_script.gd's own note on this: no frame yields happen
	# between jobs in this loop, so queue_free() would never actually run
	# until the whole batch finishes regardless of how many jobs ran
	# before it.
	scene_root.free()
	new_root.free()
	if err != OK:
		print("GODOT_WRAPPER_RESULT|%s|error|save failed (%s)" % [glb_path, err])
		return

	print("GODOT_WRAPPER_RESULT|%s|ok|%s" % [glb_path, output_path])
