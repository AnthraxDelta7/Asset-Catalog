from __future__ import annotations

import zipfile
from pathlib import Path


class UnsafeZipError(Exception):
    pass


def extract_zip(zip_path: Path, destination: Path) -> None:
    """Extract zip_path into destination, creating it if needed.

    Refuses to extract into a destination that already has files in it
    (avoids silently merging/overwriting an existing pack), and rejects any
    entry whose path would land outside destination -- a zip can contain
    "../" path traversal entries (zip-slip); this tool processes archives
    from arbitrary purchased asset packs, so that's worth guarding against.
    """
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Destination already has files in it: {destination}")

    resolved_destination = destination.resolve()

    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = (destination / member.filename).resolve()
            if not member_path.is_relative_to(resolved_destination):
                raise UnsafeZipError(
                    f"Refusing to extract unsafe path outside destination: {member.filename}"
                )
        destination.mkdir(parents=True, exist_ok=True)
        archive.extractall(destination)
