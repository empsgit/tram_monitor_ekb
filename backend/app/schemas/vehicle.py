from pydantic import BaseModel


class StopInfo(BaseModel):
    id: int
    name: str


class NextStopInfo(BaseModel):
    id: int
    name: str
    eta_seconds: int | None = None
    eta_min_seconds: int | None = None
    eta_max_seconds: int | None = None
    eta_source: str = "fallback"


class VehicleState(BaseModel):
    id: str
    board_num: str
    route: str
    route_id: int | None = None
    lat: float
    lon: float
    speed: float
    course: float
    prev_stop: StopInfo | None = None
    next_stops: list[NextStopInfo] = []
    progress: float | None = None
    timestamp: str | None = None
    signal_lost: bool = False
    direction: int | None = None
    direction_status: str = "learning"


class VehicleSnapshot(BaseModel):
    type: str = "snapshot"
    vehicles: list[VehicleState]


class VehicleUpdate(BaseModel):
    type: str = "update"
    vehicles: list[VehicleState]


class StopArrival(BaseModel):
    vehicle_id: str
    board_num: str
    route: str
    route_id: int | None = None
    eta_seconds: int | None = None
    eta_min_seconds: int | None = None
    eta_max_seconds: int | None = None
    eta_source: str = "fallback"
    distance_m: float | None = None
    signal_lost: bool = False


class StopArrivals(BaseModel):
    stop_id: int
    stop_name: str
    arrivals: list[StopArrival]
