"""Virtual-clock scheduler tests and synthetic provider traffic only."""

import asyncio

import httpx
import pytest
from starlette.requests import Request

from mypocket import scheduler
from mypocket.core import sync_schedule, templating
from mypocket.domain.models import Account, PlaidAccount
from tests.test_plaid import make_link, page, remote_account


async def simulate(monkeypatch, *, count, result=None, duration=0, oversleep=0):
    now, busy, calls, waits = 0, False, [], []
    monkeypatch.setattr(scheduler, "SYNC_INITIAL_DELAY_SECONDS", 30)
    monkeypatch.setattr(scheduler, "PLAID_SYNC_INTERVAL_SECONDS", 1800)
    monkeypatch.setattr(scheduler, "SYNC_INTERVAL_SECONDS", 21600)
    monkeypatch.setattr(scheduler, "PLAID_MAX_RETRY_SECONDS", 21600)
    monkeypatch.setattr(scheduler, "monotonic", lambda: now)

    async def sleep(seconds):
        nonlocal now
        if len(calls) >= count:
            raise asyncio.CancelledError
        waits.append(seconds)
        now += seconds + (oversleep if calls else 0)

    async def thread(fn, providers):
        nonlocal now, busy
        assert fn is scheduler._run_sync_once
        assert not busy, "Scheduled sync batches must never overlap"
        busy = True
        calls.append((now, providers))
        now += duration
        try:
            return result(providers, len(calls)) if result else {p: [{"errors": []}] for p in providers}
        finally:
            busy = False

    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    monkeypatch.setattr(scheduler.asyncio, "to_thread", thread)
    with pytest.raises(asyncio.CancelledError):
        await scheduler._scheduler_loop()
    return calls, waits


@pytest.mark.asyncio
async def test_citi_every_30_minutes_keeps_etrade_every_six_hours(monkeypatch):
    calls, waits = await simulate(monkeypatch, count=13)
    assert calls[0] == (30, ("teller", "etrade", "plaid"))
    assert calls[1:12] == [(30 + n * 1800, ("plaid",)) for n in range(1, 12)]
    assert calls[12] == (21630, ("teller", "etrade", "plaid"))
    assert waits[0] == 30


@pytest.mark.asyncio
async def test_long_run_finishes_before_next_interval_begins(monkeypatch):
    calls, _ = await simulate(monkeypatch, count=3, duration=120)
    assert [t for t, _ in calls] == [30, 1950, 3870]


@pytest.mark.asyncio
async def test_wake_runs_one_due_batch_without_catchup_bursts(monkeypatch):
    calls, _ = await simulate(monkeypatch, count=3, oversleep=86400)
    assert calls[1] == (88230, ("teller", "etrade", "plaid"))
    assert calls[2][0] - calls[1][0] == 88200


@pytest.mark.asyncio
async def test_error_backoff_then_recovery_resets_only_plaid(monkeypatch):
    def result(providers, number):
        return {
            p: [{"errors": ["Temporary failure"] if p == "plaid" and number < 3 else []}] for p in providers
        }

    calls, _ = await simulate(monkeypatch, count=5, result=result)
    assert [t for t, _ in calls] == [30, 3630, 10830, 12630, 14430]
    assert all(p == ("plaid",) for _, p in calls[1:])


@pytest.mark.asyncio
async def test_batch_exception_backs_off_and_loop_survives(monkeypatch, caplog):
    def result(providers, number):
        if number == 1:
            raise RuntimeError("DO_NOT_EXPOSE")
        return {p: [{"errors": []}] for p in providers}

    calls, _ = await simulate(monkeypatch, count=2, result=result)
    assert calls[1] == (3630, ("plaid",))
    assert "DO_NOT_EXPOSE" not in caplog.text


@pytest.mark.parametrize(
    "failures,expected", [(0, 1800), (1, 3600), (2, 7200), (3, 14400), (4, 21600), (100000, 21600)]
)
def test_plaid_backoff_cap_and_unchanged_other_interval(monkeypatch, failures, expected):
    monkeypatch.setattr(scheduler, "PLAID_SYNC_INTERVAL_SECONDS", 1800)
    monkeypatch.setattr(scheduler, "PLAID_MAX_RETRY_SECONDS", 21600)
    monkeypatch.setattr(scheduler, "SYNC_INTERVAL_SECONDS", 21600)
    assert scheduler._retry_delay("plaid", failures) == expected
    assert scheduler._retry_delay("etrade", failures) == 21600


def test_plaid_only_batch_does_not_call_other_providers_or_refresh(db, monkeypatch, plaid):
    enrollment, state = make_link(db)
    enrollment.status, state.approved = "active", True
    account = Account(source="plaid", name="Fictional Checking", institution="Test", type="checking")
    db.add(account)
    db.add(enrollment)
    db.add(state)
    db.commit()
    db.add(PlaidAccount(enrollment_id=enrollment.id, external_id="remote-checking", account_id=account.id))
    db.commit()
    monkeypatch.setattr(scheduler, "engine", db.get_bind())
    monkeypatch.setattr(scheduler.teller_sync, "sync_all", lambda *a: pytest.fail("Unexpected Teller call"))
    monkeypatch.setattr(scheduler.etrade_sync, "sync_all", lambda *a: pytest.fail("Unexpected ETRADE call"))
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/accounts/get":
            return httpx.Response(
                200, json={"accounts": [remote_account()], "item": {"item_id": "synthetic-item"}}
            )
        assert request.url.path == "/transactions/sync", "No paid refresh or new product endpoint allowed"
        return httpx.Response(200, json=page())

    monkeypatch.setattr(plaid, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    result = scheduler._run_sync_once(("plaid",))
    assert result["plaid"] and not result["plaid"][0]["errors"]
    assert result["teller"] == result["etrade"] == []
    assert paths == ["/accounts/get", "/transactions/sync"]


def test_cadence_text_uses_configured_intervals_and_explains_limits(monkeypatch, db):
    monkeypatch.setattr(sync_schedule, "PLAID_SYNC_INTERVAL_SECONDS", 1800)
    monkeypatch.setattr(sync_schedule, "SYNC_INTERVAL_SECONDS", 21600)
    monkeypatch.setattr(sync_schedule, "SYNC_INITIAL_DELAY_SECONDS", 30)
    monkeypatch.setattr(sync_schedule, "PLAID_MAX_RETRY_SECONDS", 21600)
    html = templating.templates.env.get_template("plaid.html").render(
        request=Request({"type": "http"}),
        manual_only=True,
        configured=False,
        environment="sandbox",
        enrollments=[],
        coverage=[],
        unlinked_types=[],
    )
    assert "30 minutes after each completed check" in html
    assert "30 seconds after startup" in html
    assert "Mac is awake" in html and "MyPocket is running" in html
    assert "up to 6 hours" in html and "No Transactions Refresh add-on is used" in html
    monkeypatch.setattr(sync_schedule, "PLAID_SYNC_INTERVAL_SECONDS", 3600)
    assert sync_schedule.sync_schedule()["plaid"] == "1 hour"


def test_interval_config_rejects_invalid_values_without_exposing_them(monkeypatch):
    monkeypatch.setenv("TEST_INTERVAL", "not-a-number")
    assert sync_schedule._seconds("TEST_INTERVAL", 1800, minimum=300) == 1800
    monkeypatch.setenv("TEST_INTERVAL", "-1")
    assert sync_schedule._seconds("TEST_INTERVAL", 1800, minimum=300) == 300
