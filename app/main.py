"""
Astro Cortex - FastAPI entry point.

Hosts:
- REST API endpoints (rating, forecast, locations, status)
- PWA static files (HTML, JS, Service Worker)
- Health check endpoint for systemd watchdog

Run via:
    uvicorn app.main:app --host 0.0.0.0 --port 8000

Or via systemd: astro-app.service
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    log.info("astro_cortex_starting", port=settings.port, db=str(settings.db_path))
    # Ensure DB directory exists
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    yield
    log.info("astro_cortex_stopping")


app = FastAPI(
    title="Astro Cortex",
    description="Autonomous Go/No-Go decision system for astrophotography",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Health & status -------------------------------------------------------

@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is alive."""
    return {"status": "ok"}


@app.get("/status", tags=["system"])
async def status() -> dict:
    """Readiness probe — returns DB and source availability."""
    # TODO: check DB connectivity, source reachability, last successful crawl
    return {"status": "ok", "version": "0.1.0"}


# --- API endpoints (stubs — implement as needed) --------------------------

@app.get("/api/locations", tags=["api"])
async def list_locations() -> dict:
    """List all active observing locations."""
    # TODO: query DB
    return {"locations": []}


@app.get("/api/locations/{location_id}/current", tags=["api"])
async def get_current_rating(location_id: str) -> dict:
    """Get the most recent rating for a location."""
    # TODO: query latest crawls + ratings row
    return {"location_id": location_id, "rating": None}


@app.get("/api/locations/{location_id}/forecast", tags=["api"])
async def get_forecast(location_id: str, hours: int = 48) -> dict:
    """Get multi-day forecast for a location."""
    # TODO: query forecast_log
    return {"location_id": location_id, "forecast": []}


# --- PWA static files ------------------------------------------------------

PWA_DIR = Path(__file__).parent / "pwa" / "static"
if PWA_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(PWA_DIR)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def pwa_index() -> HTMLResponse:
    """Serve the PWA shell. Real implementation reads index.html from PWA_DIR."""
    index = PWA_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Astro Cortex</h1><p>PWA not yet built. See /docs for API.</p>"
    )
