import uuid

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.database import SessionLocal
from app.core.security import decode_access_token
from app.models import User
from app.services.realtime_service import realtime_manager

router = APIRouter(tags=["realtime"])


def _resolve_websocket_user_id(token: str | None) -> uuid.UUID | None:
    """Validate the JWT signature/purpose. One DB lookup happens separately,
    once per connect (not per message), to catch a token that's since been
    revoked — see _is_token_revoked below."""
    if not token:
        return None

    try:
        payload = decode_access_token(token)
        # Reject password-reset JWTs (same rule as Bearer auth).
        if payload.get("purpose") or (
            payload.get("typ") and payload.get("typ") != "access"
        ):
            return None
        subject = payload.get("sub")
        if subject is None:
            return None

        user_id = uuid.UUID(subject)
    except (jwt.InvalidTokenError, ValueError):
        return None

    if _is_token_revoked(user_id, payload):
        return None
    return user_id


def _is_token_revoked(user_id: uuid.UUID, payload: dict) -> bool:
    """Mirrors get_current_user's is_active/token_version check — a blocked
    account or a password reset (which bumps token_version) must not keep
    receiving realtime events on an old token, even though this connection
    can't call any mutating API."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return True
        token_version = int(payload.get("tv") or 0)
        return int(getattr(user, "token_version", 0) or 0) != token_version
    finally:
        db.close()


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
