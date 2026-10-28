"""Real-model checks, skipped by default. Run with: LIVE_GROQ_API_KEY=gsk_... pytest -m live"""

import os

import pytest

from app.agent import Conversation, Receptionist
from app.llm import get_chat_model
from tests.conftest import NOW

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("LIVE_GROQ_API_KEY"), reason="LIVE_GROQ_API_KEY not set"),
]


async def say(receptionist, conversation, text):
    events = [e async for e in receptionist.respond(conversation, text)]
    assert not [e for e in events if e["type"] == "error"]
    return events


def tool_names(events):
    return [e["name"] for e in events if e["type"] == "tool_call"]


def booked(events):
    return [e for e in events if e["type"] == "tool_result" and "booked" in e["result"]]


async def test_books_only_after_the_caller_confirms():
    receptionist, conversation = Receptionist(get_chat_model(), now=lambda: NOW), Conversation(language="en")
    events = await say(receptionist, conversation, "Hi, I'd like a cleaning tomorrow morning.")
    assert "check_availability" in tool_names(events)
    events = await say(receptionist, conversation, "The first one, please. Maria Souza, 11 98888-7777.")
    assert not booked(events)
    events = await say(receptionist, conversation, "Yes, that's right.")
    assert booked(events)
