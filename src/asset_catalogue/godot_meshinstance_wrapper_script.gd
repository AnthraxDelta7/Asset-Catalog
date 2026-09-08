extends SceneTree
# Run headlessly via:
#   godot --headless --path <project_root> -s godot_meshinstance_wrapper_script.gd -- <job_list_json_path>
# job_list_json_path points at a JSON object: {"jobs": [{"glb_path":
# "res://...", "output_base": "res://.../<name>"}, ...]}
# output_base carries no extension: which one a job produces isn't known
# until the .glb is loaded and its meshes counted, so the script picks it
# and reports the real path back on its result line.
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
# scene, finds every MeshInstance3D in it, and produces one of two things
# from just those:
#
#   exactly one mesh  -> <name>.res, a bare Mesh resource. Dropping one
#     into a scene gives a plain, local MeshInstance3D you can edit like
#     any other node; a .tscn would instead give an *instanced* scene,
#     linked to its file and needing "Make Local" first. The mesh's node
#     transform is baked into its vertex data so nothing is lost by
#     dropping the node that used to carry it (see _flattened_mesh).
#   more than one     -> <name>_meshinstance.tscn, a Node3D root with one
#     named MeshInstance3D child per mesh. A scene is the only thing that
#     can express several meshes positioned relative to each other.
#
# Either way, every mesh keeps its own rotation/scale/position exactly as
# it was in the source (see _relative_transform) -- confirmed directly
# against a real Godot install with a genuine scene-graph-level transform
# (not one baked into the mesh's own vertex data, which some export tools
# do instead and needs no help from this at all).
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
	# node.mesh != null excludes a MeshInstance3D with no actual mesh
	# resource assigned -- a purely organizational/placeholder node some
	# tools leave behind, never something a real "layer" the user cares
	# about. Without this, one of those inflates the count past 1 and the
	# single visible mesh ends up wrapped in an unnecessary Node3D instead
	# of being the scene's own root. Never excludes a MeshInstance3D that
	# genuinely has geometry, no matter how it's named -- a real second
	# part (a collision proxy, a separate LOD, anything) is exactly the
	# kind of layer this is supposed to preserve, not silently drop.
	if node is MeshInstance3D and node.mesh != null:
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


# The material actually shown for a surface: an override set on the node
# wins over the one baked into the mesh, exactly as Godot itself resolves
# it at render time. Matters when flattening to a standalone Mesh, which
# has nowhere to keep a node-level override.
func _effective_material(source: MeshInstance3D, surface: int) -> Material:
	var override_mat = source.get_surface_override_material(surface)
	if override_mat != null:
		return override_mat
	return source.mesh.surface_get_material(surface)


func _all_surfaces_are_triangles(mesh: Mesh) -> bool:
	for i in range(mesh.get_surface_count()):
		if mesh.surface_get_primitive_type(i) != Mesh.PRIMITIVE_TRIANGLES:
			return false
	return true


# Rebuilds the mesh with xform applied to its vertex data and every
# surface's effective material baked in, so the result stands completely
# on its own -- no node needed to carry a transform or an override for it
# to look right. SurfaceTool.append_from does the geometry properly,
# including the inverse-transpose handling normals need under non-uniform
# scale, rather than us transforming raw arrays by hand.
func _flattened_mesh(source: MeshInstance3D, xform: Transform3D) -> Mesh:
	var mesh: Mesh = source.mesh
	var out := ArrayMesh.new()
	for i in range(mesh.get_surface_count()):
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		st.append_from(mesh, i, xform)
		out = st.commit(out)
		var mat := _effective_material(source, i)
		if mat != null:
			out.surface_set_material(out.get_surface_count() - 1, mat)
	out.resource_name = mesh.resource_name
	return out


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
		_generate_one(job["glb_path"], job["output_base"])

	quit(0)


# A lone mesh is saved as a bare Mesh resource, not a scene: dropping one
# into a Godot scene gives a plain, local MeshInstance3D, where a .tscn
# would give an *instanced* scene that has to be made local before it can
# be edited like an ordinary node. Its node transform is baked into the
# vertex data on the way out (see _flattened_mesh) -- a Mesh has nowhere
# to keep one, and silently dropping it would undo exactly the
# rotation/scale preservation this script was fixed to do.
#
# Anything with more than one mesh still becomes a scene, because that's
# the only thing that can express several meshes at their own positions
# relative to each other.
func _generate_one(glb_path: String, output_base: String) -> void:
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

	if mesh_instances.size() == 1:
		var source: MeshInstance3D = mesh_instances[0]
		# Baking needs triangles (SurfaceTool's own constraint). Godot's
		# glTF import produces them in every real case, but rather than
		# silently drop a transform we can't bake, fall back to the scene
		# form, which carries it on the node instead.
		if _all_surfaces_are_triangles(source.mesh):
			var mesh := _flattened_mesh(source, _relative_transform(source, scene_root))
			var mesh_path := "%s.res" % output_base
			var mesh_err := ResourceSaver.save(mesh, mesh_path)
			scene_root.free()
			if mesh_err != OK:
				print("GODOT_WRAPPER_RESULT|%s|error|save failed (%s)" % [glb_path, mesh_err])
				return
			print("GODOT_WRAPPER_RESULT|%s|ok|%s" % [glb_path, mesh_path])
			return

	var new_root := Node3D.new()
	for source in mesh_instances:
		var wrapper := MeshInstance3D.new()
		wrapper.name = source.name
		wrapper.mesh = source.mesh
		wrapper.transform = _relative_transform(source, scene_root)
		_copy_surface_overrides(source, wrapper)
		new_root.add_child(wrapper)
		wrapper.owner = new_root

	var output_path := "%s_meshinstance.tscn" % output_base
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
