"""Tests for RouteMatcher."""

from app.core.route_matcher import RouteMatcher


def test_basic_matching():
    """Test that a point on a route line returns valid progress."""
    matcher = RouteMatcher()

    # Simple straight route along a latitude line in Yekaterinburg
    coords = [
        [56.8389, 60.5900],
        [56.8389, 60.6000],
        [56.8389, 60.6100],
    ]
    matcher.load_route(1, coords)

    # Point exactly in the middle
    result = matcher.match(1, 56.8389, 60.6000)
    assert result is not None
    assert 0.4 < result.progress < 0.6
    assert result.distance_m < 10  # Should be very close to route


def test_no_match_far_from_route():
    """Test that a point far from route returns None."""
    matcher = RouteMatcher()
    coords = [
        [56.8389, 60.5900],
        [56.8389, 60.6100],
    ]
    matcher.load_route(1, coords)

    # Point very far from route
    result = matcher.match(1, 57.0, 61.0)
    assert result is None


def test_unknown_route():
    """Test matching against an unknown route returns None."""
    matcher = RouteMatcher()
    result = matcher.match(999, 56.8389, 60.6000)
    assert result is None


def test_line_length():
    """Test approximate line length calculation."""
    coords = [
        [56.8389, 60.5900],
        [56.8389, 60.6000],
    ]
    length = RouteMatcher._line_length_meters(coords)
    # ~0.01 degrees of longitude at ~56.8 lat ≈ 610m
    assert 500 < length < 750
