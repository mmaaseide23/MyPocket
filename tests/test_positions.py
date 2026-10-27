"""Fictional position snapshots only; no providers or real database."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mypocket.domain import positions
from mypocket.domain.models import Account, Holding

TODAY = date(2024, 3, 1)
NOW = datetime(2024, 3, 1, 18, tzinfo=UTC)


def account(db, **kw):
    obj = Account(
        name="Fictional investment",
        institution="Fictional broker",
        source="etrade_api",
        type="brokerage",
        **kw,
    )
    db.add(obj)
    db.commit()
    return obj


def holding(db, a, day=TODAY, symbol="EXAMPLE", **kw):
    fields = dict(quantity=2.5, market_value=125, cost_basis=100, day_change=-5, day_change_pct=-3.85)
    fields.update(kw)
    obj = Holding(account_id=a.id, symbol=symbol, as_of=day, created_at=NOW, **fields)
    db.add(obj)
    db.commit()
    return obj


def overview(db):
    return positions.overview(db, today=TODAY, now=NOW)


def test_latest_account_snapshot_does_not_resurrect_closed_symbol(db):
    a = account(db)
    old = holding(db, a, TODAY - timedelta(days=1), "CLOSED")
    new = holding(db, a)
    assert [p["id"] for p in overview(db)["positions"]] == [new.id]
    assert positions.history(db, old.id, today=TODAY) is None


def test_archived_and_exact_duplicate_account_links_are_excluded(db):
    old = account(db, external_id="synthetic-link", last_synced=NOW - timedelta(days=1))
    active = account(db, external_id="synthetic-link", last_synced=NOW)
    archived = account(db, archived=True)
    for a in [old, active, archived]:
        holding(db, a)
    assert [p["account_id"] for p in overview(db)["positions"]] == [active.id]
    assert positions.overview(db, old.id, today=TODAY)["positions"] == []


def test_currencies_never_combined_or_same_symbol_merged_across_accounts(db):
    for currency in ["USD", "EUR"]:
        holding(db, account(db, currency=currency))
    result = overview(db)
    assert len(result["positions"]) == 2
    assert [(t["currency"], t["value"]) for t in result["totals"]] == [("EUR", 125), ("USD", 125)]


def test_missing_basis_values_and_zero_denominators(db):
    a = account(db)
    holding(db, a, symbol="MISSING", cost_basis=None, market_value=None, day_change=None, day_change_pct=None)
    holding(db, a, symbol="ZERO", cost_basis=0, total_gain=25, total_gain_pct=99)
    rows = {p["symbol"]: p for p in overview(db)["positions"]}
    assert rows["MISSING"]["total_gain"] is None
    assert rows["MISSING"]["daily_gain"] is None
    assert rows["ZERO"]["total_gain"] == 25
    assert rows["ZERO"]["total_pct"] is None
    assert overview(db)["totals"][0]["value"] is None


def test_reported_daily_gain_not_inferred_from_snapshots_or_current_shares(db):
    a = account(db)
    holding(db, a, TODAY - timedelta(days=1), quantity=1, market_value=50)
    h = holding(db, a, quantity=10, market_value=600, day_change=7, day_change_pct=1.2)
    p = overview(db)["positions"][0]
    assert (p["daily_gain"], p["daily_pct"]) == (7, 1.2)
    history = positions.history(db, h.id, today=TODAY)
    assert history["quantity_changed"]
    assert history["value_change"] == 550  # value change, never a return
    assert [p["quantity"] for p in history["points"]] == [1, 10]
    assert [p["value"] for p in history["points"]] == [50, 600]
    h.day_change = None
    h.day_change_pct = None
    db.add(h)
    db.commit()
    assert overview(db)["positions"][0]["daily_gain"] is None


def test_unknown_daily_semantics_and_short_basis_not_invented(db):
    a = account(db)
    a.source = "csv"
    db.add(a)
    db.commit()
    holding(db, a, quantity=-2.5, cost_basis=100, total_gain=None)
    p = overview(db)["positions"][0]
    assert p["daily_gain"] is None and p["daily_pct"] is None
    assert p["total_gain"] is None


def test_stale_future_clock_and_newer_sync_warnings(db):
    a = account(db, last_synced=NOW)
    h = holding(db, a, TODAY - timedelta(days=2))
    p = overview(db)["positions"][0]
    assert p["stale"] and p["status_unconfirmed"]
    assert overview(db)["warnings"]
    h.as_of = TODAY
    h.created_at = NOW + timedelta(hours=1)
    db.add(h)
    db.commit()
    assert overview(db)["positions"][0]["future_stamp"]


@pytest.mark.parametrize(
    "anchor", [date(2024, 2, 29), date(2026, 1, 1), date(2024, 3, 10), date(2024, 11, 3)]
)
@pytest.mark.parametrize("range_name", positions.RANGES)
def test_every_range_has_exact_boundaries_without_outside_points(db, anchor, range_name):
    a = account(db)
    days = positions.RANGES[range_name] or 400
    first = anchor - timedelta(days=days - 1)
    for d in [first - timedelta(days=1), first, anchor, anchor + timedelta(days=1)]:
        h = holding(db, a, d)
        if d == anchor:
            current = h
    result = positions.history(db, current.id, range_name, today=anchor)
    assert result["range_end"] == anchor.isoformat()
    if range_name != "all":
        assert result["range_start"] == first.isoformat()
        assert [p["date"] for p in result["points"]] == [first.isoformat(), anchor.isoformat()]
    else:
        assert len(result["points"]) == 3
    assert overview(db) is not None


def test_missing_symbol_and_ambiguous_rows_make_gaps_not_zero(db):
    a = account(db)
    holding(db, a, TODAY - timedelta(days=3))
    holding(db, a, TODAY - timedelta(days=2), symbol="OTHER")
    holding(db, a, TODAY - timedelta(days=1))
    holding(db, a, TODAY - timedelta(days=1))
    current = holding(db, a)
    result = positions.history(db, current.id, today=TODAY)
    assert [p["value"] for p in result["points"]] == [125, None, None, 125]
    assert result["ambiguous"]
    holding(db, a, quantity=1.25)
    result = positions.history(db, current.id, today=TODAY)
    assert result["ambiguous"] and all(p["value"] is None for p in result["points"])
    assert len(overview(db)["positions"]) == 2


def test_split_like_change_is_recorded_not_back_adjusted(db):
    a = account(db)
    holding(db, a, TODAY - timedelta(days=1), quantity=1, last_price=125)
    h = holding(db, a, quantity=2, last_price=62.5)
    result = positions.history(db, h.id, today=TODAY)
    assert result["quantity_changed"] and result["value_change"] == 0
    assert [(p["quantity"], p["price"]) for p in result["points"]] == [(1, 125), (2, 62.5)]


def test_empty_single_observation_and_missing_account(db):
    a = account(db)
    assert overview(db)["positions"] == []
    assert overview(db)["warnings"]
    h = holding(db, a)
    r = positions.history(db, h.id, today=TODAY)
    assert len(r["points"]) == 1 and r["value_change"] is None
    assert positions.history(db, 99999, today=TODAY) is None


def test_new_pages_and_history_api_do_not_use_legacy_performance_or_dividends(db, monkeypatch):
    from mypocket.core.db import get_session
    from mypocket.domain import analytics
    from mypocket.routes import api, pages

    def forbidden(*args, **kw):
        raise AssertionError("Obsolete presentation path called")

    monkeypatch.setattr(analytics, "dividend_history", forbidden)
    monkeypatch.setattr(analytics, "holdings_period_gains", forbidden)
    a = account(db)
    holding(db, a, date.today() - timedelta(days=1))
    h = holding(db, a, date.today())
    app = FastAPI()
    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        for path in ["/brokerage", f"/brokerage/{a.id}"]:
            text = client.get(path).text
            assert "data-position-id" in text and "Daily gain" in text
            assert "Dividends" not in text and ">Performance<" not in text
            assert " units" in text and "data-ranges" not in text
        assert client.get(f"/api/positions/{h.id}/history").status_code == 200
        assert client.get(f"/api/positions/{h.id}/history?range=bad").status_code == 422
        assert client.get("/api/positions/99999/history").status_code == 404


def test_allocation_percentage_and_tooltip_formatting():
    import shutil
    import subprocess
    from pathlib import Path

    result = subprocess.run(
        [shutil.which("node"), str(Path(__file__).with_name("positions_chart.cjs"))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("case", ["empty", "missing", "stale", "ambiguous", "escaped"])
def test_presented_states_are_explicit_and_provider_text_is_escaped(db, case):
    from mypocket.core.db import get_session
    from mypocket.routes import pages

    a = account(db)
    if case != "empty":
        fields = {"cost_basis": None, "market_value": None} if case == "missing" else {}
        day = date.today() - timedelta(days=10) if case == "stale" else date.today()
        holding(db, a, day, symbol="<script>fictional</script>" if case == "escaped" else "EXAMPLE", **fields)
        if case == "ambiguous":
            holding(db, a, day)
    app = FastAPI()
    app.include_router(pages.router)
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        text = client.get("/brokerage").text
        expected = {
            "empty": "No recorded positions",
            "missing": "position-static",
            "stale": ">Older<",
            "ambiguous": "Multiple saved positions share this symbol",
            "escaped": "&lt;script&gt;fictional&lt;/script&gt;",
        }[case]
        assert expected in text
        assert "<script>fictional</script>" not in text


@pytest.mark.parametrize(
    "offsets,expected",
    [
        ([0], []),
        ([0, 1], ["7d", "30d", "90d", "1y", "all"]),
        ([0, 40], ["90d", "1y", "all"]),
        ([100, 200], ["1y", "all"]),
        ([400, 800], ["all"]),
    ],
)
def test_only_ranges_with_two_real_observations_are_offered(db, offsets, expected):
    a = account(db)
    for days in offsets:
        holding(db, a, TODAY - timedelta(days=days))
    p = overview(db)["positions"][0]
    assert [r["value"] for r in p["ranges"]] == expected
    assert p["has_history"] == bool(expected)


def test_partial_history_does_not_offer_missing_metrics_and_preserves_zero(db):
    a = account(db)
    for days in [0, 1]:
        holding(
            db,
            a,
            TODAY - timedelta(days=days),
            market_value=None,
            cost_basis=None,
            day_change=0,
            day_change_pct=0,
        )
    p = overview(db)["positions"][0]
    assert p["value"] is None and p["daily_gain"] == 0
    assert all(r["metrics"] == ["daily_gain"] for r in p["ranges"])
    top = overview(db)["totals"][0]["summary"]
    assert top["total_value"] is None and top["total_cost"] is None and top["day_gain"] == 0


def test_restored_summary_uses_latest_snapshot_without_closed_or_archived_positions(db):
    a = account(db)
    holding(db, a, TODAY - timedelta(days=1), symbol="CLOSED", market_value=9999)
    holding(db, a, market_value=125, cost_basis=100, total_gain=25)
    archived = account(db, archived=True)
    holding(db, archived, market_value=9999)
    t = overview(db)["totals"][0]
    assert t["summary"] == {
        "total_value": 125,
        "total_cost": 100,
        "total_gain": 25,
        "total_gain_pct": 25,
        "day_gain": -5,
    }
    assert [p["symbol"] for p in t["allocation"]] == ["EXAMPLE"]


def test_no_history_renders_ticker_without_disclosure_chart_or_empty_fields(db):
    from mypocket.core.db import get_session
    from mypocket.routes import pages

    a = account(db)
    holding(db, a, date.today(), cost_basis=None, market_value=0, day_change=0, day_change_pct=0)
    app = FastAPI()
    app.include_router(pages.router)
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        text = client.get("/brokerage").text
    lower = text.split('<section class="positions"')[1].split("</section>")[0]
    assert "$0.00" in lower and "+0.00%" in lower
    assert "position-static" in lower
    assert "<details" not in lower and "<canvas" not in lower and "<select" not in lower
    assert "Unavailable" not in lower and "Cost basis" not in lower


def test_restored_top_component_order_and_original_visual_classes(db):
    from mypocket.core.db import get_session
    from mypocket.routes import pages

    a = account(db, balance=150)
    holding(db, a, date.today())
    app = FastAPI()
    app.include_router(pages.router)
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        text = client.get("/brokerage").text
    assert "Brokerage and retirement accounts." in text
    markers = [
        "mp-hero rounded-2xl p-5 sm:p-6",
        "Cost Basis",
        "Total Gain",
        "Latest trading day",
        "Allocation · latest snapshots",
        ">Accounts</h2>",
        '<section class="positions"',
    ]
    indices = [text.index(m) for m in markers]
    assert indices == sorted(indices)
    assert "grid grid-cols-3 gap-2.5 sm:gap-4 mb-6 sm:mb-8" in text
    assert "Dividends" not in text and "Dividend history" not in text


def test_daily_summary_does_not_combine_different_saved_dates(db):
    a = account(db)
    b = account(db)
    holding(db, a, TODAY, day_change=5)
    holding(db, b, TODAY - timedelta(days=1), day_change=8)
    assert overview(db)["totals"][0]["summary"]["day_gain"] is None


def test_all_history_ends_at_last_observation_when_snapshots_are_old(db):
    a = account(db)
    holding(db, a, TODAY - timedelta(days=20))
    h = holding(db, a, TODAY - timedelta(days=10))
    result = positions.history(db, h.id, "all", today=TODAY)
    assert result["range_end"] == (TODAY - timedelta(days=10)).isoformat()


def test_allocation_percentages_match_positive_displayed_values_per_currency(db):
    usd = account(db, currency="USD")
    eur = account(db, currency="EUR")
    for symbol, value in [("BIG", 750), ("SMALL", 250), ("SHORT", -400), ("ZERO", 0)]:
        holding(db, usd, symbol=symbol, market_value=value)
    holding(db, eur, symbol="EUR-EXAMPLE", market_value=300)
    totals = {t["currency"]: t for t in overview(db)["totals"]}
    assert totals["USD"]["allocation_total"] == 1000
    assert [(p["symbol"], p["percentage_label"]) for p in totals["USD"]["allocation"]] == [
        ("BIG", "75.0%"),
        ("SMALL", "25.0%"),
    ]
    assert sum(p["percentage"] for p in totals["USD"]["allocation"]) == 100
    assert totals["EUR"]["allocation_total"] == 300 and totals["EUR"]["allocation"][0]["percentage"] == 100


def test_small_allocations_not_rounded_to_zero_and_duplicate_tickers_labeled(db):
    a = account(db)
    b = account(db)
    b.name = "Second fictional account"
    db.add(b)
    db.commit()
    holding(db, a, market_value=100000)
    holding(db, b, market_value=1)
    rows = overview(db)["totals"][0]["allocation"]
    assert rows[1]["percentage_label"] == "<0.1%"
    assert rows[0]["label"] != rows[1]["label"]
    assert rows[0]["percentage"] + rows[1]["percentage"] == pytest.approx(100)


def test_reported_percentage_only_gain_can_be_shown_without_inventing_basis(db):
    a = account(db)
    holding(db, a, cost_basis=None, total_gain=None, total_gain_pct=12.5)
    row = overview(db)["positions"][0]
    assert row["total_gain"] is None and row["basis"] is None and row["total_pct"] == 12.5
    assert row["gain_source"] == "Reported total gain/loss"


@pytest.mark.parametrize("missing", [False, True])
def test_compact_gains_without_graphs_weekly_estimates_or_empty_metrics(db, missing):
    from mypocket.core.db import get_session
    from mypocket.routes import pages

    a = account(db)
    holding(db, a, date.today() - timedelta(days=7), market_value=10, quantity=1)
    holding(
        db,
        a,
        date.today(),
        quantity=10,
        market_value=100,
        total_gain=None if missing else 0,
        cost_basis=None if missing else 100,
        day_change=None if missing else 0,
        day_change_pct=None if missing else 0,
    )
    app = FastAPI()
    app.include_router(pages.router)
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        text = client.get("/brokerage").text
    lower = text.split('<section class="positions"')[1].split("</section>")[0]
    assert "<details" not in lower and "<canvas" not in lower and "<select" not in lower
    assert "Week" not in lower and "Unavailable" not in lower
    if missing:
        assert "Total gain" not in lower and "Daily gain" not in lower
    else:
        assert "Total gain" in lower and "Daily gain" in lower and "$0.00" in lower and "+0.00%" in lower
    assert "allocation-legend" not in text
    assert 'tabindex="0"' in text and "data-allocation-announcement" in text
    assert "percentage of visible positive holdings" in text
