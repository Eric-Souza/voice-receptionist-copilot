"""The HTTP API.

uvicorn app.main:app --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent import Receptionist
from app.config import get_settings
from app.db import init_db
from app.llm import ChatModel, get_chat_model
from app.routers import chat, voice
from app.seed import seed_if_empty
from app.voice.call import VoiceServices


def create_app(llm: ChatModel | None = None, voice_services: VoiceServices | None = None) -> FastAPI:
    """Tests pass fakes; the server uses the configured provider and loads the voice models on first call."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        seed_if_empty()
        app.state.receptionist = Receptionist(llm or get_chat_model())
        app.state.conversations = {}
        app.state.voice = voice_services
        yield

    app = FastAPI(title="Voice Receptionist Copilot", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router)
    app.include_router(voice.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
