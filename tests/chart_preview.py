"""Fictional, read-only, memory-only chart preview. Never run in the live repo.

Run in a source-only workspace without .env: uvicorn tests.chart_preview:app
--host 127.0.0.1 --port 8766 --no-access-log. No provider calls or main lifespan.
"""

import asyncio
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
assert not (ROOT / ".env").exists(), "Preview requires an isolated source-only workspace"
os.environ["DATABASE_URL"] = "sqlite://"

from datetime import date, timedelta  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from mypocket.core import templating  # noqa: E402
from mypocket.core.db import get_session  # noqa: E402
from mypocket.domain.models import Account, Holding, Transaction  # noqa: E402
from mypocket.routes import api, pages  # noqa: E402
from mypocket.security import keys  # noqa: E402


def forbidden(*args, **kwargs):
    raise AssertionError("No provider or Keychain access in fictional preview")


keys._get_or_create = forbidden
engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
SQLModel.metadata.create_all(engine)
templating.engine = engine
with Session(engine) as s:
    checking = Account(
        name="Fictional checking", institution="Demo bank", source="fixture", type="checking", balance=1000
    )
    brokerage = Account(
        name="Fictional brokerage",
        institution="Demo broker",
        source="fixture",
        type="brokerage",
        balance=1200,
    )
    s.add(checking)
    s.add(brokerage)
    s.commit()
    for age in range(1100):
        day = date.today() - timedelta(days=age)
        if age % 5 == 0:
            s.add(
                Transaction(
                    account_id=checking.id,
                    tx_date=day,
                    amount=-10,
                    description="Fictional purchase",
                    category="Food",
                )
            )
        if age % 14 == 0:
            s.add(
                Transaction(
                    account_id=checking.id,
                    tx_date=day,
                    amount=100,
                    description="Fictional deposit",
                    category="Income",
                )
            )
        if day.day == 1:
            s.add(
                Transaction(
                    account_id=brokerage.id,
                    tx_date=day,
                    amount=5,
                    description="Fictional dividend",
                    subcategory="Dividends",
                )
            )
    for age in (0, 7, 30):
        s.add(
            Holding(
                account_id=brokerage.id,
                symbol="DEMO",
                name="Fictional holding",
                quantity=1,
                market_value=1200 - age,
                cost_basis=1000,
                total_gain=200 - age,
                as_of=date.today() - timedelta(days=age),
            )
        )
    s.commit()


def session():
    with Session(engine) as s:
        yield s


app = FastAPI()
app.include_router(pages.router)
app.include_router(api.router, prefix="/api")
app.mount("/static", StaticFiles(directory=ROOT / "mypocket/static"))
app.dependency_overrides[get_session] = session


# StaticPool shares one in-memory SQLite connection, so serialize fixture
# requests. Production uses its existing file-backed connection pool unchanged.
preview_lock = asyncio.Lock()


@app.middleware("http")
async def read_only(request, call_next):
    if request.method != "GET":
        return Response(status_code=405)
    async with preview_lock:
        response = await call_next(request)
        body = b"".join([part async for part in response.body_iterator])
        headers = dict(response.headers)
        if headers.get("content-type", "").startswith("text/html"):
            body = (
                body.decode()
                .replace(
                    '<body class="font-sans antialiased min-h-screen">',
                    '<body class="font-sans antialiased min-h-screen"><div style="position:fixed;bottom:0;left:0;right:0;z-index:100;background:#fff4cc;color:#342900;padding:4px;text-align:center;font-size:12px">Fictional data preview — no linked accounts</div>',
                )
                .encode()
            )
            headers.pop("content-length", None)
        return Response(body, status_code=response.status_code, headers=headers)


@app.get("/bar-baselines")
def bar_baseline_preview(request: Request):
    """Fictional renderer-only cases; no data queries or provider operations."""
    from fastapi.responses import HTMLResponse

    template = templating.templates.env.from_string("""
{% extends "base.html" %}
{% block title %}Fictional bar baseline QA{% endblock %}
{% block content %}
<h1 class="text-2xl font-semibold mb-3">Fictional bar baseline QA</h1>
<p class="text-sm text-zinc-400 mb-4">Synthetic amounts only. Positive bars rise from zero; negatives extend below it.</p>
<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
  <section><h2 class="font-semibold">Amount bars</h2><div style="height:260px"><canvas id="amount" aria-label="Fictional positive and negative amount bars"></canvas></div></section>
  <section><h2 class="font-semibold">Grouped amount bars</h2><div style="height:260px"><canvas id="grouped" aria-label="Fictional grouped positive and negative amount bars"></canvas></div></section>
</div>
<p id="baseline-check" class="text-sm mt-4" role="status">Checking rendered baselines…</p>
{% endblock %}
{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded',()=>{
 const labels=['Jul 2026 (partial)','Aug 2026','Sep 2026','Oct 2026 (partial)'];
 const items=[charts.bar(document.getElementById('amount'),labels,[40,-25,0,15]),charts.groupedBar(document.getElementById('grouped'),labels,[{label:'Series A',data:[50,-20,0,10],color:'#34d399'},{label:'Series B',data:[10,40,0,-30],color:'#fb7185'}])];
 items.forEach(chart=>{chart.options.scales.x.ticks={maxRotation:0,autoSkip:true,maxTicksLimit:8};chart.update('none');});
 function verify(){
  const ok=items.every(chart=>chart.scales.y.min<=0&&chart.scales.y.max>=0&&chart.data.datasets.every((dataset,i)=>chart.getDatasetMeta(i).data.every(bar=>Math.abs(bar.base-chart.scales.y.getPixelForValue(0))<=.51)));
  document.getElementById('baseline-check').textContent=ok?'Verified: every ordinary bar starts at zero.':'Baseline mismatch detected.';
 }
 setTimeout(verify,1200);
 window.addEventListener('resize',()=>setTimeout(verify,300));
});
</script>
{% endblock %}""")
    return HTMLResponse(template.render(request=request, active="overview", manual_only=True))
