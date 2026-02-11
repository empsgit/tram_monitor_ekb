"""Tests for StopDetector."""

from app.core.stop_detector import StopDetector, StopOnRoute


def make_stops() -> list[StopOnRoute]:
    return [
        StopOnRoute(stop_id=1, name="Stop A", lat=0, lon=0, order=0, direction=0, distance_along=0),
        StopOnRoute(stop_id=2, name="Stop B", lat=0, lon=0, order=1, direction=0, distance_along=500),
        StopOnRoute(stop_id=3, name="Stop C", lat=0, lon=0, order=2, direction=0, distance_along=1000),
        StopOnRoute(stop_id=4, name="Stop D", lat=0, lon=0, order=3, direction=0, distance_along=1500),
    ]


def test_detect_between_stops():
    detector = StopDetector()
    detector.load_route_stops(1, make_stops())

    result = detector.detect(1, distance_along=750, direction=0)
    assert result.prev_stop is not None
    assert result.prev_stop.stop_id == 2  # Stop B at 500m
    assert len(result.next_stops) > 0
    assert result.next_stops[0].stop_id == 3  # Stop C at 1000m


def test_detect_at_start():
    detector = StopDetector()
    detector.load_route_stops(1, make_stops())

    result = detector.detect(1, distance_along=0, direction=0)
    assert result.prev_stop is None or result.prev_stop.stop_id == 1
    assert len(result.next_stops) > 0


def test_detect_unknown_route():
    detector = StopDetector()
    result = detector.detect(999, distance_along=100, direction=0)
    assert result.prev_stop is None
    assert result.next_stops == []
