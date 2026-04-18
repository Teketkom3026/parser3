"""WebSocket progress endpoint."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.logging import get_logger


log = get_logger("api.ws")
router = APIRouter()


@router.websocket("/ws/{task_id}")
async def ws_task(websocket: WebSocket, task_id: str):
    tm = websocket.app.state.task_manager
    db = websocket.app.state.db
    await websocket.accept()
    q = tm.subscribe(task_id)
    try:
        # Send snapshot
        t = await db.get_task(task_id)
        if t:
            await websocket.send_text(json.dumps({"type": "snapshot", "task": t}))
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_text(json.dumps(msg, default=str))
            except asyncio.TimeoutError:
                # keepalive
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("ws_error", error=str(e))
    finally:
        tm.unsubscribe(task_id, q)
