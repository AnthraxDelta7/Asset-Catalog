"""On-demand rendering and caching of an animation clip's frames.

Nothing here runs until someone presses Play on a specific clip. That's
deliberate: a rigged character commonly ships six or more clips, and
rendering them all during ingest would add minutes per asset for frames
that may never be looked at -- the whole point of this being lazy. Once
rendered, a clip is cached under the same content-hash identity the
static thumbnail and interactive preview already use (see
model_preview.py), so replaying it is instant and identical content is
never rendered twice.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from asset_catalogue import paths

ProgressCallback = Callable[[str], None]

ANIMATION_SCRIPT_PATH = paths.package_dir() / "blender_animation_script.py"
RENDER_TIMEOUT_SECONDS = 600
# Enough to read as motion without making a rigged character's Play
# button feel like a build step. A longer clip is subsampled to this and
# played back slower to keep its real duration (see frame_interval_ms).
MAX_FRAMES = 24
DEFAULT_CLIP_SECONDS = 1.5


def _safe_clip_name(clip_name: str) -> str:
    """Clip names come from the asset, not from us -- glTF happily allows
    "@run", "Armature|Take 001", and worse. Slugged for readability plus
    a short hash of the original so two clips that slug identically can
    never share a cache folder.
    """
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", clip_name).strip("_") or "clip"
    digest = hashlib.sha1(clip_name.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:40]}_{digest}"


def clip_frames_dir(preview_dir: Path, content_hash: str, clip_name: str) -> Path:
    return preview_dir / "animations" / content_hash / _safe_clip_name(clip_name)


def cached_frames(frames_dir: Path) -> list[Path]:
    """Rendered frames in order, or [] if this clip hasn't been rendered.
    Sorted by name, which the zero-padded frame_NNNN naming makes
    equivalent to sorting by frame number.
    """
    if not frames_dir.is_dir():
        return []
    return sorted(frames_dir.glob("frame_*.png"))


def frame_interval_ms(frame_count: int, clip_seconds: float = DEFAULT_CLIP_SECONDS) -> int:
    """How long to hold each frame so a subsampled clip still takes about
    as long to play as the original did. Floored so a very short clip
    can't spin fast enough to look like a flicker.
    """
    if frame_count <= 0:
        return 100
    return max(40, int(round(clip_seconds * 1000 / frame_count)))


def render_clip(
    blender_exe: Path,
    source_path: Path,
    pack_root: Path,
    extension: str,
    corrections: dict,
    frames_dir: Path,
    clip_name: str,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[Path], str | None]:
    """Renders clip_name into frames_dir. Returns (frames, error).

    A cache hit returns immediately without launching Blender at all --
    checked here rather than by callers so every entry point gets it.
    """
    report = on_progress or (lambda _text: None)
    existing = cached_frames(frames_dir)
    if existing:
        return existing, None

    job = {
        "source_path": str(source_path),
        "pack_root": str(pack_root),
        "extension": extension,
        "corrections": corrections,
        "output_dir": str(frames_dir),
        "clip_name": clip_name,
        "max_frames": MAX_FRAMES,
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(job, f)
        job_path = Path(f.name)

    report(f"Rendering {clip_name}...")
    try:
        process = subprocess.run(
            [
                str(blender_exe),
                "--background",
                "--python",
                str(ANIMATION_SCRIPT_PATH),
                "--",
                str(job_path),
            ],
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return [], f"Rendering {clip_name} timed out"
    finally:
        job_path.unlink(missing_ok=True)

    output = process.stdout + process.stderr
    for line in output.splitlines():
        if line.startswith("ASSET_CATALOGUE_ANIM_FRAME|"):
            _, done, total = line.split("|", 2)
            report(f"Rendering {clip_name}: frame {done}/{total}")
    result = next(
        (line for line in output.splitlines() if line.startswith("ASSET_CATALOGUE_ANIM_RESULT|")),
        None,
    )
    if result is None:
        return [], "Blender exited without rendering the animation"
    _, status, detail = result.split("|", 2)
    if status != "ok":
        return [], detail

    frames = cached_frames(frames_dir)
    if not frames:
        return [], "No frames were produced"
    return frames, None
