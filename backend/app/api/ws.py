"""WebSocket endpoint for real-time vehicle updates."""

import asyncio
import logging

import orjson
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

# Will be set by main.py on startup
broadcaster = None
tracker = None


@router.websocket("/ws/vehicles")
async def vehicle_ws(websocket: WebSocket) -> None:
    """Stream real-time vehicle position updates."""
    await websocket.accept()

    if broadcaster is None:
        await websocket.close(code=1011, reason="Service not ready")
        return

    # Send current snapshot first
    state_data = await broadcaster.get_current_state()
    if state_data:
        snapshot = orjson.loads(state_data)
        snapshot["type"] = "snapshot"
        await websocket.send_bytes(orjson.dumps(snapshot))

    # Subscribe to updates
    queue = broadcaster.subscribe()
    try:
        while True:
            data = await queue.get()
            await websocket.send_bytes(data)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("WebSocket error")
    finally:
        broadcaster.unsubscribe(queue)
