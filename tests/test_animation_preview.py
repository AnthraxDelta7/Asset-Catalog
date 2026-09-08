from __future__ import annotations

from pathlib import Path

from asset_catalogue import animation_preview


def test_clip_names_are_slugged_but_never_collide(tmp_path: Path) -> None:
    """Clip names come from the asset, not from us -- glTF allows "@run",
    "Armature|Take 001" and worse. Two that slug identically must still
    get separate cache folders.
    """
    assert "/" not in animation_preview._safe_clip_name("Armature|Take 001")
    a = animation_preview.clip_frames_dir(tmp_path, "hash", "@run")
    b = animation_preview.clip_frames_dir(tmp_path, "hash", "#run")
    assert a != b
    # Same clip, same folder -- that's what makes the cache work at all.
    assert a == animation_preview.clip_frames_dir(tmp_path, "hash", "@run")


def test_cached_frames_are_ordered_and_empty_when_unrendered(tmp_path: Path) -> None:
    assert animation_preview.cached_frames(tmp_path / "never_rendered") == []
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for index in (2, 0, 10, 1):
        (frames_dir / f"frame_{index:04d}.png").write_bytes(b"png")

    found = animation_preview.cached_frames(frames_dir)

    # Zero-padded names make name order equal frame order, including
    # past frame 9 where a naive sort would put 10 before 2.
    assert [p.name for p in found] == [
        "frame_0000.png",
        "frame_0001.png",
        "frame_0002.png",
        "frame_0010.png",
    ]


def test_render_clips_reuses_a_cached_render_without_launching_blender(tmp_path: Path) -> None:
    """The whole point of rendering lazily is undone if a second Play
    re-renders, so the cache check has to happen before Blender is
    considered at all -- proven here by passing a path that would fail
    outright if it were ever executed.
    """
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "frame_0000.png").write_bytes(b"png")

    results, error = animation_preview.render_clips(
        Path("definitely-not-a-real-blender.exe"),
        tmp_path / "model.glb",
        tmp_path,
        ".glb",
        {},
        {"@idle": frames_dir},
    )

    assert error is None
    assert [p.name for p in results["@idle"]] == ["frame_0000.png"]


def test_render_clips_only_asks_blender_for_the_clips_not_yet_cached(tmp_path: Path) -> None:
    """Every clip of a model is rendered in one session, but a clip
    already on disk must not be re-rendered -- so a call where only some
    are cached still has to reach Blender, while one where all are
    cached must not.
    """
    cached, missing = tmp_path / "cached", tmp_path / "missing"
    cached.mkdir()
    (cached / "frame_0000.png").write_bytes(b"png")

    results, error = animation_preview.render_clips(
        Path("definitely-not-a-real-blender.exe"),
        tmp_path / "model.glb",
        tmp_path,
        ".glb",
        {},
        {"@idle": cached},
    )
    assert error is None and results["@idle"]

    # With one uncached clip it does try to run, and reports the failure
    # rather than pretending it succeeded.
    results, error = animation_preview.render_clips(
        Path("definitely-not-a-real-blender.exe"),
        tmp_path / "model.glb",
        tmp_path,
        ".glb",
        {},
        {"@walk": missing},
    )
    assert error is not None
    assert results["@walk"] == []


def test_playback_interval_preserves_roughly_the_clips_duration() -> None:
    # 24 frames across ~1.5s is about 62ms each.
    assert animation_preview.frame_interval_ms(24) == 62
    # Floored so a 2-frame clip can't flicker.
    assert animation_preview.frame_interval_ms(2) >= 40
    assert animation_preview.frame_interval_ms(0) > 0
