from __future__ import annotations

import json
import sqlite3


def get_corrections(conn: sqlite3.Connection, pack_id: int) -> dict:
    row = conn.execute("SELECT corrections FROM packs WHERE id = ?", (pack_id,)).fetchone()
    if row is None or not row["corrections"]:
        return {}
    return json.loads(row["corrections"])


def set_corrections(conn: sqlite3.Connection, pack_id: int, corrections: dict) -> None:
    conn.execute(
        "UPDATE packs SET corrections = ? WHERE id = ?",
        (json.dumps(corrections) if corrections else None, pack_id),
    )
    conn.commit()
