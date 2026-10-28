"""Text conversations over HTTP: the voice WebSocket (milestone 2) drives the same Receptionist."""

import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.agent import Conversation, clinic_now
from app.clinic import CLINIC_NAME, DENTISTS, SERVICES
from app.db import new_session
from app.models import Appointment
from app.prompts import GREETING

router = APIRouter(prefix="/api")


class NewSession(BaseModel):
    language: str = "pt"


class Message(BaseModel):
    text: str


def _conversation(request: Request, session_id: str) -> Conversation:
    conversation = request.app.state.conversations.get(session_id)
    if conversation is None:
        raise HTTPException(404, "Unknown session")
    return conversation


@router.post("/sessions")
async def create_session(request: Request, body: NewSession | None = None) -> dict:
    language = (body or NewSession()).language
    language = language if language in GREETING else "pt"
    greeting = GREETING[language]
    session_id = uuid.uuid4().hex
    # The greeting is part of the history so the model knows it already introduced itself
    conversation = Conversation([{"role": "assistant", "content": greeting}], language)
    request.app.state.conversations[session_id] = conversation
    return {"session_id": session_id, "greeting": greeting}


@router.post("/sessions/{session_id}/messages")
async def send_message(request: Request, session_id: str, body: Message) -> dict:
    conversation = _conversation(request, session_id)
    if not body.text.strip():
        raise HTTPException(422, "Empty message")
    events = [e async for e in request.app.state.receptionist.respond(conversation, body.text.strip())]
    error = next((e["message"] for e in events if e["type"] == "error"), None)
    if error:
        raise HTTPException(502, f"The language model call failed: {error}")
    return {"reply": events[-1]["text"], "events": [e for e in events if e["type"] != "text"]}


@router.get("/appointments")
async def list_appointments(start: date | None = None, days: int = 7) -> list[dict]:
    first = datetime.combine(start or clinic_now().date(), time())
    with new_session() as session:
        rows = session.exec(
            select(Appointment)
            .where(
                Appointment.status == "booked",
                Appointment.start >= first,
                Appointment.start < first + timedelta(days=max(1, min(days, 31))),
            )
            .order_by(Appointment.start)
        ).all()
    return [
        {
            "id": a.id,
            "patient_name": a.patient_name,
            "dentist_id": a.dentist_id,
            "service": a.service,
            "start": a.start.isoformat(timespec="minutes"),
            "end": a.end.isoformat(timespec="minutes"),
        }
        for a in rows
    ]


@router.get("/clinic")
async def clinic() -> dict:
    return {
        "name": CLINIC_NAME,
        "dentists": [{"id": d.id, "name": d.name, "services": list(d.services)} for d in DENTISTS.values()],
        "services": [
            {"key": s.key, "name_pt": s.name_pt, "name_en": s.name_en, "minutes": s.minutes, "price_brl": s.price_brl}
            for s in SERVICES.values()
        ],
    }
