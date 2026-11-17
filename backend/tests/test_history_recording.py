"""Source-time journey and persistence regressions."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

from app.config import settings
from app.core.ettu_client import RawVehicle
from app.core.history import History
from app.core.journey_tracker import JourneyTracker, route_version
from app.core.stop_detector import StopOnRoute
from app.core.vehicle_tracker import VehicleTracker

START = datetime(2026, 11, 17, 8, tzinfo=timezone.utc)


def catalog():
    return {0: [StopOnRoute(i, str(i), 56.84 + i * .005, 60.6, i, 0) for i in range(5)],
            1: [StopOnRoute(10 + i, str(i), 56.84 + (4 - i) * .005, 60.6, i, 1) for i in range(5)]}


def observe(tracker, pos, seconds, speed=20, received=None, version=None):
    stops = catalog()
    at = START + timedelta(seconds=seconds)
    return tracker.observe("tram", 1, version or route_version(stops), stops,
                           56.84 + pos * .005, 60.6, speed, at, received or at)


def confirmed():
    tracker = JourneyTracker()
    observe(tracker, 0, 0)
    observe(tracker, 1, 60)
    return tracker


def test_sequence_confirms_direction_not_first_fix():
    t = JourneyTracker()
    observe(t, 0, 0)
    assert t.states["tram"].direction is None
    observe(t, 1, 60)
    assert t.states["tram"].direction == 0
    assert t.next_stops("tram", catalog())[0].stop_id == 1


def test_reverse_sequence_and_stationary_keep_direction():
    t = JourneyTracker()
    observe(t, 4, 0)
    observe(t, 3, 60)
    observe(t, 3, 70, speed=0)
    assert t.states["tram"].direction == 1


def test_segment_uses_departure_to_arrival_not_repeated_atime():
    t = confirmed()
    assert observe(t, 1, 60, received=START + timedelta(seconds=70)).quality == "duplicate"
    depart = observe(t, 1.2, 80)
    assert depart.visits[0]["departed_at"] == START + timedelta(seconds=80)
    arrival = observe(t, 2, 140)
    assert len(arrival.segments) == 1
    assert arrival.segments[0]["seconds"] == 60
    assert arrival.segments[0]["from_index"] == 1


def test_gps_jump_does_not_move_state_or_record_segment():
    t = confirmed()
    observe(t, 1.2, 80)
    assert observe(t, 15, 90).quality == "gps_jump"
    assert t.states["tram"].observed_at == START + timedelta(seconds=80)
    assert not observe(t, 2, 140).segments


def test_out_of_order_stale_future_and_invalid_are_rejected():
    t = confirmed()
    for result in (observe(t, 2, 50), observe(t, 2, 200, received=START + timedelta(seconds=500)),
                   observe(t, 2, 200, received=START), observe(t, float("nan"), 70)):
        assert not result.accepted
    assert t.states["tram"].passed == 0


def test_gap_and_catalog_change_require_reconfirmation():
    t = confirmed()
    assert not observe(t, 2, 300).segments
    assert t.states["tram"].direction is None
    observe(t, 3, 360)
    assert t.states["tram"].direction == 0
    observe(t, 3, 370, version="changed")
    assert t.states["tram"].direction is None


def test_slow_terminal_departure_uses_minimum_distance():
    t = confirmed()
    observe(t, 2, 120)
    observe(t, 3, 180)
    observe(t, 4, 240)
    for n in range(1, 6):
        observe(t, 4 - .04 * n, 240 + n * 10, speed=5)
    assert t.states["tram"].direction == 1
    assert t.states["tram"].passed == 0
    assert t.states["tram"].departure is None


def test_missed_stop_does_not_create_multi_stop_segment():
    t = confirmed()
    observe(t, 1.2, 80)
    assert not observe(t, 3, 170).segments
    assert t.states["tram"].passed == 2


def test_stationary_gap_preserves_direction_but_not_timing_sample():
    t = confirmed()
    observe(t, 1, 70, speed=0)
    observe(t, 1, 300, speed=0)
    assert t.states["tram"].direction == 0
    observe(t, 1.2, 320)
    assert not observe(t, 2, 380).segments


def test_route_change_never_records_cross_route_time():
    t = confirmed()
    observe(t, 1.2, 80)
    result = t.observe("tram", 2, "different", catalog(), 56.85, 60.6, 20,
                       START + timedelta(seconds=140), START + timedelta(seconds=140))
    assert not result.segments
    assert t.states["tram"].route_id == 2
    assert t.states["tram"].direction is None


def test_pending_history_preserved_on_failure_and_local_weekday():
    history = History(Mock(side_effect=RuntimeError("offline")))
    t = confirmed()
    observe(t, 1.2, 80)
    rows = observe(t, 2, 140)
    rows.segments[0]["departed_at"] = datetime(2026, 11, 15, 20, tzinfo=timezone.utc)
    history.enqueue(None, rows.visits, rows.segments)
    assert next(iter(history.segments.values()))["weekday"] == 0
    try:
        asyncio.run(history.flush())
    except RuntimeError:
        pass
    assert len(history.segments) == 1
    assert len(history.visits) == 1


def test_disabled_history_keeps_live_updates_without_writes(monkeypatch):
    monkeypatch.setattr(settings, "history_recording_enabled", False)
    subject = VehicleTracker(AsyncMock(), AsyncMock(), AsyncMock())
    subject.ettu.fetch_vehicles.return_value = [RawVehicle(
        dev_id="test", board_num="1", route_num="1", lat=56.84, lon=60.60,
        speed=10, course=0, on_route=True, layer=0, atime_utc=datetime.now(timezone.utc))]
    subject.history.flush = AsyncMock()
    asyncio.run(subject.poll_vehicles())
    subject.broadcaster.publish.assert_awaited_once()
    subject.history.flush.assert_not_awaited()
    assert not subject.history.positions


def test_arrival_route_filter_before_limit_and_stationary_no_countdown():
    subject = VehicleTracker(AsyncMock(), AsyncMock(), AsyncMock())
    subject._stop_arrivals_snapshot[1] = [dict(vehicle_id=str(i), board_num=str(i), route=str(i),
        route_id=i, eta_seconds=100, eta_min_seconds=80, eta_max_seconds=120, eta_source="fallback",
        signal_lost=False, source_age=0, countdown_budget=0) for i in range(40)]
    import time
    subject._stop_arrivals_updated_monotonic = time.monotonic() - 20
    arrivals = subject.get_vehicles_for_stop(1, route_filter=39)
    assert len(arrivals) == 1
    assert arrivals[0]["eta_seconds"] == 100
    subject._stop_arrivals_updated_monotonic -= 120
    assert subject.get_vehicles_for_stop(1, 39)[0]["eta_seconds"] is None


def test_history_disabled_by_default():
    assert type(settings).model_fields["history_recording_enabled"].default is False
