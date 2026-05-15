import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder


class RealtimeConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        sockets = self._connections.get(user_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(user_id, None)

    async def publish_user_event(
        self,
        user_id: str,
        event_type: str,
        payload: Any,
    ) -> None:
        sockets = list(self._connections.get(user_id, set()))
        if not sockets:
            return

        message = {
            "type": event_type,
            "payload": jsonable_encoder(payload),
            "occurred_at": datetime.now(UTC).isoformat(),
        }

        stale_sockets: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(message)
            except Exception:
                stale_sockets.append(websocket)

        for websocket in stale_sockets:
            self.disconnect(user_id, websocket)

    async def publish_many_user_event(
        self,
        user_ids: list[str],
        event_type: str,
        payload: Any,
    ) -> None:
        unique_ids = list(dict.fromkeys(user_ids))
        for user_id in unique_ids:
            await self.publish_user_event(user_id, event_type, payload)

    async def broadcast_event(self, event_type: str, payload: Any) -> None:
        user_ids = list(self._connections.keys())
        if not user_ids:
            return
        await self.publish_many_user_event(user_ids, event_type, payload)

    def publish_user_event_sync(self, user_id: str, event_type: str, payload: Any) -> None:
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self.publish_user_event(user_id, event_type, payload),
            self._loop,
        )

    def publish_many_user_event_sync(
        self,
        user_ids: list[str],
        event_type: str,
        payload: Any,
    ) -> None:
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self.publish_many_user_event(user_ids, event_type, payload),
            self._loop,
        )

    def broadcast_event_sync(self, event_type: str, payload: Any) -> None:
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self.broadcast_event(event_type, payload),
            self._loop,
        )


realtime_manager = RealtimeConnectionManager()
