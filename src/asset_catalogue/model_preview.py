"""Cache location for the lightweight interactive-preview .glb generated
alongside every model asset's static thumbnail render (see
blender_thumbnail_script.py -- same Blender import, one extra export, no
second Blender launch). Deliberately separate from thumbnails.py's PNG
cache: same identity scheme (content-hash-keyed, so identical content
never needs re-exporting), different consumer -- the PNG is a flat 2D
render shown everywhere; the .glb is real 3D geometry loaded into an
orbit/zoom viewer only when someone explicitly asks for it (see
ui/model_preview_dialog.py), so it's kept apart to make its purpose
(and its very different file size/growth pattern) obvious in the
library folder.
"""

from __future__ import annotations

from pathlib import Path


def preview_path(preview_dir: Path, content_hash: str) -> Path:
    return preview_dir / f"{content_hash}.glb"


def colors_path(preview_dir: Path, content_hash: str) -> Path:
    """Sidecar JSON, same content-hash identity as the .glb it describes:
    per-material color metadata resolved by Blender's own material graph
    at export time (see blender_common.py's resolve_material_metadata) --
    the interactive preview reads this instead of re-deriving a display
    color from the exported glb's material fields itself, which is what
    let its own hand-rolled linear/sRGB handling diverge from what
    Blender actually rendered. Missing for a preview exported before this
    existed; the preview dialog falls back to its own derivation then.
    """
    return preview_dir / f"{content_hash}.colors.json"
