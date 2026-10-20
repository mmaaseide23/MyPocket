"""Verified local backup and narrowly scoped recovery for a Citi replacement.

Recovery (stop MyPocket first): .venv/bin/python -m mypocket.citi_reset restore
Restores the old account view, archives the fresh view, and pauses that Plaid
connection locally. No transactions or credentials are deleted or decrypted.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def fingerprint(conn):
    digest = hashlib.sha256()
    for (table,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        quoted = '"' + table.replace('"', '""') + '"'
        digest.update(table.encode())
        for row in conn.execute(f"SELECT * FROM {quoted} ORDER BY rowid"):
            digest.update(json.dumps(tuple(row), default=str).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def verified_backup(database, locked_connection):
    """Caller holds BEGIN IMMEDIATE; backup reads the last committed snapshot."""
    database = Path(database)
    if database.is_symlink() or not database.is_file():
        raise RuntimeError("Expected an existing local database; no reset made.")
    directory = database.parent / "backups"
    if directory.is_symlink():
        raise RuntimeError("Backup directory cannot be a symlink.")
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    name = f"before-citi-reset-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.sqlite3"
    destination = directory / name
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with (
        sqlite3.connect(f"file:{database}?mode=ro", uri=True) as source,
        sqlite3.connect(destination) as saved,
    ):
        source.backup(saved)
        if saved.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("Backup integrity verification failed; no reset made.")
        if fingerprint(saved) != fingerprint(locked_connection):
            raise RuntimeError("Backup does not match current data; no reset made.")
        # Verify SQLite can restore the complete copy before changing live data.
        with sqlite3.connect(":memory:") as restored:
            saved.backup(restored)
            if fingerprint(restored) != fingerprint(saved):
                raise RuntimeError("Backup restoration verification failed; no reset made.")
    return destination


def backup_session(session):
    bind = session.get_bind()
    database = bind.url.database
    if bind.dialect.name != "sqlite" or not database or database == ":memory:":
        raise RuntimeError("A verified local SQLite backup is required for replacement.")
    connection = session.connection().connection.driver_connection
    if connection.in_transaction:
        raise RuntimeError("Finish the existing database transaction before replacement.")
    connection.execute("BEGIN IMMEDIATE")
    return verified_backup(database, connection)


def restore(database):
    """Undo exactly one replacement without overwriting later unrelated records."""
    with sqlite3.connect(database, timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        changes = conn.execute(
            "SELECT id,original_id,replacement_id,enrollment_id FROM citireset WHERE restored=0"
        ).fetchall()
        if not changes:
            return None
        if len(changes) != 1:
            raise RuntimeError("Multiple replacements require an explicit scoped recovery.")
        reset_id, original_id, replacement_id, enrollment_id = changes[0]
        original = conn.execute(
            "SELECT source,type,archived FROM account WHERE id=?", (original_id,)
        ).fetchone()
        replacement = conn.execute(
            "SELECT source,type,archived FROM account WHERE id=?", (replacement_id,)
        ).fetchone()
        if (
            not original
            or original[0] not in {"teller", "citi_csv"}
            or not original[2]
            or not replacement
            or replacement != ("plaid", original[1], 0)
            or conn.execute("SELECT provider FROM enrollment WHERE id=?", (enrollment_id,)).fetchone()
            != ("plaid",)
            or conn.execute(
                "SELECT account_id FROM plaidaccount WHERE enrollment_id=?", (enrollment_id,)
            ).fetchall()
            != [(replacement_id,)]
        ):
            raise RuntimeError("Replacement scope changed; no recovery changes made.")
        backup = verified_backup(database, conn)
        conn.execute("UPDATE account SET archived=0 WHERE id=?", (original_id,))
        conn.execute("UPDATE account SET archived=1 WHERE id=?", (replacement_id,))
        conn.execute("UPDATE enrollment SET status='paused' WHERE id=?", (enrollment_id,))
        conn.execute("UPDATE citireset SET restored=1 WHERE id=?", (reset_id,))
        return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["restore"])
    parser.parse_args()
    try:
        backup = restore(Path(__file__).resolve().parents[1] / "data/mypocket.db")
    except (RuntimeError, OSError, sqlite3.Error):
        raise SystemExit(
            "Recovery stopped; inspect local state before retrying. No partial change committed."
        ) from None
    print(
        "Old Citi view restored; fresh history archived and Plaid paused locally."
        if backup
        else "No active Citi replacement needs restoration."
    )


if __name__ == "__main__":
    main()
