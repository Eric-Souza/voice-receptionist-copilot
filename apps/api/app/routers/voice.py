"""The voice WebSocket (protocol in app/voice/call.py) and a bare test page for it."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from app.voice import VoiceUnavailable, load_services
from app.voice.call import VoiceCall, VoiceServices

log = logging.getLogger(__name__)
router = APIRouter()
_loading = asyncio.Lock()


async def _services(ws: WebSocket) -> VoiceServices:
    state = ws.app.state
    async with _loading:  # the first call loads the models; later calls reuse them
        if state.voice is None:
            state.voice = await asyncio.to_thread(load_services)
    return state.voice


@router.websocket("/ws/voice")
async def voice(ws: WebSocket, language: str = "pt") -> None:
    await ws.accept()
    try:
        services = await _services(ws)
    except VoiceUnavailable as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close(code=1011)
        return
    call = VoiceCall(ws.app.state.receptionist, services, language, ws.send_json, ws.send_bytes)
    await call.start()
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                await call.feed(message["bytes"])
            elif message.get("text"):
                log.debug("Ignoring client message %s", json.loads(message["text"]).get("type"))
    except WebSocketDisconnect:
        pass
    finally:
        await call.close()


@router.get("/voice", include_in_schema=False)
async def test_page() -> FileResponse:
    return FileResponse(Path(__file__).parent.parent / "static" / "voice.html")
