"""The realtime WebSocket: `GET /api/v1/ws` (m5 design, "Realtime").

Server to client only (see `app.realtime` for the messages); the one thing a client sends is
"ping", answered with "pong". The handshake checks the Origin and the session cookie, then the
database session is closed again: a socket never holds one open.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import Settings
from app.deps import user_from_cookie
from app.realtime import hub
from app.services.auth import SESSION_COOKIE_NAME

router = APIRouter(tags=["realtime"])

CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_UNAUTHENTICATED = 4401


@router.websocket("/ws")
async def realtime_socket(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    if websocket.headers.get("origin") != settings.origin:
        await websocket.close(code=CLOSE_FORBIDDEN_ORIGIN)
        return
    raw = websocket.cookies.get(SESSION_COOKIE_NAME)
    current = None
    if raw:
        async with websocket.app.state.sessionmaker() as session:
            current = await user_from_cookie(session, raw)
    if current is None or current.couple_id is None:
        await websocket.close(code=CLOSE_UNAUTHENTICATED)
        return
    couple_id, user_id = current.couple_id, current.user.id
    await websocket.accept()
    await hub.connect(couple_id, user_id, websocket)
    try:
        while True:
            message = await websocket.receive()  # not receive_text: a binary frame is ignored
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(couple_id, user_id, websocket)
