from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from asset_catalogue import (
    archives,
    blender_render,
    db,
    importing,
    ingest,
    library_assets,
    packs,
    removal,
    settings,
    tagging,
    thumbnails,
)


def _connect() -> sqlite3.Connection:
    s = settings.load()
    if not s.library_folder:
        raise SystemExit(
            "No library folder configured. Run: "
            "asset-catalogue settings set --library-folder <path>"
        )
    Path(s.library_folder).mkdir(parents=True, exist_ok=True)
    return db.connect(s.db_path())


def _print_ingest_result(pack_name: str, stats: ingest.IngestStats) -> None:
    print(
        f"Ingested '{pack_name}': {stats.new} new, "
        f"{stats.duplicate} duplicate, {stats.total} scanned"
    )
    if stats.nested_zips_extracted:
        print(
            f"  (unpacked {stats.nested_zips_extracted} nested zip file(s) "
            "found inside the pack)"
        )
    if stats.skipped_engine_files or stats.skipped_engine_folders:
        print(
            f"  (skipped {stats.skipped_engine_files} Unity/Unreal project file(s) "
            f"and {stats.skipped_engine_folders} project folder(s) -- not asset content)"
        )
    print(f"  (archived {stats.archived} asset(s) to the library)")


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
    source_path = staging_folder / args.pack_folder

    # A child of the staging folder that turns out to be a .zip is handled
    # transparently -- extracted first -- rather than requiring the
    # separate ingest-zip command for something already sitting in staging.
    if source_path.is_file() and source_path.suffix.lower() == ".zip":
        pack_folder = source_path.stem
        pack_root = staging_folder / pack_folder
        try:
            archives.extract_zip(source_path, pack_root)
        except (FileExistsError, archives.UnsafeZipError) as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Extracted '{source_path.name}' to {pack_root}")
    else:
        pack_folder = args.pack_folder
        pack_root = source_path
        if not pack_root.is_dir():
            raise SystemExit(f"Pack folder not found: {pack_root}")

    conn = _connect()
    pack_id, updated_fields = ingest.get_or_create_pack(
        conn, args.pack_name, pack_folder, args.creator, args.licence, args.source_url
    )
    if updated_fields:
        print(f"Updated pack metadata: {', '.join(updated_fields)}")
    stats = ingest.ingest_pack(conn, pack_root, pack_id)
    stats.archived = library_assets.archive_pack(conn, staging_folder, s.assets_dir(), pack_id)
    _print_ingest_result(args.pack_name, stats)


def cmd_ingest_zip(args: argparse.Namespace) -> None:
    s = settings.load()
    if not s.staging_folder:
        raise SystemExit(
            "No staging folder configured. Run: "
            "asset-catalogue settings set --staging-folder <path>"
        )
    zip_path = Path(args.zip_path)
    if not zip_path.is_file():
        raise SystemExit(f"Zip file not found: {zip_path}")

    pack_folder = args.pack_folder or zip_path.stem
    pack_root = Path(s.staging_folder) / pack_folder

    try:
        archives.extract_zip(zip_path, pack_root)
    except (FileExistsError, archives.UnsafeZipError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Extracted '{zip_path.name}' to {pack_root}")

    conn = _connect()
    pack_id, updated_fields = ingest.get_or_create_pack(
        conn, args.pack_name, pack_folder, args.creator, args.licence, args.source_url
    )
    if updated_fields:
        print(f"Updated pack metadata: {', '.join(updated_fields)}")
    stats = ingest.ingest_pack(conn, pack_root, pack_id)
    stats.archived = library_assets.archive_pack(
        conn, Path(s.staging_folder), s.assets_dir(), pack_id
    )
    _print_ingest_result(args.pack_name, stats)


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


def cmd_import(args: argparse.Namespace) -> None:
    if not any([args.pack, args.type, args.tag, args.asset_id, args.all]):
        raise SystemExit(
            "Refusing to import the entire catalogue unfiltered. Pass --pack, --type, "
            "--tag, or --asset-id, or --all to import everything on purpose."
        )

    s = settings.load()
    if not s.staging_folder:
        raise SystemExit(
            "No staging folder configured. Run: "
            "asset-catalogue settings set --staging-folder <path>"
        )

    project_root = Path(args.project_root)
    if not project_root.is_dir():
        raise SystemExit(f"Project folder not found: {project_root}")

    if importing.is_godot_project(project_root):
        print(f"Detected Godot project at {project_root}")
    else:
        print(f"No {importing.GODOT_PROJECT_FILE} found at {project_root} -- proceeding anyway")

    conn = _connect()
    assets = importing.select_assets(
        conn, pack=args.pack, asset_type=args.type, tag=args.tag, asset_id=args.asset_id
    )
    if not assets:
        print("No matching assets to import.")
        return

    project_identifier = str(project_root.resolve())
    stats = importing.import_assets(
        conn,
        Path(s.staging_folder),
        project_root,
        project_identifier,
        args.dest_subfolder,
        assets,
    )
    print(f"Imported {stats.copied} asset(s) into {project_root}")


def cmd_imports(args: argparse.Namespace) -> None:
    conn = _connect()
    query = (
        "SELECT imports.id, imports.project_identifier, imports.timestamp, "
        "assets.filename, packs.name AS pack_name "
        "FROM imports JOIN assets ON assets.id = imports.asset_id "
        "JOIN packs ON packs.id = assets.pack_id"
    )
    clauses = []
    params: list = []
    if args.project:
        clauses.append("imports.project_identifier = ?")
        params.append(str(Path(args.project).resolve()))
    if args.asset_id:
        clauses.append("imports.asset_id = ?")
        params.append(args.asset_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY imports.timestamp DESC"

    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("No import history found.")
        return
    for row in rows:
        print(
            f"[{row['id']}] {row['timestamp']}  {row['pack_name']} / {row['filename']}"
            f" -> {row['project_identifier']}"
        )
    print(f"{len(rows)} import record(s)")


def cmd_remove(args: argparse.Namespace) -> None:
    if not any([args.asset_id, args.pack, args.type, args.tag, args.all]):
        raise SystemExit(
            "Refusing to remove the entire catalogue unfiltered. Pass --asset-id, --pack, "
            "--type, --tag, or --all to remove everything on purpose."
        )
    conn = _connect()

    if args.asset_id:
        placeholders = ",".join("?" for _ in args.asset_id)
        rows = conn.execute(
            "SELECT assets.id, assets.filename, packs.name AS pack_name "
            "FROM assets JOIN packs ON packs.id = assets.pack_id "
            f"WHERE assets.id IN ({placeholders})",
            args.asset_id,
        ).fetchall()
    else:
        query = (
            "SELECT assets.id, assets.filename, packs.name AS pack_name "
            "FROM assets JOIN packs ON packs.id = assets.pack_id"
        )
        clauses = []
        params: list = []
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
        rows = conn.execute(query, params).fetchall()

    if not rows:
        print("No matching assets to remove.")
        return

    print(f"About to remove {len(rows)} asset(s) from the catalogue (source files are untouched):")
    for row in rows[:20]:
        print(f"  [{row['id']}] {row['pack_name']} / {row['filename']}")
    if len(rows) > 20:
        print(f"  ... and {len(rows) - 20} more")

    if not args.yes:
        answer = input("Continue? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    s = settings.load()
    stats = removal.remove_assets(
        conn, s.thumbnail_dir(), s.assets_dir(), [row["id"] for row in rows]
    )
    print(f"Removed {stats.removed} asset(s) from the catalogue.")


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
    if args.unused:
        clauses.append("NOT EXISTS (SELECT 1 FROM imports WHERE imports.asset_id = assets.id)")
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

    ingest_zip_parser = subparsers.add_parser(
        "ingest-zip", help="Extract a zip archive into the staging folder and ingest it"
    )
    ingest_zip_parser.add_argument("zip_path", help="Path to the .zip file (anywhere on disk)")
    ingest_zip_parser.add_argument(
        "--pack-folder",
        help="Destination folder name inside the staging folder (default: zip filename)",
    )
    ingest_zip_parser.add_argument("--pack-name", required=True)
    ingest_zip_parser.add_argument("--creator")
    ingest_zip_parser.add_argument("--licence")
    ingest_zip_parser.add_argument("--source-url")
    ingest_zip_parser.set_defaults(func=cmd_ingest_zip)

    list_parser = subparsers.add_parser("list", help="List catalogued assets")
    list_parser.add_argument("--pack")
    list_parser.add_argument("--type")
    list_parser.add_argument("--tag")
    list_parser.add_argument(
        "--unused", action="store_true", help="Only show assets never imported into any project"
    )
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

    import_parser = subparsers.add_parser(
        "import", help="Copy selected assets into a target project and record it"
    )
    import_parser.add_argument("project_root", help="Path to the target project")
    import_parser.add_argument("--pack")
    import_parser.add_argument("--type")
    import_parser.add_argument("--tag")
    import_parser.add_argument("--asset-id", type=int)
    import_parser.add_argument(
        "--all", action="store_true", help="Import the entire catalogue, unfiltered"
    )
    import_parser.add_argument(
        "--dest-subfolder",
        default="imported_assets",
        help="Subfolder under the project root that imports land in, grouped by pack "
        "(default: imported_assets)",
    )
    import_parser.set_defaults(func=cmd_import)

    imports_parser = subparsers.add_parser("imports", help="Show import history")
    imports_parser.add_argument("--project", help="Filter to one target project path")
    imports_parser.add_argument("--asset-id", type=int)
    imports_parser.set_defaults(func=cmd_imports)

    remove_parser = subparsers.add_parser(
        "remove",
        help="Remove assets from the catalogue (does not touch the original source files)",
    )
    remove_parser.add_argument(
        "--asset-id", type=int, action="append", help="Repeatable; remove specific asset ids"
    )
    remove_parser.add_argument("--pack")
    remove_parser.add_argument("--type")
    remove_parser.add_argument("--tag")
    remove_parser.add_argument(
        "--all", action="store_true", help="Remove the entire catalogue, unfiltered"
    )
    remove_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )
    remove_parser.set_defaults(func=cmd_remove)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
