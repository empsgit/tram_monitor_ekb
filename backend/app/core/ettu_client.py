"""Async client for the ETTU (Gortrans) API at map.ettu.ru."""

import logging
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ETTU API layer identifiers
LAYER_TRAM = 0


@dataclass
class RawVehicle:
    dev_id: str
    board_num: str
    route_num: str
    lat: float
    lon: float
    speed: float
    course: float
    on_route: bool
    layer: int
    timestamp: str = ""


@dataclass
class RawStop:
    id: int
    name: str
    lat: float
    lon: float


@dataclass
class RawRoute:
    id: int
    number: str
    name: str = ""
    points: list[list[float]] = field(default_factory=list)  # [[lat, lon], ...]
    stops: list[dict] = field(default_factory=list)  # [{id, name, lat, lon, order, direction}]


class EttuClient:
    """Polls ETTU API for tram positions, routes, and stops."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ettu_base_url,
            timeout=15.0,
            headers={"Accept": "application/json"},
            params={"apiKey": "111"},
            verify=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_vehicles(self) -> list[RawVehicle]:
        """Fetch all current tram positions."""
        try:
            resp = await self._client.get("/api/v2/tram/boards/")
            resp.raise_for_status()
            data = resp.json()
            logger.info("DEBUG boards response type=%s sample=%s", type(data).__name__, str(data)[:500])
        except httpx.HTTPStatusError:
            # Fallback: try trolleybus endpoint and filter by layer
            try:
                resp = await self._client.get("/api/v2/troll/boards/")
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                logger.exception("Failed to fetch vehicles from ETTU")
                return []
        except Exception:
            logger.exception("Failed to fetch vehicles from ETTU")
            return []

        vehicles = []
        for item in data if isinstance(data, list) else data.get("vehicles", data.get("boards", [])):
            try:
                layer = int(item.get("LAYER", item.get("layer", -1)))
                on_route = item.get("ON_ROUTE", item.get("on_route", 0))

                vehicle = RawVehicle(
                    dev_id=str(item.get("DEV_ID", item.get("dev_id", ""))),
                    board_num=str(item.get("BOARD_NUM", item.get("board_num", item.get("gos_num", "")))),
                    route_num=str(item.get("ROUTE", item.get("route", item.get("marsh", "")))),
                    lat=float(item.get("LAT", item.get("lat", 0))),
                    lon=float(item.get("LON", item.get("lon", item.get("lng", 0)))),
                    speed=float(item.get("SPEED", item.get("speed", 0))),
                    course=float(item.get("COURSE", item.get("course", item.get("dir", 0)))),
                    on_route=bool(int(on_route)) if on_route is not None else False,
                    layer=layer,
                    timestamp=str(item.get("TIMESTAMP", item.get("timestamp", item.get("last_time", "")))),
                )
                # Only include trams that are on route
                if vehicle.on_route and vehicle.lat != 0 and vehicle.lon != 0:
                    vehicles.append(vehicle)
            except (ValueError, TypeError) as e:
                logger.debug("Skipping malformed vehicle record: %s", e)
                continue

        logger.info("Fetched %d active trams from ETTU", len(vehicles))
        return vehicles

    async def fetch_routes(self) -> list[RawRoute]:
        """Fetch tram route data."""
        routes = []
        try:
            resp = await self._client.get("/api/v2/tram/routes/")
            resp.raise_for_status()
            data = resp.json()
            logger.info("DEBUG routes response type=%s sample=%s", type(data).__name__, str(data)[:500])

            items = data if isinstance(data, list) else data.get("routes", [])
            for item in items:
                route = RawRoute(
                    id=int(item.get("ID", item.get("id", 0))),
                    number=str(item.get("NUM", item.get("number", item.get("name", "")))),
                    name=str(item.get("NAME", item.get("title", ""))),
                )
                # Try to get route geometry
                points = item.get("POINTS", item.get("points", item.get("geometry", [])))
                if isinstance(points, list):
                    for pt in points:
                        if isinstance(pt, dict):
                            route.points.append([
                                float(pt.get("LAT", pt.get("lat", 0))),
                                float(pt.get("LON", pt.get("lon", pt.get("lng", 0)))),
                            ])
                        elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            route.points.append([float(pt[0]), float(pt[1])])

                # Try to get stops for this route
                stop_data = item.get("STOPS", item.get("stops", []))
                if isinstance(stop_data, list):
                    for s in stop_data:
                        if isinstance(s, dict):
                            route.stops.append({
                                "id": int(s.get("ID", s.get("id", 0))),
                                "name": str(s.get("NAME", s.get("name", ""))),
                                "lat": float(s.get("LAT", s.get("lat", 0))),
                                "lon": float(s.get("LON", s.get("lon", s.get("lng", 0)))),
                                "order": int(s.get("ORDER", s.get("order", 0))),
                                "direction": int(s.get("DIRECTION", s.get("direction", 0))),
                            })

                routes.append(route)
        except Exception:
            logger.exception("Failed to fetch routes from ETTU")

        logger.info("Fetched %d tram routes from ETTU", len(routes))
        return routes

    async def fetch_stops(self) -> list[RawStop]:
        """Fetch all tram stops."""
        stops = []
        try:
            resp = await self._client.get("/api/v2/tram/stops/")
            resp.raise_for_status()
            data = resp.json()
            logger.info("DEBUG stops response type=%s sample=%s", type(data).__name__, str(data)[:500])

            items = data if isinstance(data, list) else data.get("stops", [])
            for item in items:
                try:
                    stops.append(RawStop(
                        id=int(item.get("ID", item.get("id", 0))),
                        name=str(item.get("NAME", item.get("name", ""))),
                        lat=float(item.get("LAT", item.get("lat", 0))),
                        lon=float(item.get("LON", item.get("lon", item.get("lng", 0)))),
                    ))
                except (ValueError, TypeError):
                    continue
        except Exception:
            logger.exception("Failed to fetch stops from ETTU")

        logger.info("Fetched %d tram stops from ETTU", len(stops))
        return stops
