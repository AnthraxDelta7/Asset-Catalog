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


# Bumped whenever the render settings change in a way that makes older
# cached frames look wrong beside new ones -- v1 was 320px on a
# near-black backdrop, v2 is 512px on the viewport's own grey. Old
# folders are simply never looked at again rather than deleted, since
# they cost little and nothing else knows how to clean them.
RENDER_VERSION = 2


def clip_frames_dir(preview_dir: Path, content_hash: str, clip_name: str) -> Path:
    return (
        preview_dir
        / "animations"
        / f"v{RENDER_VERSION}"
        / content_hash
        / _safe_clip_name(clip_name)
    )


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


def render_clips(
    blender_exe: Path,
    source_path: Path,
    pack_root: Path,
    extension: str,
    corrections: dict,
    targets: dict[str, Path],
    on_progress: ProgressCallback | None = None,
) -> tuple[dict[str, list[Path]], str | None]:
    """Renders every clip in `targets` (clip name -> frames directory) in
    a single Blender session. Returns ({clip: frames}, error).

    Clips already cached are dropped before Blender is considered at all,
    so a fully-cached model never launches it. Batching the rest into one
    session is what makes this bearable: importing a large rigged
    character costs far more than rendering a Workbench frame, so paying
    that import once and rendering six clips beats six launches by a wide
    margin.
    """
    report = on_progress or (lambda _text: None)
    results = {clip: cached_frames(path) for clip, path in targets.items()}
    missing = {clip: path for clip, path in targets.items() if not results[clip]}
    if not missing:
        return results, None

    job = {
        "source_path": str(source_path),
        "pack_root": str(pack_root),
        "extension": extension,
        "corrections": corrections,
        "clips": {clip: str(path) for clip, path in missing.items()},
        "max_frames": MAX_FRAMES,
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(job, f)
        job_path = Path(f.name)

    report(
        f"Rendering {len(missing)} animation{'s' if len(missing) != 1 else ''} "
        "(one Blender session for all of them)..."
    )
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
        return results, "Rendering the animations timed out"
    except OSError as exc:
        # Blender resolved when the job started but couldn't actually be
        # launched -- moved, uninstalled, or permission-denied since.
        # Reported like any other failure rather than escaping as a raw
        # traceback out of a background job.
        return results, f"Could not run Blender ({blender_exe}): {exc}"
    finally:
        job_path.unlink(missing_ok=True)

    failures = []
    for line in (process.stdout + process.stderr).splitlines():
        # Clip name last, and it takes the whole remainder: it's the one
        # field that can legitimately contain "|" (Blender names an
        # FBX-imported action "Object|Object|Action").
        if line.startswith("ASSET_CATALOGUE_ANIM_FRAME|"):
            _, done, total, clip = line.split("|", 3)
            report(f"Rendering {clip}: frame {done}/{total}")
        elif line.startswith("ASSET_CATALOGUE_ANIM_RESULT|"):
            _, status, detail, clip = line.split("|", 3)
            if status == "ok":
                results[clip] = cached_frames(targets[clip])
            else:
                failures.append(f"{clip}: {detail}")

    for clip in missing:
        if not results[clip] and not any(f.startswith(f"{clip}:") for f in failures):
            failures.append(f"{clip}: Blender produced no frames")
    # Only an outright error if nothing at all came back -- one bad clip
    # shouldn't hide the others that rendered fine.
    if not any(results.values()):
        return results, "; ".join(failures) or "Blender exited without rendering anything"
    return results, None
