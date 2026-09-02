# Asset Catalogue

Standalone tool for cataloguing, tagging, previewing and importing game assets. Design rationale lives in [asset-catalogue-seed.md](asset-catalogue-seed.md); this file tracks day-to-day usage as commands land.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -e .
```

Configure the staging folder (required before `ingest`) and, later, the Blender path:

```
asset-catalogue settings set --staging-folder "D:\path\to\staging"
asset-catalogue settings show
```

Settings live in `settings.json` at the repo root (gitignored — it's a local machine path). The catalogue database defaults to `catalogue.db` and thumbnails to `thumbnails/`, both at the repo root; override with `--db-path` / `--thumbnail-dir` on `settings set`.

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

## Search

```
asset-catalogue list [--pack "Pack Name"] [--type model|texture|audio|other] [--tag tag_name]
```

## Status

Build order from the seed doc, tracked here:

- [x] Schema + ingest
- [x] Tagging (pack cascade + per-file tags, CLI)
- [x] 2D thumbnails (Pillow)
- [ ] Blender thumbnails
- [ ] Qt UI
- [ ] Per-pack calibration
- [ ] Import + tracking
