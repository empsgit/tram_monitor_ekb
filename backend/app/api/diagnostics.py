"""Diagnostics API for verifying route-stop data pipeline."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

# Will be set by main.py
tracker = None


@router.get("")
async def get_diagnostics():
    """Get full pipeline diagnostics: route-stop resolution, geometry, vehicle matching."""
    if tracker is None:
        return {"error": "Tracker not initialized"}
    return tracker.get_diagnostics()


@router.get("/routes/{route_id}")
async def get_route_diagnostics(route_id: int):
    """Get diagnostics for a specific route."""
    if tracker is None:
        return {"error": "Tracker not initialized"}
    diag = tracker.get_diagnostics()
    for r in diag["routes"]:
        if r["route_id"] == route_id:
            return r
    return {"error": "Route not found"}
