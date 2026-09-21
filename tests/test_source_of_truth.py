"""The library's archived copy, not the unpacked source, is what gets read.

That ordering is the whole point of archiving: a pack has to behave
identically whether or not its unpacked source still exists, otherwise
deleting that source is a thing that quietly breaks re-rendering and
re-exporting weeks later.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from asset_catalogue import blender_render, db, ingest, library_assets

from conftest import write_minimal_glb


def _pack_with_a_gltf(tmp_path: Path) -> tuple[Path, Path, Path]:
    staging, library = tmp_path / "staging", tmp_path / "library"
    pack = staging / "Pack"
    (pack / "models").mkdir(parents=True)
    (pack / "models" / "prop.bin").write_bytes(b"\x00\x01\x02\x03")
    (pack / "models" / "prop.gltf").write_text(
        json.dumps(
            {
                "asset": {"version": "2.0"},
                "meshes": [{"name": "m"}],
                "buffers": [{"uri": "prop.bin", "byteLength": 4}],
            }
        ),
        encoding="utf-8",
    )
    library.mkdir()
    return staging, library, pack


def test_the_archived_copy_wins_over_the_original(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    (assets_dir / "Pack").mkdir(parents=True)
    (assets_dir / "Pack" / "prop.glb").write_bytes(b"archived")
    staging = tmp_path / "staging"
    (staging / "PackFolder").mkdir(parents=True)
    (staging / "PackFolder" / "prop.glb").write_bytes(b"original")

    resolved = library_assets.source_file(
        assets_dir, staging, "Pack", "PackFolder", "prop.glb"
    )
    assert resolved.read_bytes() == b"archived"
    assert library_assets.source_root(assets_dir, staging, "Pack", "PackFolder") == (
        assets_dir / "Pack"
    )


def test_it_falls_back_to_the_original_when_nothing_is_archived(tmp_path: Path) -> None:
    """A library from before archiving existed, or a file added since the
    last archive pass, still has to resolve.
    """
    assets_dir = tmp_path / "assets"
    staging = tmp_path / "staging"
    (staging / "PackFolder").mkdir(parents=True)
    (staging / "PackFolder" / "prop.glb").write_bytes(b"original")

    resolved = library_assets.source_file(
        assets_dir, staging, "Pack", "PackFolder", "prop.glb"
    )
    assert resolved.read_bytes() == b"original"


def test_a_partly_archived_pack_falls_back_per_file(tmp_path: Path) -> None:
    """Resolution is per file, not per pack: an interrupted archive or an
    asset added later would otherwise take the whole pack down with it.
    """
    assets_dir = tmp_path / "assets"
    (assets_dir / "Pack").mkdir(parents=True)
    (assets_dir / "Pack" / "archived.glb").write_bytes(b"archived")
    staging = tmp_path / "staging"
    (staging / "PackFolder").mkdir(parents=True)
    (staging / "PackFolder" / "archived.glb").write_bytes(b"original")
    (staging / "PackFolder" / "only_in_staging.glb").write_bytes(b"original")

    assert library_assets.source_file(
        assets_dir, staging, "Pack", "PackFolder", "archived.glb"
    ).read_bytes() == b"archived"
    assert library_assets.source_file(
        assets_dir, staging, "Pack", "PackFolder", "only_in_staging.glb"
    ).read_bytes() == b"original"


def test_rendering_still_resolves_after_the_source_folder_is_deleted(tmp_path: Path) -> None:
    """The end this is all for. Ingest, archive, delete the unpacked
    folder, and the render job must still point at real files -- both the
    model and the pack root its textures resolve against.
    """
    staging, library, pack = _pack_with_a_gltf(tmp_path)
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    assets_dir = library / "assets"
    library_assets.archive_pack(conn, staging, assets_dir, pack_id)

    shutil.rmtree(pack)
    assert not pack.exists()

    jobs, _already = blender_render.build_job_list(
        conn, staging, library / "thumbnails", "Pack", force=True, assets_dir=assets_dir
    )
    assert len(jobs) == 1
    source = Path(jobs[0]["source_path"])
    pack_root = Path(jobs[0]["pack_root"])
    assert source.is_file(), "the render would have pointed at a deleted file"
    assert pack_root.is_dir()
    # And the buffer it needs came along, so the model is actually loadable.
    assert (source.parent / "prop.bin").is_file()
    conn.close()


def test_without_assets_dir_behaviour_is_unchanged(tmp_path: Path) -> None:
    """The parameter is optional so every existing caller -- the CLI
    included -- keeps its old behaviour until it opts in.
    """
    staging, library, pack = _pack_with_a_gltf(tmp_path)
    write_minimal_glb(pack / "extra.glb", {"meshes": [{"name": "x"}]})
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)

    jobs, _already = blender_render.build_job_list(
        conn, staging, library / "thumbnails", "Pack", force=True
    )
    assert jobs
    for job in jobs:
        assert str(staging) in job["source_path"]
    conn.close()


def _pack_with_a_texture_and_a_sound(tmp_path: Path):
    """A pack whose non-model assets also need resolving."""
    import struct
    import wave

    from PIL import Image

    staging, library = tmp_path / "staging", tmp_path / "library"
    pack = staging / "Pack"
    pack.mkdir(parents=True)
    library.mkdir()
    Image.new("RGB", (8, 8), (200, 40, 40)).save(pack / "atlas.png")
    with wave.open(str(pack / "hit.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(struct.pack("<h", 0) * 800)
    return staging, library, pack


def test_textures_and_sounds_also_resolve_after_the_source_is_deleted(tmp_path: Path) -> None:
    """The source-of-truth switch originally covered models and export
    only. Textures and audio kept reading the unpacked folder, so a 2D or
    waveform re-render broke once it was cleaned up -- the one thing the
    cleanup was not supposed to be able to do.
    """
    import shutil

    from asset_catalogue import audio_thumbnails, library_assets, thumbnails

    staging, library, pack = _pack_with_a_texture_and_a_sound(tmp_path)
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    assets_dir = library / "assets"
    library_assets.archive_pack(conn, staging, assets_dir, pack_id)

    shutil.rmtree(pack)
    assert not pack.exists()

    texture_stats = thumbnails.generate_texture_thumbnails(
        conn, staging, library / "thumbnails", force=True, assets_dir=assets_dir
    )
    assert texture_stats.generated == 1
    assert texture_stats.failed == 0

    audio_stats = audio_thumbnails.generate_audio_thumbnails(
        conn, staging, library / "thumbnails", force=True, assets_dir=assets_dir
    )
    assert audio_stats.generated == 1
    assert audio_stats.failed == 0
    conn.close()


def test_without_assets_dir_those_two_still_read_the_original(tmp_path: Path) -> None:
    """Optional, so every caller that has not opted in is unaffected."""
    import shutil

    from asset_catalogue import library_assets, thumbnails

    staging, library, pack = _pack_with_a_texture_and_a_sound(tmp_path)
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    library_assets.archive_pack(conn, staging, library / "assets", pack_id)

    shutil.rmtree(pack)
    stats = thumbnails.generate_texture_thumbnails(
        conn, staging, library / "thumbnails", force=True
    )
    # Reads the deleted original, so it fails -- which is exactly the old
    # behaviour, kept until a caller asks for the new one.
    assert stats.generated == 0
    conn.close()
