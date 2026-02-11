"""APScheduler setup for periodic tasks."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


def create_scheduler(tracker) -> AsyncIOScheduler:
    """Create and configure the scheduler with all jobs."""
    from app.config import settings

    scheduler = AsyncIOScheduler()

    # Poll vehicles every N seconds
    scheduler.add_job(
        tracker.poll_vehicles,
        "interval",
        seconds=settings.poll_interval_seconds,
        id="poll_vehicles",
        name="Poll ETTU for vehicle positions",
        max_instances=1,
    )

    # Refresh routes and stops every N hours
    scheduler.add_job(
        tracker.load_routes_and_stops,
        "interval",
        hours=settings.route_refresh_hours,
        id="refresh_routes",
        name="Refresh routes and stops from ETTU",
        max_instances=1,
    )

    return scheduler
