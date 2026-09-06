# Asset Catalogue

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Standalone tool for cataloguing, tagging, previewing and exporting game assets. Design rationale lives in [asset-catalogue-seed.md](asset-catalogue-seed.md); this file tracks day-to-day usage as commands land.

If you'd like to support development, you can do so here: [ko-fi.com/anthraxdelta7](https://ko-fi.com/anthraxdelta7)

App icon by Gajah Mada from [Flaticon](https://www.flaticon.com/authors/gajah-mada).

## Setup

Grab the latest build from the [Releases page](https://github.com/AnthraxDelta7/Asset-Catalog/releases/latest), download `AssetCatalogue-vX.Y.Z-windows.zip`, and extract it anywhere. Run `AssetCatalogue.exe` inside the extracted `AssetCatalogue` folder — no Python install required. On first launch (or anytime no library is configured yet), a Settings dialog walks you through picking a staging folder (where unprocessed packs land, needed before ingesting anything) and a library folder (where the catalogue itself lives); nothing else to install or configure by hand. Blender 4.0+ is optional, only needed for 3D model thumbnails/conversion — auto-detected if it's already installed, or point at it directly in that same Settings dialog. Godot 4.0+ is likewise optional, only needed for **Tools > Extract Godot Scenes to GLB...** (see below) — Godot has no standard install location the way Blender does, so auto-detect only covers it being on PATH; point at its `.exe` directly in Settings otherwise.

Want to build from source instead, or use the CLI? See "Development setup" below.

## Ingest

A pack is a subfolder of the staging folder. `--pack-name` identifies the pack in the catalogue (re-using an existing name reuses that pack rather than duplicating it, so re-ingesting after adding files to a pack is safe):

```
asset-catalogue ingest <pack_folder_name> --pack-name "Pack Name" --creator "Creator" --licence "CC0" [--source-url URL]
```

Every file is SHA-256 hashed. Identical content — whether re-scanning the same pack or the same file shipped in two different packs — is recognized and skipped, never duplicated.

**Quality control — a whitelist, not a blacklist:** only file extensions this catalogue actually knows how to handle get catalogued at all — see `ASSET_TYPE_BY_EXTENSION` in `ingest.py` for the exact list (currently: `.obj`/`.fbx`/`.gltf`/`.glb`/`.stl`/`.blend` as models, `.png`/`.jpg`/`.jpeg`/`.tga`/`.bmp`/`.tiff`/`.webp` as textures, `.wav`/`.mp3`/`.ogg`/`.flac` as audio). Anything else — Unity/Unreal project files (`.meta` sidecars, `.prefab`/`.unity`/`.uasset`/`.uproject`, `.cs` scripts, etc.), a stray readme, an unsupported format, anything not on that list — is skipped during ingest, not catalogued as a generic "other" asset. This is deliberately a whitelist rather than a blacklist of known-bad extensions: a blacklist only excludes what it explicitly recognizes as junk, so anything unanticipated still got in; a whitelist only lets in what it explicitly knows how to handle, so nothing unrecognized slips through either way. Whole engine build/cache folders (`Library/`, `ProjectSettings/`, `Binaries/`, `Intermediate/`, `Saved/`, `.git/`, etc.) are still skipped entirely on top of this — never walked into at all, since they can contain files with whitelisted extensions that are the engine's own internal cache, not real pack content. The ingest summary reports how many files/folders were skipped this way when it's non-zero.

**Re-ingesting an existing pack with different metadata updates only what changed** (the delta) — e.g. re-running with a corrected `--creator` updates just that field; a `--licence` you already had recorded and don't re-specify this time is left alone, never blanked out. Only fields you actually pass and that differ from what's stored get overwritten.

If a pack is a `.zip` (the common case for purchased packs) and it's already sitting inside the staging folder, plain `ingest` handles it transparently — no separate step:

```
asset-catalogue ingest <pack_name.zip> --pack-name "Pack Name" --creator "Creator" --licence "CC0"
```

If the given path resolves to a `.zip` file rather than a folder, it's extracted first (into a same-named sibling folder) and then ingested normally, same as any other pack. **Re-running `ingest` against the same zip afterward — e.g. picking it again to catch new files — reuses the already-extracted folder instead of failing**: it only extracts when that destination doesn't already exist, so a second run against the same zip behaves exactly like re-ingesting the already-extracted folder directly (idempotent, deduping by content hash). Extraction still refuses to run on top of some *other*, unrelated non-empty folder that happens to share the zip's name (won't silently merge/overwrite a pack that isn't this zip's own prior extraction).

For a zip that lives *outside* the staging folder (e.g. still in your Downloads folder), use `ingest-zip` instead, which brings it in:

```
asset-catalogue ingest-zip <path\to\pack.zip> --pack-name "Pack Name" [--pack-folder folder_name] --creator "Creator" --licence "CC0"
```

`<path\to\pack.zip>` can be anywhere on disk — it's extracted into a new subfolder of the staging folder (named after the zip file by default, or `--pack-folder`) and then ingested normally, with the same re-run behavior as above: a destination that already exists (from an earlier run of this same command) is ingested from as-is, not re-extracted. Extraction rejects any archive entry whose path would land outside the destination folder (zip-slip protection — this processes archives from arbitrary purchased packs, so that's a real risk worth guarding against, not just a theoretical one).

**A `.zip` found anywhere *inside* a pack while ingesting — not just at the top level — is handled the same way**, automatically: extracted into a same-named sibling folder, its contents catalogued under that folder name, and this applies recursively (a zip inside a zip unpacks both). The original `.zip` is left in place, not deleted — only its contents get catalogued. Re-running ingest afterward is still fully idempotent: an already-extracted nested zip is recognized and re-walked for new content rather than re-extracted.

### Every ingested asset is copied into the library

Ingest also copies each file into `library_folder/assets/<Pack Name>/<relative_path>`, if it isn't there already. This is what makes a library folder actually self-contained: without it, moving or sharing the library (its own separate feature, see "Switch Library" below) would only carry the catalogue database and thumbnails, never usable files — the real assets would still be sitting only in the staging folder on the original machine. `ingest`/`ingest-zip` report how many files got archived this way. `remove` (below) deletes an asset's archived copy along with everything else about it; tagging/untagging never touches it either way.

### Thumbnails are generated automatically, dispatched by type

Ingest also renders a thumbnail for every asset it catalogues — Pillow for `texture` and `audio` assets, Blender for `model` assets, dispatched per-asset by `asset_type`, no separate step needed. If a pack has no `model` assets, Blender is never even checked for (skips the startup/version-check cost entirely for texture- or audio-only packs). If it does and Blender isn't available (not installed, not configured), that's a soft skip, not a failure — the ingest still succeeds, 2D/audio thumbnails still render, and the summary says why 3D ones didn't (`ingest`/`ingest-zip` report generated/failed counts and the skip reason). The manual `thumbnail generate` / `thumbnail generate-audio` / `thumbnail generate-models` commands below still exist for regenerating (`--force`), previewing corrections, or catching a pack ingested before this existed — ingest just means you don't need them for the common case anymore.

**The first time a pack ever gets a model thumbnail, only one model asset is actually rendered** — a calibration preview, not the whole batch. The rest are left `pending` rather than rendered up front. This is exactly the "Per-pack calibration" workflow below (render one, check it, adjust corrections, then render the rest), just triggered automatically instead of requiring a manual first step: if the pack turns out to need `up_axis`/`scale`/`material_fallback` corrections, only that one preview asset needs a corrected re-render, not the whole pack's worth. The ingest summary says so explicitly when it happens, with the exact follow-up command. Once a pack has at least one successfully-rendered model — from that preview, or from any prior run — later ingests into the same pack (adding more files) skip the preview step and render new assets normally; calibration is a once-per-pack concern, not once-per-ingest.

## Tagging

Pack-level tags cascade to every asset currently in the pack. Safe to re-run after ingesting new files into an already-tagged pack — it only backfills assets that don't have the tag yet, and never touches assets that were tagged explicitly:

```
asset-catalogue tag pack <pack_name> <tag_name> [--category theme|type|style]
```

Per-file tags apply directly to one asset (by id, from `list` output) and take priority over an inherited tag of the same name:

```
asset-catalogue tag asset <asset_id> <tag_name> [--category ...]
asset-catalogue untag asset <asset_id> <tag_name>
```

List the tag vocabulary and how many assets carry each tag:

```
asset-catalogue tags
```

Renaming or re-categorizing a tag changes it everywhere it's used at once (`asset_tags` references tags by id, not name, so every asset carrying it just sees the new name); deleting a tag removes it from the vocabulary and from every asset carrying it:

```
asset-catalogue tag rename <tag_name> <new_name> [--category ...] [--clear-category]
asset-catalogue tag delete <tag_name> [--yes]
```

Removing a tag from one asset "sticks" even across a later pack-wide re-tag: `untag asset` records the exclusion (a small `excluded_tags` table, checked by `tag pack`'s cascade), so re-running `tag pack` on that pack/tag afterward never silently re-applies it to the asset you deliberately untagged. Explicitly re-tagging that asset with the same tag clears the exclusion again — explicit always wins, same precedence tags already use elsewhere.

## Thumbnails (2D)

Renders a 512x512 PNG for every `texture`-type asset, named after its content hash — so identical content, wherever it's re-encountered, never gets rendered twice:

```
asset-catalogue thumbnail generate [--pack "Pack Name"] [--force] [--asset-id ID]
```

Assets are retried on the next run if they previously failed (unreadable/corrupt file), but skipped once `thumbnail_status` is `done` — pass `--force` to re-render everything regardless of status, or `--asset-id` to target just one asset (also regardless of status, same as `--force` but scoped to it alone — this is what backs the UI's per-asset **Generate Thumbnail** button, see "UI" below). Model (3D) assets are untouched here; they wait on Blender integration (build order step 4).

## Thumbnails (audio)

```
asset-catalogue thumbnail generate-audio [--pack "Pack Name"] [--force] [--asset-id ID]
```

Same content-hash naming, same retry/`--force`/skip-if-done semantics as the 2D path above. `.wav` decodes with Python's stdlib `wave` module (zero new dependencies) and gets a real rendered waveform — peak amplitude per column, drawn as vertical bars, first channel only for multi-channel audio. `.mp3`/`.ogg`/`.flac` have no stdlib decoder; rather than add a decoding dependency (and, for mp3, likely a separate ffmpeg install) just for a thumbnail, they get a plain "audio file" placeholder icon instead — still visually distinct in the grid from other asset types, without pretending to show real waveform data it didn't actually decode. Revisit if real waveforms for those formats ever becomes worth a new dependency.

## Thumbnails (3D / Blender)

Requires Blender 4.0+ (auto-detected in the standard Windows install locations, or point at it explicitly):

```
asset-catalogue settings set --blender-path "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
asset-catalogue thumbnail generate-models [--pack "Pack Name"] [--force]
```

One Blender process handles the whole batch (startup dominates the per-asset cost, so this matters). Supports OBJ, FBX, GLTF/GLB, STL, and `.blend` (via library append). Rendered at 512x512 with EEVEE, a single sun light, dark neutral background, framed to each asset's bounding box. An asset that imports but produces zero mesh objects (or errors outright) is marked `failed` rather than saving a blank thumbnail, and is retried on the next run — same failed/done semantics as the 2D path above.

Per-pack corrections (`up_axis: "Y_UP"`, `scale`, `material_fallback: true`) are read from `packs.corrections` and applied after import, before framing.

Every source file path handed to a Blender importer is prefixed with Windows' `\\?\` extended-length marker before use — Python's own file access is long-path-aware and never needed this, but Blender's importers call straight into the OS APIs that cap a path at 260 characters (`MAX_PATH`), which a deeply nested staging path (a long pack/creator folder name plus a multi-level zip extraction) can genuinely exceed. Without it, an asset like that fails to import with a plain "cannot open file" even though the file is completely readable — this bit a real pack during development and is now fixed for every importer (OBJ/FBX/GLTF/GLB/STL/`.blend`), not just the one that happened to trigger it first.

**A broken texture reference is flagged, not silently rendered wrong.** After import, every asset is checked for any material referencing an image that failed to load (Blender's own reliable signal: the loaded image's pixel size is `(0, 0)`) — in practice, almost always an absolute path baked into the file at export time from the original author's own machine (e.g. `L:\UNITY\...`, confirmed against a real downloaded pack), meaningless on any other one, with the real texture often not included in the pack at all. Rather than let that render as Blender's own bright-pink "missing image" placeholder and leave someone thinking *this app* is broken, the ingest/thumbnail-generation completion message calls it out by name: `N asset(s) reference a texture that failed to load (Shotgun.fbx, ...) -- the pack may not actually include the textures it promises. Worth checking if that's grounds for a refund.` This is a genuinely different situation from an asset with no material at all (also common, and not flagged — a `baseColorFactor`-only or default-gray material isn't broken, it's just plain): checked directly by walking each mesh's actual texture nodes, not guessed from the render's appearance. A new `broken_texture_fallback` correction (**Edit Pack Metadata**, or `--broken-texture-fallback` on the CLI, alongside `material_fallback`) replaces just the affected mesh(es) with a flat gray material — surgical, not blanket like `material_fallback`: a pack can easily have some assets with real, working materials right next to others with a dead texture link (confirmed against a real pack: guns with a broken absolute path next to walls with genuine flat colors), so only the actually-broken ones get replaced.

**Relink recovers an exact match automatically; anything less certain goes to a human instead of a guess.** A broken image reference is repointed to a file with the exact same basename found anywhere else in the pack, if one exists — this is the one form of automatic recovery that's always safe, since it only ever restores the literal file a mesh's own material already named, never guesses across meshes.

An earlier version of this also tried a second, riskier pass: guessing that a material with *no* texture reference at all (`RecessA`, say) should use a same-named file found elsewhere in the pack, by naming convention alone. Removed after it repeatedly guessed wrong on real downloaded packs — a material name shared by several structurally different meshes in a modular kit (the bake belonging to only one of them got pasted onto the rest, visible as a seam with no relation to any real edge), and a same-suffixed file that turned out to be something other than a usable color texture (one vendor reused the exact `_albedo` suffix for a channel-mask image meant for a custom recolor shader, not direct use as Base Color — nothing about the filename gave that away). No naming convention reliably tells these apart from the pack alone, and a confidently-wrong guess is worse than an honest "I don't know."

**Missing Textures review, instead:** when relink can't resolve a broken reference, it's left flagged rather than guessed at, and shows up in a review list — the status bar's **⚠ N missing textures — click to review** badge (visible whenever the list isn't empty; same "persistent, click-to-act" pattern as the pending-conversions badge), or the asset detail panel's own **Fix Texture...** button when the currently selected asset is one of the affected ones. Each row (one per still-broken material, per asset) offers:

- **Browse...** — pick the correct file yourself, restricted to the pack's own staging folder. This sets a manual texture override (below) for that material and re-renders every asset in the pack using it — materials are pack-scoped, so fixing one row resolves every other row sharing that same material at once, not just the one selected.
- **Add Supplementary File...** — embeds an extra file (a mask, another map) into the exported `.glb` for that material without wiring it into anything or resolving the row — for a file the user wants to keep with the asset for later hand-wiring (a vendor's own custom recolor shader, say) that this app has no way to correctly auto-apply itself. Wired into Emission Color with Emission Strength forced to zero (the one Principled BSDF input a pack is least likely to already be using for a real effect) purely as a smuggling mechanism — Blender's glTF exporter silently drops any image that isn't actually reachable from the material graph, confirmed directly, so an unconnected node doesn't survive export at all. Skipped (and reported, not silently) if that material's Emission is already doing something real, rather than risking overwriting an actual glow effect; only one supplementary file per material is supported, since there's no second slot in the core glTF material model this safe to commandeer.
- **No Texture Needed** — marks the material as intentionally textureless so it stops being flagged on future renders (stored per-pack as `acknowledged_materials`), without assigning any file.
- **Skip** — drops the row from this review session only, not persisted; it reappears next time a render reports the same material still broken. The difference from No Texture Needed is "not now" versus "never."

Manual texture overrides (`texture_overrides` in a pack's corrections — Edit Pack Metadata's "Manual texture overrides" list, `--texture-override MATERIAL=path/relative/to/pack.png` on the CLI, or the review dialog's Browse... above, all the same underlying storage) pin a specific material to a specific file, checked before relink and applied unconditionally — the user's own explicit instruction, never second-guessed. `disable_smart_texture_matching` (Edit Pack Metadata, or `--disable-smart-texture-matching` on the CLI) turns off automatic relink for the whole pack; manual overrides still apply even then.

## Godot scene extraction

Requires Godot 4.0+ — no standard install location the way Blender has, so point at it explicitly rather than relying on auto-detect (which only checks PATH):

```
asset-catalogue settings set --godot-path "C:\Path\To\Godot_v4.4-stable_win64.exe"
asset-catalogue godot-extract <staged-folder> [--no-colliders]
```

A pre-ingest step, not part of `ingest`/`ingest-zip` themselves: scans `<staged-folder>` for every Godot project inside it (anything with its own `project.godot`), and exports each scene -- both the text `.tscn` format and Godot's equally-valid compressed-binary `.scn` format, since a marketplace pack converted from another engine commonly ships `.scn` exclusively (confirmed against a real Synty POLYGON pack where a `.tscn`-only search silently found nothing at all to export) -- to a real, textured `.glb` sitting right next to it, via the real Godot editor running headlessly (`godot --headless --path <project> -s godot_export_script.gd`, one process per project, all its scenes in one run). Anything under a project's own `.godot/` folder is excluded from that scan -- Godot's editor re-import cache, which mirrors every real scene as its own auto-generated, hash-named `.scn` under `.godot/imported/`; on that same real pack, 200 of the 305 files a naive search turned up were exactly this cache, not real content. This exists because a Godot pack's mesh files commonly carry no material of their own — the texture is assigned in the scene or a separate `.tres` resource — which is exactly the linkup this app's Blender-based importer can't see when given the mesh file directly; ingesting a Godot pack's raw files without this step is what produces a textureless model. Rather than parse that linkup ourselves (fragile across material/shader variations), this asks Godot's own `GLTFDocument` API to resolve it — the same mechanism behind Godot's **Scene > Export As > glTF2 Scene** editor menu item, just scripted. A scene with no mesh content (checked by loading the export back and looking for real vertex data, not by guessing from the scene's own content upfront) has its output deleted and is reported as skipped rather than left behind as a near-useless near-empty file.

Collision shapes (`CollisionShape3D`) have no visual mesh of their own, so by default (`--no-colliders` to turn it off) the export script also injects a `MeshInstance3D` built from `Shape3D.get_debug_mesh()` — the same low-poly wireframe the Godot editor itself uses to visualize collision shapes — as a sibling of each collider before exporting, named `<CollisionShapeName>_collider`. For a mesh-based collider (`Concave`/`ConvexPolygonShape3D`, the common result of a Unity `MeshCollider` carried through a conversion pipeline) that "debug mesh" is the actual collision geometry itself, not a simplified primitive, so it can be just as high-poly as the visual mesh -- expected, not a bug. Verified for real against actual Godot 4.4 projects before shipping (a skeletal-mesh/inherited-scene case for the texture path, a synthetic `StaticBody3D` + `BoxShape3D` scene for the collider path, transform correctness confirmed at the raw glTF level, and a real Synty POLYGON pack -- Unity-converted, `.scn`-only, mesh-based colliders throughout -- for both together) — not assumed from documentation alone, since this is the one piece of the feature that can't be pytest-covered (Godot's own scene resolution isn't something to mock meaningfully).

Once extraction finishes, run `ingest`/`ingest-zip` on the same staged folder as normal — the new `.glb` files are picked up like any other model asset, no special-casing needed in ingest itself. The UI counterpart is **Tools > Extract Godot Scenes to GLB...** (see below), which lists the Godot project(s) found with checkboxes and an "Include colliders" toggle instead of a `--no-colliders` flag.

## Interactive 3D preview

A model asset's interactive-preview `.glb` (content-hash-keyed, same identity scheme as thumbnails — see `model_preview.py`) is generated **on demand only**, never automatically at ingest or alongside a normal thumbnail render. It used to be generated for every model as part of every Blender render pass, piggybacking on the same import (no second launch needed) — but that meant ingesting or bulk-rendering a large pack paid the extra export cost for every single model, including ones nobody would ever actually view in 3D, which made those operations noticeably slower for no benefit most of the time. Three ways to actually get one rendered:

- **Grid right-click > 3D Preview (Orbit/Zoom)...** on a single `model` asset — renders it on the spot if it isn't cached yet (same background job, same progress dialog as everything else), then opens the viewer once it's ready. Always available, never disabled.
- **Grid right-click > Render N 3D Preview(s)** on a multi-selection — pre-warms the cache for whatever's selected without opening a viewer; assets that already have one cached are skipped.
- **Right-click a pack > Render 3D Previews for Pack (N)** — the same bulk pre-warm, scoped to every model in that pack.

All three funnel through `Catalogue.render_model_previews_bg`, the one and only place a preview actually gets rendered — regenerating a thumbnail, converting to glTF, or reverting a conversion no longer touch the preview cache at all. The UI's grid right-click menu offers **3D Preview (Orbit/Zoom)...** for any `model` asset — a real orbit/pan/zoom viewer (`pyqtgraph`'s `GLViewWidget`, loaded via `trimesh`), not just a bigger version of the static render.

Deliberately lightweight rather than a full PBR renderer: `pyqtgraph`'s `GLMeshItem` has no UV-mapped texture support, so each part's actual material/texture is baked down to per-vertex colors instead (`trimesh`'s `Visuals.to_color()` — real per-pixel texture sampling where the asset has an image texture, or the material's own flat colour where it doesn't), rendered against a neutral grey background rather than pure black. Close, but not pixel-identical to the final in-engine look — that's still what the Blender-rendered static thumbnail is for — this is for spinning a model around to check topology, proportions, color, and orientation before committing to use it. Both the extra dependencies (`pyqtgraph`, `PyOpenGL`, `trimesh`, `scipy`) and the viewer module itself are only ever loaded into memory the first time someone actually opens a 3D preview, not on every app launch.

`scipy` specifically matters here: `trimesh` silently falls back to degenerate all-identical vertex normals without it (rather than raising), which showed up as a real bug during development — some models rendered as a solid black silhouette in the viewer despite the static thumbnail looking completely normal, since the shading math collapsed with every normal pointing the same direction. Now a required dependency for exactly this reason.

**A "Parts" panel lists every sub-mesh in the file individually, each with its own checkbox** — not just the visible surface: a downloaded pack's collision mesh, LOD variants, or any other extra geometry baked into the same `.glb` shows up here too, exactly as `trimesh`'s scene graph reports it, regardless of what it's named. Detection of *which* part is probably a collider is only ever a naming heuristic used for convenience, never a filter on what's shown — every part gets a row and a checkbox no matter what it's called, since there's no single naming convention across sources and an unrecognized name (the common case for a random downloaded pack) still needs to be toggleable by hand. The heuristic itself recognizes `godot_export_script.gd`'s own `<name>_collider` suffix, Unreal/FBX's long-standing `UCX_`/`UBX_`/`USP_`/`UCP_`/`MCDCX_` prefix convention, and generic `collision`/`collider`/`hitbox`/`phys`/`col` name tokens (matched as whole underscore/space/dash-delimited tokens, not a raw substring — so "Column" or "Colonial" never false-positive just for containing "col"); a row flagged this way renders with a translucent orange tint and a wireframe outline so it reads as a diagnostic overlay rather than real surface color, since a collision debug mesh has no material of its own to fall back on. **Show All** and **Hide Likely Colliders** bulk-toggle using that same flag as a starting point, but any individual row can still be checked/unchecked regardless. The panel always appears, even for a single-part asset -- confirming "this file contains exactly one part, named X, and nothing else is bundled in" is itself useful, not just something to isolate among several. A **Textures...** button, shown whenever at least one part actually has one, opens a small gallery of every distinct source texture actually used -- not just the per-vertex colors sampled from it for the 3D view -- click one for a bigger view.

Loading a raw multi-file `.gltf` passes an explicit resolver allowing its image URI to point outside the model's own folder (`trimesh`'s default resolver otherwise refuses to follow a path like `../Textures/atlas.png` -- a real, common shared-texture-atlas pack layout, confirmed against an actual Synty POLYGON asset pack -- and fails *silently*, with the texture just absent rather than an error raised). Since this is always a local file already sitting on disk, not untrusted remote content, there's nothing that restriction is actually protecting against here.

A few other rendering details worth knowing about, all found and fixed by actually comparing preview output against the real static thumbnail for several assets:

- **Color space:** glTF's `baseColorFactor` is defined in linear space, but this simple vertex-color shader has no HDR/tonemapping step — displaying that value directly (skipping the sRGB encode a real glTF/PBR renderer always does) made every asset look far too dark, often solid black for anything with a moderately dark material. Baked colors are now explicitly sRGB-encoded before display.
- **Up axis:** Blender's glTF exporter always writes Y-up files regardless of the pack's own `up_axis` correction, but `pyqtgraph`'s orbit camera assumes Z-up (same convention as Blender's own viewport) — without correcting for that mismatch, a model's "up" in the 3D preview didn't match its "up" in the static thumbnail or in the calibration-preview dialog. Loaded geometry is now rotated back into Z-up so all three agree.
- **Second-preview crash:** opening a second 3D preview in the same app session used to throw `GL_INVALID_VALUE` on every draw call (and could take the whole packaged app down with it) — each `GLViewWidget` got its own separate OpenGL context, but `pyqtgraph` caches compiled shader programs globally by name, not per-context, so the second widget tried reusing a program handle that belonged to the first, already-different context. Fixed by sharing one GL context across the whole app (`QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)`, set before the `QApplication` is constructed).
- **First-preview freeze:** the very *first* 3D preview in a session used to look like the app had frozen or crashed, with zero feedback while it happened — a cold import of `pyqtgraph`/`PyOpenGL`/`trimesh` alone measured over a second, done synchronously on the GUI thread. The file parsing and color-baking now run on a background thread with the same progress dialog every other background job in this app uses ("Rendering 3D preview...") — only the actual (fast) GL widget construction happens on the GUI thread, since Qt requires that part specifically.
- **Overlapping-job crash risk:** every background job in this app shares a single `MainWindow._active_worker` slot; starting a second job while one was still running would silently drop the only Python reference to its `QThread` object while the thread was still alive natively — a real "destroyed while still running" crash risk. A real click can't normally trigger this (the progress dialog is modal), but it surfaced immediately in testing once the on-demand preview render added a second Blender-invoking action reachable in quick succession. `_run_background_job` now refuses to start a second job while one is active (with a message) rather than relying on the modal dialog alone, and clears the slot back to `None` as soon as a job's worker thread actually finishes — it previously left a stale reference behind that itself raised once the underlying Qt object was deleted.
- **First-preview window flicker:** the very first time a `GLViewWidget` appeared anywhere in the process, the whole app would flicker/flash once, as if it had briefly closed and reopened, even though nothing was actually crashing (confirmed: no crash event in Windows' own Application log). `QSurfaceFormat.setDefaultFormat(...)` (still set, for reasonable default GL capabilities) turned out not to be the actual fix — `GLViewWidget` never calls `setFormat()` itself, confirmed by reading `pyqtgraph`'s own source, so a global default format was never going to change what surface it got. The real cause: the *main* window is created long before any 3D preview and starts out as a plain, non-GL window; the moment the first `QOpenGLWidget`-based widget is realized inside it, Windows' compositor has to switch that window's whole backing surface to a GL-capable one, on the spot — visible as a flash regardless of which format bits were requested. Actually fixed by `_prewarm_opengl()` (`main_window.py`), which builds one (tiny, off-screen) `GLViewWidget` as a child of the main window *before its first `show()`* — a child widget's own `.show()` is deferred by Qt until its top-level parent actually appears, so the window comes into existence already GL-capable in a single step, with no separate "before" state for a flicker to show up against.

## Per-pack calibration

Corrections are set once per pack and apply to every asset in it, since a pack that's rotated or scaled wrong is wrong the same way throughout (seed doc §7). A brand-new pack with model assets already gets this workflow's first step done automatically at ingest — see "Thumbnails are generated automatically" above:

```
asset-catalogue pack set-corrections <pack_name> [--up-axis Y_UP|Z_UP] [--scale 1.0] [--material-fallback | --no-material-fallback] [--broken-texture-fallback | --no-broken-texture-fallback] [--disable-smart-texture-matching | --no-disable-smart-texture-matching] [--texture-override MATERIAL=path/to/texture.png ...] [--clear-texture-overrides]
asset-catalogue pack show-corrections <pack_name>
asset-catalogue pack set-corrections <pack_name> --clear
```

Workflow: render one asset to check a pack's import looks right, adjust corrections, preview again on just that asset, then re-render the whole pack once it's right:

```
asset-catalogue thumbnail generate-models --asset-id <id>          # preview one asset, ignores done/pending status
asset-catalogue thumbnail generate-models --pack "Pack Name" --force   # re-render the whole pack after settling on corrections
```

**Note:** `scale` won't visibly change the thumbnail — the camera always reframes to fit the asset's bounding box, so a uniformly-scaled object still fills the same portion of frame. It's stored and applied to the imported object regardless (for correctness ahead of the eventual import step, where absolute scale will matter), just not something you can visually calibrate by eye the way `up_axis` and `material_fallback` are.

## Editing, renaming, and removing a pack

Creator/licence/source URL can be edited after ingest, not just set once at ingest time. Only fields you pass are touched — omit one to leave it as-is, or clear it explicitly:

```
asset-catalogue pack set-metadata <pack_name> [--creator ...] [--licence ...] [--source-url ...]
asset-catalogue pack set-metadata <pack_name> [--clear-creator] [--clear-licence] [--clear-source-url]
```

Renaming a pack also moves its archived library folder (`library_folder/assets/<old name>/` → `.../<new name>/`) so already-archived copies and "Show in Library Folder" keep working under the new name instead of being silently orphaned under the old one:

```
asset-catalogue pack rename <pack_name> <new_name>
```

Removing a pack deletes every one of its assets (catalogue rows, thumbnails, archived copies — same as `remove --pack`) *and* the `packs` row and its entire archived library folder, which plain `remove --pack` never did (that only ever deleted the asset rows, leaving an empty pack entry and any stray library files behind):

```
asset-catalogue pack remove <pack_name> [--yes]
```

A pack's rating (1-5) and notes are personal, opinion fields — separate from creator/licence/source URL (facts about the pack) — most useful for remembering later which of a big purchased-pack backlog is actually worth reusing:

```
asset-catalogue pack notes <pack_name> [--notes "..."] [--rating 1-5]
asset-catalogue pack notes <pack_name> [--clear-notes] [--clear-rating]
```

## Favorites

A quick personal flag on individual assets, independent of tags (tags categorize; favorites just mark "I liked this one specifically"). UI-only for now — toggled via the grid's right-click menu or the detail panel's star button, and filterable via the **★ Favorites only** checkbox (see the UI section below); no dedicated CLI command yet.

## Converting models to glTF

On demand, per model asset (never automatic) — imports the asset in its original format via Blender, applies the pack's corrections (same ones used for thumbnails), and exports it as `.glb`. Same asset id, same tags/import history — only its file (`relative_path`, `extension`, `content_hash`, `file_size`) changes in place, and its thumbnail is regenerated immediately afterward so a broken conversion is obvious right away rather than discovered later:

```
asset-catalogue convert to-gltf --asset-id <id> [--asset-id <id> ...]
```

`--asset-id` is repeatable — pass several to convert them in one batch, via a single Blender process for the whole set (same batching rationale as thumbnail generation: startup dominates the per-asset cost). **Anything not a model asset, or already `.glb`, is silently skipped rather than treated as an error** — this is what lets you point it at a mixed, multi-format selection without pre-filtering it yourself.

**The pre-conversion original is never deleted automatically.** It's left sitting in staging (and its library copy stays archived) until you explicitly decide the conversion is good or bad:

```
asset-catalogue convert revert --asset-id <id>     # undo: restore the original, discard the .glb
asset-catalogue convert cleanup --asset-id <id>     # confirm: delete the original, keep the .glb
asset-catalogue convert cleanup-all [--yes]         # confirm every pending conversion at once
```

Reverting restores the exact pre-conversion `relative_path`/`extension`/`content_hash`/`file_size` and re-archives the original to the library; cleanup deletes the pre-conversion original (staging + library copy) and leaves the `.glb` as the permanent version. Both are recorded per-asset in a `pending_conversions` table, so `has_pending_conversion`/`count_pending_conversions` can drive UI state — see the UI section below.

**FBX (and other non-`.glb` formats) stay fully supported and importable on their own** — conversion is never forced at ingest time. The reason isn't just convenience: a plain export (`export`/`Export to Project`) is a raw file copy, so a texture fix that only exists inside Blender's in-memory scene (smart texture matching's broken-link relink or name-match injection, or the pack's own `broken_texture_fallback`) never leaves with it — only `.glb` embeds the fix for real, because conversion re-imports through the same corrected pipeline and bakes the result into the file. So a non-`.glb` asset can *look* right in every thumbnail and preview while still being one export away from shipping with a missing texture. Rather than force every FBX through conversion up front (real risk for rigged/animated FBX, and unnecessary for assets with no texture problem to begin with), each asset tracks this with a `needs_glb_conversion` flag: set automatically by thumbnail rendering whenever that asset's last render relied on a relink or name-match injection (and the asset isn't already `.glb`), cleared automatically once it's actually converted. This drives the grid's ⚠ badge, the **⚠ Needs conversion only** filter checkbox, and a bulk **Convert All Flagged** action — see the UI section below — plus the equivalent CLI command:

```
asset-catalogue convert flagged [--yes]
```

Converts every asset library-wide currently flagged, regardless of the current filter, via the same batched Blender pass and pending-conversion safety net as `convert to-gltf` above (nothing is deleted automatically; revert/cleanup still apply per-asset afterward).

## Search (CLI)

```
asset-catalogue list [--pack "Pack Name"] [--type model|texture|audio|other] [--tag tag_name] [--format fbx] [--search text] [--unused]
```

`--unused` shows assets that have never been exported into any project. `--format` filters by file extension (with or without the leading dot — `fbx` and `.fbx` are equivalent). `--search` matches anywhere in the filename, case-insensitive; a literal `%` or `_` in the search text is matched literally, not treated as a SQL wildcard.

## Removing assets

Removes one, many, or all assets from the catalogue — the database row, tags, export history, rendered thumbnail, and any archived library copy. **Never touches the original files in the staging folder**; those stay exactly as shipped, same as everywhere else in this tool. Requires a filter (or `--all`) for the same reason `export` does — an easy fat-finger away from wiping the whole catalogue otherwise:

```
asset-catalogue remove --asset-id <id> [--asset-id <id> ...] | --pack "Pack Name" | --type texture | --tag tag_name | --all [--yes]
```

Prints what's about to be removed and asks for confirmation unless `--yes` is passed. This is a real, immediate delete — for a reversible one, see Trash below.

## Trash

A soft-delete: hides assets from the normal grid/listings (`deleted_at` set) without touching the catalogue row, tags, thumbnail, or archived library copy — reversible via restore, unlike `remove` above. This is what the UI's grid delete action (**Move to Trash**) actually does now:

```
asset-catalogue trash move --asset-id <id> [--asset-id <id> ...]
asset-catalogue trash list
asset-catalogue trash restore [--asset-id <id> ...]      # omit --asset-id to restore everything
asset-catalogue trash empty [--asset-id <id> ...] [--yes] # omit --asset-id to empty the whole Trash
```

`trash empty` is the actual, permanent delete (same file/DB cleanup as `remove`) — it prompts for confirmation unless `--yes` is passed, same convention as `remove`.

## Library statistics and health

```
asset-catalogue stats
```

Total assets/size, favorited count, breakdowns by asset type and thumbnail status, and the largest packs by size — read-only, computed from each asset's `file_size` recorded at ingest (no filesystem walk needed). Same report backs **Tools > Library Statistics...** in the UI.

```
asset-catalogue check [--fix]
```

Scans for drift between the catalogue's records and reality on disk: a missing archived library copy, a thumbnail file gone despite `thumbnail_status` saying `done`, or a staging source that's disappeared since ingest (informational only — many people clean up staging on purpose once a pack's archived). Trashed assets are skipped. `--fix` applies the two safe automatic fixes: resets a broken thumbnail's status back to `pending` (so the next thumbnail pass re-renders it), and re-archives a missing library copy from staging where the source file is still there. An asset that's lost both its staging source and its library copy has no automatic fix. Same checks and fixes back **Tools > Check Library Integrity...** in the UI.

## Export

Copies selected assets out to a target project, rebuilding each asset's relative folder structure under a per-pack subfolder (default `exported_assets/<Pack Name>/...`, not dumped flat) rather than symlinking — see the design note below for why. Requires at least one selection filter, or `--all` to export the entire catalogue on purpose (a bare `export <project>` with nothing else is refused, since it's an easy fat-finger away from copying everything):

```
asset-catalogue export <project_root> [--pack "Pack Name"] [--type texture] [--tag tag_name] [--asset-id ID] [--all] [--dest-subfolder exported_assets]
```

Every export is recorded — asset, target project (by resolved absolute path), and timestamp — which is what makes these queries possible:

```
asset-catalogue exports [--project <project_root>] [--asset-id ID]
```

`exports` with no filter is the full history; `--project` shows what's already in one project; `--asset-id` shows every project a given asset has been exported into.

**Design note (copy vs symlink):** copy was chosen deliberately over symlink — simpler, safer, and avoids Windows' symlink creation normally needing Developer Mode or admin rights. The trade-off is real: a symlinked project would auto-pick-up pack updates and use less disk, but at the cost of the project silently breaking if the library folder isn't present at build/package time. Not revisited unless it becomes a real pain point.

**This used to be called "Import"** — renamed because "Ingest" (bring assets *into* the catalogue) and "Import to Project" (send assets *out* to a project) read as near-synonyms despite being opposite directions, which was genuinely confusing. "Export" pairs cleanly with "Ingest" instead (in / out). This was a full rename, CLI included: the old `import`/`imports` subcommands and the `imports` database table are gone, along with the old Godot-specific "remember one project, locked once set" toggle (`settings.json`'s old `godot_project_path`/`godot_export_enabled`) — replaced by the always-on, engine-agnostic recent-projects list described in the UI section below. An existing `catalogue.db` migrates its `imports` table to `exports` automatically and losslessly the next time it's opened (a plain `ALTER TABLE ... RENAME TO`, no rows touched); an existing `settings.json` carrying the old Godot setting has its remembered project folder carried forward into the new list the first time settings are loaded.

## Credits report

Generates a plain-text attribution report — creator, licence, and source URL per pack, the fields most asset licences actually require crediting — for dropping into a shipped game's credits screen or `credits.md`:

```
asset-catalogue credits [project_root]
```

With `project_root`, only packs that have at least one asset actually exported into that project (via the `exports` history) are included — what you'd want when crediting a specific shipped game. Without it, every pack currently in the catalogue is listed, regardless of whether it's been used anywhere yet. A pack missing a creator/licence/source URL shows `(not specified)` for that field rather than being silently skipped, so gaps in your own metadata are obvious before you ship. Also available from the UI (**Tools > Generate Credits Report...**), with a live preview and a Save As button.

## UI

```
asset-catalogue-ui
```

No CLI setup required first. A startup splash (icon, name, "by" credit, version) shows while the catalogue opens and the OpenGL pre-warm above runs, closing once the main window is actually ready — mostly there to cover for that pre-warm's cold `pyqtgraph`/`PyOpenGL` import, which otherwise has no other visible progress indicator this early in startup. A library folder is required above everything else — the app checks on startup and won't open the main window without one, looping back to a **Settings** dialog (folder/file browse buttons, Blender "Auto-detect") until either a library actually opens or you quit (the splash steps aside for this, so it's never competing with a dialog that needs your input). A path that's merely unconfigured is prompted for silently; a path that's configured but broken (bad permissions, points at a file, etc.) shows the actual error in the dialog rather than crashing. The window title always shows which library is currently open.

**File > Settings...** reopens that same dialog anytime, for staging folder / library folder / Blender path. **Leaving Blender path blank is a valid, working choice** (it's re-detected on demand every time it's actually needed, never cached in settings) but used to just look unconfigured with nothing in the field — it now shows the auto-detected path as greyed placeholder text when one is found, so a blank field reads as "auto-detecting, and here's what it found" rather than "nothing set up." **File > Switch Library...** is the faster, more deliberate path for just swapping libraries (a personal one vs. a shared one, say) — it browses directly to a folder and confirms before switching, telling you plainly whether it found an *existing* library there or would be creating a *new, empty* one, so a wrong folder pick doesn't silently start a fresh catalogue by accident. Either path live-switches the whole catalogue (closes the old DB connection, opens the new one, rebuilds the pack/tag/type lists) without restarting the app.

Filter panel (search / type / format / pack / tag) on the left, a thumbnail grid in the middle, and a tagging panel at the bottom for the selected asset. **Search** filters live as you type, matching anywhere in the filename (case-insensitive) — same matching as the CLI's `list --search`. **Format** lists whatever file extensions actually exist in the catalogue right now (e.g. `FBX`, `GLB`, `PNG`) — it updates immediately after a conversion changes an asset's extension, without resetting the other filters. The grid supports multi-select (Ctrl/Shift-click, or **Edit > Select All** / Ctrl+A). **Double-click any thumbnail** for a larger view of it (up to 512px) — the same rendered PNG shown bigger, not a live re-render, useful for a busy texture or a detailed model render the 128px grid icon doesn't show much of. For a `model` asset, a **View in 3D (Orbit/Zoom)...** button sits alongside Close, handing off to the same live viewer as the grid's right-click **3D Preview** action (rendering it on the spot first if it isn't cached yet) — the natural next step from "let me see this bigger" to "let me actually spin it around."

- **Exactly one asset selected:** the filename **with its extension clipped off** (e.g. `dragon`, not `dragon.fbx`) as the title — the extension is shown explicitly instead as **format: `EXT`** right below type (e.g. `type: model   format: FBX`), so it's not lost, just not repeated in two places. A **Pack: `<name>`** line, full tag editing (add/remove, with autocomplete against the existing tag vocabulary — typing "fi" suggests anything containing it, not just tags starting with it — so a typo doesn't quietly create a near-duplicate tag alongside the one you meant to reuse), and a **Show in Library Folder** button that opens the asset's archived copy directly in Explorer — enabled once it's actually in the library (which happens automatically at ingest, not tagging), disabled otherwise (e.g. its source file went missing before ingest could copy it). A **Generate Thumbnail** button appears whenever this asset's thumbnail isn't `done` yet and its type actually has a generator (`texture`/`audio`/`model` — not `other`) — renders just this one asset regardless of its current status, same as `--asset-id` on the matching CLI command, and disappears again once it succeeds. For an `audio` asset that's been archived, a **Play/Stop** button plays it directly from its archived library copy (Qt's built-in media playback, no new dependency) — hidden for every other asset type, and for an audio asset that hasn't been archived yet. The button fills left-to-right as the clip plays, like a small progress bar built into the button itself, so it's obvious both that something is playing and roughly how far through it is; also reachable via **right-click > Play** on the asset in the grid, without needing to select it into the detail panel first. If that asset has a pending glTF conversion (see below), **Revert Conversion** and **Delete Pre-Conversion Original** buttons also appear here — the panel is where you review a converted `.glb` next to its regenerated thumbnail and decide whether to keep it.
- **Two or more selected:** the panel switches to an "N assets selected" state — adding a tag applies it to every selected asset at once (as one background job, with a progress dialog). The tag list shows only tags common to *every* selected asset (the intersection, not the union of everyone's tags) — removing one of those is unambiguous, so it's enabled here too, taking that tag off all of them at once. If nothing is common to the whole selection, the list is empty and removal is disabled (there's nothing shared to remove). The **Pack:** line shows up here too, but only if every selected asset happens to share the same pack — otherwise it's blank, same "only show what's actually common" rule as the tag list.

**The Pack: line is a link** — clicking the pack name filters the grid down to just that pack (selects it in the Pack list on the left), a quick way to jump from "this one asset looks off" to "let me see the whole pack it came from."

**The Export button** (bottom-right of the detail panel, for either a single asset or a multi-selection) is a smart shortcut for "send this to a project" that gets faster the more you use it. The first time, it reads **Export to Project...** and clicking it opens the same dialog as **Tools > Export Selected to Project...**. After any successful export (from this button, that dialog, or the right-click menu), it remembers the project folder and relabels itself **Export to `<project name>`** — clicking it again re-exports the current selection straight there, no dialog. Its dropdown arrow lists every recently used project (newest first, one click each) plus a **Browse for Project...** entry that always reopens the full dialog, so picking a new destination is never more than one extra click away even once the button has a memory.

**Tools > Tag Pack...** cascades a tag onto an entire pack from a dropdown (defaults to whatever pack is currently filtered), same as the CLI's `tag pack` — the third way to select "how much" to tag, alongside one asset and a multi-selection.

**Ingest Pack...** is a toolbar button, not tucked in a menu — ingest is the most common action, so it's the first thing visible under the menu bar. One **Browse Folder/Zip...** button opens a small custom browser scoped to the staging folder, listing subfolders *and* `.zip` files side by side — either one double-clickable as a pack source (a `.zip` picked this way is auto-extracted at ingest time, same as `ingest`'s zip auto-detection on the CLI). Single-clicking an entry to highlight it, then pressing **Select**, works too, for either a folder or a `.zip` — picking that entry directly without entering it (a folder this way, not its contents); with nothing highlighted, Select picks whatever folder you're currently browsing. A custom browser rather than a native picker is a real Qt/OS limitation, not a design choice: a native picker is either a folder picker or a file picker, never both — there's no stock dialog that shows folders and files together and lets either be the result. Picking a source refreshes the suggested **Pack name** to match it, unless you've typed your own name first — re-picking a *different* source no longer leaves a stale name behind (a real bug, now fixed: it used to only fill the field once, the first time it was empty, so choosing another source afterward silently kept whatever was already there).

Once accepted, ingest runs as a single background job (which also archives every ingested asset into the library, reported in the completion message), with a live progress feed of what's happening file by file (see "Background jobs show a live progress feed" below) — and re-ingesting an existing pack with different metadata only updates the fields that changed (same delta behavior as the CLI).

**When ingest triggers a pack's first-ever calibration preview** (see "Thumbnails are generated automatically" above), a **Calibration Preview** dialog opens instead of just reporting it in the completion message: the rendered preview thumbnail, the same up axis/scale/material fallback fields as Edit Pack Metadata, a **Re-render Preview** button to try corrections against just that one asset as many times as needed, then **Render Remaining N Model(s)** to render the rest of the pack once it looks right, **Skip for Now** to leave them `pending` (same as closing the dialog), or **Cancel Import (Remove This Pack)** to undo the ingest entirely — the catalogue entries, thumbnails, and archived library copies it just created (never the original files in staging, same guarantee as `pack remove`). If the model that happened to get picked as the preview turns out to be a poor representative of the pack (an outlier shape, a part with its own texture quirks), **Skip and Render Next** steps to a different, still-unrendered model instead of forcing a Cancel-and-restart just to see how the pack looks on a different asset — whatever's currently in the corrections form carries over to the new one rather than resetting, and **Render Remaining N Model(s)**'s own count updates to match. Disabled once every model in the pack has already been rendered, since there's nothing left to step to.

A separate **Browse Zip...** option (for a `.zip` living *outside* the staging folder, e.g. still in Downloads) used to exist alongside Browse Folder/Zip; it's been removed as a rarely-needed extra option in the UI — the CLI's `ingest-zip <path>` still covers that exact case (extracts anywhere on disk into staging, then ingests), it just isn't duplicated in the ingest dialog anymore.

**If the picked source ships the same model in more than one file format** (`Model.fbx` sitting next to `Model.glb`, a real pattern — one downloaded pack ships all 114 of its models as both, 228 files for what's conceptually 114 assets), a **Choose Formats** dialog appears before ingest runs: checkboxes for every format involved in at least one such duplicate (`.glb` pre-checked by default, this app's own established preference), plus an **Import All Formats** button for the old, unfiltered behavior. A format left unchecked is only skipped where a same-named file in a checked format also exists — something genuinely unique to that format (no duplicate at all) is always kept regardless of the checkboxes. Same check on the CLI via `ingest`/`ingest-zip`'s `--only-formats glb` (or `fbx,glb`, comma-separated) — omit it to import everything, unchanged from the default.

**Batch Ingest...** sits next to Ingest Pack... on the toolbar, for ingesting several packs from one staging folder in a single pass instead of running Ingest Pack once per folder. **Browse Folders/Zips...** opens the same staging browser as above with multi-select turned on (Ctrl/Shift-click siblings at the current level, then Select) — each one becomes its own pack, named after its folder or file name (a source needing a custom pack name still goes through the regular single-pack dialog). Two modes decide how creator/licence/source URL get filled in: **Use the same info for every pack** (the default) applies one shared form to all of them, for a bundle bought from a single source; **Ask me for each pack** instead walks through a small form once per pack, in order, before any ingesting starts — each one opens pre-filled with whatever the previous pack was given rather than starting blank, so a run where only the licence changes between folders is just one edit per pack instead of retyping the creator every time. All the metadata collection happens up front (cancelling partway abandons the whole batch rather than ingesting a partial set) — including a **Choose Formats** prompt (see above) for any pack in the batch that ships the same model in more than one format, named in that dialog's own title so it's clear which pack it's about; cancelling one of these abandons the whole batch too, same as cancelling anywhere else in the up-front collection. The actual ingest work then runs as one background job, reporting a header line between packs in the progress feed, with a combined completion summary (totals plus one line per pack) and any first-ever calibration review dialogs shown in turn once the whole batch finishes.

**Tools > Extract Godot Scenes to GLB...** turns a staged Godot project into real, textured `.glb` files, before ingesting it — a Godot pack's mesh files commonly carry no material of their own (the texture is assigned in the `.tscn` scene or a separate `.tres` material resource), which is exactly the linkup this app's Blender-based importer has no way to see on its own; ingesting a Godot pack's raw mesh files directly is what produces a textureless model. Rather than parse that linkup ourselves, this shells out to the real Godot editor headlessly and asks it to export each scene through its own `GLTFDocument` API — the same mechanism behind Godot's own **Scene > Export As > glTF2 Scene** menu item, just scripted, so materials/textures resolve exactly as Godot itself resolves them. Point it at a staged folder; it lists every Godot project found inside (anything with its own `project.godot`), and exports every `.tscn` scene in each checked one to a `.glb` sitting right next to it — a subsequent Ingest Pack / Batch Ingest then picks those up as ordinary, already-recognized model assets on its own. A scene with no mesh content (UI, autoloads) is detected and skipped automatically rather than left behind as a near-empty file. **Include colliders as low-poly meshes** (on by default) additionally exports each `CollisionShape3D` as a low-poly stand-in mesh — the same debug shape the Godot editor itself uses to visualize collision shapes — since a collider has no visual representation of its own and would otherwise be silently absent from the export entirely. Verified against real Godot 4.4 projects (including an inherited-scene/skeletal-mesh case) before shipping, not assumed from documentation alone. Same command available headlessly via the CLI's `godot-extract <staged-folder>` (`--no-colliders` to skip the collider stand-ins).

**Menu structure: Edit is selection-editing only; Tools is everything that transforms, exports, or maintains rather than edits.** These grew feature-by-feature into one crowded Edit menu; split apart so Edit doesn't become a junk drawer:

- **Edit > Select All** (Ctrl+A) and **Edit > Move Selected to Trash** (Delete key) — pure catalogue-editing, scoped to the current grid selection. Moving to Trash is reversible (see above); nothing is actually deleted until confirmed in **Tools > View Trash...**.
- **Tools > Extract Godot Scenes to GLB...** (described above) — a pre-ingest step, not selection-scoped like the rest of this menu.
- **Tools > Convert Selected to glTF (.glb)...** — the menu-bar equivalent of the grid's right-click Convert action (below), for when you don't want to right-click: converts whatever's selected and eligible (model assets not already `.glb`; anything else in the selection is silently skipped, same as the CLI's multi `--asset-id`), dispatching to the single- or batch-conversion path depending on how many end up eligible.
- **Tools > Convert All Flagged to glTF (.glb)...** — the bulk counterpart to the ⚠ badge (see "Favorites" above): converts every `needs_glb_conversion` asset library-wide, ignoring the current filter/selection entirely, after a confirmation naming the count. Same as the CLI's `convert flagged`.
- **Tools > Export Selected to Project...** copies whatever's selected out to a target project — the UI counterpart to the CLI's `export`, operating on the current grid selection instead of a `--pack`/`--type`/`--tag` filter. The dialog asks for a project folder (pre-filled with the most recently used one, if any — always editable) and destination subfolder (default `exported_assets`). For repeat exports to the same project, the detail panel's **Export** button (described above) skips this dialog entirely.
- **Tools > Tag Pack...** (described above) and **Tools > Clean Up Pre-Conversion Assets...** — confirms every pending glTF conversion at once (see "Converting models to glTF" above), permanently deleting each one's pre-conversion original (staging + library copy) while keeping the converted `.glb`, after a confirmation dialog stating the count. This is the bulk counterpart to the per-asset Revert/Delete buttons in the detail panel; it's a no-op with an info message if nothing is pending.
- **Tools > Generate Credits Report...** opens a dialog with a live preview of the attribution report described in "Credits report" above — a project-folder field (blank for the whole catalogue) plus Generate/Save As, so you can check it before it ends up in a real credits screen.
- **Tools > Library Statistics...** shows a read-only snapshot of the library's size and composition: total assets/size across all packs, favorited count, breakdowns by asset type and thumbnail status, and the largest packs by size. Same report as the CLI's `stats` command (see below).
- **Tools > View Trash...** opens the Trash review dialog: a table of everything currently trashed (pack, filename, when it was trashed), with per-row **Restore Selected** / **Delete Selected Permanently**, plus a bulk **Empty Trash (Delete All Permanently)**. Restoring needs no confirmation (it's just clearing a flag); the two permanent-delete actions do, since — unlike moving to Trash — they're the real, unrecoverable delete (catalogue entry, thumbnail, and archived library copy, same as `remove`/`Remove Pack`). The dialog closes itself once Trash is empty.
- **Tools > Check Library Integrity...** scans for drift between the catalogue's records and reality on disk: an archived library copy that went missing, a thumbnail file gone despite `thumbnail_status` saying `done`, or a staging source that's disappeared since ingest (informational only — many people clean up staging on purpose once a pack's archived, so this one's just a heads-up that a future re-render/re-convert would need that file back). Trashed assets are skipped, since their files not being touched is the whole point of Trash. Two one-click fixes are offered for the rows they apply to: **Reset Selected Thumbnail Status** (marks a broken-thumbnail row `pending` again so the next thumbnail pass re-renders it) and **Re-archive Selected from Staging** (re-copies a missing library copy from staging, if it's still there). An asset that's lost both its staging source and its library copy has no automatic fix — Trash or Remove is the honest next step, done manually from the grid. Same checks (and the `--fix` flag for both automatic fixes) are available via the CLI's `check` command.

**A pending-conversions reminder lives in the status bar** whenever at least one exists (`⚠ N pending conversions -- click to review`) — a pending conversion is easy to forget about otherwise, since nothing else in the UI surfaces it unless you're already looking at that specific asset. Clicking it opens a **Pending Conversions** review dialog: a real table (pack, original filename, converted-to filename, when it was converted) rather than a bare "delete N originals?" prompt with no more detail than the badge itself already gave. Select one or more rows to **Revert** (restore the pre-conversion original, discard the `.glb`) or **Keep** (permanently delete just those originals, keep their `.glb`s) just those assets, or use **Keep All** for the same bulk action **Tools > Clean Up Pre-Conversion Assets...** already offered. The dialog refreshes itself after every action and closes on its own once nothing is left to review.

**Right-click the grid** for a context menu scoped to whatever's selected — right-clicking outside the current selection switches to just that item first, same convention as a normal file manager. One asset selected: Show in Library Folder (disabled if it hasn't been archived), a **☆ Add to Favorites**/**★ Remove from Favorites** toggle, **Regenerate Thumbnail** (offered for any thumbnail-capable type — `texture`/`audio`/`model`, not `other`), **3D Preview (Orbit/Zoom)...** for any `model` asset — renders it on the spot first if it isn't cached yet (see "Interactive 3D preview" below), **Convert to glTF (.glb)...** (only offered for a model asset that isn't already `.glb` and has no conversion already pending), **Export to Project...**, and **Move to Trash**. Multiple selected: bulk **★ Add N to Favorites**/**☆ Remove N from Favorites**, **Regenerate N Thumbnail(s)** (the label's count is however many of the selection are thumbnail-capable; anything else is silently left out), **Render N 3D Preview(s)** (pre-warms the preview cache for however many selected assets are models, without opening a viewer; ones already cached are skipped), **Convert N to glTF (.glb)...** (only offered if at least one selected asset is an eligible model — the label's count is the eligible subset; anything not a model, or already `.glb`, is silently left out of the batch, same as the CLI's multi `--asset-id`), **Export N to Project...**, and **Move N to Trash**.

Unlike the detail panel's **Generate Thumbnail** button (which only appears once, for an asset whose thumbnail isn't `done` yet), **Regenerate Thumbnail(s)** always re-renders regardless of current status — the one to reach for when a thumbnail is already `done` but looks wrong (a bad render, or after a resolution change) rather than actually missing.

**Favorites** are a personal flag independent of tags — tags are for categorization, favorites are for "I liked this specific one" out of a big purchased-pack backlog. A favorited asset shows a ★ prefix on its filename in the grid. The detail panel's single-select view also has its own **☆ Add to Favorites**/**★ Favorited** toggle button, and the filter panel has a **★ Favorites only** checkbox that narrows the grid down to just favorited assets (combines with every other filter, same AND semantics as the rest of the filter panel).

**A ⚠ prefix (alongside ★, if both apply) marks an asset with `needs_glb_conversion` set** — see "Converting models to glTF" above for what that means and why it's tracked. Hovering the thumbnail explains it in the tooltip. The filter panel's **⚠ Needs conversion only** checkbox narrows the grid to just these (same AND semantics as every other filter), and **Tools > Convert All Flagged to glTF (.glb)...** converts every flagged asset library-wide in one batch — not just whatever's currently filtered or selected — after a confirmation naming the count, reusing the same conversion-plus-thumbnail-regen path as **Convert Selected**.

**"Move to Trash" doesn't delete anything** — it just hides the selection from the normal grid (`assets.deleted_at` set); the catalogue rows, thumbnails, and archived library copy are all left exactly as they are, and nothing is actually removed until you say so explicitly in **Tools > View Trash...** (below). No confirmation prompt is needed for moving to Trash itself, since it's fully reversible.

**Right-click a pack in the Pack list** for **Edit Pack Metadata...** (name, creator, licence, source URL, a 1-5 star **Rating**, personal **Notes**, and render corrections all in one dialog — the same fields as `pack set-metadata`/`rename`/`set-corrections`/`notes` combined, pre-filled with the pack's current values; renaming moves its archived library folder to match, same as the CLI), **Render 3D Previews for Pack (N)** (only shown if the pack has any model assets — pre-warms the interactive-preview cache for every one of them, skipping ones already cached; see "Interactive 3D preview" above), and **Remove Pack '\<name\>'...** (deletes every asset in the pack, the pack entry itself, and its whole archived library folder, after a confirmation naming the asset count — the UI counterpart to `pack remove`; this is a real, immediate delete, not Trash). Right-clicking "All packs" shows no menu, since it isn't a real pack.

Rating and notes are personal, opinion-not-fact fields (unlike creator/licence/source URL) — most useful for remembering, months later, which of a huge purchased-pack backlog is actually worth reusing. The detail panel shows a selected asset's pack rating as ★★★☆☆-style stars right on the **Pack:** line, and its notes as a tooltip on that same line (hover to read).

**Right-click a tag in the Tags list** for **Edit Tag...** (rename and/or re-category — updates every asset carrying it at once) and **Delete Tag '\<name\>'...** (removes it from the vocabulary and from every asset carrying it, after a confirmation naming the usage count) — the UI counterparts to `tag rename`/`tag delete`. Right-clicking "All tags" shows no menu.

Editing any of the above updates the affected filter lists (and tag usage counts / the Format list, where relevant) in place, without resetting whatever else you had filtered to.

**What's intentionally *not* editable from the app:** fields the catalogue derives from the real file on disk (`relative_path`, `filename`, `extension`, `content_hash`, `file_size`, `asset_type`, `thumbnail_status`) — hand-editing those would desync the database from reality; the correct way to change them is to actually change the file (re-ingest, or **Convert to glTF**), not overwrite the record. Export history (`exports`) is an audit log, not metadata — it's meant to answer "what happened," so it's shown (`exports` / `list --unused`) but not edited; it's still cleared automatically for an asset that gets removed, same as its tags.

Thumbnails now render automatically as part of ingest (dispatched by type — see "Thumbnails are generated automatically" above), so **Thumbnails > Generate 2D Thumbnails** / **Generate Audio Thumbnails** / **Generate 3D Thumbnails via Blender** are for the manual cases: re-rendering after `--force`-worthy changes, previewing per-pack corrections, or catching a pack ingested before this existed. They run against whatever pack is currently selected in the filter panel (or all packs, if "All packs" is selected), on a background thread — the window stays responsive while Blender works.

### Background jobs show a live progress feed

Every background job (ingest, exporting, removing, converting, and all three thumbnail-generation commands) shows a small modal window with an indeterminate progress bar and a live-appending text log underneath it, describing what's happening to which file as it happens (e.g. "Hashing dragon.fbx...", "Archiving dragon.fbx to library...", "Rendered thumbnail for dragon.fbx (3/12)") rather than just a bare spinner — useful for seeing that a long-running job (especially Blender-based ones) is actually making progress, and roughly how far along it is, rather than wondering whether it's hung.

Reads and writes through `src/asset_catalogue/catalogue.py`'s `Catalogue` class, not the filesystem, raw SQL, or `ingest`/`thumbnails`/`blender_render` directly, per the seed doc's architecture rule (§3). Ingest and thumbnail generation from the UI go through `Catalogue.*_bg()` methods, which each open and close their own SQLite connection rather than sharing the main one — SQLite connections aren't safe to share across threads, and these run on a background `QThread`. The CLI's `list`/`tags` commands still use their own direct queries (they predate this layer and aren't part of the seed's UI-facing architecture), so if CLI and UI query behavior ever need to match exactly, that's the one place they currently diverge.

## Updates

The app checks quietly in the background on launch for a newer version, and **Help > Check for Updates...** runs the same check on demand (always reporting a real outcome — "you're up to date" or an actual error — rather than staying silent). The check compares this build's own version (`src/asset_catalogue/version.py`, the single source of truth `pyproject.toml` also reads from) against GitHub's latest published release for this repo.

When a newer version exists, a dialog offers **Download && Install Update** (packaged `.exe` builds only — see below), **Open Release Page** (opens it in your browser to download manually), **Skip This Version** (remembered in settings — the automatic background check won't mention that specific version again, though a manual check still always reports the real current state), or **Remind Me Later** (does nothing, asks again next launch). The background check fails silently on any error (no network, GitHub unreachable, etc.) — only the manual check surfaces a failure, since a launch-time network hiccup shouldn't interrupt someone who's just trying to catalogue assets.

**One-click download and install** (`src/asset_catalogue/self_update.py`): a running `.exe` can't overwrite its own files on Windows, so this can't just download over the current install in place. Instead, clicking the button downloads the release's `.zip` asset with a live progress bar, extracts it to a temp folder, then hands off to a detached PowerShell script and exits. That script waits for this process to actually exit (by PID), renames the current install folder to `<install>.old`, moves the freshly-extracted build into its place, relaunches the new `.exe`, and only then deletes the `.old` backup — if the swap fails partway (e.g. a file still locked), the backup is renamed back into place instead of being left half-swapped, and the user is shown a message box pointing them at the release page instead of being left with a broken install. This only ever appears for packaged builds (`self_update.is_frozen()`) — a dev-mode run has no single install folder to replace, so it always falls back to just the release-page/skip/remind-later choices.

This relaunch script is the one piece of this feature that can never run under pytest (it only executes as a detached process after this app has already exited), so it's been verified with a real dry run instead: copying an actual packaged `dist/AssetCatalogue` build to stand in for both the "old" and "new" install, running the real script end to end, and confirming via `tasklist` that the relaunched real `.exe` came up afterward. That dry run caught a real bug — PowerShell requires a line-continuation `+` at the *end* of the preceding line, not the start of the next one; getting it backwards silently fails to parse the *entire* script, including the unrelated wait/rename/move logic above it.

**Cutting a release:** bump `__version__` in `src/asset_catalogue/version.py`, commit, tag it (`git tag vX.Y.Z`), push the tag, then publish an actual GitHub Release for that tag with the built `dist/AssetCatalogue` folder (zipped) attached — the release object is what the update check actually queries (both for the version number and, now, the `.zip` asset the one-click installer downloads), a pushed tag alone isn't enough.

## Development setup

For running from source, using the CLI, or contributing:

```
python -m venv .venv
.venv\Scripts\pip install -e .
```

Configure the staging folder (where unprocessed packs land, required before `ingest`) and the library folder (where the catalogue lives, required before almost everything else):

```
asset-catalogue settings set --staging-folder "D:\path\to\staging" --library-folder "D:\path\to\library"
asset-catalogue settings show
```

Settings live in `settings.json` at the repo root when running from source (gitignored — it's a local machine path, different per install; the packaged `.exe` keeps its own copy under `%APPDATA%\AssetCatalogue\` instead). The **library folder** is the portable part: it holds `catalogue.db` and `thumbnails/`, derived automatically from `--library-folder` (there's no separate way to configure the DB or thumbnail paths — they always live together, so the whole catalogue moves as one unit). To move the library to another machine, or share it over a network drive, just copy the folder and point `--library-folder` at the copy — `settings set` doesn't need to know anything else about it, and an existing `catalogue.db` found there is picked up as-is, no import step. The staging folder, by contrast, is *not* meant to travel with the library — it's wherever unprocessed packs happen to sit on this particular machine before `ingest`.

## Testing

```
pip install -e ".[dev]"
pytest
```

A `tests/` suite covers the backend modules (`db`, `settings`, `archives`, `ingest`, `tagging`, `packs`, `library_assets`, `thumbnails`, `audio_thumbnails`, `removal`, `exporting`, `conversion`, `credits`) against real temp SQLite databases and real files on disk — not mocked filesystem calls — plus the `Catalogue` facade and a couple of its `_bg()` wrappers, and one smoke test confirming `MainWindow` still constructs without error. Nothing here touches the real `settings.json` or a real library `catalogue.db`; every test uses `tmp_path` and, where a test needs `settings.load()`/`save()`, monkeypatches `settings.SETTINGS_PATH` to a temp file first. Blender-dependent code paths (actually rendering a model thumbnail or converting one) aren't covered here — those still rely on the scripted-interaction verification approach described throughout this file's history, since they need a real Blender install to mean anything.

## Status

Build order from the seed doc, tracked here:

- [x] Schema + ingest
- [x] Tagging (pack cascade + per-file tags, CLI)
- [x] 2D thumbnails (Pillow)
- [x] Blender thumbnails
- [x] Qt UI (filter panel, thumbnail grid, tagging panel)
- [x] Per-pack calibration
- [x] Export + tracking

All seven build-order steps from the seed doc are now done, and every one of them (ingest, tagging, calibration, and now export) is also reachable from the UI, not just the CLI — the UI is a full front end, not just a browsing/tagging viewer.

## License

GPLv3 (GNU General Public License v3.0) — see [LICENSE](LICENSE). Free to use, modify, and redistribute; any redistributed modified version must also be licensed under the GPL and made available in source form, which is what keeps a repackaged/closed-source resale off the table.
