"""GPS-based stop detection using ETTU route stop ordering.

Finds a vehicle's position on a route by measuring GPS distance to
consecutive stop segments, then returns prev/next stops in route order.
Direction is auto-detected by trying both and picking the best match.
"""

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Approximate meters per degree at Yekaterinburg latitude (~56.8)
_LAT_M = 111_320.0
_LON_M = 111_320.0 * math.cos(math.radians(56.84))


def _gps_dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two GPS points (flat-earth approximation)."""
    dlat = (lat2 - lat1) * _LAT_M
    dlon = (lon2 - lon1) * _LON_M
    return math.sqrt(dlat * dlat + dlon * dlon)


def _point_to_segment_dist_sq(
    plat: float, plon: float,
    alat: float, alon: float,
    blat: float, blon: float,
) -> float:
    """Squared distance (m²) from a point to a line segment."""
    px, py = plon * _LON_M, plat * _LAT_M
    ax, ay = alon * _LON_M, alat * _LAT_M
    bx, by = blon * _LON_M, blat * _LAT_M
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-6:  # degenerate segment
        dx, dy = px - ax, py - ay
        return dx * dx + dy * dy
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    cx, cy = ax + t * dx, ay + t * dy
    dx, dy = px - cx, py - cy
    return dx * dx + dy * dy


def _segment_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Bearing in degrees from point 1 to point 2."""
    dx = (lon2 - lon1) * _LON_M
    dy = (lat2 - lat1) * _LAT_M
    return math.degrees(math.atan2(dx, dy)) % 360


@dataclass
class StopOnRoute:
    stop_id: int
    name: str
    lat: float
    lon: float
    order: int
    direction: int
    cumulative_distance_m: float = 0.0  # filled by load_route_stops


@dataclass
class DetectionResult:
    prev_stop: StopOnRoute | None
    next_stops: list[StopOnRoute]
    direction: int = 0


class StopDetector:
    """Finds prev/next stops by GPS proximity to the ETTU-defined stop sequence."""

    def __init__(self) -> None:
        # route_id -> {direction -> [StopOnRoute sorted by order]}
        self._stops: dict[int, dict[int, list[StopOnRoute]]] = {}

    def load_route_stops(self, route_id: int, stops: list[StopOnRoute]) -> None:
        """Load stops organized by direction, sorted by order, with cumulative distances."""
        by_dir: dict[int, list[StopOnRoute]] = {}
        for s in stops:
            by_dir.setdefault(s.direction, []).append(s)

        for d in by_dir:
            by_dir[d].sort(key=lambda x: x.order)
            # Compute cumulative GPS distances along the stop sequence
            cum = 0.0
            for i, s in enumerate(by_dir[d]):
                if i > 0:
                    prev = by_dir[d][i - 1]
                    cum += _gps_dist_m(prev.lat, prev.lon, s.lat, s.lon)
                s.cumulative_distance_m = cum

        self._stops[route_id] = by_dir
        total_dirs = {d: len(sl) for d, sl in by_dir.items()}
        logger.debug("Route %d: loaded stops by direction: %s", route_id, total_dirs)

    def detect(
        self, route_id: int, lat: float, lon: float,
        course: float | None = None, max_next: int = 5,
        preferred_direction: int | None = None,
    ) -> DetectionResult:
        """Find vehicle's position on route using GPS proximity and stop sequence.

        Tries all directions, picks the best match. Uses course as a tiebreaker
        when segments in different directions are similarly close.
        preferred_direction adds a penalty for switching to maintain consistency.
        """
        if route_id not in self._stops:
            return DetectionResult(prev_stop=None, next_stops=[], direction=0)

        best: DetectionResult | None = None
        best_score = float("inf")

        for d, stops in self._stops[route_id].items():
            if not stops:
                continue

            seg_idx, seg_dist_sq = self._find_nearest_segment(stops, lat, lon)

            # Course-based penalty: if vehicle heading opposes segment direction,
            # penalise this direction so the other one wins.
            score = seg_dist_sq
            if course is not None and seg_idx < len(stops) - 1:
                seg_bear = _segment_bearing(
                    stops[seg_idx].lat, stops[seg_idx].lon,
                    stops[seg_idx + 1].lat, stops[seg_idx + 1].lon,
                )
                diff = abs(course - seg_bear) % 360
                if diff > 180:
                    diff = 360 - diff
                if diff > 90:
                    score += 500_000  # ~707m equivalent penalty (strong)

            # Preferred direction: penalize switching to maintain consistency
            if preferred_direction is not None and d != preferred_direction:
                score += 200_000  # ~447m equivalent penalty

            if score < best_score:
                best_score = score
                prev_stop = stops[seg_idx]
                next_list = stops[seg_idx + 1: seg_idx + 1 + max_next]

                best = DetectionResult(
                    prev_stop=prev_stop,
                    next_stops=next_list,
                    direction=d,
                )

        return best or DetectionResult(prev_stop=None, next_stops=[], direction=0)

    # ------------------------------------------------------------------

    @staticmethod
    def _find_nearest_segment(
        stops: list[StopOnRoute], lat: float, lon: float,
    ) -> tuple[int, float]:
        """Return (index, squared_distance_m) of the nearest segment.

        Index i means the vehicle is between stops[i] and stops[i+1].
        """
        if len(stops) == 0:
            return 0, float("inf")
        if len(stops) == 1:
            dlat = (stops[0].lat - lat) * _LAT_M
            dlon = (stops[0].lon - lon) * _LON_M
            return 0, dlat * dlat + dlon * dlon

        best_idx = 0
        best_dist = float("inf")

        for i in range(len(stops) - 1):
            dist = _point_to_segment_dist_sq(
                lat, lon,
                stops[i].lat, stops[i].lon,
                stops[i + 1].lat, stops[i + 1].lon,
            )
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        return best_idx, best_dist

    def get_all_stops(self, route_id: int) -> list[StopOnRoute]:
        """Get all stops for a route across all directions."""
        if route_id not in self._stops:
            return []
        result = []
        for stops in self._stops[route_id].values():
            result.extend(stops)
        return result
