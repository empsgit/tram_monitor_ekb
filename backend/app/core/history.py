"""Bounded, retryable history writes and real sample quantiles."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.models.tables import PositionObservation, StopVisit, SegmentObservation

logger = logging.getLogger(__name__)
EKB = timezone(timedelta(hours=5))


class History:
    MAX_PENDING = 10000

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.positions: dict[tuple, dict] = {}
        self.visits: dict[tuple, dict] = {}
        self.segments: dict[tuple, dict] = {}
        self.counters = Counter()
        self.stats: dict[tuple, tuple[float, float, float, int]] = {}
        self.stats_updated_at: str | None = None

    def enqueue(self, position: dict | None, visits: list[dict], segments: list[dict]) -> None:
        for visit in visits:
            self.visits[(visit["trip_id"], visit["stop_index"])] = visit
        for segment in segments:
            local = segment["departed_at"].astimezone(EKB)
            row = {**segment, "weekday": local.weekday(), "hour": local.hour}
            self.segments[(row["trip_id"], row["from_index"], row["to_index"])] = row
        if position:
            self.positions.setdefault((position["vehicle_id"], position["observed_at"]), position)
        for queue in (self.positions, self.visits, self.segments):
            excess = len(queue) - self.MAX_PENDING
            if excess > 0:
                for key in list(queue)[:excess]:
                    del queue[key]
                self.counters["dropped_overflow"] += excess
                logger.error("History queue overflow: discarded %d oldest pending rows", excess)

    async def flush(self) -> None:
        # Capture a batch before awaiting. Failed transactions leave it queued.
        batches = [(PositionObservation, self.positions, dict(self.positions)),
                   (StopVisit, self.visits, dict(self.visits)),
                   (SegmentObservation, self.segments, dict(self.segments))]
        if not any(batch for _, _, batch in batches):
            return
        async with self.session_factory() as session:
            for model, _, batch in batches:
                rows = list(batch.values())
                for offset in range(0, len(rows), 500):
                    stmt = insert(model).values(rows[offset:offset + 500])
                    if model is StopVisit:
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["trip_id", "stop_index"],
                            set_={"departed_at": stmt.excluded.departed_at, "quality": stmt.excluded.quality})
                    else:
                        stmt = stmt.on_conflict_do_nothing()
                    await session.execute(stmt)
            await session.commit()
        for _, queue, batch in batches:
            for key, row in batch.items():
                if queue.get(key) is row:
                    del queue[key]
        self.counters["committed_batches"] += 1

    async def refresh_stats(self) -> None:
        new_stats = {}
        # Each fallback aggregates RAW samples, never averages of group medians.
        sources = {
            "run": "SELECT route_id, route_version, direction, from_index AS idx, weekday, hour, seconds FROM segment_observations",
            "dwell": """SELECT route_id, route_version, direction, stop_index AS idx,
                EXTRACT(ISODOW FROM arrived_at AT TIME ZONE 'Asia/Yekaterinburg')::int - 1 AS weekday,
                EXTRACT(HOUR FROM arrived_at AT TIME ZONE 'Asia/Yekaterinburg')::int AS hour,
                EXTRACT(EPOCH FROM departed_at - arrived_at) AS seconds FROM stop_visits
                WHERE quality = 'valid' AND departed_at - arrived_at BETWEEN interval '2 seconds' AND interval '10 minutes'""",
        }
        async with self.session_factory() as session:
            for kind, source in sources.items():
                result = await session.execute(text(f"""
                    WITH samples AS ({source})
                    SELECT route_id, route_version, direction, idx, weekday, hour,
                        percentile_cont(0.25) WITHIN GROUP (ORDER BY seconds) AS p25,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds) AS median,
                        percentile_cont(0.75) WITHIN GROUP (ORDER BY seconds) AS p75, count(*) AS n
                    FROM samples GROUP BY route_id, route_version, direction, idx,
                    GROUPING SETS ((weekday, hour), (hour), ())
                """))
                for r in result.mappings():
                    new_stats[(kind, r["route_id"], r["route_version"], r["direction"], r["idx"], r["weekday"], r["hour"])] = (
                        r["p25"], r["median"], r["p75"], r["n"])
        self.stats = new_stats
        self.stats_updated_at = datetime.now(timezone.utc).isoformat()

    def lookup(self, kind: str, route_id: int, version: str, direction: int,
               index: int, at: datetime) -> tuple[float, float, float, int] | None:
        local = at.astimezone(EKB)
        for weekday, hour, minimum in ((local.weekday(), local.hour, 5), (None, local.hour, 8), (None, None, 10)):
            value = self.stats.get((kind, route_id, version, direction, index, weekday, hour))
            if value and value[3] >= minimum:
                return value
        return None

    async def retain(self, position_days: int, sample_days: int) -> None:
        now = datetime.now(timezone.utc)
        for table, column, days in (("position_observations", "observed_at", position_days),
                                    ("stop_visits", "arrived_at", sample_days),
                                    ("segment_observations", "arrived_at", sample_days)):
            # Bounded transactions avoid holding a large deletion lock / WAL burst.
            for _ in range(20):
                async with self.session_factory() as session:
                    result = await session.execute(text(f"""DELETE FROM {table} WHERE id IN
                        (SELECT id FROM {table} WHERE {column} < :cutoff ORDER BY {column} LIMIT 5000)"""),
                        {"cutoff": now - timedelta(days=max(1, days))})
                    await session.commit()
                    self.counters["retained_deleted"] += result.rowcount
                    if result.rowcount < 5000:
                        break
