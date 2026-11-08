"""Text conversations over HTTP: the voice WebSocket (milestone 2) drives the same Receptionist."""

import asyncio
import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.agent import Conversation, clinic_now
from app.clinic import CLINIC_CITY, CLINIC_NAME, DENTISTS, HOURS, SERVICES, SLOT_MINUTES
from app.db import new_session
from app.guard import OUTPUT_REPLACEMENT
from app.models import Appointment
from app.prompts import GREETING
from app.voice.speech_text import SentenceSplitter

router = APIRouter(prefix="/api")


class Message(BaseModel):
    text: str


def _conversation(request: Request, session_id: str) -> Conversation:
    conversation = request.app.state.conversations.get(session_id)
    if conversation is None:
        raise HTTPException(404, "Unknown session")
    return conversation


@router.post("/sessions")
async def create_session(request: Request) -> dict:
    session_id = uuid.uuid4().hex
    # The greeting is part of the history so the model knows it already introduced itself
    request.app.state.conversations[session_id] = Conversation([{"role": "assistant", "content": GREETING}])
    return {"session_id": session_id, "greeting": GREETING}


@router.post("/sessions/{session_id}/messages")
async def send_message(request: Request, session_id: str, body: Message) -> dict:
    conversation = _conversation(request, session_id)
    if not body.text.strip():
        raise HTTPException(422, "Empty message")
    events = [e async for e in request.app.state.receptionist.respond(conversation, body.text.strip())]
    error = next((e["message"] for e in events if e["type"] == "error"), None)
    if error:
        raise HTTPException(502, f"The language model call failed: {error}")
    reply = events[-1]["text"]
    events = [e for e in events if e["type"] != "text"]
    refused = any(e["type"] == "guardrail" and not e["allowed"] for e in events)
    guard = request.app.state.receptionist.guard
    if guard and reply and not refused:
        reply = await _screen(guard, conversation, body.text.strip(), reply, events)
    return {"reply": reply, "events": events}


async def _screen(guard, conversation: Conversation, user_text: str, reply: str, events: list[dict]) -> str:
    """The output rail, sentence by sentence as the voice layer does it, but all at once since nothing has been
    said yet. Returns the reply cut at the first blocked sentence, followed by the replacement."""
    splitter = SentenceSplitter()
    sentences = [*splitter.push(reply), *filter(None, [splitter.flush()])]
    so_far = [" ".join(sentences[: n + 1]) for n in range(len(sentences))]
    verdicts = await asyncio.gather(*(guard.check_output(user_text, text) for text in so_far))
    for n, verdict in enumerate(verdicts):
        events.append({"type": "guardrail", "stage": "output", "allowed": verdict.allowed, "rail": verdict.rail})
        events[-1] |= {"ms": verdict.ms, "text": sentences[n]}
        if not verdict.allowed:
            said = " ".join(sentences[:n])
            conversation.withdraw(said, OUTPUT_REPLACEMENT)
            return f"{said} {OUTPUT_REPLACEMENT}".strip()
    return reply


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
        "city": CLINIC_CITY,
        # The clinic's own clock, so the calendar opens on the right day whatever the browser's time zone
        "now": clinic_now().isoformat(timespec="minutes"),
        "slot_minutes": SLOT_MINUTES,
        # Monday = 0; each day is a list of [open, close] blocks, with lunch as the gap
        "hours": {day: [[f"{a:%H:%M}", f"{b:%H:%M}"] for a, b in blocks] for day, blocks in HOURS.items()},
        "dentists": [{"id": d.id, "name": d.name, "services": list(d.services)} for d in DENTISTS.values()],
        "services": [
            {"key": s.key, "name": s.name, "minutes": s.minutes, "price_usd": s.price_usd} for s in SERVICES.values()
        ],
    }
