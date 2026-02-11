"""Async client for the ETTU (Gortrans) API at map.ettu.ru."""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ETTU API layer identifiers
LAYER_TRAM = 0

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = [2, 4, 8]  # seconds between retries


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
    direction: str = ""  # e.g. "на Пионерскую"


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
            timeout=30.0,
            headers={"Accept": "application/json"},
            params={"apiKey": "111"},
            verify=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_with_retry(self, path: str, label: str) -> httpx.Response | None:
        """GET request with retry and exponential backoff."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self._client.get(path)
                resp.raise_for_status()
                return resp
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF[attempt]
                    logger.warning(
                        "%s attempt %d/%d failed (%s), retrying in %ds",
                        label, attempt + 1, MAX_RETRIES + 1, type(e).__name__, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("%s failed after %d attempts: %s", label, MAX_RETRIES + 1, e)
                    return None
            except Exception:
                logger.exception("Failed to fetch %s from ETTU", label)
                return None
        return None

    async def fetch_vehicles(self) -> list[RawVehicle]:
        """Fetch all current tram positions."""
        resp = await self._get_with_retry("/api/v2/tram/boards/", "vehicles")
        if resp is None:
            return []
        try:
            data = resp.json()
            logger.debug("Boards response keys=%s count=%d", list(data.keys()) if isinstance(data, dict) else "list", len(data if isinstance(data, list) else data.get("vehicles", [])))
        except Exception:
            logger.exception("Failed to parse vehicles response from ETTU")
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
                    speed=float(item.get("VELOCITY", item.get("SPEED", item.get("speed", 0)))),
                    course=float(item.get("COURSE", item.get("course", item.get("dir", 0)))),
                    on_route=bool(int(on_route)) if on_route is not None else False,
                    layer=layer,
                    timestamp=str(item.get("ATIME", item.get("TIMESTAMP", item.get("timestamp", "")))),
                )
                # Include trams with valid coordinates and a route assigned
                if vehicle.lat != 0 and vehicle.lon != 0 and vehicle.route_num:
                    vehicles.append(vehicle)
            except (ValueError, TypeError) as e:
                logger.debug("Skipping malformed vehicle record: %s", e)
                continue

        logger.info("Fetched %d active trams from ETTU", len(vehicles))
        return vehicles

    async def fetch_routes(self) -> list[RawRoute]:
        """Fetch tram route data."""
        routes = []
        resp = await self._get_with_retry("/api/v2/tram/routes/", "routes")
        if resp is None:
            logger.info("Fetched 0 tram routes from ETTU")
            return routes

        try:
            data = resp.json()
            logger.debug("Routes response keys=%s count=%d", list(data.keys()) if isinstance(data, dict) else "list", len(data if isinstance(data, list) else data.get("routes", [])))

            items = data if isinstance(data, list) else data.get("routes", [])
            for item in items:
                route = RawRoute(
                    id=int(item.get("id", item.get("ID", 0))),
                    number=str(item.get("num", item.get("NUM", item.get("number", "")))),
                    name=str(item.get("name", item.get("NAME", item.get("title", "")))),
                )

                # Parse elements → extract ordered stop IDs from path
                elements = item.get("elements", [])
                if isinstance(elements, list):
                    for elem in elements:
                        direction = int(elem.get("ind", 0))
                        path = elem.get("path", [])
                        if isinstance(path, list):
                            for order, stop_id_str in enumerate(path):
                                try:
                                    route.stops.append({
                                        "id": int(stop_id_str),
                                        "name": "",
                                        "lat": 0.0,
                                        "lon": 0.0,
                                        "order": order,
                                        "direction": direction,
                                    })
                                except (ValueError, TypeError):
                                    continue

                routes.append(route)
        except Exception:
            logger.exception("Failed to parse routes from ETTU")

        logger.info("Fetched %d tram routes from ETTU", len(routes))
        return routes

    async def fetch_stops(self) -> list[RawStop]:
        """Fetch all tram stops."""
        stops = []
        resp = await self._get_with_retry("/api/v2/tram/points/", "stops")
        if resp is None:
            logger.info("Fetched 0 tram stops from ETTU")
            return stops

        try:
            data = resp.json()
            items = data if isinstance(data, list) else (
                data.get("points", data.get("stops", data.get("stations", [])))
            )
            for item in items:
                try:
                    stop_id = int(item.get("ID", item.get("id", 0)))
                    if stop_id == 0:
                        continue
                    name = str(item.get("NAME", item.get("name", ""))).strip()
                    lat = float(item.get("LAT", item.get("lat", 0)))
                    lon = float(item.get("LON", item.get("lon", item.get("lng", 0))))
                    if lat == 0 or lon == 0:
                        continue
                    direction = str(item.get("DIRECTION", item.get("direction", ""))).strip()
                    stops.append(RawStop(id=stop_id, name=name, lat=lat, lon=lon, direction=direction))
                except (ValueError, TypeError):
                    continue
        except Exception:
            logger.exception("Failed to parse stops from ETTU")

        logger.info("Fetched %d tram stops from ETTU", len(stops))
        return stops
