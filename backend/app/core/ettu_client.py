"""Async client for the ETTU (Gortrans) API at map.ettu.ru."""

import asyncio
import datetime
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

# ETTU timestamps are in Asia/Yekaterinburg (UTC+5)
_EKB_TZ = datetime.timezone(datetime.timedelta(hours=5))


def _parse_atime(raw: str) -> datetime.datetime | None:
    """Parse ETTU ATIME string like '2026-02-13 16:30:42' (Yekaterinburg local) to UTC datetime."""
    if not raw:
        return None
    try:
        local = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_EKB_TZ)
        return local.astimezone(datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


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
    atime_utc: datetime.datetime | None = None  # parsed ATIME in UTC


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
    geometry_stops: list[dict] = field(default_factory=list)  # subset used for geometry only


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
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF[attempt]
                    logger.warning(
                        "%s attempt %d/%d got HTTP %d, retrying in %ds",
                        label, attempt + 1, MAX_RETRIES + 1, e.response.status_code, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("Failed to fetch %s from ETTU: %s", label, e)
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

                raw_ts = str(item.get("ATIME", item.get("TIMESTAMP", item.get("timestamp", ""))))
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
                    timestamp=raw_ts,
                    atime_utc=_parse_atime(raw_ts),
                )
                # Include trams with valid coordinates and a route assigned
                if vehicle.lat != 0 and vehicle.lon != 0 and vehicle.route_num:
                    vehicles.append(vehicle)
            except (ValueError, TypeError) as e:
                logger.debug("Skipping malformed vehicle record: %s", e)
                continue

        logger.info("Fetched %d active trams from ETTU", len(vehicles))
        return vehicles

    @staticmethod
    def _extract_stop_id(item) -> int | None:
        """Extract stop ID from various formats: int, str, or dict with id/ID."""
        if isinstance(item, dict):
            raw = item.get("id", item.get("ID"))
            if raw is not None:
                return int(raw)
            return None
        return int(item)

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
                    for dir_idx, elem in enumerate(elements):
                        # Use element position as direction (0=forward, 1=reverse),
                        # NOT elem["ind"] which is an opaque element ID (e.g. 30, 40)
                        direction = dir_idx
                        # full_path has ALL stops (for tracking);
                        # path has major stops only (for clean geometry)
                        full_path = elem.get("full_path", elem.get("path", []))
                        geom_path = elem.get("path", full_path)
                        # Also check for element-level 'stops' as alternative source
                        if not full_path:
                            elem_stops = elem.get("stops", elem.get("stations", []))
                            if isinstance(elem_stops, list):
                                full_path = elem_stops
                                if not geom_path:
                                    geom_path = elem_stops
                        if isinstance(full_path, list):
                            for order, stop_item in enumerate(full_path):
                                try:
                                    sid = self._extract_stop_id(stop_item)
                                    if sid is None:
                                        continue
                                    route.stops.append({
                                        "id": sid,
                                        "name": "",
                                        "lat": 0.0,
                                        "lon": 0.0,
                                        "order": order,
                                        "direction": direction,
                                    })
                                except (ValueError, TypeError):
                                    continue
                        if isinstance(geom_path, list):
                            for order, stop_item in enumerate(geom_path):
                                try:
                                    sid = self._extract_stop_id(stop_item)
                                    if sid is None:
                                        continue
                                    route.geometry_stops.append({
                                        "id": sid,
                                        "name": "",
                                        "lat": 0.0,
                                        "lon": 0.0,
                                        "order": order,
                                        "direction": direction,
                                    })
                                except (ValueError, TypeError):
                                    continue

                # Fallback: route-level stops/stations if elements yielded nothing
                if not route.stops:
                    route_stops = item.get("stops", item.get("stations", []))
                    if isinstance(route_stops, list):
                        for order, stop_item in enumerate(route_stops):
                            try:
                                sid = self._extract_stop_id(stop_item)
                                if sid is None:
                                    continue
                                direction = 0
                                if isinstance(stop_item, dict):
                                    direction = int(stop_item.get("direction", stop_item.get("ind", 0)))
                                route.stops.append({
                                    "id": sid,
                                    "name": "",
                                    "lat": 0.0,
                                    "lon": 0.0,
                                    "order": order,
                                    "direction": direction,
                                })
                            except (ValueError, TypeError):
                                continue

                if not route.stops:
                    logger.warning(
                        "Route %s (%s): 0 stops parsed. keys=%s, elements_count=%d",
                        route.number, route.name,
                        list(item.keys()),
                        len(elements) if isinstance(elements, list) else -1,
                    )

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
                    name = str(item.get("NAME") or item.get("name") or "").strip()
                    lat = float(item.get("LAT", item.get("lat", 0)))
                    lon = float(item.get("LON", item.get("lon", item.get("lng", 0))))
                    if lat == 0 or lon == 0:
                        continue
                    direction = str(item.get("DIRECTION") or item.get("direction") or "").strip()
                    stops.append(RawStop(id=stop_id, name=name, lat=lat, lon=lon, direction=direction))
                except (ValueError, TypeError):
                    continue
        except Exception:
            logger.exception("Failed to parse stops from ETTU")

        logger.info("Fetched %d tram stops from ETTU", len(stops))
        return stops
