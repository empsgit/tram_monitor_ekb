import asyncio
from unittest.mock import AsyncMock

import orjson

from app.core.broadcaster import Broadcaster


def test_slow_subscriber_gets_latest_snapshot_and_remains_subscribed():
    async def scenario():
        subject = Broadcaster()
        queue = subject.subscribe()
        for i in range(20):
            await subject.publish([{"id": str(i)}])
        assert queue in subject._subscribers
        assert queue.qsize() == 1
        assert orjson.loads(await queue.get())["vehicles"] == [{"id": "19"}]
    asyncio.run(scenario())


def test_redis_failure_preserves_live_snapshot():
    async def scenario():
        subject = Broadcaster()
        subject._redis = AsyncMock()
        subject._redis.set.side_effect = ConnectionError("unavailable")
        queue = subject.subscribe()
        await subject.publish([{"id": "test"}])
        assert await subject.get_current_state() == await queue.get()
    asyncio.run(scenario())


def test_redis_health_failure_is_reported():
    async def scenario():
        subject = Broadcaster()
        subject._redis = AsyncMock()
        subject._redis.ping.side_effect = ConnectionError("unavailable")
        assert await subject.is_healthy() is False
    asyncio.run(scenario())
