"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import diagnostics, routes, stops, vehicles, ws
from app.core.broadcaster import Broadcaster
from app.core.ettu_client import EttuClient
from app.core.scheduler import create_scheduler
from app.core.vehicle_tracker import VehicleTracker
from app.db.session import async_session, engine
from app.models.base import Base
from app.models import tables  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    # Create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Initialize services
    ettu = EttuClient()
    broadcaster = Broadcaster()
    await broadcaster.connect()

    tracker = VehicleTracker(ettu, broadcaster, async_session)

    # Wire up API modules
    ws.broadcaster = broadcaster
    ws.tracker = tracker
    vehicles.tracker = tracker
    stops.tracker = tracker
    routes.tracker = tracker
    diagnostics.tracker = tracker

    # Load initial routes and stops
    try:
        await tracker.load_routes_and_stops()
    except Exception:
        logger.exception("Failed to load initial routes/stops - will retry")

    # Start scheduler
    scheduler = create_scheduler(tracker)
    scheduler.start()
    logger.info("Tram Monitor started - polling ETTU every %ds", 10)

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    await ettu.close()
    await broadcaster.close()
    await engine.dispose()
    logger.info("Tram Monitor shut down")


app = FastAPI(
    title="Yekaterinburg Tram Monitor",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)
app.include_router(stops.router)
app.include_router(vehicles.router)
app.include_router(diagnostics.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
