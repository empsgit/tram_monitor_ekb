"""Main orchestrator: fetches vehicle data, processes through pipeline, publishes updates."""

import asyncio
import datetime
import json
import logging
import math
import time
from collections import deque

import httpx
from sqlalchemy import text

from app.config import settings
from app.core.broadcaster import Broadcaster
from app.core.eta_calculator import EtaCalculator
from app.core.history import History
from app.core.journey_tracker import JourneyTracker, route_version
from app.core.ettu_client import EttuClient, RawRoute, RawStop, RawVehicle
from app.core.route_matcher import RouteMatcher
from app.core.stop_detector import StopDetector, StopOnRoute
from app.schemas.vehicle import VehicleState, NextStopInfo, StopInfo

logger = logging.getLogger(__name__)

OSRM_BASE = "https://router.project-osrm.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Bounding box for Ekaterinburg tram network (south,west,north,east)
EKB_BBOX = "56.7,60.4,56.95,60.8"

# Approximate meters per degree at Yekaterinburg latitude (~56.8)
LAT_M_PER_DEG = 111_320.0
LON_M_PER_DEG = 111_320.0 * math.cos(math.radians(56.84))


def _stop_display_name(name: str, direction: str) -> str:
    """Combine stop name with direction label, e.g. '1-й км (на Пионерскую)'."""
    if direction:
        return f"{name} ({direction})"
    return name


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two lat/lon points."""
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


class VehicleTracker:
    """Orchestrates the vehicle tracking pipeline."""

    def __init__(
        self,
        ettu: EttuClient,
        broadcaster: Broadcaster,
        session_factory,
    ) -> None:
        self.ettu = ettu
        self.broadcaster = broadcaster
        self.session_factory = session_factory
        self.route_matcher = RouteMatcher()
        self.stop_detector = StopDetector()
        self.eta_calculator = EtaCalculator()
        self.journeys = JourneyTracker()
        self.history = History(session_factory)
        self._route_versions: dict[int, str] = {}
        self._history_seen: dict[str, datetime.datetime] = {}

        # route_num -> route_id mapping
        self._route_num_to_id: dict[str, int] = {}
        self._route_id_to_num: dict[int, str] = {}

        # Route geometries for API exposure: route_id -> [[lat, lon], ...]
        self._route_geometries: dict[int, list[list[float]]] = {}

        # Route stop IDs (named only) for frontend filtering
        self._route_stop_ids: dict[int, list[int]] = {}

        # Stop progress cache on route geometry: route_id -> {stop_id -> progress 0..1}
        self._route_stop_progress: dict[int, dict[int, float]] = {}

        # stop_id -> set of route_ids that serve it
        self._stop_to_routes: dict[int, set[int]] = {}

        # stop_id -> (lat, lon) for distance calculations
        self._stop_coords: dict[int, tuple[float, float]] = {}

        # stop_id -> direction label
        self._stop_directions: dict[int, str] = {}

        # Current vehicle states (vehicle_id -> VehicleState)
        self.current_states: dict[str, VehicleState] = {}

        # Full next-stops list per vehicle (for station arrival queries)
        # vehicle_id -> [StopOnRoute] (all remaining stops, not just first 5)
        self._vehicle_all_next_stops: dict[str, list[StopOnRoute]] = {}

        # Per-vehicle data age (seconds since ATIME) for ETA correction in arrivals
        self._vehicle_data_age: dict[str, float] = {}

        # Precomputed per-stop arrivals snapshot, rebuilt each poll cycle.
        # {stop_id: [arrival_dict, ...]}
        self._stop_arrivals_snapshot: dict[int, list[dict]] = {}
        self._stop_arrivals_updated_monotonic: float = 0.0

        # Ghost vehicle tracking: vehicle_id -> last seen UTC timestamp
        self._last_seen: dict[str, datetime.datetime] = {}

        # Diagnostics: track unresolved stop IDs per route
        self._diag_unresolved: dict[int, list[int]] = {}  # route_id -> [stop_ids not in points]
        self._diag_total_path_stops: dict[int, int] = {}  # route_id -> total path entries
        self._projection_events: deque[dict] = deque(maxlen=500)

    # Cache TTL constants
    STOPS_CACHE_TTL = 7 * 86400  # 7 days for stops (rarely change)
    ROUTES_CACHE_TTL = 86400  # 24 hours for routes

    async def load_routes_and_stops(self) -> None:
        # Build off to the side. A failed fetch/transaction never mutates the live catalog.
        staged = VehicleTracker(self.ettu, self.broadcaster, self.session_factory)
        await staged._load_catalog()
        fields = ("route_matcher", "stop_detector", "_route_num_to_id", "_route_id_to_num",
                  "_route_geometries", "_route_stop_ids", "_route_stop_progress", "_stop_to_routes",
                  "_stop_coords", "_stop_directions", "_diag_unresolved", "_diag_total_path_stops")
        for name in fields:
            setattr(self, name, getattr(staged, name))
        self._route_versions = {rid: route_version(directions)
                                for rid, directions in self.stop_detector._stops.items()}
        for vid, journey in list(self.journeys.states.items()):
            if self._route_versions.get(journey.route_id) != journey.version:
                del self.journeys.states[vid]
                self._vehicle_all_next_stops.pop(vid, None)
                state = self.current_states.get(vid)
                if state:
                    state.next_stops = []
                    state.prev_stop = None
                    state.direction = None
                    state.direction_status = "learning"
        self._rebuild_stop_arrivals_snapshot()

    async def _load_catalog(self) -> None:
        """Fetch and load routes and stops from ETTU API (or DB cache) into matchers."""
        routes = await self.ettu.fetch_routes()
        if not routes:
            raise RuntimeError('Empty route catalog; keeping previous snapshot')

        # Try cached stops from DB; only fetch from ETTU if stale (>7 days) or empty
        stops = await self._load_cached_stops()
        stops_fetched = not stops
        if stops_fetched:
            stops = await self.ettu.fetch_stops()
        if not stops:
            raise RuntimeError('Empty stop catalog; keeping previous snapshot')

        # Try cached OSM geometries first; fetch fresh if cache is stale (>24h)
        osm_geometries = await self._load_cached_geometries()
        if not osm_geometries:
            osm_geometries = await self._fetch_osm_geometries()
            if osm_geometries:
                await self._save_geometry_cache(osm_geometries)

        # Build stop lookup from ALL stops (including unnamed) for route resolution
        stop_lookup: dict[int, RawStop] = {s.id: s for s in stops}

        # Cache stop coordinates and direction labels
        self._stop_directions: dict[int, str] = {}
        for s in stops:
            self._stop_coords[s.id] = (s.lat, s.lon)
            if s.direction:
                self._stop_directions[s.id] = s.direction

        for route in routes:
            self._route_num_to_id[route.number] = route.id
            self._route_id_to_num[route.id] = route.number

            # Resolve stop coordinates from the global stop list
            resolved_stops = []
            unresolved_ids = []
            total_path = len(route.stops)
            for s in route.stops:
                stop_info = stop_lookup.get(s["id"])
                if stop_info:
                    s["name"] = stop_info.name
                    s["direction_label"] = stop_info.direction
                    s["lat"] = stop_info.lat
                    s["lon"] = stop_info.lon
                    resolved_stops.append(s)
                else:
                    unresolved_ids.append(s["id"])
            route.stops = resolved_stops

            # Resolve geometry stops (used for route line rendering)
            resolved_geom = []
            for s in route.geometry_stops:
                stop_info = stop_lookup.get(s["id"])
                if stop_info:
                    s["name"] = stop_info.name
                    s["direction_label"] = stop_info.direction
                    s["lat"] = stop_info.lat
                    s["lon"] = stop_info.lon
                    resolved_geom.append(s)
            route.geometry_stops = resolved_geom
            self._diag_total_path_stops[route.id] = total_path
            if unresolved_ids:
                self._diag_unresolved[route.id] = unresolved_ids
                logger.info(
                    "Route %s (%s): %d/%d path entries are intermediate waypoints (no stop data)",
                    route.number, route.name, len(unresolved_ids), total_path,
                )

            # Build stop-route association
            named_ids = set()
            for s in route.stops:
                self._stop_to_routes.setdefault(s["id"], set()).add(route.id)
                if s["name"]:
                    named_ids.add(s["id"])
            self._route_stop_ids[route.id] = list(named_ids)

            # Route geometry priority: OSM > OSRM > stop-to-stop lines
            osm_geom = osm_geometries.get(route.number)
            if osm_geom:
                route.points = osm_geom
                logger.debug("Route %s: using OSM geometry (%d pts)", route.number, len(osm_geom))
            else:
                geom_src = route.geometry_stops or route.stops
                osrm_geom = await self._fetch_osrm_geometry(geom_src)
                if osrm_geom:
                    route.points = osrm_geom
                    logger.debug("Route %s: using OSRM geometry", route.number)
                elif not route.points and geom_src:
                    route.points = [
                        [s["lat"], s["lon"]]
                        for s in geom_src
                        if s["lat"] != 0 and s["lon"] != 0
                    ]
                    logger.debug("Route %s: using stop-to-stop fallback", route.number)

            # Store geometry for API exposure
            if route.points:
                self._route_geometries[route.id] = route.points
                self.route_matcher.load_route(route.id, route.points)

                # Precompute stop progress on geometry for section-bound checks.
                stop_prog: dict[int, float] = {}
                for s in route.stops:
                    if s["lat"] == 0 or s["lon"] == 0:
                        continue
                    m = self.route_matcher.match(route.id, s["lat"], s["lon"], 0.0)
                    if m and m.distance_m <= 120:
                        stop_prog[s["id"]] = m.progress
                self._route_stop_progress[route.id] = stop_prog

            # Load stops for this route (only named stops for the detector).
            # No geometry projection needed — the detector uses GPS distances
            # and ETTU stop ordering directly.
            route_stops = []
            for s in route.stops:
                if not s["name"]:
                    continue  # Skip unnamed stops – they show as blank in popups
                route_stops.append(StopOnRoute(
                    stop_id=s["id"],
                    name=_stop_display_name(s["name"], s.get("direction_label", "")),
                    lat=s["lat"],
                    lon=s["lon"],
                    order=s["order"],
                    direction=s["direction"],
                ))

            self.stop_detector.load_route_stops(route.id, route_stops)

            # Small delay between OSRM requests to avoid rate limiting
            await asyncio.sleep(0.3)

        # Save to database (only named stops)
        await self._persist_routes_stops(routes, stops, stops_fetched=stops_fetched)
        logger.info(
            "Loaded %d routes, %d total stops (%d with geometry)",
            len(routes), len(stops), len(self._route_geometries),
        )

    def get_route_geometry(self, route_id: int) -> list[list[float]] | None:
        """Get route geometry as [[lat, lon], ...] for API."""
        return self._route_geometries.get(route_id)

    def get_route_stop_ids(self, route_id: int) -> list[int]:
        """Get named stop IDs for a route."""
        return self._route_stop_ids.get(route_id, [])

    # Retain lost arrivals for an operational day, not forever after a tram leaves service.
    GHOST_TTL_SECONDS = 6 * 3600

    async def poll_vehicles(self) -> None:
        try:
            vehicles = await self.ettu.fetch_vehicles()
            now = datetime.datetime.now(datetime.timezone.utc)
            for rv in vehicles:
                try:
                    state = self._process_vehicle(rv, now=now)
                    if state:
                        self.current_states[state.id] = state
                except Exception:
                    logger.exception("Invalid vehicle observation: %s", rv.dev_id)
            for vid, state in list(self.current_states.items()):
                last_seen = self._last_seen.get(vid)
                age = (now - last_seen).total_seconds() if last_seen else float("inf")
                if age > self.GHOST_TTL_SECONDS:
                    self.current_states.pop(vid, None)
                    self._last_seen.pop(vid, None)
                    self._vehicle_all_next_stops.pop(vid, None)
                    self._vehicle_data_age.pop(vid, None)
                    self.journeys.states.pop(vid, None)
                    self._history_seen.pop(vid, None)
                    continue
                state.signal_lost = age > JourneyTracker.FRESH_SECONDS
                self._vehicle_data_age[vid] = age
            self._rebuild_stop_arrivals_snapshot()
            await self.broadcaster.publish([s.model_dump() for s in self.current_states.values()])
            if settings.history_recording_enabled:
                await self.history.flush()
        except Exception:
            logger.exception("Error in vehicle poll cycle")

    def _process_vehicle(self, rv: RawVehicle, data_age_s: float = 0.0,
                         now: datetime.datetime | None = None) -> VehicleState | None:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        route_id = self._route_num_to_id.get(rv.route_num)
        directions = self.stop_detector._stops.get(route_id, {})
        version = self._route_versions.get(route_id, "")
        result = self.journeys.observe(rv.dev_id, route_id, version, directions,
                                       rv.lat, rv.lon, rv.speed, rv.atime_utc, now)
        self.history.counters[result.quality] += 1
        journey = self.journeys.states.get(rv.dev_id)
        if settings.history_recording_enabled:
            position = None
            if (rv.atime_utc and result.quality not in ("invalid_position", "future_time", "duplicate", "out_of_order")
                    and self._history_seen.get(rv.dev_id) != rv.atime_utc):
                position = dict(vehicle_id=rv.dev_id, route_id=route_id, route_version=version or None,
                                direction=journey.direction if journey and result.accepted else None,
                                observed_at=rv.atime_utc, received_at=now, lat=rv.lat, lon=rv.lon,
                                speed=max(0, rv.speed), course=rv.course if math.isfinite(rv.course) else 0,
                                quality=result.quality)
                self._history_seen[rv.dev_id] = rv.atime_utc
            self.history.enqueue(position, result.visits, result.segments)
        previous = self.current_states.get(rv.dev_id)
        if not result.accepted:
            if previous:
                return previous
            if result.quality != "stale":
                return None
        if rv.atime_utc:
            self._last_seen[rv.dev_id] = rv.atime_utc
        state = VehicleState(
            id=rv.dev_id, board_num=rv.board_num, route=rv.route_num, route_id=route_id,
            lat=rv.lat, lon=rv.lon, speed=max(0, rv.speed),
            course=previous.course if previous and rv.speed == 0 else rv.course if math.isfinite(rv.course) else 0,
            timestamp=rv.atime_utc.isoformat() if rv.atime_utc else None,
            direction=journey.direction if journey else None,
            direction_status=journey.quality if journey else "learning",
        )
        self._vehicle_all_next_stops[rv.dev_id] = self.journeys.next_stops(rv.dev_id, directions)
        if journey and journey.direction in directions and journey.passed >= 0:
            stop = directions[journey.direction][journey.passed]
            state.prev_stop = StopInfo(id=stop.stop_id, name=stop.name)
        # progress remains null: map positions are raw fixes, with no animation.
        return state

    async def maintain_history(self) -> None:
        try:
            await self.history.retain(settings.position_retention_days, settings.sample_retention_days)
            await self.history.refresh_stats()
        except Exception:
            logger.exception("History maintenance failed")

    async def _fetch_osrm_geometry(
        self, stops: list[dict]
    ) -> list[list[float]] | None:
        """Fetch road-snapped geometry from OSRM for forward direction stops."""
        fwd = [s for s in stops
               if s["direction"] == 0 and s["lat"] != 0 and s["lon"] != 0]
        if len(fwd) < 2:
            return None

        coords = ";".join(f"{s['lon']:.6f},{s['lat']:.6f}" for s in fwd)
        url = f"{OSRM_BASE}/route/v1/driving/{coords}?overview=full&geometries=geojson"

        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") == "Ok" and data.get("routes"):
                    geojson = data["routes"][0]["geometry"]["coordinates"]
                    # Convert [lon, lat] → [lat, lon]
                    return [[c[1], c[0]] for c in geojson]
        except Exception as e:
            logger.debug("OSRM geometry fetch failed: %s", e)
        return None

    async def _fetch_osm_geometries(self) -> dict[str, list[list[float]]]:
        """Fetch tram route geometries from OpenStreetMap via Overpass API.

        Returns a dict mapping route ref number (e.g. "1", "3") to [[lat, lon], ...].
        Only the forward direction (first relation per ref) is used.
        Retries up to 3 times with exponential backoff on failure.
        """
        query = f"""
[out:json][timeout:90];
relation["route"="tram"]({EKB_BBOX});
out geom;
"""
        result: dict[str, list[list[float]]] = {}

        data = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0),
                    verify=False,
                ) as client:
                    resp = await client.post(OVERPASS_URL, data={"data": query})
                    resp.raise_for_status()
                    data = resp.json()
                    break  # Success
            except Exception as e:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "OSM geometry fetch attempt %d/3 failed: %s — retrying in %ds",
                    attempt + 1, e, wait,
                )
                await asyncio.sleep(wait)

        if data is None:
            logger.error("All 3 OSM geometry fetch attempts failed — falling back to OSRM")
            return result

        for element in data.get("elements", []):
            if element.get("type") != "relation":
                continue
            tags = element.get("tags", {})
            ref = tags.get("ref", "")
            if not ref or ref in result:
                continue  # Take only the first relation per route number

            # Extract geometry from member ways
            coords: list[list[float]] = []
            for member in element.get("members", []):
                if member.get("type") != "way" or member.get("role") not in ("", "forward"):
                    continue
                geom = member.get("geometry", [])
                for pt in geom:
                    lat, lon = pt.get("lat", 0), pt.get("lon", 0)
                    if lat and lon:
                        # Skip duplicate consecutive points
                        if coords and coords[-1][0] == lat and coords[-1][1] == lon:
                            continue
                        coords.append([lat, lon])

            if len(coords) >= 2:
                result[ref] = coords
                logger.debug("OSM geometry for route %s: %d points", ref, len(coords))

        logger.info("Fetched OSM geometries for %d tram routes", len(result))
        return result

    async def _load_cached_geometries(self) -> dict[str, list[list[float]]]:
        """Load OSM geometries from database cache if fresh (< 24 hours old)."""
        result: dict[str, list[list[float]]] = {}
        try:
            async with self.session_factory() as session:
                rows = await session.execute(
                    text("SELECT route_number, coords_json, fetched_at FROM route_geometry_cache")
                )
                now = datetime.datetime.now(datetime.timezone.utc)
                stale = False
                for row in rows:
                    age = now - row.fetched_at.replace(tzinfo=datetime.timezone.utc)
                    if age.total_seconds() > 86400:  # 24 hours
                        stale = True
                        break
                    coords = row.coords_json
                    if isinstance(coords, list) and len(coords) >= 2:
                        result[row.route_number] = coords

                if stale:
                    logger.info("OSM geometry cache is stale (>24h), will re-fetch from Overpass")
                    return {}
                if result:
                    logger.info("Loaded OSM geometries for %d routes from cache", len(result))
        except Exception:
            logger.exception("Failed to load geometry cache from database")
        return result

    async def _save_geometry_cache(self, geometries: dict[str, list[list[float]]]) -> None:
        """Save OSM geometries to database cache."""
        try:
            async with self.session_factory() as session:
                now = datetime.datetime.now(datetime.timezone.utc)
                for route_number, coords in geometries.items():
                    await session.execute(
                        text("""
                            INSERT INTO route_geometry_cache (route_number, coords_json, fetched_at)
                            VALUES (:rn, CAST(:coords AS jsonb), :now)
                            ON CONFLICT (route_number) DO UPDATE SET
                                coords_json = CAST(:coords AS jsonb),
                                fetched_at = :now
                        """),
                        {"rn": route_number, "coords": json.dumps(coords), "now": now},
                    )
                await session.commit()
                logger.info("Saved OSM geometries for %d routes to cache", len(geometries))
        except Exception:
            logger.exception("Failed to save geometry cache to database")

    async def _load_cached_stops(self) -> list[RawStop]:
        """Load stops from database cache if fresh (< 7 days old)."""
        try:
            async with self.session_factory() as session:
                # Check cache freshness
                meta = await session.execute(
                    # v1 stored named stops only; v2 also retains geometry waypoints.
                    text("SELECT refreshed_at FROM data_cache_meta WHERE cache_key = 'ettu_stops_v2'")
                )
                row = meta.first()
                if row:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    age = now - row.refreshed_at.replace(tzinfo=datetime.timezone.utc)
                    if age.total_seconds() > self.STOPS_CACHE_TTL:
                        logger.info("Stops cache is stale (>7 days), will re-fetch from ETTU")
                        return []
                else:
                    return []  # No cache timestamp → never cached

                # Load stops from DB
                result = await session.execute(
                    text("SELECT id, name, direction, lat, lon FROM stops")
                )
                stops = []
                for r in result:
                    stops.append(RawStop(
                        id=r.id, name=r.name, direction=r.direction or "",
                        lat=r.lat, lon=r.lon,
                    ))
                if stops:
                    logger.info("Loaded %d stops from database cache", len(stops))
                return stops
        except Exception:
            logger.exception("Failed to load stops from database cache")
        return []

    async def _persist_routes_stops(self, routes: list[RawRoute], stops: list[RawStop],
                                    stops_fetched: bool = False) -> None:
        # Route sequences and cache freshness commit together; old associations disappear.
        async with self.session_factory() as session:
            await session.execute(text('''INSERT INTO routes (id, number, name, color)
                VALUES (:id, :number, :name, '#e53935') ON CONFLICT (id)
                DO UPDATE SET number=excluded.number, name=excluded.name'''),
                [dict(id=r.id, number=r.number, name=r.name) for r in routes])
            await session.execute(text('''INSERT INTO stops (id, name, direction, lat, lon)
                VALUES (:id, :name, :direction, :lat, :lon) ON CONFLICT (id) DO UPDATE
                SET name=excluded.name, direction=excluded.direction, lat=excluded.lat, lon=excluded.lon'''),
                [dict(id=s.id, name=s.name, direction=s.direction or '', lat=s.lat, lon=s.lon) for s in stops])
            await session.execute(text('DELETE FROM route_stops'))
            links = [dict(rid=r.id, sid=s['id'], direction=s['direction'], order=s['order'])
                     for r in routes for s in r.stops if s['name']]
            if links:
                await session.execute(text('''INSERT INTO route_stops (route_id, stop_id, direction, "order")
                    VALUES (:rid, :sid, :direction, :order) ON CONFLICT DO NOTHING'''), links)
            if stops_fetched:
                await session.execute(text('''INSERT INTO data_cache_meta (cache_key, refreshed_at)
                    VALUES ('ettu_stops_v2', :now) ON CONFLICT (cache_key)
                    DO UPDATE SET refreshed_at=excluded.refreshed_at'''),
                    {'now': datetime.datetime.now(datetime.timezone.utc)})
            await session.commit()

    def _log_projection_event(self, kind: str, payload: dict) -> None:
        event = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        self._projection_events.append(event)

    def get_projection_diagnostics(self, limit: int = 100) -> dict:
        events = list(self._projection_events)[-max(1, min(limit, 500)):]
        counts: dict[str, int] = {}
        for e in self._projection_events:
            k = e.get("kind", "unknown")
            counts[k] = counts.get(k, 0) + 1
        return {
            "events_total": len(self._projection_events),
            "counts": counts,
            "latest": events,
        }

    def get_diagnostics(self) -> dict:
        """Get pipeline diagnostics for debugging route-stop resolution."""
        route_diags = []
        for route_id, route_num in self._route_id_to_num.items():
            total = self._diag_total_path_stops.get(route_id, 0)
            unresolved = self._diag_unresolved.get(route_id, [])
            resolved = total - len(unresolved)
            named = len(self._route_stop_ids.get(route_id, []))
            has_geometry = route_id in self._route_geometries
            geom_points = len(self._route_geometries.get(route_id, []))
            route_len = self.route_matcher.get_total_length(route_id)

            # Get stops loaded in detector
            detector_stops = self.stop_detector.get_all_stops(route_id)
            stops_by_dir: dict[int, list[dict]] = {}
            for s in detector_stops:
                stops_by_dir.setdefault(s.direction, []).append({
                    "id": s.stop_id,
                    "name": s.name,
                    "order": s.order,
                    "cumulative_distance_m": round(s.cumulative_distance_m, 1),
                })

            route_diags.append({
                "route_id": route_id,
                "route_number": route_num,
                "path_stop_count": total,
                "resolved_count": resolved,
                "named_count": named,
                "unresolved_ids": unresolved,
                "has_osrm_geometry": has_geometry,
                "geometry_points": geom_points,
                "route_length_m": round(route_len, 1),
                "stops_by_direction": {
                    str(d): sl for d, sl in sorted(stops_by_dir.items())
                },
            })

        # Count vehicles with/without route match
        matched = sum(1 for v in self.current_states.values() if v.progress is not None)
        total_vehicles = len(self.current_states)

        return {
            "total_stops_in_points_api": len(self._stop_coords),
            "history": {
                "enabled": settings.history_recording_enabled,
                "counters": dict(self.history.counters),
                "pending_positions": len(self.history.positions),
                "pending_visits": len(self.history.visits),
                "pending_segments": len(self.history.segments),
                "statistics_groups": len(self.history.stats),
                "statistics_updated_at": self.history.stats_updated_at,
            },
            "journeys": [{"vehicle_id": vid, "route_id": j.route_id,
                          "route_version": j.version, "direction": j.direction,
                          "status": j.quality, "passed_index": j.passed,
                          "observed_at": j.observed_at.isoformat() if j.observed_at else None}
                         for vid, j in self.journeys.states.items()],
            "total_routes": len(self._route_id_to_num),
            "total_vehicles": total_vehicles,
            "vehicles_matched_to_route": matched,
            "vehicles_unmatched": total_vehicles - matched,
            "routes": sorted(route_diags, key=lambda r: r["route_number"]),
        }

    def _rebuild_stop_arrivals_snapshot(self) -> None:
        snapshot: dict[int, list[dict]] = {}
        for vid, state in self.current_states.items():
            journey = self.journeys.states.get(vid)
            if not journey or journey.direction is None:
                continue
            directions = self.stop_detector._stops.get(journey.route_id, {})
            if journey.direction not in directions:
                continue
            predictions = self.eta_calculator.predict(journey, directions[journey.direction], self.history)
            age = self._vehicle_data_age.get(vid, 0)
            correction = min(30, max(0, age)) if state.speed > 0 else 0
            state.next_stops = []
            seen = set()
            for stop, estimate in predictions:
                for key in ("eta_seconds", "eta_min_seconds", "eta_max_seconds"):
                    value = estimate[key]
                    estimate[key] = None if state.signal_lost else max(0, int(value - correction)) if value is not None else None
                if stop.stop_id in seen:
                    continue
                seen.add(stop.stop_id)
                snapshot.setdefault(stop.stop_id, []).append(dict(
                    vehicle_id=vid, board_num=state.board_num, route=state.route,
                    route_id=state.route_id, signal_lost=state.signal_lost,
                    source_age=age, countdown_budget=max(0, 30 - correction) if state.speed > 0 else 0,
                    **estimate))
                if len(state.next_stops) < 5:
                    state.next_stops.append(NextStopInfo(id=stop.stop_id, name=stop.name, **estimate))
        self._stop_arrivals_snapshot = snapshot
        self._stop_arrivals_updated_monotonic = time.monotonic()

    def get_vehicles_for_stop(self, stop_id: int, route_filter: int | None = None) -> list[dict]:
        age = max(0, time.monotonic() - self._stop_arrivals_updated_monotonic)
        arrivals = []
        for item in self._stop_arrivals_snapshot.get(stop_id, []):
            if route_filter is not None and item['route_id'] != route_filter:
                continue
            row = {k: v for k, v in item.items() if k not in ('source_age', 'countdown_budget')}
            row['signal_lost'] = item['signal_lost'] or item['source_age'] + age > JourneyTracker.FRESH_SECONDS
            for key in ('eta_seconds', 'eta_min_seconds', 'eta_max_seconds'):
                value = row[key]
                row[key] = None if row['signal_lost'] else max(0, int(value - min(age, item['countdown_budget']))) if value is not None else None
            arrivals.append(row)
        arrivals.sort(key=lambda a: (a['signal_lost'], a['eta_seconds'] if a['eta_seconds'] is not None else 999999, a['vehicle_id']))
        return arrivals[:15]
