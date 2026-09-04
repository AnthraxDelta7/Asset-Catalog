# Asset Catalogue

Standalone tool for cataloguing, tagging, previewing and importing game assets. Design rationale lives in [asset-catalogue-seed.md](asset-catalogue-seed.md); this file tracks day-to-day usage as commands land.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -e .
```

Configure the staging folder (where unprocessed packs land, required before `ingest`) and the library folder (where the catalogue lives, required before almost everything else):

```
asset-catalogue settings set --staging-folder "D:\path\to\staging" --library-folder "D:\path\to\library"
asset-catalogue settings show
```

Settings live in `settings.json` at the repo root (gitignored — it's a local machine path, different per install). The **library folder** is the portable part: it holds `catalogue.db` and `thumbnails/`, derived automatically from `--library-folder` (there's no separate way to configure the DB or thumbnail paths — they always live together, so the whole catalogue moves as one unit). To move the library to another machine, or share it over a network drive, just copy the folder and point `--library-folder` at the copy — `settings set` doesn't need to know anything else about it, and an existing `catalogue.db` found there is picked up as-is, no import step. The staging folder, by contrast, is *not* meant to travel with the library — it's wherever unprocessed packs happen to sit on this particular machine before `ingest`.

## Ingest

A pack is a subfolder of the staging folder. `--pack-name` identifies the pack in the catalogue (re-using an existing name reuses that pack rather than duplicating it, so re-ingesting after adding files to a pack is safe):

```
asset-catalogue ingest <pack_folder_name> --pack-name "Pack Name" --creator "Creator" --licence "CC0" [--source-url URL]
```

Every file is SHA-256 hashed. Identical content — whether re-scanning the same pack or the same file shipped in two different packs — is recognized and skipped, never duplicated.

**Quality control:** this catalogue is for raw 3D/2D/audio content, not game-engine project data. A pack that bundles Unity or Unreal project files alongside the real content (`.meta` sidecars, `.prefab`/`.unity`/`.uasset`/`.uproject`, `.cs` scripts, etc. — see `ENGINE_PROJECT_EXTENSIONS` in `ingest.py`) has those skipped automatically, not catalogued as generic "other" assets. Whole engine build/cache folders (`Library/`, `ProjectSettings/`, `Binaries/`, `Intermediate/`, `Saved/`, `.git/`, etc.) are skipped entirely — never walked into at all. The ingest summary reports how many files/folders were skipped this way when it's non-zero.

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

Renders a 256x256 PNG for every `texture`-type asset, named after its content hash — so identical content, wherever it's re-encountered, never gets rendered twice:

```
asset-catalogue thumbnail generate [--pack "Pack Name"] [--force]
```

Assets are retried on the next run if they previously failed (unreadable/corrupt file), but skipped once `thumbnail_status` is `done` — pass `--force` to re-render everything regardless of status. Model (3D) assets are untouched here; they wait on Blender integration (build order step 4).

## Thumbnails (audio)

```
asset-catalogue thumbnail generate-audio [--pack "Pack Name"] [--force]
```

Same content-hash naming, same retry/`--force`/skip-if-done semantics as the 2D path above. `.wav` decodes with Python's stdlib `wave` module (zero new dependencies) and gets a real rendered waveform — peak amplitude per column, drawn as vertical bars, first channel only for multi-channel audio. `.mp3`/`.ogg`/`.flac` have no stdlib decoder; rather than add a decoding dependency (and, for mp3, likely a separate ffmpeg install) just for a thumbnail, they get a plain "audio file" placeholder icon instead — still visually distinct in the grid from other asset types, without pretending to show real waveform data it didn't actually decode. Revisit if real waveforms for those formats ever becomes worth a new dependency.

## Thumbnails (3D / Blender)

Requires Blender 4.0+ (auto-detected in the standard Windows install locations, or point at it explicitly):

```
asset-catalogue settings set --blender-path "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
asset-catalogue thumbnail generate-models [--pack "Pack Name"] [--force]
```

One Blender process handles the whole batch (startup dominates the per-asset cost, so this matters). Supports OBJ, FBX, GLTF/GLB, STL, and `.blend` (via library append). Rendered at 256x256 with EEVEE, a single sun light, dark neutral background, framed to each asset's bounding box. An asset that imports but produces zero mesh objects (or errors outright) is marked `failed` rather than saving a blank thumbnail, and is retried on the next run — same failed/done semantics as the 2D path above.

Per-pack corrections (`up_axis: "Y_UP"`, `scale`, `material_fallback: true`) are read from `packs.corrections` and applied after import, before framing.

## Per-pack calibration

Corrections are set once per pack and apply to every asset in it, since a pack that's rotated or scaled wrong is wrong the same way throughout (seed doc §7). A brand-new pack with model assets already gets this workflow's first step done automatically at ingest — see "Thumbnails are generated automatically" above:

```
asset-catalogue pack set-corrections <pack_name> [--up-axis Y_UP|Z_UP] [--scale 1.0] [--material-fallback | --no-material-fallback]
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

## Search (CLI)

```
asset-catalogue list [--pack "Pack Name"] [--type model|texture|audio|other] [--tag tag_name] [--format fbx] [--search text] [--unused]
```

`--unused` shows assets that have never been imported into any project. `--format` filters by file extension (with or without the leading dot — `fbx` and `.fbx` are equivalent). `--search` matches anywhere in the filename, case-insensitive; a literal `%` or `_` in the search text is matched literally, not treated as a SQL wildcard.

## Removing assets

Removes one, many, or all assets from the catalogue — the database row, tags, import history, rendered thumbnail, and any archived library copy. **Never touches the original files in the staging folder**; those stay exactly as shipped, same as everywhere else in this tool. Requires a filter (or `--all`) for the same reason `import` does — an easy fat-finger away from wiping the whole catalogue otherwise:

```
asset-catalogue remove --asset-id <id> [--asset-id <id> ...] | --pack "Pack Name" | --type texture | --tag tag_name | --all [--yes]
```

Prints what's about to be removed and asks for confirmation unless `--yes` is passed.

## Import

Copies selected assets into a target project, rebuilding each asset's relative folder structure under a per-pack subfolder (default `imported_assets/<Pack Name>/...`, not dumped flat) rather than symlinking — see the design note below for why. Requires at least one selection filter, or `--all` to import the entire catalogue on purpose (a bare `import <project>` with nothing else is refused, since it's an easy fat-finger away from copying everything):

```
asset-catalogue import <project_root> [--pack "Pack Name"] [--type texture] [--tag tag_name] [--asset-id ID] [--all] [--dest-subfolder imported_assets]
```

If `<project_root>` contains a `project.godot`, that's reported; if not, the tool proceeds anyway (Godot is one possible destination, not the only one this tool targets). Every import is recorded — asset, target project (by resolved absolute path), and timestamp — which is what makes these queries possible:

```
asset-catalogue imports [--project <project_root>] [--asset-id ID]
```

`imports` with no filter is the full history; `--project` shows what's already in one project; `--asset-id` shows every project a given asset has been imported into.

**Design note (copy vs symlink):** copy was chosen deliberately over symlink — simpler, safer, and avoids Windows' symlink creation normally needing Developer Mode or admin rights. The trade-off is real: a symlinked project would auto-pick-up pack updates and use less disk, but at the cost of the project silently breaking if the library folder isn't present at build/package time. Not revisited unless it becomes a real pain point.

### Godot export (optional, off by default)

`asset-catalogue settings set` has no Godot-specific flags — this lives only in the UI's Settings dialog, since it's a UI convenience, not a new import mechanism (the CLI's `import` above already works against any project folder, Godot or not, and is unaffected by this toggle either way). **Off by default**, so cataloguing raw STLs or feeding a different pipeline never shows any Godot-specific UI. Turning it on in Settings adds exactly one behavior: the *first* Import remembers whatever project folder you picked (there, or in the Import dialog itself), and every later Import reuses it automatically instead of asking again — until you clear or change it in Settings. Turning it back off (or clearing the remembered path) goes right back to a plain per-import folder picker.

## UI

```
asset-catalogue-ui
```

No CLI setup required first. A library folder is required above everything else — the app checks on startup and won't open the main window without one, looping back to a **Settings** dialog (folder/file browse buttons, Blender "Auto-detect") until either a library actually opens or you quit. A path that's merely unconfigured is prompted for silently; a path that's configured but broken (bad permissions, points at a file, etc.) shows the actual error in the dialog rather than crashing. The window title always shows which library is currently open.

**File > Settings...** reopens that same dialog anytime, for staging folder / library folder / Blender path, plus **Enable Godot export** and its remembered project folder (see "Godot export" above) — the project-folder row is only enabled while the checkbox is checked. **Leaving Blender path blank is a valid, working choice** (it's re-detected on demand every time it's actually needed, never cached in settings) but used to just look unconfigured with nothing in the field — it now shows the auto-detected path as greyed placeholder text when one is found, so a blank field reads as "auto-detecting, and here's what it found" rather than "nothing set up." **File > Switch Library...** is the faster, more deliberate path for just swapping libraries (a personal one vs. a shared one, say) — it browses directly to a folder and confirms before switching, telling you plainly whether it found an *existing* library there or would be creating a *new, empty* one, so a wrong folder pick doesn't silently start a fresh catalogue by accident. Either path live-switches the whole catalogue (closes the old DB connection, opens the new one, rebuilds the pack/tag/type lists) without restarting the app.

Filter panel (search / type / format / pack / tag) on the left, a thumbnail grid in the middle, and a tagging panel at the bottom for the selected asset. **Search** filters live as you type, matching anywhere in the filename (case-insensitive) — same matching as the CLI's `list --search`. **Format** lists whatever file extensions actually exist in the catalogue right now (e.g. `FBX`, `GLB`, `PNG`) — it updates immediately after a conversion changes an asset's extension, without resetting the other filters. The grid supports multi-select (Ctrl/Shift-click, or **Edit > Select All** / Ctrl+A):

- **Exactly one asset selected:** the filename as the title, a **Pack: `<name>`** line right below it, full tag editing (add/remove, with autocomplete against the existing tag vocabulary — typing "fi" suggests anything containing it, not just tags starting with it — so a typo doesn't quietly create a near-duplicate tag alongside the one you meant to reuse), and a **Show in Library Folder** button that opens the asset's archived copy directly in Explorer — enabled once it's actually in the library (which happens automatically at ingest, not tagging), disabled otherwise (e.g. its source file went missing before ingest could copy it). If that asset has a pending glTF conversion (see below), **Revert Conversion** and **Delete Pre-Conversion Original** buttons also appear here — the panel is where you review a converted `.glb` next to its regenerated thumbnail and decide whether to keep it.
- **Two or more selected:** the panel switches to an "N assets selected" state — adding a tag applies it to every selected asset at once (as one background job, with a progress dialog). The tag list shows only tags common to *every* selected asset (the intersection, not the union of everyone's tags) — removing one of those is unambiguous, so it's enabled here too, taking that tag off all of them at once. If nothing is common to the whole selection, the list is empty and removal is disabled (there's nothing shared to remove). The **Pack:** line shows up here too, but only if every selected asset happens to share the same pack — otherwise it's blank, same "only show what's actually common" rule as the tag list.

**The Pack: line is a link** — clicking the pack name filters the grid down to just that pack (selects it in the Pack list on the left), a quick way to jump from "this one asset looks off" to "let me see the whole pack it came from."

**Tools > Tag Pack...** cascades a tag onto an entire pack from a dropdown (defaults to whatever pack is currently filtered), same as the CLI's `tag pack` — the third way to select "how much" to tag, alongside one asset and a multi-selection.

**Ingest Pack...** is a toolbar button, not tucked in a menu — ingest is the most common action, so it's the first thing visible under the menu bar. One **Browse Folder/Zip...** button opens a small custom browser scoped to the staging folder, listing subfolders *and* `.zip` files side by side — either one double-clickable as a pack source (a `.zip` picked this way is auto-extracted at ingest time, same as `ingest`'s zip auto-detection on the CLI). Single-clicking an entry to highlight it, then pressing **Select**, works too, for either a folder or a `.zip` — picking that entry directly without entering it (a folder this way, not its contents); with nothing highlighted, Select picks whatever folder you're currently browsing. A custom browser rather than a native picker is a real Qt/OS limitation, not a design choice: a native picker is either a folder picker or a file picker, never both — there's no stock dialog that shows folders and files together and lets either be the result. Picking a source refreshes the suggested **Pack name** to match it, unless you've typed your own name first — re-picking a *different* source no longer leaves a stale name behind (a real bug, now fixed: it used to only fill the field once, the first time it was empty, so choosing another source afterward silently kept whatever was already there).

Once accepted, ingest runs as a single background job (which also archives every ingested asset into the library, reported in the completion message), and re-ingesting an existing pack with different metadata only updates the fields that changed (same delta behavior as the CLI).

A separate **Browse Zip...** option (for a `.zip` living *outside* the staging folder, e.g. still in Downloads) used to exist alongside Browse Folder/Zip; it's been removed as a rarely-needed extra option in the UI — the CLI's `ingest-zip <path>` still covers that exact case (extracts anywhere on disk into staging, then ingests), it just isn't duplicated in the ingest dialog anymore.

**Menu structure: Edit is selection-editing only; Tools is everything that transforms, exports, or maintains rather than edits.** These grew feature-by-feature into one crowded Edit menu; split apart so Edit doesn't become a junk drawer:

- **Edit > Select All** (Ctrl+A) and **Edit > Remove Selected...** (Delete key) — pure catalogue-editing, scoped to the current grid selection. Remove deletes the catalogue database rows, thumbnails, and any archived library copy for whatever's selected — one, many, or all of it; a confirmation dialog says so explicitly before anything happens, since it's easy to forget that "remove from library" doesn't touch the actual files sitting in your staging folder.
- **Tools > Convert Selected to glTF (.glb)...** — the menu-bar equivalent of the grid's right-click Convert action (below), for when you don't want to right-click: converts whatever's selected and eligible (model assets not already `.glb`; anything else in the selection is silently skipped, same as the CLI's multi `--asset-id`), dispatching to the single- or batch-conversion path depending on how many end up eligible.
- **Tools > Import Selected to Project...** copies whatever's selected into a target project — the UI counterpart to the CLI's `import`, operating on the current grid selection instead of a `--pack`/`--type`/`--tag` filter. The dialog asks for a project folder and destination subfolder (default `imported_assets`), *unless* Godot export is enabled and a project is already remembered, in which case the folder field shows up locked to that path (change it in Settings instead) — see "Godot export" above.
- **Tools > Tag Pack...** (described above) and **Tools > Clean Up Pre-Conversion Assets...** — confirms every pending glTF conversion at once (see "Converting models to glTF" above), permanently deleting each one's pre-conversion original (staging + library copy) while keeping the converted `.glb`, after a confirmation dialog stating the count. This is the bulk counterpart to the per-asset Revert/Delete buttons in the detail panel; it's a no-op with an info message if nothing is pending.

**Right-click the grid** for a context menu scoped to whatever's selected — right-clicking outside the current selection switches to just that item first, same convention as a normal file manager. One asset selected: Show in Library Folder (disabled if it hasn't been archived), **Convert to glTF (.glb)...** (only offered for a model asset that isn't already `.glb` and has no conversion already pending), **Import to Project...**, and Delete from Library. Multiple selected: **Convert N to glTF (.glb)...** (only offered if at least one selected asset is an eligible model — the label's count is the eligible subset; anything not a model, or already `.glb`, is silently left out of the batch, same as the CLI's multi `--asset-id`), **Import N to Project...**, and Delete (labeled with the total count).

**Right-click a pack in the Pack list** for **Edit Pack Metadata...** (name, creator, licence, source URL, and render corrections all in one dialog — the same fields as `pack set-metadata`/`rename`/`set-corrections` combined, pre-filled with the pack's current values; renaming moves its archived library folder to match, same as the CLI) and **Remove Pack '\<name\>'...** (deletes every asset in the pack, the pack entry itself, and its whole archived library folder, after a confirmation naming the asset count — the UI counterpart to `pack remove`). Right-clicking "All packs" shows no menu, since it isn't a real pack.

**Right-click a tag in the Tags list** for **Edit Tag...** (rename and/or re-category — updates every asset carrying it at once) and **Delete Tag '\<name\>'...** (removes it from the vocabulary and from every asset carrying it, after a confirmation naming the usage count) — the UI counterparts to `tag rename`/`tag delete`. Right-clicking "All tags" shows no menu.

Editing any of the above updates the affected filter lists (and tag usage counts / the Format list, where relevant) in place, without resetting whatever else you had filtered to.

**What's intentionally *not* editable from the app:** fields the catalogue derives from the real file on disk (`relative_path`, `filename`, `extension`, `content_hash`, `file_size`, `asset_type`, `thumbnail_status`) — hand-editing those would desync the database from reality; the correct way to change them is to actually change the file (re-ingest, or **Convert to glTF**), not overwrite the record. Import history (`imports`) is an audit log, not metadata — it's meant to answer "what happened," so it's shown (`imports` / `list --unused`) but not edited; it's still cleared automatically for an asset that gets removed, same as its tags.

Thumbnails now render automatically as part of ingest (dispatched by type — see "Thumbnails are generated automatically" above), so **Thumbnails > Generate 2D Thumbnails** / **Generate Audio Thumbnails** / **Generate 3D Thumbnails via Blender** are for the manual cases: re-rendering after `--force`-worthy changes, previewing per-pack corrections, or catching a pack ingested before this existed. They run against whatever pack is currently selected in the filter panel (or all packs, if "All packs" is selected), on a background thread — the window stays responsive while Blender works — with a progress dialog until done.

Reads and writes through `src/asset_catalogue/catalogue.py`'s `Catalogue` class, not the filesystem, raw SQL, or `ingest`/`thumbnails`/`blender_render` directly, per the seed doc's architecture rule (§3). Ingest and thumbnail generation from the UI go through `Catalogue.*_bg()` methods, which each open and close their own SQLite connection rather than sharing the main one — SQLite connections aren't safe to share across threads, and these run on a background `QThread`. The CLI's `list`/`tags` commands still use their own direct queries (they predate this layer and aren't part of the seed's UI-facing architecture), so if CLI and UI query behavior ever need to match exactly, that's the one place they currently diverge.

## Status

Build order from the seed doc, tracked here:

- [x] Schema + ingest
- [x] Tagging (pack cascade + per-file tags, CLI)
- [x] 2D thumbnails (Pillow)
- [x] Blender thumbnails
- [x] Qt UI (filter panel, thumbnail grid, tagging panel)
- [x] Per-pack calibration
- [x] Import + tracking

All seven build-order steps from the seed doc are now done, and every one of them (ingest, tagging, calibration, and now import) is also reachable from the UI, not just the CLI — the UI is a full front end, not just a browsing/tagging viewer.
