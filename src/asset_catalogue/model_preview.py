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
