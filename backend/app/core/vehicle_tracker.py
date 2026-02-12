"""Main orchestrator: fetches vehicle data, processes through pipeline, publishes updates."""

import asyncio
import datetime
import json
import logging
import math
from collections import deque

import httpx
from sqlalchemy import text

from app.core.broadcaster import Broadcaster
from app.core.eta_calculator import EtaCalculator
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

        # Per-vehicle smoothing state for progress and speed
        self._smooth: dict[str, dict] = {}
        # {vehicle_id: {"progress": float | None, "speed": float, "direction": int, "route_id": int}}

        # Recent GPS positions for bearing calculation (last 3 points)
        self._recent_positions: dict[str, list[tuple[float, float]]] = {}

        # Per-vehicle stop passage tracking for travel time recording
        # {vehicle_id: {"stop_id": int, "route_id": int, "time": datetime}}
        self._last_stop_passage: dict[str, dict] = {}
        # Batch of travel time observations to persist
        self._travel_time_batch: list[dict] = []

        # Full next-stops list per vehicle (for station arrival queries)
        # vehicle_id -> [StopOnRoute] (all remaining stops, not just first 5)
        self._vehicle_all_next_stops: dict[str, list[StopOnRoute]] = {}

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
        """Fetch and load routes and stops from ETTU API (or DB cache) into matchers."""
        routes = await self.ettu.fetch_routes()

        # Try cached stops from DB; only fetch from ETTU if stale (>7 days) or empty
        stops = await self._load_cached_stops()
        if not stops:
            stops = await self.ettu.fetch_stops()

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
                logger.warning(
                    "Route %s (%s): %d/%d stops unresolved: %s",
                    route.number, route.name, len(unresolved_ids), total_path, unresolved_ids[:10],
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
        await self._persist_routes_stops(routes, stops)
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

    # How long to keep a vehicle on the map after it disappears from API
    GHOST_TTL_SECONDS = 120  # 2 minutes

    async def poll_vehicles(self) -> None:
        """Single poll cycle: fetch positions, process, publish (including ghost vehicles)."""
        try:
            raw_vehicles = await self.ettu.fetch_vehicles()

            now = datetime.datetime.now(datetime.timezone.utc)
            current_ids: set[str] = set()
            states = []

            for rv in raw_vehicles:
                state = self._process_vehicle(rv)
                if state:
                    state.signal_lost = False
                    states.append(state)
                    self.current_states[state.id] = state
                    self._last_seen[state.id] = now
                    current_ids.add(state.id)
                    self._record_stop_passage(state, now)

            # Add ghost vehicles: recently seen but absent from current API response
            expired = []
            for vid, last_seen in self._last_seen.items():
                if vid in current_ids:
                    continue
                age = (now - last_seen).total_seconds()
                if age <= self.GHOST_TTL_SECONDS:
                    ghost = self.current_states.get(vid)
                    if ghost:
                        ghost.signal_lost = True
                        ghost.speed = 0
                        states.append(ghost)
                else:
                    expired.append(vid)
                    self.current_states.pop(vid, None)
                    self._smooth.pop(vid, None)
                    self._recent_positions.pop(vid, None)
                    self._vehicle_all_next_stops.pop(vid, None)

            for vid in expired:
                del self._last_seen[vid]

            # Publish all vehicles (live + ghosts) to subscribers
            vehicles_data = [s.model_dump() for s in states]
            await self.broadcaster.publish(vehicles_data)

            # Persist positions and travel times (only if we got data)
            if raw_vehicles:
                await self._persist_positions(raw_vehicles)
                await self._persist_travel_times()

        except Exception:
            logger.exception("Error in vehicle poll cycle")

    def _process_vehicle(self, rv: RawVehicle) -> VehicleState | None:
        """Process a single raw vehicle through the pipeline.

        Stop detection is GPS-based (independent of route geometry).
        Route geometry is only used for visual snapping + animation progress.
        """
        route_id = self._route_num_to_id.get(rv.route_num)

        state = VehicleState(
            id=rv.dev_id,
            board_num=rv.board_num,
            route=rv.route_num,
            route_id=route_id,
            lat=rv.lat,
            lon=rv.lon,
            speed=rv.speed,
            course=rv.course,
            timestamp=rv.timestamp,
        )

        if route_id is None:
            return state

        # Track recent positions for bearing calculation (keep last 5 for better averaging)
        positions = self._recent_positions.get(rv.dev_id, [])
        positions.append((rv.lat, rv.lon))
        if len(positions) > 5:
            positions = positions[-5:]
        self._recent_positions[rv.dev_id] = positions

        # Compute bearing from recent movement
        # Only use movement bearing if the vehicle has actually moved significantly
        movement_bearing = None
        movement_dist_m = 0.0
        if len(positions) >= 2:
            p_old, p_new = positions[0], positions[-1]
            dlat_m = (p_new[0] - p_old[0]) * LAT_M_PER_DEG
            dlon_m = (p_new[1] - p_old[1]) * LON_M_PER_DEG
            dist_m = math.sqrt(dlat_m * dlat_m + dlon_m * dlon_m)
            movement_dist_m = dist_m
            if dist_m > 30:  # Moved at least 30m — reliable bearing
                movement_bearing = math.degrees(math.atan2(dlon_m, dlat_m)) % 360
            elif rv.speed > 5:  # API reports moving but GPS jitter hides it
                movement_bearing = rv.course
        # If movement_bearing is None, detection relies on preferred_direction

        # --- Disable smoothing: use raw instantaneous speed ---
        prev = self._smooth.get(rv.dev_id)
        smoothed_speed = rv.speed

        # --- Stop detection (GPS-based, uses ETTU stop order) ---
        # Use previous direction as hint for sticky detection
        prev_direction = None
        if prev and prev["route_id"] == route_id:
            prev_direction = prev.get("direction")

        detection = self.stop_detector.detect(
            route_id, rv.lat, rv.lon, movement_bearing,
            max_next=50, preferred_direction=prev_direction,
        )
        direction = detection.direction

        # Store full next stops for station arrival queries
        self._vehicle_all_next_stops[rv.dev_id] = detection.next_stops

        if detection.prev_stop:
            state.prev_stop = StopInfo(
                id=detection.prev_stop.stop_id,
                name=detection.prev_stop.name,
            )

        # Show up to 5 next stops in vehicle state (for frontend display)
        if detection.next_stops:
            display_stops = detection.next_stops[:5]
            etas = self.eta_calculator.calculate(
                rv.lat, rv.lon, smoothed_speed, display_stops
            )
            state.next_stops = [
                NextStopInfo(id=stop.stop_id, name=stop.name, eta_seconds=eta_s)
                for stop, eta_s in etas
            ]

        # --- Route matching (map position): no smoothing, no extrapolation ---
        # Route matcher expects a numeric course. If movement bearing is unavailable
        # (e.g. vehicle has moved <30m), fall back to API course to avoid crashes.
        match_course = movement_bearing if movement_bearing is not None else rv.course
        match = self.route_matcher.match(route_id, rv.lat, rv.lon, match_course)

        MAX_APPLY_SNAP_DISTANCE_M = 60.0
        if match and match.distance_m <= MAX_APPLY_SNAP_DISTANCE_M:
            raw_progress = match.progress

            # Section-bound projection check from detected prev/next stops.
            stop_prog = self._route_stop_progress.get(route_id, {})
            sec_prev = detection.prev_stop.stop_id if detection.prev_stop else None
            sec_next = detection.next_stops[0].stop_id if detection.next_stops else None
            bounded_progress = raw_progress
            if sec_prev is not None and sec_next is not None:
                p0 = stop_prog.get(sec_prev)
                p1 = stop_prog.get(sec_next)
                if p0 is not None and p1 is not None:
                    lo, hi = (p0, p1) if p0 <= p1 else (p1, p0)
                    if raw_progress < lo - 0.01 or raw_progress > hi + 0.01:
                        logger.warning(
                            "Vehicle %s route %s: projection %.3f outside section [%s->%s]=[%.3f,%.3f]",
                            rv.dev_id, route_id, raw_progress, sec_prev, sec_next, lo, hi,
                        )
                        self._log_projection_event("out_of_section", {
                            "vehicle_id": rv.dev_id,
                            "route_id": route_id,
                            "raw_progress": round(raw_progress, 6),
                            "section_prev_stop": sec_prev,
                            "section_next_stop": sec_next,
                            "section_lo": round(lo, 6),
                            "section_hi": round(hi, 6),
                        })
                        bounded_progress = min(max(raw_progress, lo), hi)

            # Enforce forward movement only when we actually observed movement.
            prev_progress = prev.get("progress") if prev and prev.get("route_id") == route_id else None
            enforce_forward = movement_dist_m > 20 or rv.speed > 5
            if enforce_forward and prev_progress is not None:
                if direction == 0 and bounded_progress + 0.001 < prev_progress:
                    logger.warning(
                        "Vehicle %s route %s: backward projection %.3f -> %.3f (dir=0)",
                        rv.dev_id, route_id, prev_progress, bounded_progress,
                    )
                    self._log_projection_event("backward_projection", {
                        "vehicle_id": rv.dev_id,
                        "route_id": route_id,
                        "direction": 0,
                        "prev_progress": round(prev_progress, 6),
                        "new_progress": round(bounded_progress, 6),
                    })
                    bounded_progress = prev_progress
                elif direction == 1 and bounded_progress - 0.001 > prev_progress:
                    logger.warning(
                        "Vehicle %s route %s: backward projection %.3f -> %.3f (dir=1)",
                        rv.dev_id, route_id, prev_progress, bounded_progress,
                    )
                    self._log_projection_event("backward_projection", {
                        "vehicle_id": rv.dev_id,
                        "route_id": route_id,
                        "direction": 1,
                        "prev_progress": round(prev_progress, 6),
                        "new_progress": round(bounded_progress, 6),
                    })
                    bounded_progress = prev_progress

            snapped = self.route_matcher.interpolate_progress(route_id, bounded_progress)
            if snapped:
                snap_error_m = _haversine(rv.lat, rv.lon, snapped[0], snapped[1])
                MAX_FINAL_SNAP_ERROR_M = 80.0
                if snap_error_m <= MAX_FINAL_SNAP_ERROR_M:
                    state.progress = bounded_progress
                    state.lat, state.lon = snapped
                else:
                    state.progress = None
                    self._log_projection_event("snap_rejected_error", {
                        "vehicle_id": rv.dev_id,
                        "route_id": route_id,
                        "snap_error_m": round(snap_error_m, 2),
                        "raw_lat": rv.lat,
                        "raw_lon": rv.lon,
                        "snap_lat": round(snapped[0], 6),
                        "snap_lon": round(snapped[1], 6),
                    })
            else:
                state.progress = None
        else:
            state.progress = None
            if match:
                self._log_projection_event("snap_rejected_far", {
                    "vehicle_id": rv.dev_id,
                    "route_id": route_id,
                    "distance_m": round(match.distance_m, 2),
                })

        self._smooth[rv.dev_id] = {
            "progress": state.progress,
            "speed": smoothed_speed,
            "direction": direction,
            "route_id": route_id,
        }

        return state

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
                    text("SELECT refreshed_at FROM data_cache_meta WHERE cache_key = 'ettu_stops'")
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

    async def _update_cache_timestamp(self, cache_key: str) -> None:
        """Update the cache freshness timestamp for a given key."""
        try:
            async with self.session_factory() as session:
                now = datetime.datetime.now(datetime.timezone.utc)
                await session.execute(
                    text("""
                        INSERT INTO data_cache_meta (cache_key, refreshed_at)
                        VALUES (:key, :now)
                        ON CONFLICT (cache_key) DO UPDATE SET refreshed_at = :now
                    """),
                    {"key": cache_key, "now": now},
                )
                await session.commit()
        except Exception:
            logger.exception("Failed to update cache timestamp for %s", cache_key)

    # Ekaterinburg is UTC+5; skip travel time recording during night (no trams running)
    _EKB_UTC_OFFSET = 5
    _NIGHT_HOURS = range(0, 5)  # 00:00–04:59 local = no service

    def _record_stop_passage(self, state: VehicleState, now: datetime.datetime) -> None:
        """Track when a vehicle passes a stop; record travel time between consecutive stops."""
        if not state.prev_stop or not state.route_id:
            return

        prev = self._last_stop_passage.get(state.id)
        current_stop_id = state.prev_stop.id

        if prev and prev["stop_id"] != current_stop_id and prev["route_id"] == state.route_id:
            elapsed = (now - prev["time"]).total_seconds()
            # Sanity: only record if 10s < elapsed < 30min (filters GPS glitches)
            if 10 < elapsed < 1800:
                local_hour = (now.hour + self._EKB_UTC_OFFSET) % 24
                # Skip night hours — no regular service, data would be unreliable
                if local_hour in self._NIGHT_HOURS:
                    pass
                else:
                    dow = now.weekday()
                    if dow < 5:
                        day_type = "weekday"
                    elif dow == 5:
                        day_type = "saturday"
                    else:
                        day_type = "sunday"

                    self._travel_time_batch.append({
                        "route_id": state.route_id,
                        "from_stop_id": prev["stop_id"],
                        "to_stop_id": current_stop_id,
                        "day_type": day_type,
                        "hour": local_hour,
                        "seconds": elapsed,
                    })

        self._last_stop_passage[state.id] = {
            "stop_id": current_stop_id,
            "route_id": state.route_id,
            "time": now,
        }

    async def _persist_travel_times(self) -> None:
        """Flush travel time observations to database with incremental averaging."""
        if not self._travel_time_batch:
            return
        batch = self._travel_time_batch
        self._travel_time_batch = []

        try:
            async with self.session_factory() as session:
                now = datetime.datetime.now(datetime.timezone.utc)
                for obs in batch:
                    # Upsert: incrementally update median (running average) and sample count
                    await session.execute(
                        text("""
                            INSERT INTO travel_time_segments
                                (route_id, from_stop_id, to_stop_id, day_type, hour,
                                 median_seconds, sample_count, updated_at)
                            VALUES (:rid, :from_id, :to_id, :day_type, :hour,
                                    :seconds, 1, :now)
                            ON CONFLICT (route_id, from_stop_id, to_stop_id, day_type, hour)
                            DO UPDATE SET
                                median_seconds = travel_time_segments.median_seconds +
                                    (:seconds - travel_time_segments.median_seconds) /
                                    (travel_time_segments.sample_count + 1),
                                sample_count = travel_time_segments.sample_count + 1,
                                updated_at = :now
                        """),
                        {
                            "rid": obs["route_id"],
                            "from_id": obs["from_stop_id"],
                            "to_id": obs["to_stop_id"],
                            "day_type": obs["day_type"],
                            "hour": obs["hour"],
                            "seconds": obs["seconds"],
                            "now": now,
                        },
                    )
                await session.commit()
                logger.debug("Persisted %d travel time observations", len(batch))
        except Exception:
            logger.exception("Failed to persist travel times")

    async def _persist_positions(self, vehicles: list[RawVehicle]) -> None:
        """Save vehicle positions to database."""
        try:
            async with self.session_factory() as session:
                now = datetime.datetime.now(datetime.timezone.utc)
                for rv in vehicles:
                    route_id = self._route_num_to_id.get(rv.route_num)
                    state = self.current_states.get(rv.dev_id)
                    progress = state.progress if state else None

                    await session.execute(
                        text("""
                            INSERT INTO vehicle_positions
                                (vehicle_id, route_id, lat, lon, speed, course, progress, timestamp)
                            VALUES (:vid, :rid, :lat, :lon, :speed, :course, :progress, :ts)
                        """),
                        {
                            "vid": rv.dev_id,
                            "rid": route_id,
                            "lat": rv.lat,
                            "lon": rv.lon,
                            "speed": rv.speed,
                            "course": rv.course,
                            "progress": progress,
                            "ts": now,
                        },
                    )
                await session.commit()
        except Exception:
            logger.exception("Failed to persist vehicle positions")

    async def _persist_routes_stops(
        self, routes: list[RawRoute], stops: list[RawStop]
    ) -> None:
        """Upsert routes and stops to database (each in its own transaction)."""
        # Phase 1a: persist routes
        try:
            async with self.session_factory() as session:
                for r in routes:
                    await session.execute(
                        text("""
                            INSERT INTO routes (id, number, name, color)
                            VALUES (:id, :number, :name, :color)
                            ON CONFLICT (id) DO UPDATE SET number=:number, name=:name
                        """),
                        {"id": r.id, "number": r.number, "name": r.name, "color": "#e53935"},
                    )
                await session.commit()
        except Exception:
            logger.exception("Failed to persist routes to database")

        # Phase 1b: persist named stops + update cache timestamp
        try:
            async with self.session_factory() as session:
                for s in stops:
                    if not s.name:
                        continue
                    await session.execute(
                        text("""
                            INSERT INTO stops (id, name, direction, lat, lon)
                            VALUES (:id, :name, :direction, :lat, :lon)
                            ON CONFLICT (id) DO UPDATE SET name=:name, direction=:direction, lat=:lat, lon=:lon
                        """),
                        {"id": s.id, "name": s.name, "direction": s.direction, "lat": s.lat, "lon": s.lon},
                    )
                # Clean up any stops with name='None' from previous bug
                await session.execute(text("DELETE FROM stops WHERE name = 'None'"))
                await session.commit()
            await self._update_cache_timestamp("ettu_stops")
        except Exception:
            logger.exception("Failed to persist stops to database")

        # Phase 2: persist route_stops (only for stops that are in DB)
        if not stops:
            return
        try:
            async with self.session_factory() as session:
                named_stop_ids = {s.id for s in stops if s.name}
                for r in routes:
                    for s in r.stops:
                        if s["id"] not in named_stop_ids:
                            continue
                        await session.execute(
                            text("""
                                INSERT INTO route_stops (route_id, stop_id, direction, "order")
                                VALUES (:rid, :sid, :dir, :ord)
                                ON CONFLICT ON CONSTRAINT uq_route_stop_dir_order
                                DO NOTHING
                            """),
                            {
                                "rid": r.id,
                                "sid": s["id"],
                                "dir": s["direction"],
                                "ord": s["order"],
                            },
                        )
                await session.commit()
        except Exception:
            logger.exception("Failed to persist route_stops to database")

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
            "total_routes": len(self._route_id_to_num),
            "total_vehicles": total_vehicles,
            "vehicles_matched_to_route": matched,
            "vehicles_unmatched": total_vehicles - matched,
            "routes": sorted(route_diags, key=lambda r: r["route_number"]),
        }

    def get_vehicles_for_stop(
        self, stop_id: int, route_filter: int | None = None
    ) -> list[dict]:
        """Get upcoming vehicles for a specific stop.

        Only includes vehicles that have this stop genuinely ahead of them
        on their current route and direction. No distance-based fallback —
        a vehicle must have the stop in its detected next_stops to appear.
        """
        arrivals = []
        serving_routes = self._stop_to_routes.get(stop_id, set())

        for vid, state in self.current_states.items():
            if not state.route_id or state.route_id not in serving_routes:
                continue
            if route_filter and state.route_id != route_filter:
                continue
            if state.signal_lost:
                continue  # Don't show ghost vehicles in station arrivals

            # Check full next-stops list (all remaining stops on this direction)
            all_next = self._vehicle_all_next_stops.get(vid, [])
            stops_to_target = []
            found = False
            for ns in all_next:
                stops_to_target.append(ns)
                if ns.stop_id == stop_id:
                    found = True
                    break

            if not found:
                continue

            # Calculate ETA to this specific stop along the route
            eta = None
            if stops_to_target:
                calc = self.eta_calculator.calculate(
                    state.lat, state.lon, state.speed, stops_to_target
                )
                if calc:
                    _, eta = calc[-1]  # ETA to the target stop (last in list)

            arrivals.append({
                "vehicle_id": state.id,
                "board_num": state.board_num,
                "route": state.route,
                "route_id": state.route_id,
                "eta_seconds": eta,
            })

        arrivals.sort(key=lambda a: a.get("eta_seconds") or 9999)
        return arrivals[:15]
