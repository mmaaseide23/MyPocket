import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from mypocket import scheduler
from mypocket.core.db import init_db
from mypocket.routes import api, auth, etrade, pages, teller
from mypocket.security.headers import SecurityHeadersMiddleware, StaticCacheMiddleware
from mypocket.security.middleware import AuthMiddleware

STATIC_DIR = Path(__file__).parent / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    sync_task = scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop(sync_task)


app = FastAPI(title="MyPocket", lifespan=lifespan)

# Middleware runs OUTSIDE-IN on request, INSIDE-OUT on response. Order matters:
#   • Auth gate first so unauth'd requests never reach the rest
#   • Static cache + security headers shape the response on the way out
#   • Gzip wraps last (innermost) so it compresses the final bytes
app.add_middleware(AuthMiddleware)
app.add_middleware(StaticCacheMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")


app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(teller.router)
app.include_router(etrade.router)
app.include_router(api.router, prefix="/api")
