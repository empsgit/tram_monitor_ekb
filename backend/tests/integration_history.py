"""Run manually against a disposable database whose name starts with tram_test_."""
import asyncio
from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.history import History
from app.core.ettu_client import RawRoute, RawStop
from app.core.vehicle_tracker import VehicleTracker
from app.models.base import Base
from app.models import tables  # noqa: F401


async def main():
    url = make_url(os.environ["DATABASE_URL"]).set(database=os.environ["TRAM_TEST_DATABASE"])
    assert url.database.startswith("tram_test_"), "Disposable database required"
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine)
    now = datetime(2026, 11, 17, 8, tzinfo=timezone.utc)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    history = History(sessions)
    position = dict(vehicle_id="test", route_id=1, route_version="v1", direction=0,
                    observed_at=now, received_at=now + timedelta(seconds=12),
                    lat=56.84, lon=60.6, speed=10, course=0, quality="accepted")
    history.enqueue(position, [], [])
    await history.flush()
    history.enqueue(position, [], [])
    await history.flush()
    async with sessions() as session:
        assert (await session.execute(text("SELECT count(*) FROM position_observations"))).scalar() == 1
        assert (await session.execute(text("SELECT received_at-observed_at FROM position_observations"))).scalar().total_seconds() == 12
    segments = []
    for i, seconds in enumerate([30, 40, 50, 60, 1000]):
        segments.append(dict(trip_id=f"test-{i}", vehicle_id="test", route_id=1, route_version="v1",
            direction=0, from_index=0, to_index=1, from_stop_id=1, to_stop_id=2,
            departed_at=now, arrived_at=now + timedelta(seconds=seconds), seconds=seconds))
    visit = dict(trip_id="test-0", vehicle_id="test", route_id=1, route_version="v1", direction=0,
                 stop_index=0, stop_id=1, arrived_at=now, departed_at=None, quality="valid")
    history.enqueue(None, [visit], segments)
    await history.flush()
    history.enqueue(None, [{**visit, "departed_at": now + timedelta(seconds=20)}], segments)
    await history.flush()
    await history.refresh_stats()
    assert history.lookup("run", 1, "v1", 0, 0, now) == (40, 50, 60, 5)
    assert history.lookup("run", 1, "v2", 0, 0, now) is None
    async with sessions() as session:
        assert (await session.execute(text("SELECT count(*) FROM segment_observations"))).scalar() == 5
        assert (await session.execute(text("SELECT count(*) FROM stop_visits WHERE departed_at IS NOT NULL"))).scalar() == 1
    tracker = VehicleTracker(None, None, sessions)
    stops = [RawStop(id=i, name=f"Stop {i}", lat=56.84, lon=60.6) for i in (1, 2, 3)]
    route = RawRoute(id=1, number="1", name="Test")
    route.stops = [dict(id=i, name=f"Stop {i}", direction=0, order=i) for i in (1, 2)]
    await tracker._persist_routes_stops([route], stops, stops_fetched=True)
    async with sessions() as session:
        refreshed = (await session.execute(text("SELECT refreshed_at FROM data_cache_meta"))).scalar()
    route.stops = [dict(id=i, name=f"Stop {i}", direction=0, order=i) for i in (1, 3)]
    await tracker._persist_routes_stops([route], stops, stops_fetched=False)
    async with sessions() as session:
        assert (await session.execute(text("SELECT refreshed_at FROM data_cache_meta"))).scalar() == refreshed
        assert list((await session.execute(text("SELECT stop_id FROM route_stops ORDER BY stop_id"))).scalars()) == [1, 3]
    route.stops.append(dict(id=9999, name="Missing", direction=0, order=4))
    try:
        await tracker._persist_routes_stops([route], stops)
        raise AssertionError("Missing stop should fail atomically")
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        assert isinstance(exc, IntegrityError)
    async with sessions() as session:
        assert list((await session.execute(text("SELECT stop_id FROM route_stops ORDER BY stop_id"))).scalars()) == [1, 3]
        await session.execute(text("UPDATE position_observations SET observed_at = now() - interval '31 days'"))
        await session.execute(text("UPDATE segment_observations SET arrived_at = now() - interval '181 days'"))
        await session.execute(text("UPDATE stop_visits SET arrived_at = now() - interval '181 days'"))
        await session.commit()
    await history.retain(30, 180)
    async with sessions() as session:
        for table in ("position_observations", "segment_observations", "stop_visits"):
            assert (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0
    await engine.dispose()
    print("PASS: source time, deduplication, visit upsert, real quantiles, version separation, catalog rollback/TTL, retention")


if __name__ == "__main__":
    asyncio.run(main())
