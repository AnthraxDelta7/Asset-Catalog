"""A model's uncatalogued dependencies must reach the library with it.

Ingest catalogues a whitelist of content extensions, and the archiver
copies catalogued rows -- so a .gltf's .bin and a .obj's .mtl were never
archived at all. The library's copy of those models loaded only while the
original folder happened to still exist.
"""

from __future__ import annotations

import json
from pathlib import Path

from asset_catalogue import db, dependencies, ingest, library_assets


def _write_gltf(path: Path, buffer_uri: str, image_uri: str | None = None) -> None:
    document = {
        "asset": {"version": "2.0"},
        "meshes": [{"name": "m"}],
        "buffers": [{"uri": buffer_uri, "byteLength": 4}],
    }
    if image_uri is not None:
        document["images"] = [{"uri": image_uri}]
    path.write_text(json.dumps(document), encoding="utf-8")


def test_a_gltf_names_its_buffer_and_images(tmp_path: Path) -> None:
    pack = tmp_path / "Pack"
    (pack / "models").mkdir(parents=True)
    (pack / "textures").mkdir()
    (pack / "models" / "prop.bin").write_bytes(b"\x00\x01\x02\x03")
    (pack / "textures" / "diffuse.png").write_bytes(b"png")
    _write_gltf(pack / "models" / "prop.gltf", "prop.bin", "../textures/diffuse.png")

    found = dependencies.referenced_files(pack / "models" / "prop.gltf", pack)
    assert found == {pack / "models" / "prop.bin", pack / "textures" / "diffuse.png"}


def test_an_obj_names_its_mtl_and_that_mtls_textures(tmp_path: Path) -> None:
    pack = tmp_path / "Pack"
    pack.mkdir()
    (pack / "prop.obj").write_text("mtllib prop.mtl\nv 0 0 0\n", encoding="utf-8")
    (pack / "prop.mtl").write_text(
        "newmtl body\nmap_Kd textures/body.png\nmap_Bump -bm 0.5 textures/body_n.png\n",
        encoding="utf-8",
    )
    (pack / "textures").mkdir()
    (pack / "textures" / "body.png").write_bytes(b"png")
    (pack / "textures" / "body_n.png").write_bytes(b"png")

    found = dependencies.referenced_files(pack / "prop.obj", pack)
    assert found == {
        pack / "prop.mtl",
        pack / "textures" / "body.png",
        pack / "textures" / "body_n.png",
    }


def test_references_that_escape_the_pack_are_refused(tmp_path: Path) -> None:
    """A reference is arbitrary text out of a downloaded file. Following
    one blindly would copy files from outside the pack into the library.
    """
    pack = tmp_path / "Pack"
    pack.mkdir()
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"not yours")

    (pack / "prop.obj").write_text("mtllib prop.mtl\n", encoding="utf-8")
    (pack / "prop.mtl").write_text(
        "map_Kd ../secret.png\n"
        "map_Ks C:/Windows/System32/drivers/etc/hosts\n"
        "map_Ka /etc/passwd\n"
        "map_Bump https://example.com/tex.png\n",
        encoding="utf-8",
    )
    assert dependencies.referenced_files(pack / "prop.obj", pack) == {pack / "prop.mtl"}


def test_embedded_and_missing_references_are_ignored(tmp_path: Path) -> None:
    pack = tmp_path / "Pack"
    pack.mkdir()
    _write_gltf(pack / "a.gltf", "data:application/octet-stream;base64,AAEC")
    assert dependencies.referenced_files(pack / "a.gltf", pack) == set()

    _write_gltf(pack / "b.gltf", "nowhere.bin")
    assert dependencies.referenced_files(pack / "b.gltf", pack) == set()


def test_a_malformed_file_yields_nothing_rather_than_raising(tmp_path: Path) -> None:
    pack = tmp_path / "Pack"
    pack.mkdir()
    (pack / "broken.gltf").write_text("{not json at all", encoding="utf-8")
    (pack / "broken.obj").write_bytes(b"\x00\xff\xfe binary junk")
    assert dependencies.referenced_files(pack / "broken.gltf", pack) == set()
    assert dependencies.referenced_files(pack / "broken.obj", pack) == set()
    assert dependencies.referenced_files(pack / "nothing.fbx", pack) == set()


def test_archiving_a_gltf_brings_its_buffer_into_the_library(tmp_path: Path) -> None:
    """The end the user sees: a library copy that actually loads."""
    staging, library = tmp_path / "staging", tmp_path / "library"
    pack = staging / "Pack"
    (pack / "models").mkdir(parents=True)
    (pack / "models" / "prop.bin").write_bytes(b"\x00\x01\x02\x03")
    _write_gltf(pack / "models" / "prop.gltf", "prop.bin")
    library.mkdir()

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)

    assets_dir = library / "assets"
    archived = library_assets.archive_pack(conn, staging, assets_dir, pack_id)
    assert archived == 1  # the .bin is not an asset, and must not become one

    gltf_copy = assets_dir / "Pack" / "models" / "prop.gltf"
    bin_copy = assets_dir / "Pack" / "models" / "prop.bin"
    assert gltf_copy.is_file()
    assert bin_copy.is_file(), "a .gltf without its buffer has no geometry at all"
    assert bin_copy.read_bytes() == b"\x00\x01\x02\x03"
    conn.close()


def test_map_statement_options_are_not_mistaken_for_the_filename(tmp_path: Path) -> None:
    """map_Bump -bm 0.5 tex.png names tex.png. Options always start with
    "-" and take numeric arguments; the path is what is left, joined
    whole because a filename may contain spaces.
    """
    assert dependencies._strip_mtl_options("-bm 0.5 textures/n.png") == "textures/n.png"
    assert dependencies._strip_mtl_options("-o 1 2 3 -s 1 1 1 t.png") == "t.png"
    assert dependencies._strip_mtl_options("plain.png") == "plain.png"
    assert dependencies._strip_mtl_options("my texture.png") == "my texture.png"
    assert dependencies._strip_mtl_options("-clamp on tex.png").endswith("tex.png")
