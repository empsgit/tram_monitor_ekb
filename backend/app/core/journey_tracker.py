"""Observation-time journey state. Independent of display geometry and API course."""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
from uuid import uuid4

from app.core.stop_detector import StopOnRoute, _gps_dist_m


def route_version(directions: dict[int, list[StopOnRoute]]) -> str:
    payload = [(d, [(s.stop_id, s.order, s.lat, s.lon) for s in stops])
               for d, stops in sorted(directions.items())]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]


@dataclass
class Journey:
    route_id: int
    version: str
    trip_id: str = field(default_factory=lambda: str(uuid4()))
    direction: int | None = None
    passed: int = -1
    observed_at: datetime | None = None
    lat: float = 0
    lon: float = 0
    speed: float = 0
    candidates: dict[int, int] = field(default_factory=dict)
    visit: dict | None = None
    departure: dict | None = None
    quality: str = "learning"


@dataclass
class Observation:
    quality: str
    accepted: bool = False
    visits: list[dict] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)


class JourneyTracker:
    ARRIVAL_RADIUS = 60.0
    DEPARTURE_RADIUS = 90.0
    EVIDENCE_RADIUS = 180.0
    MAX_GAP_SECONDS = 120
    FRESH_SECONDS = 120

    def __init__(self):
        self.states: dict[str, Journey] = {}

    def observe(self, vehicle_id: str, route_id: int, version: str,
                directions: dict[int, list[StopOnRoute]], lat: float, lon: float,
                speed: float, observed_at: datetime | None, received_at: datetime) -> Observation:
        if not all(math.isfinite(v) for v in (lat, lon, speed)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return Observation("invalid_position")
        if observed_at is None:
            return Observation("missing_time")
        age = (received_at - observed_at).total_seconds()
        if age < -30:
            return Observation("future_time")
        if age > self.FRESH_SECONDS:
            return Observation("stale")
        state = self.states.get(vehicle_id)
        if state and state.observed_at and observed_at <= state.observed_at:
            return Observation("duplicate" if observed_at == state.observed_at else "out_of_order")
        if not state or (state.route_id, state.version) != (route_id, version):
            state = Journey(route_id, version)
            self.states[vehicle_id] = state
        if state.observed_at:
            dt = (observed_at - state.observed_at).total_seconds()
            distance = _gps_dist_m(state.lat, state.lon, lat, lon)
            if distance > 100 + 35 * dt:
                # Break timing continuity even if a later fix returns to the route.
                state.departure = None
                if state.visit:
                    state.visit["quality"] = "gap"
                return Observation("gps_jump")
            if dt > self.MAX_GAP_SECONDS:
                state.departure = None
                if state.visit:
                    state.visit["quality"] = "gap"
                if not (state.speed == 0 and distance < self.ARRIVAL_RADIUS):
                    # A turnaround may have happened during a gap. A tram still
                    # standing at the same fix, however, keeps its direction.
                    state = Journey(route_id, version)
                    self.states[vehicle_id] = state
        state.observed_at, state.lat, state.lon, state.speed = observed_at, lat, lon, speed
        result = Observation("accepted", accepted=True)

        if state.direction is None:
            advancing: list[tuple[int, int]] = []
            for direction, stops in directions.items():
                if not stops:
                    continue
                distances = [_gps_dist_m(lat, lon, s.lat, s.lon) for s in stops]
                index = min(range(len(stops)), key=distances.__getitem__)
                if distances[index] > self.EVIDENCE_RADIUS:
                    continue
                previous = state.candidates.get(direction)
                if previous is not None and index > previous:
                    if _gps_dist_m(stops[previous].lat, stops[previous].lon, stops[index].lat, stops[index].lon) > 100:
                        advancing.append((direction, index))
                state.candidates[direction] = index
            if len(advancing) == 1:
                state.direction, index = advancing[0]
                state.passed = index - 1
            else:
                state.quality = "learning"
                return result

        state.quality = "confirmed"
        stops = directions[state.direction]
        if state.visit:
            index = state.visit["stop_index"]
            stop = stops[index]
            distance = _gps_dist_m(lat, lon, stop.lat, stop.lon)
            state.visit["min_distance"] = min(state.visit["min_distance"], distance)
            if distance >= self.DEPARTURE_RADIUS and distance - state.visit["min_distance"] >= 40 and speed > 0:
                if index == len(stops) - 1:
                    alternatives = [d for d, seq in directions.items()
                                    if d != state.direction and len(seq) > 1
                                    and _gps_dist_m(stop.lat, stop.lon, seq[0].lat, seq[0].lon) < 250
                                    and self._forward(seq[0], seq[1], lat, lon) > 40]
                    if len(alternatives) == 1:
                        self._depart(state, observed_at, result)
                        state = Journey(route_id, version, direction=alternatives[0],
                                        passed=0, observed_at=observed_at, lat=lat, lon=lon,
                                        speed=speed, quality="confirmed")
                        self.states[vehicle_id] = state
                        # Do not invent an outbound arrival or include terminal layover.
                    else:
                        return result
                elif self._forward(stop, stops[index + 1], lat, lon) > 40:
                    self._depart(state, observed_at, result)
        stops = directions[state.direction]

        # Only forward stops are considered. Repeated stop IDs retain their sequence index.
        available = range(state.passed + 1, min(len(stops), state.passed + 4))
        nearby = [i for i in available if _gps_dist_m(lat, lon, stops[i].lat, stops[i].lon) <= self.ARRIVAL_RADIUS]
        if nearby:
            index = min(nearby)
            if state.visit is None or state.visit["stop_index"] != index:
                # A missed departure invalidates the incomplete visit, not the next one.
                if state.visit is not None:
                    state.visit = None
                    state.departure = None
                state.passed = max(state.passed, index - 1)
                visit = dict(trip_id=state.trip_id, vehicle_id=vehicle_id, route_id=route_id,
                             route_version=version, direction=state.direction, stop_index=index,
                             stop_id=stops[index].stop_id, arrived_at=observed_at,
                             departed_at=None, quality="valid")
                result.visits.append(visit.copy())
                departure = state.departure
                if departure and departure["stop_index"] + 1 == index and departure["quality"] == "valid":
                    seconds = (observed_at - departure["departed_at"]).total_seconds()
                    distance = _gps_dist_m(stops[index - 1].lat, stops[index - 1].lon, stops[index].lat, stops[index].lon)
                    if 10 <= seconds <= 1800 and distance / seconds <= 35:
                        result.segments.append(dict(
                            trip_id=state.trip_id, vehicle_id=vehicle_id, route_id=route_id,
                            route_version=version, direction=state.direction,
                            from_index=index - 1, to_index=index,
                            from_stop_id=stops[index - 1].stop_id, to_stop_id=stops[index].stop_id,
                            departed_at=departure["departed_at"], arrived_at=observed_at,
                            seconds=seconds))
                state.departure = None
                state.visit = {**visit, "min_distance": _gps_dist_m(lat, lon, stops[index].lat, stops[index].lon)}

        # Sparse observations may miss the stop radius. Advance the arrival list, but
        # never synthesize travel-time samples for these unobserved stop visits.
        if state.visit is None:
            for index in range(state.passed + 1, min(len(stops) - 1, state.passed + 4)):
                a, b = stops[index], stops[index + 1]
                length = _gps_dist_m(a.lat, a.lon, b.lat, b.lon)
                forward = self._forward(a, b, lat, lon)
                lateral_sq = max(0, _gps_dist_m(a.lat, a.lon, lat, lon) ** 2 - forward ** 2)
                if 90 < forward < length and lateral_sq < 120 ** 2:
                    state.passed = index
        return result

    @staticmethod
    def _forward(a: StopOnRoute, b: StopOnRoute, lat: float, lon: float) -> float:
        scale = math.cos(math.radians(a.lat))
        dx, dy = (b.lon - a.lon) * scale, b.lat - a.lat
        length = math.hypot(dx, dy)
        return (((lon - a.lon) * scale * dx + (lat - a.lat) * dy) / length * 111320) if length else 0

    @staticmethod
    def _depart(state: Journey, observed_at: datetime, result: Observation) -> None:
        visit = {k: v for k, v in state.visit.items() if k != "min_distance"}
        visit["departed_at"] = observed_at
        result.visits.append(visit)
        state.passed = visit["stop_index"]
        state.departure = visit
        state.visit = None

    def next_stops(self, vehicle_id: str, directions: dict[int, list[StopOnRoute]]) -> list[StopOnRoute]:
        state = self.states.get(vehicle_id)
        if not state or state.direction not in directions:
            return []
        return directions[state.direction][state.passed + 1:]
