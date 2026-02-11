"""Main orchestrator: fetches vehicle data, processes through pipeline, publishes updates."""

import datetime
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.broadcaster import Broadcaster
from app.core.eta_calculator import EtaCalculator
from app.core.ettu_client import EttuClient, RawRoute, RawStop, RawVehicle
from app.core.route_matcher import RouteMatcher
from app.core.stop_detector import StopDetector, StopOnRoute
from app.schemas.vehicle import VehicleState, NextStopInfo, StopInfo

logger = logging.getLogger(__name__)


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

        # Current vehicle states (vehicle_id -> VehicleState)
        self.current_states: dict[str, VehicleState] = {}

    async def load_routes_and_stops(self) -> None:
        """Fetch and load routes and stops from ETTU API into matchers."""
        routes = await self.ettu.fetch_routes()
        stops = await self.ettu.fetch_stops()

        # Build stop lookup
        stop_lookup: dict[int, RawStop] = {s.id: s for s in stops}

        for route in routes:
            self._route_num_to_id[route.number] = route.id
            self._route_id_to_num[route.id] = route.number

            # Resolve stop coordinates from the global stop list
            resolved_stops = []
            for s in route.stops:
                stop_info = stop_lookup.get(s["id"])
                if stop_info:
                    s["name"] = stop_info.name
                    s["lat"] = stop_info.lat
                    s["lon"] = stop_info.lon
                    resolved_stops.append(s)
            route.stops = resolved_stops

            # Build route geometry from ordered stop coordinates
            if not route.points and route.stops:
                route.points = [
                    [s["lat"], s["lon"]]
                    for s in route.stops
                    if s["lat"] != 0 and s["lon"] != 0
                ]

            # Load route geometry
            if route.points:
                self.route_matcher.load_route(route.id, route.points)

            # Load stops for this route
            route_stops = []
            for s in route.stops:
                # Calculate distance along route for each stop
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
                    name=s["name"],
                    lat=s["lat"],
                    lon=s["lon"],
                    order=s["order"],
                    direction=s["direction"],
                    distance_along=dist,
                ))

            self.stop_detector.load_route_stops(route.id, route_stops)

        # Save to database
        await self._persist_routes_stops(routes, stops)
        logger.info(
            "Loaded %d routes and %d stops", len(routes), len(stops)
        )

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

        # Stop detection
        detection = self.stop_detector.detect(
            route_id, distance_along, match.direction
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
        """Upsert routes and stops to database."""
        try:
            async with self.session_factory() as session:
                # Upsert stops
                for s in stops:
                    await session.execute(
                        text("""
                            INSERT INTO stops (id, name, lat, lon)
                            VALUES (:id, :name, :lat, :lon)
                            ON CONFLICT (id) DO UPDATE SET name=:name, lat=:lat, lon=:lon
                        """),
                        {"id": s.id, "name": s.name, "lat": s.lat, "lon": s.lon},
                    )

                # Upsert routes
                for r in routes:
                    await session.execute(
                        text("""
                            INSERT INTO routes (id, number, name)
                            VALUES (:id, :number, :name)
                            ON CONFLICT (id) DO UPDATE SET number=:number, name=:name
                        """),
                        {"id": r.id, "number": r.number, "name": r.name},
                    )

                    # Upsert route_stops
                    for s in r.stops:
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
            logger.exception("Failed to persist routes/stops to database")

    def get_vehicles_for_stop(self, stop_id: int, route_filter: int | None = None) -> list[dict]:
        """Get upcoming vehicles for a specific stop."""
        arrivals = []
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
                    break
        # Sort by ETA
        arrivals.sort(key=lambda a: a.get("eta_seconds") or 9999)
        return arrivals
