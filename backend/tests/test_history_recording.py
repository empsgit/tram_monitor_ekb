"""Regression coverage for disabled collection and passage timestamps."""

import asyncio
import datetime
from unittest.mock import AsyncMock

from app.config import settings
from app.core.ettu_client import RawVehicle
from app.core.vehicle_tracker import VehicleTracker
from app.schemas.vehicle import StopInfo, VehicleState


START = datetime.datetime(2026, 11, 17, 8, tzinfo=datetime.timezone.utc)


def tracker():
    return VehicleTracker(AsyncMock(), AsyncMock(), AsyncMock())


def state(stop_id=1, route_id=1):
    return VehicleState(
        id="test", board_num="1", route="1", route_id=route_id,
        lat=56.84, lon=60.60, speed=10, course=0,
        prev_stop=StopInfo(id=stop_id, name="Test stop"),
    )


def test_repeated_stop_preserves_passage_time():
    subject = tracker()
    subject._record_stop_passage(state(), START)
    subject._record_stop_passage(state(), START + datetime.timedelta(seconds=60))
    subject._record_stop_passage(state(2), START + datetime.timedelta(seconds=71))
    assert subject._travel_time_batch[0]["seconds"] == 71


def test_out_of_order_observation_does_not_change_passage():
    subject = tracker()
    subject._record_stop_passage(state(), START)
    subject._record_stop_passage(state(2), START - datetime.timedelta(seconds=30))
    assert subject._last_stop_passage["test"]["stop_id"] == 1
    assert subject._travel_time_batch == []


def test_route_change_does_not_record_cross_route_segment():
    subject = tracker()
    subject._record_stop_passage(state(), START)
    subject._record_stop_passage(state(2, 2), START + datetime.timedelta(seconds=71))
    assert subject._travel_time_batch == []
    assert subject._last_stop_passage["test"]["route_id"] == 2


def test_history_is_disabled_by_default():
    assert type(settings).model_fields["history_recording_enabled"].default is False


def test_disabled_history_keeps_live_updates_without_database_writes(monkeypatch):
    monkeypatch.setattr(settings, "history_recording_enabled", False)
    subject = tracker()
    subject._route_num_to_id["1"] = 1
    subject.ettu.fetch_vehicles.return_value = [RawVehicle(
        dev_id="test", board_num="1", route_num="1", lat=56.84, lon=60.60,
        speed=10, course=0, on_route=True, layer=0, atime_utc=START,
    )]
    subject._persist_positions = AsyncMock()
    subject._persist_travel_times = AsyncMock()
    asyncio.run(subject.poll_vehicles())
    subject.broadcaster.publish.assert_awaited_once()
    subject._persist_positions.assert_not_awaited()
    subject._persist_travel_times.assert_not_awaited()
    assert subject._last_stop_passage == {}
    assert subject._travel_time_batch == []
