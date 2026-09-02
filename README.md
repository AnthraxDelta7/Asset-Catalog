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

**Re-ingesting an existing pack with different metadata updates only what changed** (the delta) — e.g. re-running with a corrected `--creator` updates just that field; a `--licence` you already had recorded and don't re-specify this time is left alone, never blanked out. Only fields you actually pass and that differ from what's stored get overwritten.

If a pack is a `.zip` (the common case for purchased packs) and it's already sitting inside the staging folder, plain `ingest` handles it transparently — no separate step:

```
asset-catalogue ingest <pack_name.zip> --pack-name "Pack Name" --creator "Creator" --licence "CC0"
```

If the given path resolves to a `.zip` file rather than a folder, it's extracted first (into a same-named sibling folder) and then ingested normally, same as any other pack.

For a zip that lives *outside* the staging folder (e.g. still in your Downloads folder), use `ingest-zip` instead, which brings it in:

```
asset-catalogue ingest-zip <path\to\pack.zip> --pack-name "Pack Name" [--pack-folder folder_name] --creator "Creator" --licence "CC0"
```

`<path\to\pack.zip>` can be anywhere on disk — it's extracted into a new subfolder of the staging folder (named after the zip file by default, or `--pack-folder`) and then ingested normally. Both paths share the same extraction logic: refuses to extract into a destination that already has files in it (won't silently merge/overwrite an existing pack), and rejects any archive entry whose path would land outside the destination folder (zip-slip protection — this processes archives from arbitrary purchased packs, so that's a real risk worth guarding against, not just a theoretical one).

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

**Known limitation:** there's no way yet to explicitly exclude an asset from a pack-level tag — `untag asset` removes the tag, but re-running `tag pack` will re-apply it on the next cascade, since the schema only tracks *how* a tag was applied (inherited/explicit), not "explicitly excluded." Revisit if this comes up in practice.

## Thumbnails (2D)

Renders a 256x256 PNG for every `texture`-type asset, named after its content hash — so identical content, wherever it's re-encountered, never gets rendered twice:

```
asset-catalogue thumbnail generate [--pack "Pack Name"] [--force]
```

Assets are retried on the next run if they previously failed (unreadable/corrupt file), but skipped once `thumbnail_status` is `done` — pass `--force` to re-render everything regardless of status. Model (3D) assets are untouched here; they wait on Blender integration (build order step 4).

## Thumbnails (3D / Blender)

Requires Blender 4.0+ (auto-detected in the standard Windows install locations, or point at it explicitly):

```
asset-catalogue settings set --blender-path "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
asset-catalogue thumbnail generate-models [--pack "Pack Name"] [--force]
```

One Blender process handles the whole batch (startup dominates the per-asset cost, so this matters). Supports OBJ, FBX, GLTF/GLB, STL, and `.blend` (via library append). Rendered at 256x256 with EEVEE, a single sun light, dark neutral background, framed to each asset's bounding box. An asset that imports but produces zero mesh objects (or errors outright) is marked `failed` rather than saving a blank thumbnail, and is retried on the next run — same failed/done semantics as the 2D path above.

Per-pack corrections (`up_axis: "Y_UP"`, `scale`, `material_fallback: true`) are read from `packs.corrections` and applied after import, before framing.

## Per-pack calibration

Corrections are set once per pack and apply to every asset in it, since a pack that's rotated or scaled wrong is wrong the same way throughout (seed doc §7):

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

## Search (CLI)

```
asset-catalogue list [--pack "Pack Name"] [--type model|texture|audio|other] [--tag tag_name] [--unused]
```

`--unused` shows assets that have never been imported into any project.

## Removing assets

Removes one, many, or all assets from the catalogue — the database row, tags, import history, and rendered thumbnail. **Never touches the original files in the staging folder**; those stay exactly as shipped, same as everywhere else in this tool. Requires a filter (or `--all`) for the same reason `import` does — an easy fat-finger away from wiping the whole catalogue otherwise:

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

## UI

```
asset-catalogue-ui
```

No CLI setup required first. A library folder is required above everything else — the app checks on startup and won't open the main window without one, looping back to a **Settings** dialog (folder/file browse buttons, Blender "Auto-detect") until either a library actually opens or you quit. A path that's merely unconfigured is prompted for silently; a path that's configured but broken (bad permissions, points at a file, etc.) shows the actual error in the dialog rather than crashing. The window title always shows which library is currently open.

**File > Settings...** reopens that same dialog anytime, for staging folder / library folder / Blender path together. **File > Switch Library...** is the faster, more deliberate path for just swapping libraries (a personal one vs. a shared one, say) — it browses directly to a folder and confirms before switching, telling you plainly whether it found an *existing* library there or would be creating a *new, empty* one, so a wrong folder pick doesn't silently start a fresh catalogue by accident. Either path live-switches the whole catalogue (closes the old DB connection, opens the new one, rebuilds the pack/tag/type lists) without restarting the app.

Filter panel (type / pack / tag) on the left, a thumbnail grid in the middle, and a tagging panel at the bottom for the selected asset — add/remove tags directly from the grid instead of going through the CLI. The grid supports multi-select (Ctrl/Shift-click, or **Edit > Select All** / Ctrl+A) — selecting more than one asset switches the bottom panel to a "N assets selected" state (tag editing needs exactly one asset selected).

**Ingest Pack...** is a toolbar button, not tucked in a menu — ingest is the most common action, so it's the first thing visible under the menu bar. One dialog handles both pack sources: **Browse Folder...** for an already-extracted pack inside the staging folder, or **Browse Zip...** for a `.zip` anywhere on disk (extracted into the staging folder first, using an editable destination folder name). Two browse buttons rather than one folder-or-file picker is a real OS/Qt limitation, not a design choice — a native picker is either a folder picker or a file picker, never both. Either way, one "Ingest" action runs it as a single background job, and re-ingesting an existing pack with different metadata only updates the fields that changed (same delta behavior as the CLI).

**Edit > Remove Selected...** (or the Delete key) removes whatever's selected in the grid — one, many, or all of it — from the catalogue database and thumbnails only; a confirmation dialog says so explicitly before anything happens, since it's easy to forget that "remove from library" doesn't touch the actual files sitting in your staging folder.

**Thumbnails > Generate 2D Thumbnails** / **Generate 3D Thumbnails via Blender** run against whatever pack is currently selected in the filter panel (or all packs, if "All packs" is selected). Both run on a background thread — the window stays responsive while Blender works, which matters since a full pack can take a while — and show a progress dialog until done.

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

All seven build-order steps from the seed doc are now done. The UI (§ above) only covers browsing/tagging so far — import, calibration, and ingest are still CLI-only; folding them into the UI would be natural next work, but isn't part of the seed's stated build order.
