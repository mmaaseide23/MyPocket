import json
import sqlite3
from datetime import date

from mypocket.integrations import teller_sync
from mypocket.security import crypto
from tests.test_integrations import make_enrollment


def test_disabled_teller_is_not_decrypted_or_called(db, monkeypatch):
    make_enrollment(db, "teller", "disabled")

    def forbidden(*args):
        raise AssertionError("Retired provider must not decrypt credentials")

    monkeypatch.setattr(crypto, "decrypt", forbidden)
    assert teller_sync.sync_all(db) == []


def test_direct_disabled_teller_sync_makes_no_request(db, monkeypatch):
    e = make_enrollment(db, "teller", "disabled")

    def forbidden(*args):
        raise AssertionError("Retired provider must not be called")

    monkeypatch.setattr(teller_sync.teller, "list_accounts", forbidden)
    assert teller_sync.sync_enrollment(db, e)["errors"] == []
    assert e.status == "disabled"


def test_cleanup_and_scoped_restore_preserve_every_other_field(tmp_path):
    from mypocket.local_cleanup import restore, retire

    path = tmp_path / "app.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE account(id INTEGER PRIMARY KEY,source TEXT,type TEXT,institution TEXT,archived INTEGER,balance REAL);
        INSERT INTO account VALUES (1,'teller','checking','Citi',1,10),(2,'teller','credit','Citi',0,20),
          (3,'plaid','checking','Citi',0,30),(4,'etrade_api','brokerage','E*TRADE',0,40);
        CREATE TABLE enrollment(id INTEGER PRIMARY KEY,provider TEXT,status TEXT,created_at TEXT,access_token TEXT);
        INSERT INTO enrollment VALUES (1,'teller','active','2026-05-01','keep1'),
          (2,'etrade','needs_reauth','2026-05-01','keep2'),
          (3,'etrade','pending_verifier','2026-10-20 10:00','keep3'),
          (4,'etrade','needs_reauth','2026-10-20 11:00','keep4'),
          (5,'etrade','active','2026-10-20 12:00','keep5'),(6,'plaid','active','2026-10-20 13:00','keep6');
        CREATE TABLE plaidaccount(account_id INTEGER,enrollment_id INTEGER);
        INSERT INTO plaidaccount VALUES (3,6);
        CREATE TABLE plaidconnection(enrollment_id INTEGER,approved INTEGER);
        INSERT INTO plaidconnection VALUES (6,1);
        CREATE TABLE "transaction"(id INTEGER PRIMARY KEY,notes TEXT);
        INSERT INTO "transaction" VALUES (1,'Keep notes');
        """)
    receipt = retire(path, date(2026, 10, 20))
    assert receipt.stat().st_mode & 0o077 == 0
    assert retire(path, date(2026, 10, 20)) is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT archived FROM account WHERE id=2").fetchone() == (1,)
        assert db.execute("SELECT status FROM enrollment ORDER BY id").fetchall() == [
            ("disabled",),
            ("needs_reauth",),
            ("archived",),
            ("archived",),
            ("active",),
            ("active",),
        ]
        assert db.execute("SELECT access_token FROM enrollment ORDER BY id").fetchall() == [
            (f"keep{i}",) for i in range(1, 7)
        ]
        db.execute("UPDATE account SET balance=41 WHERE id=4")
    restore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT archived FROM account WHERE id=2").fetchone() == (0,)
        assert db.execute("SELECT status FROM enrollment WHERE id=1").fetchone() == ("active",)
        assert db.execute("SELECT balance FROM account WHERE id=4").fetchone() == (41,)
        assert db.execute('SELECT notes FROM "transaction"').fetchone() == ("Keep notes",)
    assert restore(path) is None
    data = json.loads(receipt.read_text())
    assert (path.parent / "backups" / data["backup"]).exists()


def test_retired_teller_ui_is_explicit_and_has_no_live_controls(db):
    from starlette.requests import Request

    from mypocket.routes.teller import connect_page

    e = make_enrollment(db, "teller", "disabled")
    e.last_error = "Old TLS error"
    db.add(e)
    db.commit()
    html = connect_page(Request({"type": "http"}), db).body.decode()
    assert "Disabled locally" in html
    assert "Old TLS error" not in html
    assert "https://cdn.teller.io/connect/connect.js" not in html
