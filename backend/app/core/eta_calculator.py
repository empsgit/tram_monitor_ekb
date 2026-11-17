"""Calculate ETA to upcoming stops based on speed and GPS distance."""

import logging
import math
from datetime import timedelta

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

    def predict(self, journey, stops, history) -> list[tuple[StopOnRoute, dict]]:
        """Use versioned run/dwell samples; speed is not extrapolated over a whole trip.

        The summed interquartile bounds are indicative, not a calibrated confidence interval.
        """
        if journey.direction is None or journey.observed_at is None:
            return []
        low = middle = high = 0.0
        historical = fallback = 0
        results = []
        start = journey.passed + 1
        for index in range(start, len(stops)):
            stop = stops[index]
            at = journey.observed_at + timedelta(seconds=middle)
            distance = _gps_dist_m(journey.lat, journey.lon, stop.lat, stop.lon)
            at_stop = index == start and journey.visit is not None and journey.visit["stop_index"] == index
            if not at_stop:
                previous = stops[index - 1] if index else None
                length = _gps_dist_m(previous.lat, previous.lon, stop.lat, stop.lon) if previous else distance
                run = history.lookup("run", journey.route_id, journey.version, journey.direction, index - 1, at) if previous else None
                fraction = min(1.0, distance / max(1, length)) if index == start else 1.0
                if run:
                    a, b, c, _ = run
                    historical += 1
                else:
                    # Conservative network-wide fallback until enough valid samples exist.
                    b = max(15, length / 5.0)
                    a, c = b * 0.65, b * 1.6
                    fallback += 1
                low += a * fraction
                middle += b * fraction
                high += c * fraction
            results.append((stop, {
                "eta_seconds": round(middle) if middle <= 5400 else None,
                "eta_min_seconds": round(low) if middle <= 5400 else None,
                "eta_max_seconds": round(high) if middle <= 5400 else None,
                "eta_source": "mixed" if historical and fallback else "history" if historical else "fallback",
            }))
            dwell = history.lookup("dwell", journey.route_id, journey.version, journey.direction, index, at)
            a, b, c = dwell[:3] if dwell else (10, 20, 45)
            elapsed = (journey.observed_at - journey.visit["arrived_at"]).total_seconds() if at_stop else 0
            low += max(0, a - elapsed)
            middle += max(0, b - elapsed)
            high += max(0, c - elapsed)
            if c > elapsed:
                historical += int(dwell is not None)
                fallback += int(dwell is None)
        return results

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
