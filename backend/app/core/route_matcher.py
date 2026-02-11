"""Snap GPS positions to route LineStrings using Shapely linear referencing."""

import logging
import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point

logger = logging.getLogger(__name__)

# Max distance (meters) from route to consider a valid snap
MAX_SNAP_DISTANCE_M = 300

# Approximate meters per degree at Yekaterinburg latitude (~56.8)
LAT_M_PER_DEG = 111_320.0
LON_M_PER_DEG = 111_320.0 * math.cos(math.radians(56.84))


@dataclass
class MatchResult:
    progress: float  # 0.0–1.0 along the route
    distance_m: float  # perpendicular distance from route in meters
    direction: int  # 0=forward, 1=reverse (based on heading)


class RouteMatcher:
    """Matches GPS coordinates to pre-loaded route geometries."""

    def __init__(self) -> None:
        # route_id -> (LineString in degrees, total_length_m)
        self._routes: dict[int, tuple[LineString, float]] = {}

    def load_route(self, route_id: int, coords: list[list[float]]) -> None:
        """Load route geometry. coords = [[lat, lon], ...]"""
        if len(coords) < 2:
            return
        # Shapely uses (x, y) = (lon, lat)
        line = LineString([(c[1], c[0]) for c in coords])
        # Approximate total length in meters
        total_m = self._line_length_meters(coords)
        self._routes[route_id] = (line, total_m)

    def match(self, route_id: int, lat: float, lon: float, course: float = 0.0) -> MatchResult | None:
        """Snap a point to a route, returning progress and distance."""
        if route_id not in self._routes:
            return None

        line, total_m = self._routes[route_id]
        point = Point(lon, lat)

        # Project point onto line (normalized 0.0–1.0)
        progress = line.project(point, normalized=True)

        # Distance from point to line (in degrees, convert to meters)
        dist_deg = line.distance(point)
        dist_m = dist_deg * LON_M_PER_DEG  # rough conversion

        if dist_m > MAX_SNAP_DISTANCE_M:
            return None

        # Determine direction from course heading
        direction = self._infer_direction(line, progress, course)

        return MatchResult(progress=progress, distance_m=dist_m, direction=direction)

    def get_distance_at_progress(self, route_id: int, progress: float) -> float:
        """Get distance in meters along route at given progress."""
        if route_id not in self._routes:
            return 0.0
        _, total_m = self._routes[route_id]
        return progress * total_m

    def get_total_length(self, route_id: int) -> float:
        if route_id not in self._routes:
            return 0.0
        return self._routes[route_id][1]

    def interpolate_progress(self, route_id: int, progress: float) -> tuple[float, float] | None:
        """Return (lat, lon) at given progress (0.0–1.0) along the route."""
        if route_id not in self._routes:
            return None
        line, _ = self._routes[route_id]
        pt = line.interpolate(max(0.0, min(1.0, progress)), normalized=True)
        return (pt.y, pt.x)  # (lat, lon)

    def _infer_direction(self, line: LineString, progress: float, course: float) -> int:
        """Compare vehicle heading with route bearing to infer direction."""
        if progress < 0.01 or progress > 0.99:
            return 0
        # Get route bearing at this progress point
        p1 = line.interpolate(max(0, progress - 0.005), normalized=True)
        p2 = line.interpolate(min(1, progress + 0.005), normalized=True)
        route_bearing = math.degrees(math.atan2(p2.x - p1.x, p2.y - p1.y)) % 360

        # Compare with vehicle course
        diff = abs(course - route_bearing) % 360
        if diff > 180:
            diff = 360 - diff

        # If course is roughly opposite to route direction, vehicle goes reverse
        return 1 if diff > 90 else 0

    @staticmethod
    def _line_length_meters(coords: list[list[float]]) -> float:
        """Approximate length of a polyline in meters."""
        total = 0.0
        for i in range(1, len(coords)):
            dlat = (coords[i][0] - coords[i - 1][0]) * LAT_M_PER_DEG
            dlon = (coords[i][1] - coords[i - 1][1]) * LON_M_PER_DEG
            total += math.sqrt(dlat * dlat + dlon * dlon)
        return total
