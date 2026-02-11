"""Redis pub/sub broadcaster for vehicle state updates."""

import asyncio
import logging

import orjson
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

CHANNEL = "tram:vehicles"
STATE_KEY = "tram:state"


class Broadcaster:
    """Publishes vehicle state to Redis and manages WebSocket subscribers."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._subscribers: set[asyncio.Queue] = set()

    async def connect(self) -> None:
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=False)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def publish(self, vehicles_data: list[dict]) -> None:
        """Publish vehicle state update to Redis and fan out to WebSocket subscribers."""
        payload = orjson.dumps({"type": "update", "vehicles": vehicles_data})

        if self._redis:
            try:
                # Store current state for new connections
                await self._redis.set(STATE_KEY, payload)
                # Publish to channel
                await self._redis.publish(CHANNEL, payload)
            except Exception:
                logger.exception("Failed to publish to Redis")

        # Fan out directly to WebSocket subscribers
        dead = set()
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.add(q)
        self._subscribers -= dead

    async def get_current_state(self) -> bytes | None:
        """Get latest vehicle state snapshot from Redis."""
        if self._redis:
            try:
                data = await self._redis.get(STATE_KEY)
                return data
            except Exception:
                logger.exception("Failed to get state from Redis")
        return None

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue for WebSocket fan-out."""
        q: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
