"""Tests for EtaCalculator (GPS-distance based)."""

from app.core.eta_calculator import EtaCalculator
from app.core.stop_detector import StopOnRoute


def test_basic_eta():
    calc = EtaCalculator()
    # Stop ~500m north of the vehicle (at ~56.84°N, each 0.001° lat ≈ 111m)
    stops = [
        StopOnRoute(
            stop_id=1, name="Next", lat=56.8445, lon=60.600,
            order=0, direction=0, cumulative_distance_m=500,
        ),
    ]
    # Vehicle at (56.840, 60.600), speed 36 km/h = 10 m/s
    results = calc.calculate(
        vehicle_lat=56.840, vehicle_lon=60.600,
        speed_kmh=36, next_stops=stops,
    )
    assert len(results) == 1
    stop, eta = results[0]
    assert stop.stop_id == 1
    # ~500m at 10 m/s = ~50s
    assert eta is not None
    assert 40 <= eta <= 60


def test_zero_speed_uses_minimum():
    calc = EtaCalculator()
    stops = [
        StopOnRoute(
            stop_id=1, name="Next", lat=56.841, lon=60.600,
            order=0, direction=0, cumulative_distance_m=100,
        ),
    ]
    # Zero speed should use MIN_SPEED_KMH (5 km/h = 1.39 m/s)
    results = calc.calculate(
        vehicle_lat=56.840, vehicle_lon=60.600,
        speed_kmh=0, next_stops=stops,
    )
    assert len(results) == 1
    _, eta = results[0]
    assert eta is not None
    assert eta > 0
