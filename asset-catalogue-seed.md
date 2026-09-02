# Game Asset Catalogue — Project Seed

A standalone Python tool for cataloguing, tagging, previewing and importing game assets.

This document is the starting point for the project. It describes the intended shape of the tool and the reasoning behind the decisions made so far. It deliberately stops short of prescribing implementation details that are better decided while building.

---

## 1. The problem

A growing library of game assets — some self-made, mostly purchased packs (low-poly 3D art, 2D art, audio). Two requirements that pull in opposite directions:

- **Provenance must be preserved.** Which pack an asset came from, who made it, what licence it carries. Packs should stay exactly as they shipped so updates can be dropped in cleanly.
- **Discovery must cut across packs.** "Show me every fantasy asset" or "every sci-fi prop" needs to work regardless of which pack a thing lives in.

A folder hierarchy can only express one of those at a time. That's the core tension the tool exists to resolve: **folders keep provenance, a database provides the cross-cutting view.**

A secondary requirement: assets should be tagged *before* they enter any game project, so tagging happens in a staging area that sits outside every project.

---

## 2. Scope and non-goals

**In scope**
- Cataloguing files from a staging area
- Tagging at pack level and file level
- Thumbnail generation, including for 3D formats
- Searching by tag, type, pack, licence
- Importing selected assets into a target project, and remembering that it happened

**Explicit non-goals**
- Not a Godot editor addon. This was considered and rejected — a standalone tool can be run against external media (drives, archives, network storage) and can target destinations other than Godot. Godot is one possible destination, not the frame.
- Not an asset editor. It catalogues and moves files; it does not modify them.
- Not bundling Blender. See §6.

---

## 3. Architecture

Four layers, with a deliberate rule: **the UI talks only to the catalogue, never to the filesystem directly.** That separation means the interface can be replaced later without touching anything beneath it.

```
┌─────────────────────────────────────┐
│  UI layer (PySide6)                 │
│  filter tree · thumbnail grid       │
└──────────────┬──────────────────────┘
               │  queries and commands only
┌──────────────▼──────────────────────┐
│  Catalogue                          │
│  owns the schema and all queries    │
└───┬──────────────┬──────────────┬───┘
    │              │              │
┌───▼──────┐ ┌─────▼──────┐ ┌─────▼────┐
│ Ingest   │ │ Thumbnails │ │ Import   │
│ walk +   │ │ Blender    │ │ copy +   │
│ hash     │ │ subprocess │ │ record   │
└──────────┘ └────────────┘ └──────────┘
```

**Stack**

| Concern | Choice | Why |
|---|---|---|
| Language | Python | Already comfortable; good subprocess and filesystem story |
| Catalogue | SQLite | Built into the standard library, no server, single portable file |
| UI | Qt via PySide6 | Native file dialogs, drag-and-drop, thumbnail grid widgets |
| 3D previews | Blender, headless | Handles OBJ / FBX / GLTF / STL / .blend without writing importers |
| 2D previews | Pillow | Direct load of PNG / JPEG / etc. |

Nothing exotic. That's intentional.

---

## 4. Data model (starting sketch)

Five tables. Field lists below are a starting point, not a final schema — expect to revise while building.

**`packs`** — one row per purchased or self-made collection
- id, name, creator, licence, source URL, date added
- render corrections (see §7) — stored here, as JSON or discrete columns

**`assets`** — one row per file
- id, pack_id, relative path within the pack, filename, extension, file size
- `content_hash` — **UNIQUE**. This is load-bearing; see below.
- asset type (model / texture / audio / other), thumbnail status

**`tags`** — the tag vocabulary
- id, name, optional category (theme / type / style)

**`asset_tags`** — many-to-many join between assets and tags
- Pack-level tags cascade to everything inside; per-file tags add to or override that cascade. Worth recording *how* a tag was applied (inherited vs explicit) so re-tagging a pack doesn't stamp on manual work.

**`imports`** — where each asset has been deployed
- asset_id, project identifier, destination path, timestamp
- One asset can appear here many times, once per project.

### The content hash

A checksum of the file's bytes, stored and made unique. It does three jobs at once:

1. **Deduplication** — the same model shipped in two different packs is recognised as one asset.
2. **Re-scan safety** — re-running ingest over the staging folder skips anything already known, so the operation is idempotent.
3. **Thumbnail identity** — thumbnails are named after the hash, not the file path. Before rendering, check whether a thumbnail with that hash already exists; if so, skip. This is the mechanism that guarantees nothing is ever rendered twice.

---

## 5. Pipeline

**Ingest.** A pack is dropped into the staging folder, untouched. The tool walks it, hashes every file, and inserts rows for anything new. Pack-level metadata (creator, licence) is entered once.

**Tag.** Pack-level tags cascade down. Per-file tags refine.

**Thumbnail.** 2D files load directly via Pillow. 3D files are queued for Blender (§6).

**Search.** Filter by tag, type, pack, licence. Results come back as a thumbnail grid.

**Import.** Point at a target project. For Godot, the tool can confirm it's a valid destination by looking for `project.godot` at the root, and derive a project identifier from the folder name or a stored ID. Selected assets are copied in — rebuilding a sensible slice of the folder structure rather than dumping flat — and each copy is recorded in `imports`.

Because imports are recorded, the catalogue can answer questions it otherwise couldn't: what's already in this project, what's never been used anywhere, which projects share an asset.

**Copy vs symlink** is an open question. Copying is simpler and safer; symlinking saves disk and propagates updates. Decide during build.

---

## 6. Blender integration

Blender is **detected, not bundled** — it's a few hundred megabytes and bundling raises licensing complications. Instead: look in the standard install paths, allow the user to point at it in settings, and check the version at startup. Blender's Python API shifts across major releases, so write the render script against a stated minimum version and warn when something older is found. In practice a thumbnail script is simple enough to survive most upgrades untouched.

**The user should never see Blender.** It runs with the background flag, opens invisibly, imports, renders, saves a PNG, and exits.

### One process, many files

Startup dominates the cost. A single model at modest thumbnail resolution takes roughly 2–5 seconds, most of which is Blender launching. Launching once per asset means a 100-asset pack takes around five minutes; keeping one process alive and feeding it a list cuts that several times over. **Worth doing from the start** rather than retrofitting.

The render script therefore loops internally: for each entry — clear the scene, import, frame the camera, render, save, continue.

### The job list

Rather than a flat list of paths, pass a list of job objects (as a temp JSON file, or on stdin):

```
[
  {
    "source_path": "...",
    "output_path": "...",
    "corrections": { "up_axis": "...", "scale": 1.0, ... }
  },
  ...
]
```

Because corrections travel with each entry, a single Blender run can process **several packs with different settings** in one pass.

### Format dispatch

Blender has a separate importer per format (OBJ, FBX, GLTF, STL; `.blend` opens directly). The script picks the importer from the file extension — a small dispatch table at the top — and everything downstream is a shared code path.

Caveat: formats vary in reliability, FBX especially, depending on which tool exported it.

### Failure detection

A poor import usually still renders *something* — wrong materials, odd rotation — which is recoverable. The failure worth guarding against is **nothing importing at all**, which silently produces a blank background PNG.

So before rendering, check whether any objects actually landed in the scene. If not, mark the asset as failed rather than saving a blank thumbnail. Failed assets can then be reviewed in bulk.

### Format conversion

Since Blender is already a dependency, it can also convert formats on import, not just render thumbnails. Not day one, but the door is open.

---

## 7. Per-pack calibration

Packs are internally consistent — if one model comes in rotated or scaled wrongly, they all will. So calibrate once per pack rather than fixing assets individually:

1. Render one representative asset from the pack.
2. Review it.
3. If it's wrong, set corrections — up axis, scale factor, material fallback.
4. Store the corrections against the pack.
5. Everything else from that pack inherits them automatically.
6. Offer a **re-render whole pack** action for when corrections are changed later.

This fits the existing model cleanly: packs already carry creator and licence, so corrections are simply more pack-level metadata, cascading the same way tags do.

---

## 8. Progress reporting

The render script prints one line per completed asset. The Python side reads that output stream so the UI can show real progress rather than appearing to hang. This matters more than it sounds — a five-minute silent operation feels broken.

---

## 9. Open questions

- Copy or symlink on import?
- Should search results support preview-before-import, or is the thumbnail grid enough?
- How should the same asset imported into several projects be surfaced in the UI?
- Should thumbnail appearance (angle, lighting, background) be user-configurable? Probably a later settings panel, not day one.
- Where does audio fit? It has no visual preview — waveform, or just an icon and a play button?

---

## 10. Suggested build order

Each step should be usable on its own before moving to the next.

1. **Schema + ingest** — walk a folder, hash files, populate SQLite. Verify with a CLI query.
2. **Tagging** — pack-level cascade and per-file tags, still CLI.
3. **2D thumbnails** — Pillow only. Proves the thumbnail-by-hash mechanism cheaply.
4. **Blender thumbnails** — single file first, then batching, then failure detection.
5. **Qt UI** — filter tree, thumbnail grid, tagging panel.
6. **Calibration** — per-pack corrections and re-render.
7. **Import + tracking** — project detection, copy, record.

---

## Note on working style

Preference is for **explanation over autonomous implementation** — the goal is to understand the code, not just to have it exist. Walk through reasoning and design trade-offs before writing; prefer explaining an approach and letting the decision be made rather than picking one silently.
