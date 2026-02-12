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


def make_bidirectional_stops() -> list[StopOnRoute]:
    """Stops for a route with forward (dir=0) and reverse (dir=1) directions."""
    return [
        # Forward: south to north
        StopOnRoute(stop_id=1, name="Stop A", lat=56.840, lon=60.600, order=0, direction=0),
        StopOnRoute(stop_id=2, name="Stop B", lat=56.844, lon=60.600, order=1, direction=0),
        StopOnRoute(stop_id=3, name="Stop C", lat=56.848, lon=60.600, order=2, direction=0),
        StopOnRoute(stop_id=4, name="Stop D", lat=56.852, lon=60.600, order=3, direction=0),
        # Reverse: north to south (different stop IDs, same locations roughly)
        StopOnRoute(stop_id=14, name="Stop D'", lat=56.852, lon=60.601, order=0, direction=1),
        StopOnRoute(stop_id=13, name="Stop C'", lat=56.848, lon=60.601, order=1, direction=1),
        StopOnRoute(stop_id=12, name="Stop B'", lat=56.844, lon=60.601, order=2, direction=1),
        StopOnRoute(stop_id=11, name="Stop A'", lat=56.840, lon=60.601, order=3, direction=1),
    ]


def test_preferred_direction_sticky():
    """Preferred direction should bias detection toward the previous direction."""
    detector = StopDetector()
    detector.load_route_stops(1, make_bidirectional_stops())

    # Vehicle between B and C, going north (direction 0)
    # Without preference, either direction could win
    # With preferred_direction=0, should stay on direction 0
    result = detector.detect(1, lat=56.846, lon=60.600, preferred_direction=0)
    assert result.direction == 0
    assert result.prev_stop.stop_id == 2  # Stop B (forward)
    assert result.next_stops[0].stop_id == 3  # Stop C (forward)


def test_preferred_direction_overridden_by_course():
    """Strong course evidence should override preferred direction."""
    detector = StopDetector()
    detector.load_route_stops(1, make_bidirectional_stops())

    # Vehicle between B and C, but heading south (180°) — should pick reverse direction
    # Course penalty (500,000) > preferred penalty (200,000), so course wins
    result = detector.detect(
        1, lat=56.846, lon=60.6005,
        course=180, preferred_direction=0,
    )
    assert result.direction == 1  # Reversed due to strong course evidence


def test_max_next_returns_all_remaining():
    """max_next=50 should return all remaining stops on the route."""
    detector = StopDetector()
    detector.load_route_stops(1, make_stops())

    result = detector.detect(1, lat=56.840, lon=60.600, max_next=50)
    assert result.prev_stop.stop_id == 1  # Stop A
    assert len(result.next_stops) == 3  # B, C, D — all remaining
