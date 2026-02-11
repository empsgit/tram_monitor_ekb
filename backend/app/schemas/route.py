from pydantic import BaseModel


class RouteInfo(BaseModel):
    id: int
    number: str
    name: str
    color: str
    geometry: list[list[float]] | None = None


class RouteStopInfo(BaseModel):
    id: int
    name: str
    lat: float
    lon: float
    order: int
    direction: int


class RouteDetail(BaseModel):
    id: int
    number: str
    name: str
    color: str
    stops: list[RouteStopInfo] = []
    geometry: list[list[float]] | None = None  # [[lat, lon], ...]


class StopInfoFull(BaseModel):
    id: int
    name: str
    lat: float
    lon: float
    routes: list[str] = []
