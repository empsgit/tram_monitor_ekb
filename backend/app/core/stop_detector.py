"""Detect previous and next stops based on vehicle progress along route."""

import bisect
import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Approximate meters per degree at Yekaterinburg latitude (~56.8)
_LAT_M = 111_320.0
_LON_M = 111_320.0 * math.cos(math.radians(56.84))


@dataclass
class StopOnRoute:
    stop_id: int
    name: str
    lat: float
    lon: float
    order: int
    direction: int
    distance_along: float  # meters from start of route


@dataclass
class DetectionResult:
    prev_stop: StopOnRoute | None
    next_stops: list[StopOnRoute]  # up to N upcoming stops


class StopDetector:
    """Binary-searches sorted stop lists to find prev/next stops."""

    def __init__(self) -> None:
        # route_id -> {direction: [StopOnRoute sorted by distance_along]}
        self._stops: dict[int, dict[int, list[StopOnRoute]]] = {}
        self._distances: dict[int, dict[int, list[float]]] = {}  # for bisect

    def load_route_stops(self, route_id: int, stops: list[StopOnRoute]) -> None:
        """Load stops for a route, organized by direction."""
        by_dir: dict[int, list[StopOnRoute]] = {}
        for s in stops:
            by_dir.setdefault(s.direction, []).append(s)

        for d in by_dir:
            by_dir[d].sort(key=lambda x: x.distance_along)

        self._stops[route_id] = by_dir
        self._distances[route_id] = {
            d: [s.distance_along for s in sl] for d, sl in by_dir.items()
        }

    def detect(
        self, route_id: int, distance_along: float, direction: int = 0, max_next: int = 3
    ) -> DetectionResult:
        """Find previous and next stops for a vehicle at given distance along route."""
        if route_id not in self._stops:
            return DetectionResult(prev_stop=None, next_stops=[])

        dir_stops = self._stops[route_id].get(direction)
        dir_dists = self._distances[route_id].get(direction)

        if not dir_stops or not dir_dists:
            # Fall back to direction 0
            dir_stops = self._stops[route_id].get(0, [])
            dir_dists = self._distances[route_id].get(0, [])
            if not dir_stops:
                return DetectionResult(prev_stop=None, next_stops=[])

        # Binary search for position
        idx = bisect.bisect_right(dir_dists, distance_along)

        prev_stop = dir_stops[idx - 1] if idx > 0 else None
        next_stops = dir_stops[idx: idx + max_next]

        return DetectionResult(prev_stop=prev_stop, next_stops=next_stops)

    def detect_by_position(
        self, route_id: int, lat: float, lon: float, max_next: int = 3
    ) -> DetectionResult:
        """Fallback: find nearest stop by GPS distance when route matching fails."""
        if route_id not in self._stops:
            return DetectionResult(prev_stop=None, next_stops=[])

        # Try both directions, pick the one with closest stop
        best_dir = None
        best_idx = 0
        best_dist = float("inf")

        for d, stops in self._stops[route_id].items():
            for i, s in enumerate(stops):
                dlat = (s.lat - lat) * _LAT_M
                dlon = (s.lon - lon) * _LON_M
                dist = dlat * dlat + dlon * dlon  # squared, no need for sqrt
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i
                    best_dir = d

        if best_dir is None:
            return DetectionResult(prev_stop=None, next_stops=[])

        dir_stops = self._stops[route_id][best_dir]
        prev_stop = dir_stops[best_idx] if best_idx >= 0 else None
        next_stops = dir_stops[best_idx + 1: best_idx + 1 + max_next]

        return DetectionResult(prev_stop=prev_stop, next_stops=next_stops)

    def get_all_stops(self, route_id: int) -> list[StopOnRoute]:
        """Get all stops for a route across all directions."""
        if route_id not in self._stops:
            return []
        result = []
        for stops in self._stops[route_id].values():
            result.extend(stops)
        return result
