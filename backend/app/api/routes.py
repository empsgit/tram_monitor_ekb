"""Route REST API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.tables import Route, RouteStop, Stop
from app.schemas.route import RouteDetail, RouteInfo, RouteStopInfo

router = APIRouter(prefix="/api/routes", tags=["routes"])

# Will be set by main.py
tracker = None


@router.get("", response_model=list[RouteInfo])
async def list_routes(session: AsyncSession = Depends(get_session)):
    """Get all tram routes with geometry."""
    result = await session.execute(select(Route).order_by(Route.number))
    routes = result.scalars().all()
    route_infos = []
    for r in routes:
        geometry = None
        if tracker:
            geometry = tracker.get_route_geometry(r.id)
        route_infos.append(RouteInfo(
            id=r.id, number=r.number, name=r.name, color=r.color,
            geometry=geometry,
        ))
    return route_infos


@router.get("/{route_id}", response_model=RouteDetail)
async def get_route(route_id: int, session: AsyncSession = Depends(get_session)):
    """Get route detail with stops and geometry."""
    result = await session.execute(
        select(Route).where(Route.id == route_id).options(selectinload(Route.stops).selectinload(RouteStop.stop))
    )
    route = result.scalar_one_or_none()
    if not route:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Route not found")

    stops = []
    for rs in route.stops:
        stops.append(RouteStopInfo(
            id=rs.stop.id,
            name=rs.stop.name,
            lat=rs.stop.lat,
            lon=rs.stop.lon,
            order=rs.order,
            direction=rs.direction,
        ))

    geometry = None
    if tracker:
        geometry = tracker.get_route_geometry(route.id)

    return RouteDetail(
        id=route.id,
        number=route.number,
        name=route.name,
        color=route.color,
        stops=stops,
        geometry=geometry,
    )
