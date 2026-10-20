"""Recoverable local archive of the one legacy E*TRADE CSV account.

Run from the repo: .venv/bin/python -m mypocket.maintenance archive
Recovery:         .venv/bin/python -m mypocket.maintenance restore
No account, holding, transaction, category, note or authorization is deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _fingerprints(connection):
    result = {}
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        columns = [r[1] for r in connection.execute(f"PRAGMA table_info({_quoted(table)})")]
        # This is the sole field this operation is permitted to change.
        if table == "account":
            columns = [c for c in columns if c != "archived"]
        query = f"SELECT {','.join(_quoted(c) for c in columns)} FROM {_quoted(table)} ORDER BY rowid"
        digest = hashlib.sha256()
        for row in connection.execute(query):
            digest.update(json.dumps(row, default=str, ensure_ascii=True).encode())
            digest.update(b"\n")
        result[table] = digest.hexdigest()
    return result


def set_legacy_archived(database: Path, archived: bool) -> Path | None:
    """Require exactly one CSV source; create and verify a private SQLite backup."""
    if database.is_symlink() or not database.is_file():
        raise RuntimeError("Expected a regular existing MyPocket database.")
    backup = None
    with sqlite3.connect(database, timeout=10) as conn:
        candidates = conn.execute("SELECT id, archived FROM account WHERE source='etrade_csv'").fetchall()
        if len(candidates) != 1:
            raise RuntimeError("Expected exactly one legacy E*TRADE CSV account; no change made.")
        account_id, current = candidates[0]
        if bool(current) == archived:
            return None
        directory = database.parent / "backups"
        if directory.is_symlink():
            raise RuntimeError("Backup directory must not be a symlink.")
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = directory / f"before-etrade-csv-archive-{stamp}-{uuid4().hex[:8]}.sqlite3"
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(backup) as saved:
            conn.backup(saved)
        with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as saved:
            if saved.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("Backup verification failed; no account changed.")
            before = _fingerprints(saved)
        conn.execute("BEGIN IMMEDIATE")
        if _fingerprints(conn) != before:
            raise RuntimeError(
                "Database changed during backup; no account changed. Retry after sync finishes."
            )
        other_states = conn.execute(
            "SELECT id,archived FROM account WHERE id!=? ORDER BY id", (account_id,)
        ).fetchall()
        changed = conn.execute(
            "UPDATE account SET archived=? WHERE id=? AND source='etrade_csv' AND archived=?",
            (int(archived), account_id, int(bool(current))),
        )
        if changed.rowcount != 1 or _fingerprints(conn) != before:
            raise RuntimeError("Archive preservation check failed; transaction rolled back.")
        if (
            conn.execute("SELECT id,archived FROM account WHERE id!=? ORDER BY id", (account_id,)).fetchall()
            != other_states
        ):
            raise RuntimeError("Unrelated account state changed; transaction rolled back.")
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("archive", "restore"))
    args = parser.parse_args()
    database = Path(__file__).resolve().parents[1] / "data/mypocket.db"
    try:
        backup = set_legacy_archived(database, args.action == "archive")
    except (RuntimeError, sqlite3.Error, OSError):
        raise SystemExit(
            "Operation stopped without a committed account change; inspect local state before retrying."
        ) from None
    if backup is None:
        print("Legacy CSV account already has the requested archive state. No changes made.")
    else:
        print("Legacy CSV archive state updated. Verified private backup is in data/backups/.")
        print(
            "All other account fields, holdings, transactions, notes, categories and authorizations preserved."
        )


if __name__ == "__main__":
    main()
