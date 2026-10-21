"""Fictional calculations and a database-free page shell; no real account access."""

import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[1]


def test_monthly_projection_math_and_invalid_inputs():
    node = shutil.which("node")
    assert node, "Node is required for the browser calculation tests"
    result = subprocess.run(
        [
            node,
            "-e",
            r"""
const assert = require('node:assert/strict');
const {project} = require('./mypocket/static/projection.js');
const p = {initial: 10000, monthly: 250, years: 20, annualReturn: 5, inflation: 2.5};
const close = (a,b) => assert(Math.abs(a-b) < Math.max(1,Math.abs(b))*1e-10, `${a} != ${b}`);
let r = project({...p, years: 1, monthly: 0, annualReturn: 12});
close(r.endValue,11200); // Effective annual rate, NOT 12% / 12.
close(r.points.at(-1).value,r.endValue);
r = project({...p, annualReturn: 0, inflation: 0});
close(r.endValue,70000); close(r.contributed,70000); close(r.growth,0); close(r.realValue,70000);
const m = Math.pow(1.05,1/12)-1;
r = project(p);
close(r.endValue,10000*Math.pow(1.05,20)+250*(Math.pow(1.05,20)-1)/m);
close(r.realValue,r.endValue/Math.pow(1.025,20));
assert.equal(r.points.length,21); assert.equal(r.points[0].value,10000);
r = project({...p, monthly: 0, years: 1, annualReturn: -25, inflation: -2});
close(r.endValue,7500); close(r.growth,-2500); close(r.realValue,7500/.98);
close(project({...p, initial:0, monthly:0}).endValue,0);
r = project({...p, years:0}); close(r.endValue,10000); close(r.contributed,10000);
close(project({...p, years:60, annualReturn:0.000001}).endValue,
      10000*Math.pow(1.00000001,60)+250*Math.expm1(60*Math.log1p(.00000001))/Math.expm1(Math.log1p(.00000001)/12));
assert(Number.isFinite(project({...p, annualReturn:100, inflation:-20,years:60}).realValue));
for(const bad of [
 {annualReturn:-100},{annualReturn:101},{inflation:-100},{inflation:101},
 {initial:-1},{monthly:-1},{years:1.5},{years:61},{initial:Infinity},
 {monthly:NaN},{years:'20'},{initial:1e10},{inflation:undefined}
]) assert.throws(()=>project({...p,...bad}),RangeError);
""",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_planner_shell_has_no_account_queries_example_mode_or_starting_input(monkeypatch):
    from mypocket.routes import planning

    def forbidden(*args, **kwargs):
        raise AssertionError("The page shell must not query financial data")

    monkeypatch.setitem(planning.templates.env.globals, "last_sync_at", forbidden)
    app = FastAPI()
    app.include_router(planning.router)
    with TestClient(app) as client:
        response = client.get("/planning")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "data-sync-btn" not in response.text
        assert '<input id="plan-initial"' not in response.text
        for removed in [
            "Use current holdings",
            "Use manual inputs",
            "Reset example",
            "plan-holdings",
            "plan-lower",
            "plan-higher",
        ]:
            assert removed not in response.text
        assert "Assumed average annual return" in response.text
        assert 'name="rate"' in response.text
        assert client.post("/planning", data={"initial": "12345"}).status_code == 405


def test_planning_shell_has_no_financial_runtime_dependencies():
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import mypocket.routes.planning
for name in sys.modules:
    assert not name.startswith(('mypocket.integrations','mypocket.domain','mypocket.core','mypocket.scheduler')), name
""",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_planning_scripts_have_no_storage_external_transmission_or_logging():
    for name in ("planning.js", "projection.js"):
        source = (ROOT / "mypocket/static" / name).read_text()
        for forbidden in (
            "XMLHttpRequest",
            "sendBeacon",
            "WebSocket",
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "document.cookie",
            "console.",
            "location.",
        ):
            assert forbidden not in source, (name, forbidden)
    source = (ROOT / "mypocket/static/planning.js").read_text()
    assert source.count("fetch(") == 1
    assert "fetch('/api/planning/accounts'" in source
    assert "cache:'no-store'" in source


def test_ui_auto_loads_combined_totals_no_fallback_and_clears_data():
    result = subprocess.run(
        [shutil.which("node"), "tests/planning_accounts_ui.cjs"], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr


def test_time_varying_contributions_month_boundaries_growth_and_totals():
    result = subprocess.run(
        [shutil.which("node"), "tests/contribution_schedule.cjs"], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
