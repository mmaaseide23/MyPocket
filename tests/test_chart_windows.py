from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mypocket.domain import analytics
from mypocket.domain.models import Account, Holding, Transaction
from mypocket.domain.windows import DAY_CHOICES, MONTH_CHOICES, buckets, calendar_months, trailing_days

BOUNDARIES = [
    date(2024, 2, 29),
    date(2025, 3, 1),
    date(2026, 1, 1),
    date(2026, 12, 31),
    date(2026, 10, 20),
    date(2024, 3, 10),
    date(2024, 11, 3),
]


@pytest.mark.parametrize("as_of", BOUNDARIES)
@pytest.mark.parametrize("days", DAY_CHOICES)
def test_day_windows_and_every_bucket_cover_exactly_n_dates(as_of, days):
    window = trailing_days(days, as_of)
    assert window.end == as_of
    assert (window.stop - window.start).days == days
    for period in ("day", "week", "month"):
        rows = buckets(window, period)
        covered = []
        for row in rows:
            start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
            covered.extend(start + timedelta(days=i) for i in range((end - start).days + 1))
        assert covered == [window.start + timedelta(days=i) for i in range(days)]


@pytest.mark.parametrize("as_of", BOUNDARIES)
@pytest.mark.parametrize("months", (1, *MONTH_CHOICES))
def test_calendar_month_choices_never_add_an_extra_month(as_of, months):
    rows = buckets(calendar_months(months, as_of), "month")
    assert len(rows) == months
    assert rows[-1]["end"] == as_of.isoformat()
    assert len({row["month"] for row in rows}) == months
    assert all(str(date.fromisoformat(row["start"]).year) in row["label"] for row in rows)


def account(db, kind="checking"):
    obj = Account(name="Fictional chart account", institution="Example", source="fixture", type=kind)
    db.add(obj)
    db.commit()
    return obj


def txn(db, aid, day, amount, **kwargs):
    db.add(Transaction(account_id=aid, tx_date=day, amount=amount, description="Fictional fixture", **kwargs))


@pytest.mark.parametrize("days", DAY_CHOICES)
def test_stats_categories_and_both_trends_share_inclusive_dates(db, days):
    a = account(db)
    window = trailing_days(days, date(2024, 3, 1))
    for day in (window.start - timedelta(days=1), window.start, window.end, window.stop):
        txn(db, a.id, day, -10, category="Food")
        txn(db, a.id, day, 20, category="Income")
    db.commit()
    assert analytics.spending(db, window=window) == 20
    assert analytics.income(db, window=window) == 40
    assert sum(r["amount"] for r in analytics.category_breakdown(db, window=window)) == 20
    for period in ("day", "week", "month"):
        trend = analytics.by_period(db, window=window, period=period)
        flows = analytics.monthly_flows(db, window=window, period=period)
        assert sum(r["spending"] for r in trend) == 20
        assert sum(r["outflow"] for r in flows) == 20
        assert sum(r["inflow"] for r in flows) == 40
        assert [(r["start"], r["end"]) for r in trend] == [(r["start"], r["end"]) for r in flows]


@pytest.mark.parametrize("months", MONTH_CHOICES)
def test_dividends_zero_fill_and_filter_both_bounds(db, months):
    a = account(db, "brokerage")
    window = calendar_months(months, date(2026, 1, 1))
    for day in (window.start - timedelta(days=1), window.start, window.end, window.stop):
        txn(db, a.id, day, 5, subcategory="Dividends")
    db.commit()
    rows = analytics.dividend_history(db, months=months, window=window)
    assert len(rows) == months
    assert sum(row["amount"] for row in rows) == 10


@pytest.mark.parametrize(
    "as_of, previous_end",
    [
        (date(2024, 3, 31), date(2024, 2, 29)),
        (date(2025, 3, 31), date(2025, 2, 28)),
        (date(2026, 1, 5), date(2025, 12, 5)),
    ],
)
def test_comparisons_are_matching_calendar_portions(db, as_of, previous_end):
    result = analytics.period_compare(db, as_of=as_of)
    assert result["current"]["end"] == as_of.isoformat()
    assert result["previous"]["end"] == previous_end.isoformat()
    weekly = analytics.period_compare(db, weeks=True, as_of=as_of)
    assert date.fromisoformat(weekly["previous"]["end"]) == as_of - timedelta(days=7)


def test_snapshot_labels_report_actual_dates_and_exclude_future(db):
    a = account(db, "brokerage")
    today = date.today()
    for age, value in [(10, 100), (0, 120), (-1, 999)]:
        db.add(
            Holding(
                account_id=a.id,
                symbol="DEMO",
                quantity=1,
                market_value=value,
                as_of=today - timedelta(days=age),
            )
        )
    db.commit()
    row = analytics.holdings_period_gains(db, period_days=7)["rows"][0]
    assert row["gain"] == 20
    assert row["current_date"] == today.isoformat()
    assert row["past_date"] == (today - timedelta(days=10)).isoformat()


@pytest.mark.parametrize("days", DAY_CHOICES)
def test_pages_and_api_keep_window_and_anchor_in_sync(db, days):
    from mypocket.core.db import get_session
    from mypocket.routes import api, pages

    a = account(db)
    app = FastAPI()
    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        for path in ("/", "/banking", "/spending", f"/banking/{a.id}"):
            response = client.get(path, params={"days": days})
            assert response.status_code == 200
            assert trailing_days(days).label in response.text
            assert "months=12&account_id" not in response.text
        anchor = "2024-02-29"
        rows = client.get("/api/flows", params={"days": days, "as_of": anchor, "period": "day"}).json()
        assert len(rows) == days
        assert rows[-1]["end"] == anchor
        for path in ("/api/flows", "/api/dividends", "/api/spending/trend"):
            assert client.get(path, params={"as_of": "2999-01-01"}).status_code == 422
        assert client.get("/api/spending/trend?period=bad").status_code == 422
        assert client.get("/?days=-1").status_code == 422


@pytest.mark.parametrize("months", MONTH_CHOICES)
def test_every_brokerage_choice_and_account_chart_has_exact_month_count(db, months):
    from mypocket.core.db import get_session
    from mypocket.routes import api, pages

    a = account(db, "brokerage")
    app = FastAPI()
    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        for path in ("/brokerage", f"/brokerage/{a.id}"):
            response = client.get(path, params={"months": months})
            assert response.status_code == 200
            assert calendar_months(months).label in response.text
            assert f"{months} calendar months" in response.text
            assert "7-day target" in response.text or "No historical snapshots" in response.text
        for scope in ({"domain": "brokerage"}, {"account_id": a.id}):
            response = client.get("/api/dividends", params={"months": months, **scope})
            assert len(response.json()) == months
        assert client.get("/brokerage?months=2").status_code == 422


def test_date_only_labels_do_not_shift_in_browser_timezones():
    import os
    import subprocess
    import sys

    for zone in ("America/Los_Angeles", "Pacific/Kiritimati", "UTC"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import time
from datetime import date
from mypocket.domain.windows import trailing_days,buckets
time.tzset()
for day in (date(2024,3,10),date(2024,11,3),date(2024,2,29)):
    rows=buckets(trailing_days(7,day),'day')
    assert len(rows)==7 and rows[-1]['end']==day.isoformat()
""",
            ],
            env={**os.environ, "TZ": zone},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_history_ui_uses_server_dates_and_full_bucket_extent():
    import shutil
    import subprocess
    from pathlib import Path

    root = Path(__file__).parents[1]
    source = (root / "mypocket/static/history-charts.js").read_text()
    assert "new Date(" not in source
    harness = r"""
const assert=require('node:assert/strict');
let ready;const jobs=[];const configs=[];
const rows=[{label:'Dec 2025 (partial)',range:'Dec 31, 2025 – Dec 31, 2025',start:'2025-12-31',end:'2025-12-31',spending:0,inflow:0,outflow:0,amount:0},{label:'Jan 2026 (partial)',range:'Jan 01, 2026 – Jan 02, 2026',start:'2026-01-01',end:'2026-01-02',spending:10,inflow:20,outflow:10,amount:5}];
const canvases=['flows','account-flows','dividends','spending'].map((kind,i)=>({id:String(i),dataset:{historyKind:kind,historyUrl:'/synthetic?days=3&as_of=2026-01-02'}}));
const status={};global.document={addEventListener:(_e,cb)=>{ready=cb;},querySelectorAll:()=>({forEach:cb=>canvases.forEach(c=>jobs.push(cb(c)))}),getElementById:id=>status[id]||(status[id]={})};
global.fetch=async url=>{assert(url.includes('as_of=2026-01-02'));return {ok:true,json:async()=>rows};};
function chart(canvas,labels,data){assert.deepEqual(labels,rows.map(r=>r.label));const c={options:{scales:{x:{}},plugins:{tooltip:{callbacks:{}}}},update(){}};configs.push(c);return c;}
global.charts={bar:chart,groupedBar:chart};
"""
    checks = r"""
(async()=>{ready();await Promise.all(jobs);for(const c of canvases){assert.equal(c.dataset.bucketCount,'2');assert.equal(c.dataset.rangeStart,'2025-12-31');assert.equal(c.dataset.rangeEnd,'2026-01-02');}for(const c of configs)assert.equal(c.options.plugins.tooltip.callbacks.title([{dataIndex:0}]),rows[0].range);})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", harness + source + checks], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
