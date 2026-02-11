"""Stop REST API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.tables import Stop
from app.schemas.route import StopInfoFull
from app.schemas.vehicle import StopArrivals

router = APIRouter(prefix="/api/stops", tags=["stops"])

# Will be set by main.py
tracker = None


@router.get("", response_model=list[StopInfoFull])
async def list_stops(session: AsyncSession = Depends(get_session)):
    """Get all tram stops."""
    result = await session.execute(select(Stop).order_by(Stop.name))
    stops = result.scalars().all()
    return [StopInfoFull(id=s.id, name=s.name, direction=s.direction, lat=s.lat, lon=s.lon) for s in stops]


@router.get("/{stop_id}/arrivals", response_model=StopArrivals)
async def get_arrivals(
    stop_id: int,
    route: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Get upcoming tram arrivals at a stop."""
    # Get stop info
    result = await session.execute(select(Stop).where(Stop.id == stop_id))
    stop = result.scalar_one_or_none()
    if not stop:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Stop not found")

    arrivals_data = []
    if tracker:
        arrivals_data = tracker.get_vehicles_for_stop(stop_id, route_filter=route)

    return StopArrivals(
        stop_id=stop.id,
        stop_name=stop.name,
        arrivals=arrivals_data,
    )
