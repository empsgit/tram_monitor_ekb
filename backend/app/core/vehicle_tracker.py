"""Main orchestrator: fetches vehicle data, processes through pipeline, publishes updates."""

import asyncio
import datetime
import logging
import math

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

        # stop_id -> set of route_ids that serve it
        self._stop_to_routes: dict[int, set[int]] = {}

        # stop_id -> (lat, lon) for distance calculations
        self._stop_coords: dict[int, tuple[float, float]] = {}

        # stop_id -> direction label
        self._stop_directions: dict[int, str] = {}

        # Current vehicle states (vehicle_id -> VehicleState)
        self.current_states: dict[str, VehicleState] = {}

        # Diagnostics: track unresolved stop IDs per route
        self._diag_unresolved: dict[int, list[int]] = {}  # route_id -> [stop_ids not in points]
        self._diag_total_path_stops: dict[int, int] = {}  # route_id -> total path entries

    async def load_routes_and_stops(self) -> None:
        """Fetch and load routes and stops from ETTU API into matchers."""
        routes = await self.ettu.fetch_routes()
        stops = await self.ettu.fetch_stops()

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

            # Try OSRM for road-snapped geometry, fall back to stop-to-stop lines
            osrm_geom = await self._fetch_osrm_geometry(route.stops)
            if osrm_geom:
                route.points = osrm_geom
            elif not route.points and route.stops:
                route.points = [
                    [s["lat"], s["lon"]]
                    for s in route.stops
                    if s["lat"] != 0 and s["lon"] != 0
                ]

            # Store geometry for API exposure
            if route.points:
                self._route_geometries[route.id] = route.points
                self.route_matcher.load_route(route.id, route.points)

            # Load stops for this route
            route_stops = []
            for s in route.stops:
                if route.points:
                    match = self.route_matcher.match(route.id, s["lat"], s["lon"])
                    if match:
                        total_len = self.route_matcher.get_total_length(route.id)
                        dist = match.progress * total_len
                    else:
                        dist = 0.0
                else:
                    dist = 0.0

                route_stops.append(StopOnRoute(
                    stop_id=s["id"],
                    name=_stop_display_name(s["name"], s.get("direction_label", "")),
                    lat=s["lat"],
                    lon=s["lon"],
                    order=s["order"],
                    direction=s["direction"],
                    distance_along=dist,
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

    async def poll_vehicles(self) -> None:
        """Single poll cycle: fetch positions, process, publish."""
        try:
            raw_vehicles = await self.ettu.fetch_vehicles()
            if not raw_vehicles:
                return

            states = []
            for rv in raw_vehicles:
                state = self._process_vehicle(rv)
                if state:
                    states.append(state)
                    self.current_states[state.id] = state

            # Publish to subscribers
            vehicles_data = [s.model_dump() for s in states]
            await self.broadcaster.publish(vehicles_data)

            # Persist positions asynchronously
            await self._persist_positions(raw_vehicles)

        except Exception:
            logger.exception("Error in vehicle poll cycle")

    def _process_vehicle(self, rv: RawVehicle) -> VehicleState | None:
        """Process a single raw vehicle through the pipeline."""
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

        # Route matching
        match = self.route_matcher.match(route_id, rv.lat, rv.lon, rv.course)
        if not match:
            return state

        state.progress = match.progress
        total_len = self.route_matcher.get_total_length(route_id)
        distance_along = match.progress * total_len

        # Stop detection (up to 5 next stops for richer data)
        detection = self.stop_detector.detect(
            route_id, distance_along, match.direction, max_next=5
        )

        if detection.prev_stop:
            state.prev_stop = StopInfo(
                id=detection.prev_stop.stop_id,
                name=detection.prev_stop.name,
            )

        # ETA calculation
        if detection.next_stops:
            etas = self.eta_calculator.calculate(
                distance_along, rv.speed, detection.next_stops
            )
            state.next_stops = [
                NextStopInfo(
                    id=stop.stop_id,
                    name=stop.name,
                    eta_seconds=eta_s,
                )
                for stop, eta_s in etas
            ]

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

        # Phase 1b: persist named stops
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
                await session.commit()
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
                    "distance_along_m": round(s.distance_along, 1),
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
        """Get upcoming vehicles for a specific stop."""
        # Try pipeline-based approach first (vehicles with this stop in next_stops)
        arrivals = []
        seen_vehicles: set[str] = set()
        for vid, state in self.current_states.items():
            if route_filter and state.route_id != route_filter:
                continue
            for ns in state.next_stops:
                if ns.id == stop_id:
                    arrivals.append({
                        "vehicle_id": state.id,
                        "board_num": state.board_num,
                        "route": state.route,
                        "route_id": state.route_id,
                        "eta_seconds": ns.eta_seconds,
                    })
                    seen_vehicles.add(vid)
                    break

        # Fallback: find vehicles on routes serving this stop, estimate by distance
        serving_routes = self._stop_to_routes.get(stop_id, set())
        stop_loc = self._stop_coords.get(stop_id)
        if serving_routes and stop_loc:
            for vid, state in self.current_states.items():
                if vid in seen_vehicles:
                    continue
                if state.route_id not in serving_routes:
                    continue
                if route_filter and state.route_id != route_filter:
                    continue

                dist_m = _haversine(state.lat, state.lon, stop_loc[0], stop_loc[1])
                speed = max(state.speed, 5.0)
                eta_s = int(dist_m / (speed / 3.6))
                if eta_s > 3600:
                    continue

                arrivals.append({
                    "vehicle_id": state.id,
                    "board_num": state.board_num,
                    "route": state.route,
                    "route_id": state.route_id,
                    "eta_seconds": eta_s,
                })

        arrivals.sort(key=lambda a: a.get("eta_seconds") or 9999)
        return arrivals[:15]
