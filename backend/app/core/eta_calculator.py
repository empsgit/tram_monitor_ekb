"""Calculate ETA to upcoming stops based on speed and distance."""

import logging

from app.core.stop_detector import StopOnRoute

logger = logging.getLogger(__name__)

# Minimum speed to use for ETA (km/h) - prevents division by zero / extreme ETAs
MIN_SPEED_KMH = 5.0
# Maximum reasonable ETA (seconds)
MAX_ETA_SECONDS = 3600


class EtaCalculator:
    """Simple speed-based ETA calculation (Tier 1).

    Tier 2/3 (historical) will be added once position data accumulates.
    """

    def calculate(
        self,
        current_distance_m: float,
        speed_kmh: float,
        next_stops: list[StopOnRoute],
    ) -> list[tuple[StopOnRoute, int | None]]:
        """Calculate ETA in seconds to each next stop.

        Returns list of (stop, eta_seconds) tuples.
        """
        effective_speed = max(speed_kmh, MIN_SPEED_KMH)
        speed_ms = effective_speed / 3.6  # km/h -> m/s

        results = []
        for stop in next_stops:
            remaining_m = stop.distance_along - current_distance_m
            if remaining_m < 0:
                remaining_m = 0

            eta_s = int(remaining_m / speed_ms)
            if eta_s > MAX_ETA_SECONDS:
                eta_s = None  # Too far out to be reliable
            results.append((stop, eta_s))

        return results
