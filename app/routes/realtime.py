import uuid

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.database import SessionLocal
from app.core.security import decode_access_token
from app.models import User
from app.services.realtime_service import realtime_manager

router = APIRouter(tags=["realtime"])


def _resolve_websocket_user(token: str | None) -> User | None:
    if not token:
        return None

    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if subject is None:
            return None

        user_id = uuid.UUID(subject)
    except (jwt.InvalidTokenError, ValueError):
        return None

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        db.expunge(user)
        return user
    finally:
        db.close()


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    user = _resolve_websocket_user(token)
    if not user:
        await websocket.close(code=4401)
        return

    user_id = str(user.id)
    await realtime_manager.connect(user_id, websocket)
    try:
        await websocket.send_json(
            {
                "type": "connection.ready",
                "payload": {"user_id": user_id},
            }
        )

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        realtime_manager.disconnect(user_id, websocket)
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass
