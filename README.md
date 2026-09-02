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
asset-catalogue list [--pack "Pack Name"] [--type model|texture|audio|other] [--tag tag_name]
```

## UI

```
asset-catalogue-ui
```

Filter panel (type / pack / tag) on the left, a thumbnail grid in the middle, and a tagging panel at the bottom for the selected asset — add/remove tags directly from the grid instead of going through the CLI. Reads and writes through `src/asset_catalogue/catalogue.py`'s `Catalogue` class, not the filesystem or raw SQL directly, per the seed doc's architecture rule (§3) — the CLI's `list`/`tags` commands still use their own direct queries (they predate this layer and aren't part of the seed's UI-facing architecture), so if CLI and UI query behavior ever need to match exactly, that's the one place they currently diverge.

## Status

Build order from the seed doc, tracked here:

- [x] Schema + ingest
- [x] Tagging (pack cascade + per-file tags, CLI)
- [x] 2D thumbnails (Pillow)
- [x] Blender thumbnails
- [x] Qt UI (filter panel, thumbnail grid, tagging panel)
- [x] Per-pack calibration
- [ ] Import + tracking
