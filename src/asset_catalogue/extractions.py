"""Folders this app unpacked, so it can clear up after itself.

Deleting a leftover unpacked folder is only safe if we are certain we
created it. A folder sitting beside a zip of the same name looks
identical whether this app extracted it or the user did months ago, and
guessing wrong deletes someone's work -- so nothing is inferred. Each
extraction is written down as it happens, and only paths on that list are
ever removed.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def record(conn: sqlite3.Connection, path: Path, pack_id: int | None = None) -> None:
    """Notes that this app created `path`. Safe to call more than once."""
    conn.execute(
        "INSERT INTO extractions (path, pack_id, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET pack_id = COALESCE(excluded.pack_id, pack_id)",
        (str(path), pack_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def claim(conn: sqlite3.Connection, pack_id: int) -> None:
    """Attaches any not-yet-attributed extraction to a pack.

    An extraction happens before the pack row exists -- the folder has to
    be unpacked before there is anything to catalogue -- so the link is
    made afterwards rather than at record time.
    """
    conn.execute("UPDATE extractions SET pack_id = ? WHERE pack_id IS NULL", (pack_id,))
    conn.commit()


def for_pack(conn: sqlite3.Connection, pack_id: int) -> list[Path]:
    return [
        Path(row["path"])
        for row in conn.execute(
            "SELECT path FROM extractions WHERE pack_id = ?", (pack_id,)
        )
    ]


def forget(conn: sqlite3.Connection, path: Path) -> None:
    conn.execute("DELETE FROM extractions WHERE path = ?", (str(path),))
    conn.commit()


def forget_pack(conn: sqlite3.Connection, pack_id: int) -> None:
    conn.execute("DELETE FROM extractions WHERE pack_id = ?", (pack_id,))
    conn.commit()


def cleanup_pack(conn: sqlite3.Connection, pack_id: int) -> tuple[int, list[str]]:
    """Removes the folders this app unpacked for a pack. Returns
    (folders removed, problems).

    Deepest first, so a nested extraction inside a pack folder is gone
    before the folder containing it -- otherwise removing the outer one
    leaves a stale row pointing into nothing.

    The caller is responsible for having archived the pack first; this
    deliberately does not check, because "is the library copy complete"
    is a question about assets, not about folders, and answering it here
    would mean this module knowing about both.
    """
    problems: list[str] = []
    removed = 0
    for path in sorted(for_pack(conn, pack_id), key=lambda p: len(p.parts), reverse=True):
        if not path.exists():
            forget(conn, path)
            continue
        if not path.is_dir():
            # Never recorded as anything but a directory; if it is a file
            # now, something else has been at it and it is not ours to
            # remove.
            problems.append(f"{path} is no longer a folder -- left alone")
            continue
        try:
            shutil.rmtree(path)
            removed += 1
            forget(conn, path)
        except OSError as exc:
            problems.append(f"Could not remove {path}: {exc}")
    return removed, problems
