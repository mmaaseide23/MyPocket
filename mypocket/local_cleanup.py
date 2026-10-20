"""Recoverable local retirement of stale Citi display and obsolete connection attempts.

Never deletes records, reads decrypted credentials, or contacts a provider.
"""

import argparse
import json
import os
import sqlite3
from datetime import date
from pathlib import Path

from mypocket.citi_reset import verified_backup


def _receipt(database):
    return Path(database).parent / "backups/local-cleanup.json"


def _read(conn, change):
    if (change["table"], change["column"]) not in {("account", "archived"), ("enrollment", "status")}:
        raise RuntimeError("Invalid recovery scope.")
    return conn.execute(
        f'SELECT "{change["column"]}" FROM "{change["table"]}" WHERE id=?', (change["id"],)
    ).fetchone()[0]


def _apply(conn, changes, key):
    for change in changes:
        conn.execute(
            f'UPDATE "{change["table"]}" SET "{change["column"]}"=? WHERE id=?',
            (change[key], change["id"]),
        )


def retire(database, since):
    database = Path(database)
    receipt = _receipt(database)
    with sqlite3.connect(database, timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if receipt.exists():
            changes = json.loads(receipt.read_text())["changes"]
            if all(_read(conn, c) == c["after"] for c in changes):
                return None
            raise RuntimeError("Existing cleanup receipt requires review before another cleanup.")
        cards = conn.execute(
            """SELECT a.id FROM account a WHERE source IN ('teller','citi_csv')
            AND type='credit' AND lower(institution) LIKE 'citi%' AND archived=0
            AND NOT EXISTS(SELECT 1 FROM plaidaccount p WHERE p.account_id=a.id)"""
        ).fetchall()
        if len(cards) != 1:
            raise RuntimeError("Expected one unlinked legacy Citi card; no cleanup made.")
        card = cards[0][0]
        if not conn.execute(
            """SELECT 1 FROM account a JOIN plaidaccount p ON p.account_id=a.id
            JOIN plaidconnection c ON c.enrollment_id=p.enrollment_id
            JOIN enrollment e ON e.id=c.enrollment_id
            WHERE a.type='checking' AND a.archived=0 AND c.approved=1 AND e.status='active'"""
        ).fetchone():
            raise RuntimeError("An active approved Plaid checking connection is required.")
        if conn.execute(
            """SELECT 1 FROM account a WHERE a.source='teller' AND a.archived=0 AND a.id!=?
            AND NOT EXISTS(SELECT 1 FROM plaidaccount p WHERE p.account_id=a.id)""",
            (card,),
        ).fetchone():
            raise RuntimeError("Another active Teller account still needs the provider.")
        active = conn.execute(
            "SELECT created_at FROM enrollment WHERE provider='etrade' AND status='active'"
        ).fetchall()
        if len(active) != 1:
            raise RuntimeError("Expected one working E*TRADE authorization before archiving attempts.")
        changes = [{"table": "account", "column": "archived", "id": card, "before": 0, "after": 1}]
        for eid, status in conn.execute(
            "SELECT id,status FROM enrollment WHERE provider='teller' AND status IN ('active','needs_reauth')"
        ):
            changes.append(
                {"table": "enrollment", "column": "status", "id": eid, "before": status, "after": "disabled"}
            )
        for eid, status in conn.execute(
            """SELECT id,status FROM enrollment WHERE provider='etrade'
            AND status IN ('needs_reauth','pending_verifier')
            AND datetime(created_at)>=datetime(?) AND datetime(created_at)<datetime(?)""",
            (since.isoformat(), active[0][0]),
        ):
            changes.append(
                {"table": "enrollment", "column": "status", "id": eid, "before": status, "after": "archived"}
            )
        backup = verified_backup(database, conn)
        fd = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump({"backup": backup.name, "changes": changes}, out)
        _apply(conn, changes, "after")
        return receipt


def restore(database):
    receipt = _receipt(database)
    if receipt.is_symlink():
        raise RuntimeError("Recovery receipt must not be a symlink.")
    changes = json.loads(receipt.read_text())["changes"]
    with sqlite3.connect(database, timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if all(_read(conn, c) == c["before"] for c in changes):
            return None
        if not all(_read(conn, c) == c["after"] for c in changes):
            raise RuntimeError("Local state changed after cleanup; review before recovery.")
        backup = verified_backup(database, conn)
        _apply(conn, changes, "before")
        return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["retire", "restore"])
    parser.add_argument("--since", type=date.fromisoformat)
    args = parser.parse_args()
    if args.action == "retire" and args.since is None:
        parser.error("--since is required to scope obsolete attempts to a repair session.")
    database = Path(__file__).resolve().parents[1] / "data/mypocket.db"
    try:
        result = retire(database, args.since) if args.action == "retire" else restore(database)
    except (RuntimeError, OSError, sqlite3.Error, ValueError):
        raise SystemExit(
            "Cleanup stopped. Inspect state before retrying; no partial database change committed."
        ) from None
    print(
        "Scoped local cleanup completed; private backup and recovery receipt retained."
        if result
        else "Local state already matches the requested cleanup/recovery."
    )


if __name__ == "__main__":
    main()
