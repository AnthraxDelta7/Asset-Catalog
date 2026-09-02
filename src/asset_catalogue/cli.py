from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from asset_catalogue import blender_render, db, ingest, packs, settings, tagging, thumbnails


def _connect() -> sqlite3.Connection:
    s = settings.load()
    if not s.library_folder:
        raise SystemExit(
            "No library folder configured. Run: "
            "asset-catalogue settings set --library-folder <path>"
        )
    Path(s.library_folder).mkdir(parents=True, exist_ok=True)
    return db.connect(s.db_path())


def _get_pack_id(conn: sqlite3.Connection, pack_name: str) -> int:
    row = conn.execute("SELECT id FROM packs WHERE name = ?", (pack_name,)).fetchone()
    if row is None:
        raise SystemExit(f"No such pack: {pack_name}")
    return row["id"]


def _get_asset_id(conn: sqlite3.Connection, asset_id: int) -> int:
    row = conn.execute("SELECT id FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise SystemExit(f"No such asset id: {asset_id}")
    return row["id"]


def _get_tag_id(conn: sqlite3.Connection, tag_name: str) -> int:
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
    if row is None:
        raise SystemExit(f"No such tag: {tag_name}")
    return row["id"]


def cmd_settings_show(args: argparse.Namespace) -> None:
    s = settings.load()
    print(f"staging_folder: {s.staging_folder}")
    print(f"library_folder: {s.library_folder}")
    if s.library_folder:
        print(f"  -> database:   {s.db_path()}")
        print(f"  -> thumbnails: {s.thumbnail_dir()}")
    print(f"blender_path:   {s.blender_path}")


def cmd_settings_set(args: argparse.Namespace) -> None:
    s = settings.load()
    if args.staging_folder is not None:
        s.staging_folder = args.staging_folder
    if args.library_folder is not None:
        s.library_folder = args.library_folder
    if args.blender_path is not None:
        s.blender_path = args.blender_path
    settings.save(s)
    print(f"Saved to {settings.SETTINGS_PATH}")


def cmd_ingest(args: argparse.Namespace) -> None:
    s = settings.load()
    if not s.staging_folder:
        raise SystemExit(
            "No staging folder configured. Run: "
            "asset-catalogue settings set --staging-folder <path>"
        )
    staging_folder = Path(s.staging_folder)
    pack_root = staging_folder / args.pack_folder
    if not pack_root.is_dir():
        raise SystemExit(f"Pack folder not found: {pack_root}")

    conn = _connect()
    pack_id = ingest.get_or_create_pack(
        conn, args.pack_name, args.pack_folder, args.creator, args.licence, args.source_url
    )
    stats = ingest.ingest_pack(conn, pack_root, pack_id)
    print(
        f"Ingested '{args.pack_name}': {stats.new} new, "
        f"{stats.duplicate} duplicate, {stats.total} scanned"
    )


def cmd_tag_pack(args: argparse.Namespace) -> None:
    conn = _connect()
    pack_id = _get_pack_id(conn, args.pack_name)
    tag_id = tagging.get_or_create_tag(conn, args.tag_name, args.category)
    applied = tagging.tag_pack(conn, pack_id, tag_id)
    print(
        f"Applied '{args.tag_name}' to {applied} asset(s) in '{args.pack_name}' "
        "(already-tagged assets left untouched)"
    )


def cmd_tag_asset(args: argparse.Namespace) -> None:
    conn = _connect()
    asset_id = _get_asset_id(conn, args.asset_id)
    tag_id = tagging.get_or_create_tag(conn, args.tag_name, args.category)
    tagging.tag_asset(conn, asset_id, tag_id)
    print(f"Tagged asset {asset_id} with '{args.tag_name}' (explicit)")


def cmd_untag_asset(args: argparse.Namespace) -> None:
    conn = _connect()
    asset_id = _get_asset_id(conn, args.asset_id)
    tag_id = _get_tag_id(conn, args.tag_name)
    removed = tagging.untag_asset(conn, asset_id, tag_id)
    if removed:
        print(f"Removed '{args.tag_name}' from asset {asset_id}")
    else:
        print(f"Asset {asset_id} did not have tag '{args.tag_name}'")


def cmd_tags(args: argparse.Namespace) -> None:
    conn = _connect()
    rows = conn.execute(
        "SELECT tags.name, tags.category, COUNT(asset_tags.asset_id) AS usage_count "
        "FROM tags LEFT JOIN asset_tags ON asset_tags.tag_id = tags.id "
        "GROUP BY tags.id ORDER BY tags.name"
    ).fetchall()
    if not rows:
        print("No tags yet.")
        return
    for row in rows:
        category = row["category"] or "-"
        print(f"{row['name']} (category={category}): {row['usage_count']} asset(s)")


def cmd_thumbnail_generate(args: argparse.Namespace) -> None:
    s = settings.load()
    if not s.staging_folder:
        raise SystemExit(
            "No staging folder configured. Run: "
            "asset-catalogue settings set --staging-folder <path>"
        )
    conn = _connect()
    stats = thumbnails.generate_texture_thumbnails(
        conn,
        Path(s.staging_folder),
        s.thumbnail_dir(),
        pack_name=args.pack,
        force=args.force,
    )
    print(
        f"Thumbnails: {stats.generated} generated, "
        f"{stats.already_done} already done, {stats.failed} failed"
    )


def cmd_thumbnail_generate_models(args: argparse.Namespace) -> None:
    s = settings.load()
    if not s.staging_folder:
        raise SystemExit(
            "No staging folder configured. Run: "
            "asset-catalogue settings set --staging-folder <path>"
        )

    blender_exe = blender_render.find_blender(s.blender_path)
    if blender_exe is None:
        raise SystemExit(
            "Blender not found. Install it, or point at it with: "
            "asset-catalogue settings set --blender-path <path to blender.exe>"
        )

    version = blender_render.get_blender_version(blender_exe)
    if version is None:
        raise SystemExit(f"Could not determine Blender's version from {blender_exe}")
    if version < blender_render.MIN_BLENDER_VERSION:
        min_version = ".".join(str(part) for part in blender_render.MIN_BLENDER_VERSION)
        found_version = ".".join(str(part) for part in version)
        raise SystemExit(
            f"Blender {found_version} is older than the minimum supported "
            f"{min_version} (found at {blender_exe})"
        )

    conn = _connect()
    stats = blender_render.generate_model_thumbnails(
        conn,
        Path(s.staging_folder),
        s.thumbnail_dir(),
        blender_exe,
        pack_name=args.pack,
        force=args.force,
        asset_id=args.asset_id,
    )
    print(
        f"Model thumbnails: {stats.generated} generated, "
        f"{stats.already_done} already done, {stats.failed} failed"
    )


def cmd_pack_show_corrections(args: argparse.Namespace) -> None:
    conn = _connect()
    pack_id = _get_pack_id(conn, args.pack_name)
    corrections = packs.get_corrections(conn, pack_id)
    if not corrections:
        print(f"'{args.pack_name}' has no corrections set.")
        return
    for key, value in corrections.items():
        print(f"{key}: {value}")


def cmd_pack_set_corrections(args: argparse.Namespace) -> None:
    conn = _connect()
    pack_id = _get_pack_id(conn, args.pack_name)

    if args.clear:
        packs.set_corrections(conn, pack_id, {})
        print(f"Cleared corrections for '{args.pack_name}'")
        return

    corrections = packs.get_corrections(conn, pack_id)
    if args.up_axis is not None:
        corrections["up_axis"] = args.up_axis
    if args.scale is not None:
        corrections["scale"] = args.scale
    if args.material_fallback is not None:
        corrections["material_fallback"] = args.material_fallback

    if not corrections:
        raise SystemExit("No corrections given. Pass --up-axis, --scale, or --material-fallback.")

    packs.set_corrections(conn, pack_id, corrections)
    print(f"Corrections for '{args.pack_name}': {corrections}")
    print(
        "Re-render to see the effect: asset-catalogue thumbnail generate-models "
        f"--pack \"{args.pack_name}\" --force"
    )


def cmd_list(args: argparse.Namespace) -> None:
    conn = _connect()
    query = (
        "SELECT assets.id, assets.filename, assets.asset_type, "
        "assets.thumbnail_status, packs.name AS pack_name "
        "FROM assets JOIN packs ON packs.id = assets.pack_id"
    )
    clauses = []
    params: list[str] = []
    if args.tag:
        query += (
            " JOIN asset_tags ON asset_tags.asset_id = assets.id"
            " JOIN tags ON tags.id = asset_tags.tag_id"
        )
        clauses.append("tags.name = ?")
        params.append(args.tag)
    if args.pack:
        clauses.append("packs.name = ?")
        params.append(args.pack)
    if args.type:
        clauses.append("assets.asset_type = ?")
        params.append(args.type)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY packs.name, assets.relative_path"

    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("No assets found.")
        return
    for row in rows:
        tag_rows = conn.execute(
            "SELECT tags.name FROM tags JOIN asset_tags ON asset_tags.tag_id = tags.id "
            "WHERE asset_tags.asset_id = ? ORDER BY tags.name",
            (row["id"],),
        ).fetchall()
        tag_names = ", ".join(t["name"] for t in tag_rows) or "-"
        print(
            f"[{row['id']}] {row['pack_name']} / {row['filename']} "
            f"({row['asset_type']}, thumbnail={row['thumbnail_status']}, tags={tag_names})"
        )
    print(f"{len(rows)} asset(s)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asset-catalogue")
    subparsers = parser.add_subparsers(dest="command", required=True)

    settings_parser = subparsers.add_parser("settings", help="View or change settings")
    settings_sub = settings_parser.add_subparsers(dest="settings_command", required=True)

    show_parser = settings_sub.add_parser("show", help="Show current settings")
    show_parser.set_defaults(func=cmd_settings_show)

    set_parser = settings_sub.add_parser("set", help="Set one or more settings")
    set_parser.add_argument("--staging-folder")
    set_parser.add_argument(
        "--library-folder",
        help="Folder holding catalogue.db and thumbnails/ -- portable, point at it "
        "on a new machine or a shared location to pick up an existing library",
    )
    set_parser.add_argument("--blender-path")
    set_parser.set_defaults(func=cmd_settings_set)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest a pack from the staging folder")
    ingest_parser.add_argument("pack_folder", help="Folder name inside the staging folder")
    ingest_parser.add_argument("--pack-name", required=True)
    ingest_parser.add_argument("--creator")
    ingest_parser.add_argument("--licence")
    ingest_parser.add_argument("--source-url")
    ingest_parser.set_defaults(func=cmd_ingest)

    list_parser = subparsers.add_parser("list", help="List catalogued assets")
    list_parser.add_argument("--pack")
    list_parser.add_argument("--type")
    list_parser.add_argument("--tag")
    list_parser.set_defaults(func=cmd_list)

    tag_parser = subparsers.add_parser("tag", help="Apply a tag to a pack or an asset")
    tag_sub = tag_parser.add_subparsers(dest="tag_command", required=True)

    tag_pack_parser = tag_sub.add_parser(
        "pack", help="Cascade a tag onto every asset currently in a pack"
    )
    tag_pack_parser.add_argument("pack_name")
    tag_pack_parser.add_argument("tag_name")
    tag_pack_parser.add_argument("--category")
    tag_pack_parser.set_defaults(func=cmd_tag_pack)

    tag_asset_parser = tag_sub.add_parser("asset", help="Explicitly tag one asset")
    tag_asset_parser.add_argument("asset_id", type=int)
    tag_asset_parser.add_argument("tag_name")
    tag_asset_parser.add_argument("--category")
    tag_asset_parser.set_defaults(func=cmd_tag_asset)

    untag_parser = subparsers.add_parser("untag", help="Remove a tag from an asset")
    untag_sub = untag_parser.add_subparsers(dest="untag_command", required=True)

    untag_asset_parser = untag_sub.add_parser("asset", help="Remove a tag from one asset")
    untag_asset_parser.add_argument("asset_id", type=int)
    untag_asset_parser.add_argument("tag_name")
    untag_asset_parser.set_defaults(func=cmd_untag_asset)

    tags_parser = subparsers.add_parser("tags", help="List the tag vocabulary and usage counts")
    tags_parser.set_defaults(func=cmd_tags)

    thumbnail_parser = subparsers.add_parser("thumbnail", help="Generate thumbnails")
    thumbnail_sub = thumbnail_parser.add_subparsers(dest="thumbnail_command", required=True)

    thumbnail_generate_parser = thumbnail_sub.add_parser(
        "generate", help="Generate thumbnails for texture (2D) assets"
    )
    thumbnail_generate_parser.add_argument("--pack")
    thumbnail_generate_parser.add_argument(
        "--force", action="store_true", help="Re-render even assets already marked done"
    )
    thumbnail_generate_parser.set_defaults(func=cmd_thumbnail_generate)

    thumbnail_generate_models_parser = thumbnail_sub.add_parser(
        "generate-models", help="Generate thumbnails for model (3D) assets via Blender"
    )
    thumbnail_generate_models_parser.add_argument("--pack")
    thumbnail_generate_models_parser.add_argument(
        "--force", action="store_true", help="Re-render even assets already marked done"
    )
    thumbnail_generate_models_parser.add_argument(
        "--asset-id",
        type=int,
        help="Render just this one asset (for previewing pack corrections), "
        "regardless of its current thumbnail status",
    )
    thumbnail_generate_models_parser.set_defaults(func=cmd_thumbnail_generate_models)

    pack_parser = subparsers.add_parser("pack", help="View or set per-pack render corrections")
    pack_sub = pack_parser.add_subparsers(dest="pack_command", required=True)

    show_corrections_parser = pack_sub.add_parser(
        "show-corrections", help="Show a pack's current render corrections"
    )
    show_corrections_parser.add_argument("pack_name")
    show_corrections_parser.set_defaults(func=cmd_pack_show_corrections)

    set_corrections_parser = pack_sub.add_parser(
        "set-corrections",
        help="Set render corrections for a pack, applied by 'thumbnail generate-models'",
    )
    set_corrections_parser.add_argument("pack_name")
    set_corrections_parser.add_argument(
        "--up-axis", choices=["Y_UP", "Z_UP"], help="Rotate imports 90deg if the pack is Y-up"
    )
    set_corrections_parser.add_argument("--scale", type=float)
    set_corrections_parser.add_argument(
        "--material-fallback",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Replace imported materials with a flat gray fallback",
    )
    set_corrections_parser.add_argument(
        "--clear", action="store_true", help="Remove all corrections for this pack"
    )
    set_corrections_parser.set_defaults(func=cmd_pack_set_corrections)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
