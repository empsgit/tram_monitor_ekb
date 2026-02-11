"""Tests for EtaCalculator."""

from app.core.eta_calculator import EtaCalculator
from app.core.stop_detector import StopOnRoute


def test_basic_eta():
    calc = EtaCalculator()
    stops = [
        StopOnRoute(stop_id=1, name="Next", lat=0, lon=0, order=0, direction=0, distance_along=1000),
    ]
    results = calc.calculate(current_distance_m=500, speed_kmh=36, next_stops=stops)
    assert len(results) == 1
    stop, eta = results[0]
    assert stop.stop_id == 1
    # 500m at 36km/h = 500m at 10m/s = 50s
    assert eta is not None
    assert 45 <= eta <= 55


def test_zero_speed_uses_minimum():
    calc = EtaCalculator()
    stops = [
        StopOnRoute(stop_id=1, name="Next", lat=0, lon=0, order=0, direction=0, distance_along=100),
    ]
    # Zero speed should use MIN_SPEED_KMH (5 km/h = 1.39 m/s)
    results = calc.calculate(current_distance_m=0, speed_kmh=0, next_stops=stops)
    assert len(results) == 1
    _, eta = results[0]
    assert eta is not None
    assert eta > 0
