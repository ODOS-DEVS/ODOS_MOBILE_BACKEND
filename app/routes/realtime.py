import uuid

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.security import decode_access_token
from app.services.realtime_service import realtime_manager

router = APIRouter(tags=["realtime"])


def _resolve_websocket_user_id(token: str | None) -> uuid.UUID | None:
    """Validate the JWT only — avoid a DB round-trip per websocket connect."""
    if not token:
        return None

    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if subject is None:
            return None

        return uuid.UUID(subject)
    except (jwt.InvalidTokenError, ValueError):
        return None


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    user_id = _resolve_websocket_user_id(token) if token else None
    if token and not user_id:
        await websocket.accept()
        await websocket.close(code=4401, reason="Unauthorized")
        return

    connection_id = str(user_id) if user_id else f"public:{uuid.uuid4()}"
    await realtime_manager.connect(connection_id, websocket)
    try:
        await websocket.send_json(
            {
                "type": "connection.ready",
                "payload": {
                    "scope": "authenticated" if user_id else "public",
                    "user_id": str(user_id) if user_id else None,
                },
            }
        )

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        realtime_manager.disconnect(connection_id, websocket)
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass
