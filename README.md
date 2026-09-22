# Asset Catalogue

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

A desktop app for keeping track of game asset packs. It catalogues what you own, renders thumbnails so you can see it, and exports what you need into a project.

If you've ever bought a bundle of 40 asset packs and then never found anything in them again, that's the problem this solves.

Support development at [ko-fi.com/anthraxdelta7](https://ko-fi.com/anthraxdelta7). App icon by Gajah Mada from [Flaticon](https://www.flaticon.com/authors/gajah-mada).

---

## Install

Download the latest zip from [Releases](https://github.com/AnthraxDelta7/Asset-Catalog/releases/latest), extract it anywhere, and run `AssetCatalogue.exe`. You don't need Python.

On first launch you'll be asked for two folders:

- **Ingest folder** — where you keep asset packs. It's just a starting point for the file picker, not a place packs have to live.
- **Library folder** — where the catalogue lives. This one is portable: copy it to another machine and point the app at it.

**File > Switch Library...** swaps between libraries without restarting, and tells you whether it found an existing one or is about to create a new empty one.

Two optional extras:

| | Needed for | Setup |
|---|---|---|
| **Blender 4.0+** | 3D model thumbnails, glTF conversion | Auto-detected if installed |
| **Godot 4.0+** | Extracting scenes from Godot projects | Point at the `.exe` in Settings |

Neither is required. Without Blender you still get texture and audio thumbnails, and everything else works.

---

## Adding packs

Click **Ingest Pack...**, then **Browse...**. The picker shows folders, `.zip` files and individual asset files side by side. Any of them can be a pack:

- a **folder** becomes a pack named after itself
- a **zip** is extracted and catalogued, then the unpacked copy is cleaned up
- a **single file** becomes a pack of one

Ctrl-click to pick several at once, or use **Batch Ingest...** to give them all the same creator and licence.

You can also drag a folder or zip straight onto the window.

### What gets catalogued

Models (`.obj` `.fbx` `.gltf` `.glb` `.stl` `.blend`), textures (`.png` `.jpg` `.jpeg` `.tga` `.bmp` `.tiff` `.webp`) and audio (`.wav` `.mp3` `.ogg` `.flac`).

Everything else is skipped. Unity and Unreal project files, readmes, scripts and engine cache folders never make it in, so a pack that ships a whole Unity project doesn't fill your library with `.meta` files.

Every file is hashed. If the same file turns up in two packs, or you re-ingest a pack after adding to it, you get one entry, not two. Re-ingesting is always safe.

### Where your files go

Each asset is copied into `library/assets/<Pack Name>/`. That's what makes the library portable. Your original files are never moved, changed or deleted.

Models that need extra files to load get those copied too. A `.gltf` brings its `.bin`, an `.obj` brings its `.mtl` and that `.mtl`'s textures.

---

## Thumbnails

Ingest renders thumbnails automatically. Textures and audio use Pillow, models use Blender.

For the first model in a new pack, only one thumbnail is rendered. It's a calibration preview: check it looks right, fix the pack's up-axis or scale if it doesn't, then render the rest. The ingest summary tells you the exact command.

`.wav` files get a real waveform. Other audio formats get a placeholder, since decoding them would mean adding an ffmpeg dependency for a picture.

### When a texture is missing

Packs sometimes ship with texture paths baked in from the author's own machine, pointing at folders that don't exist on yours. Instead of rendering a pink model and letting you think the app is broken, these get flagged.

If the missing file is somewhere else in the pack, it's relinked automatically. If it isn't, the asset shows up in **Missing Textures**, reachable from the status bar badge or the detail panel's **Fix Texture...** button. Each row offers:

- **Browse...** — pick the right file. Fixes every asset in the pack sharing that material.
- **Add Supplementary File...** — bundle an extra map into the exported `.glb` without wiring it up.
- **No Texture Needed** — mark it as intentionally plain so it stops being flagged.
- **Skip** — not now, ask again next render.

### Packs that ship their own fixes

Drop an `asset-catalogue.json` in a pack folder and it's read at ingest, so the pack comes in correct the first time. It travels with the pack and survives re-ingest.

```json
{
  "texture_overrides": {
    "main": "Assets/Polygon-ForestVillage/Textures/Polygon_Texture_vol3.png"
  },
  "acknowledged_materials": ["Glass"]
}
```

It accepts the same settings as **Edit Pack Metadata**: `texture_overrides`, `texture_extras`, `acknowledged_materials`, `up_axis`, `scale`, `material_fallback`, `broken_texture_fallback`, `disable_smart_texture_matching` and `prefer_source_models`. Paths are relative to the pack folder. Mistakes are reported in the ingest summary and don't stop the ingest.

---

## Finding things

The left panel filters by search text, type, format, pack and tag, plus checkboxes for favourites and assets needing conversion.

The pack list shows your **10 most recently used packs**. The search next to it looks past that, across every pack you have, and **Packs ›** opens the full manager where you can edit metadata, hide packs, re-ingest or remove them.

**Tags** cascade from a pack to its assets. Untag one asset and it stays untagged, even if you re-tag the whole pack later. **Favourites** are separate from tags and just mark the ones you liked.

A model with a rig or animations shows a dot on its thumbnail: blue for a rig, amber for clips. No dot means either nothing's there or nothing has looked yet. The status bar says how many models still need rendering.

### Keyboard

Around 40 commands have shortcuts, and you can rebind any of them from **File > Keyboard Shortcuts** (`Ctrl+K`).

Single keys work too. `F` favourites, `Space` opens the 3D preview, `E` exports, `R` re-renders a thumbnail, and the arrow keys move between assets. They're suppressed while you're typing in a text box, so searching for "fbx" doesn't fire three commands.

---

## Looking at assets

Double-click a model for a bigger preview, or right-click for **3D Preview (Orbit/Zoom)** to spin it around properly. Double-click a sound and it plays.

The 3D viewer lists every part in the file with its own checkbox, so you can see whether that prop ships with a collision mesh or LOD variants bundled in. Parts that look like colliders are tinted orange and can be hidden in one click. A **Textures...** button shows every image the model actually uses.

It's a quick look, not a render. Use it to check topology, proportions and orientation before committing to an asset.

---

## Godot packs

A lot of marketplace packs are Godot projects where the textures are assigned in the scene rather than in the mesh file. Ingesting those directly gives you untextured models.

**Tools > Extract Godot Scenes to GLB...** fixes that. It finds the Godot projects in a folder, runs the real Godot editor headlessly, and exports each scene as a textured `.glb`. Then ingest the folder normally.

Collision shapes get a visible mesh so you can see them, unless you turn that off. Scenes with no geometry are skipped rather than left as empty files.

---

## Exporting

Select assets, then **Export**. Files land in `<project>/exported_assets/<Pack Name>/`, flattened, with name collisions disambiguated.

**Export to Godot** does more. A single-mesh model becomes a `.res` Mesh resource. Anything else becomes a `.tscn`. A model with a rig, animations or morph targets is left whole, because flattening it would destroy them. Non-`.glb` models go through Blender first so their textures survive.

Every export is recorded, so you can ask what's already in a project, or which projects an asset ended up in.

**Tools > Generate Credits Report...** produces an attribution list from your pack metadata. Point it at a project and it only lists packs you actually used there.

---

## Removing things

**Move to Trash** hides an asset and is reversible. **Trash > Delete Permanently** removes the catalogue entry, thumbnail and library copy for real.

Neither touches your original files. Nothing in this app ever does.

---

## Converting to glTF

Right-click a model and convert it. Blender re-imports it with the pack's corrections applied and writes a `.glb`, keeping the same asset, tags and history.

This matters for a specific reason. A texture fix that only lives in the render doesn't survive a plain file copy, so an `.fbx` can look perfect in every thumbnail and still export with a missing texture. Assets in that position get a ⚠ badge and show up under **Needs conversion only**.

The original is kept until you decide. **Revert** puts it back, **Keep Converted** deletes it.

---

## Command line

Everything the UI does is available from a terminal. Install from source first (see below).

| Command | What it does |
|---|---|
| `ingest <folder\|zip>` | Catalogue a pack |
| `ingest-zip <path>` | Catalogue a zip from outside the ingest folder |
| `godot-extract <folder>` | Export Godot scenes to `.glb` |
| `list` | Search and filter assets |
| `tag pack\|asset` | Add tags |
| `tags` | Show the tag vocabulary |
| `thumbnail generate[-audio\|-models]` | Render thumbnails |
| `convert to-gltf\|revert\|cleanup\|flagged` | Convert models |
| `pack set-metadata\|set-corrections\|rename\|remove\|notes` | Manage packs |
| `export <project> [--godot]` | Export assets |
| `exports` | Export history |
| `trash move\|list\|restore\|empty` | Soft delete |
| `remove` | Permanent delete |
| `stats` / `check [--fix]` | Library size and integrity |
| `credits [project]` | Attribution report |
| `settings set\|show` | Configure paths |

Add `--help` to any of them. Most take `--pack`, `--type`, `--tag`, `--asset-id` or `--all` to choose what they act on, and commands that delete things ask first unless you pass `--yes`.

---

## Updates

The app checks for a newer version on launch and stays quiet unless there is one. **Help > Check for Updates...** checks on demand and always tells you the result.

One-click install is currently switched off pending code signing, so updates mean downloading the zip yourself. The mechanism is built and working, but Windows Defender intermittently locks the install folder while an unsigned exe exits, which isn't worth working around.

---

## Building from source

```
python -m venv .venv
.venv\Scripts\pip install -e .
asset-catalogue settings set --staging-folder "D:\path\to\packs" --library-folder "D:\path\to\library"
asset-catalogue-ui
```

Settings live in `settings.json` at the repo root. The packaged app keeps its own under `%APPDATA%\AssetCatalogue\`.

Tests:

```
pip install -e ".[dev]"
pytest
```

The suite runs against real SQLite databases and real files in temp folders, not mocks. It never touches your real settings or library. Anything needing Blender or Godot is verified by hand against real installs, since mocking those proves nothing.

To cut a release: bump `__version__` in `src/asset_catalogue/version.py`, tag it, push the tag, and publish a GitHub Release with the built zip attached. The update check reads the release, not the tag.

---

## Design notes

The reasoning behind the bigger decisions is in [asset-catalogue-seed.md](asset-catalogue-seed.md).

Two worth knowing here:

**Exports are copies, not symlinks.** Symlinks on Windows normally need Developer Mode or admin rights, and a project that silently breaks when the library isn't mounted is worse than using a bit more disk.

**Nothing is inferred about your files.** When the app extracts a zip it writes down that it did, so cleanup only ever removes folders it created. A folder you unpacked yourself is never a candidate, however much it looks like one of ours.

---

## License

GPLv3 — see [LICENSE](LICENSE). Use it, change it, share it. Redistributed versions have to stay GPL and ship their source, which keeps a closed-source repackage off the table.
