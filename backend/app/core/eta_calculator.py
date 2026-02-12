"""Calculate ETA to upcoming stops based on speed and GPS distance."""

import logging
import math

from app.core.stop_detector import StopOnRoute

logger = logging.getLogger(__name__)

# Minimum speed to use for ETA (km/h) - prevents division by zero / extreme ETAs
MIN_SPEED_KMH = 5.0
# Maximum reasonable ETA (seconds)
MAX_ETA_SECONDS = 3600

# Approximate meters per degree at Yekaterinburg latitude (~56.8)
_LAT_M = 111_320.0
_LON_M = 111_320.0 * math.cos(math.radians(56.84))


def _gps_dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * _LAT_M
    dlon = (lon2 - lon1) * _LON_M
    return math.sqrt(dlat * dlat + dlon * dlon)


class EtaCalculator:
    """Speed-based ETA using GPS distances along the stop sequence."""

    def calculate(
        self,
        vehicle_lat: float,
        vehicle_lon: float,
        speed_kmh: float,
        next_stops: list[StopOnRoute],
    ) -> list[tuple[StopOnRoute, int | None]]:
        """Calculate ETA in seconds to each next stop.

        Uses GPS distance from the vehicle to the first next stop, then
        cumulative inter-stop distances for subsequent stops.
        """
        if not next_stops:
            return []

        effective_speed = max(speed_kmh, MIN_SPEED_KMH)
        speed_ms = effective_speed / 3.6  # km/h -> m/s

        # Distance from vehicle to first upcoming stop
        dist_to_first = _gps_dist_m(
            vehicle_lat, vehicle_lon,
            next_stops[0].lat, next_stops[0].lon,
        )
        first_cum = next_stops[0].cumulative_distance_m

        results = []
        for stop in next_stops:
            # Distance = (vehicle→first_stop) + (first_stop→this_stop along route)
            remaining_m = dist_to_first + (stop.cumulative_distance_m - first_cum)
            if remaining_m < 0:
                remaining_m = 0

            eta_s = int(remaining_m / speed_ms)
            if eta_s > MAX_ETA_SECONDS:
                eta_s = None  # Too far out to be reliable
            results.append((stop, eta_s))

        return results
