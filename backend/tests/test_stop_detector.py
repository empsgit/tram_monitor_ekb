"""Tests for StopDetector (GPS-based detection)."""

from app.core.stop_detector import StopDetector, StopOnRoute


def make_stops() -> list[StopOnRoute]:
    """Stops along a north-south line in Ekaterinburg (~56.84°N, 60.6°E)."""
    return [
        StopOnRoute(stop_id=1, name="Stop A", lat=56.840, lon=60.600, order=0, direction=0),
        StopOnRoute(stop_id=2, name="Stop B", lat=56.844, lon=60.600, order=1, direction=0),
        StopOnRoute(stop_id=3, name="Stop C", lat=56.848, lon=60.600, order=2, direction=0),
        StopOnRoute(stop_id=4, name="Stop D", lat=56.852, lon=60.600, order=3, direction=0),
    ]


def test_detect_between_stops():
    detector = StopDetector()
    detector.load_route_stops(1, make_stops())

    # Vehicle between Stop B and Stop C
    result = detector.detect(1, lat=56.846, lon=60.600)
    assert result.prev_stop is not None
    assert result.prev_stop.stop_id == 2  # Stop B
    assert len(result.next_stops) > 0
    assert result.next_stops[0].stop_id == 3  # Stop C


def test_detect_at_start():
    detector = StopDetector()
    detector.load_route_stops(1, make_stops())

    # Vehicle near Start A
    result = detector.detect(1, lat=56.840, lon=60.600)
    assert result.prev_stop is not None
    assert result.prev_stop.stop_id == 1
    assert len(result.next_stops) > 0


def test_detect_unknown_route():
    detector = StopDetector()
    result = detector.detect(999, lat=56.840, lon=60.600)
    assert result.prev_stop is None
    assert result.next_stops == []


def test_cumulative_distance_computed():
    detector = StopDetector()
    stops = make_stops()
    detector.load_route_stops(1, stops)
    all_stops = detector.get_all_stops(1)
    # Cumulative distances should be increasing
    dists = sorted(s.cumulative_distance_m for s in all_stops)
    assert dists[0] == 0.0
    assert all(dists[i] <= dists[i + 1] for i in range(len(dists) - 1))
    assert dists[-1] > 0
