"""FastAPI application entrypoint for the Budapest Transit Monitor backend.

This module wires up the FastAPI app, CORS for the React frontend, and a basic
health/root endpoint. Feature routers (delays, lines, weather, predictions,
etc.) are added in later tasks; this scaffold only establishes the app so it
imports and starts cleanly.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import __version__
from backend import config
from backend.api import get_services, router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the GTFS-RT background poller on startup, stop it on shutdown.

    Kept in the lifespan hook (not at import time) so importing the app or the
    API module has no network side effects, which keeps the TestClient suite
    fast and offline.
    """
    services = get_services()
    services.realtime.start()
    try:
        yield
    finally:
        services.realtime.stop()


app = FastAPI(
    title="Budapest Public Transport Delay Monitor",
    description=(
        "Real-time delay monitoring for Budapest transit using free data "
        "sources (BKK GTFS, BKK GTFS-Realtime, Open-Meteo)."
    ),
    version=__version__,
    lifespan=lifespan,
)

# Allow the local React dev server (and same-origin production build) to call
# the API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Feature routers: all /api/* endpoints with the shared data_status envelope.
app.include_router(api_router)


@app.get("/")
def root() -> dict:
    """Basic service descriptor."""
    return {
        "service": "budapest-transit-monitor",
        "version": __version__,
        "mock_mode": config.MOCK_MODE,
    }


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


def run() -> None:
    """Run the app with uvicorn using the configured host/port."""
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=True,
    )


if __name__ == "__main__":
    run()
