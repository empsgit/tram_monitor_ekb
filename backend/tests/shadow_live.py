"""Read-only live smoke test. Does not publish, connect to DB, or flush history."""
import asyncio
from datetime import datetime, timezone
import json

from app.config import settings
from app.core.ettu_client import EttuClient
from app.core.journey_tracker import route_version
from app.core.stop_detector import StopOnRoute
from app.core.vehicle_tracker import VehicleTracker


async def main():
    client = EttuClient()
    tracker = VehicleTracker(client, None, None)
    stops = {s.id: s for s in await client.fetch_stops()}
    for route in await client.fetch_routes():
        tracker._route_num_to_id[route.number] = route.id
        sequence = []
        for entry in route.stops:
            stop = stops.get(entry["id"])
            if stop and stop.name:
                sequence.append(StopOnRoute(stop.id, stop.name, stop.lat, stop.lon,
                                            entry["order"], entry["direction"]))
        tracker.stop_detector.load_route_stops(route.id, sequence)
    tracker._route_versions = {rid: route_version(dirs) for rid, dirs in tracker.stop_detector._stops.items()}
    settings.history_recording_enabled = True  # Queue only; no database object exists here.
    for tick in range(19):
        now = datetime.now(timezone.utc)
        for raw in await client.fetch_vehicles():
            state = tracker._process_vehicle(raw, now=now)
            if state:
                tracker.current_states[state.id] = state
                tracker._vehicle_data_age[state.id] = (now - tracker._last_seen[state.id]).total_seconds()
        tracker._rebuild_stop_arrivals_snapshot()
        print(json.dumps(dict(tick=tick, vehicles=len(tracker.current_states),
            confirmed=sum(j.direction is not None for j in tracker.journeys.states.values()),
            positions=len(tracker.history.positions), visits=len(tracker.history.visits),
            segments=len(tracker.history.segments), quality=dict(tracker.history.counters))), flush=True)
        if tick < 18:
            await asyncio.sleep(10)
    await client.close()
    assert tracker.history.counters["accepted"] > 0


if __name__ == "__main__":
    asyncio.run(main())
