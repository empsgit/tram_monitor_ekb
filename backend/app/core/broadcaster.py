"""Redis pub/sub broadcaster for vehicle state updates."""

import asyncio
import logging
import time

import orjson
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

CHANNEL = "tram:vehicles"
STATE_KEY = "tram:state"
STATE_TTL_SECONDS = 60


class Broadcaster:
    """Publishes vehicle state to Redis and manages WebSocket subscribers."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._latest: bytes | None = None
        self._latest_at = 0.0

    async def connect(self) -> None:
        self._redis = aioredis.from_url(
            settings.redis_url, decode_responses=False,
            socket_connect_timeout=2, socket_timeout=2,
        )
        await self._redis.ping()

    async def is_healthy(self) -> bool:
        try:
            return bool(self._redis and await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def publish(self, vehicles_data: list[dict]) -> None:
        """Publish vehicle state update to Redis and fan out to WebSocket subscribers."""
        payload = orjson.dumps({"type": "update", "vehicles": vehicles_data})
        self._latest = payload
        self._latest_at = time.monotonic()

        if self._redis:
            try:
                # Store current state for new connections
                await self._redis.set(STATE_KEY, payload, ex=STATE_TTL_SECONDS)
                # Publish to channel
                await self._redis.publish(CHANNEL, payload)
            except Exception:
                logger.exception("Failed to publish to Redis")

        # Fan out directly to WebSocket subscribers
        for q in self._subscribers:
            if q.full():
                q.get_nowait()
            q.put_nowait(payload)

    async def get_current_state(self) -> bytes | None:
        """Get latest vehicle state snapshot from Redis."""
        if self._latest is not None and time.monotonic() - self._latest_at < STATE_TTL_SECONDS:
            return self._latest
        if self._redis:
            try:
                data = await self._redis.get(STATE_KEY)
                return data
            except Exception:
                logger.exception("Failed to get state from Redis")
        return None

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue for WebSocket fan-out."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
