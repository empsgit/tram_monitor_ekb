"""Vehicle REST API endpoints."""

from fastapi import APIRouter

from app.schemas.vehicle import VehicleState

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])

# Will be set by main.py
tracker = None


@router.get("", response_model=list[VehicleState])
async def list_vehicles(route: str | None = None):
    """Get all currently active vehicles."""
    if tracker is None:
        return []
    states = list(tracker.current_states.values())
    if route:
        states = [s for s in states if s.route == route]
    return states


@router.get("/{vehicle_id}", response_model=VehicleState | None)
async def get_vehicle(vehicle_id: str):
    """Get a specific vehicle by ID."""
    if tracker is None:
        return None
    return tracker.current_states.get(vehicle_id)
