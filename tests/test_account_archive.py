from datetime import date

from sqlalchemy import text
from sqlmodel import select

from mypocket.domain import analytics
from mypocket.domain.models import Account, Holding, Transaction


def test_archived_csv_is_excluded_from_active_views_but_history_is_retained(db):
    old = Account(
        source="etrade_csv",
        name="Old synthetic",
        type="brokerage",
        institution="E*TRADE",
        balance=100,
        archived=True,
    )
    live = Account(
        source="etrade_api", name="Live synthetic", type="brokerage", institution="E*TRADE", balance=200
    )
    db.add(old)
    db.add(live)
    db.commit()
    for account in (old, live):
        db.add(
            Holding(
                account_id=account.id,
                symbol="TEST",
                quantity=1,
                market_value=account.balance,
                as_of=date.today(),
            )
        )
        db.add(
            Transaction(
                account_id=account.id,
                tx_date=date.today(),
                amount=5,
                description="Synthetic",
                category="Income",
                subcategory="Dividends",
                notes="Keep this note",
            )
        )
    db.commit()
    assert analytics.net_worth(db) == 200
    assert analytics.total_invested(db) == 200
    assert [a["id"] for a in analytics.account_breakdown(db)] == [live.id]
    assert analytics.holdings_summary(db)["total_value"] == 200
    assert len(analytics.recent_transactions(db)) == 1
    assert sum(row["amount"] for row in analytics.dividend_history(db)) == 5
    assert len(analytics.holdings_period_gains(db)["rows"]) == 1
    assert len(db.exec(select(Transaction)).all()) == 2
    assert len(db.exec(select(Holding)).all()) == 2
    assert all(t.notes == "Keep this note" for t in db.exec(select(Transaction)).all())
    # Explicit historical detail remains readable for recovery and review.
    assert len(analytics.recent_transactions(db, account_id=old.id)) == 1


def test_archive_column_migration_preserves_old_account(monkeypatch, tmp_path):
    from sqlmodel import create_engine

    from mypocket.core import db as database

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE account (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO account VALUES (1, 'Keep')"))
    monkeypatch.setattr(database, "engine", engine)
    database._apply_lightweight_migrations()
    database._apply_lightweight_migrations()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name, archived FROM account")).one() == ("Keep", 0)


def test_recoverable_archive_preserves_all_records_and_verified_backup(tmp_path):
    import sqlite3

    from mypocket.maintenance import set_legacy_archived

    path = tmp_path / "app.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE account (id INTEGER PRIMARY KEY, source TEXT, archived INTEGER DEFAULT 0);
        INSERT INTO account VALUES (1,'etrade_csv',0),(2,'etrade_api',0);
        CREATE TABLE "transaction" (id INTEGER PRIMARY KEY, notes TEXT, category TEXT);
        INSERT INTO "transaction" VALUES (1,'Preserve notes','Preserve category');
        CREATE TABLE enrollment (id INTEGER PRIMARY KEY, access_token TEXT);
        INSERT INTO enrollment VALUES (1,'synthetic-encrypted-token');
        """)
    backup = set_legacy_archived(path, True)
    assert backup is not None and backup.stat().st_mode & 0o077 == 0
    with sqlite3.connect(backup) as saved:
        assert saved.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert saved.execute("SELECT archived FROM account WHERE source='etrade_csv'").fetchone() == (0,)
    assert set_legacy_archived(path, True) is None
    set_legacy_archived(path, False)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT id, archived FROM account ORDER BY id").fetchall() == [(1, 0), (2, 0)]
        assert conn.execute('SELECT notes,category FROM "transaction"').fetchone() == (
            "Preserve notes",
            "Preserve category",
        )
        assert conn.execute("SELECT access_token FROM enrollment").fetchone() == (
            "synthetic-encrypted-token",
        )
