"""The receptionist's turn loop: stream the model's reply, run its tool calls, repeat until it just talks.

respond() yields events as they happen, so the voice layer can start speaking the first sentence while
the model is still writing the rest:

    {"type": "text", "delta": "..."}                        part of the spoken reply
    {"type": "tool_call", "id", "name", "arguments"}       the model called a tool
    {"type": "tool_result", "id", "name", "result"}        what the tool returned
    {"type": "error", "message"}                           the model call failed; the turn was dropped
    {"type": "done", "text"}                               the whole spoken reply for this turn
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.config import get_settings
from app.db import new_session
from app.llm import ChatModel
from app.prompts import system_prompt
from app.tools import Pending, ToolContext, run_tool, tool_schemas

log = logging.getLogger(__name__)

Event = dict[str, Any]


def clinic_now() -> datetime:
    """Naive current time in the clinic's time zone, matching how appointments are stored."""
    return datetime.now(ZoneInfo(get_settings().clinic_timezone)).replace(tzinfo=None, second=0, microsecond=0)


@dataclass
class Conversation:
    # Chat history without the system prompt, which is rebuilt every turn with the current time
    messages: list[dict[str, Any]] = field(default_factory=list)
    language: str = "pt"  # the language the call started in; the caller may switch
    turns: int = 0  # caller messages so far
    pending: dict[str, Pending] = field(default_factory=dict)  # changes waiting for the caller's yes


class Receptionist:
    def __init__(
        self,
        llm: ChatModel,
        session_factory: Callable[[], Session] = new_session,
        now: Callable[[], datetime] = clinic_now,
        max_rounds: int | None = None,
    ):
        self.llm = llm
        self.session_factory = session_factory
        self.now = now
        self.max_rounds = max_rounds or get_settings().max_tool_rounds
        self.tools = tool_schemas()

    async def respond(self, conversation: Conversation, user_text: str) -> AsyncIterator[Event]:
        history = conversation.messages
        turn_start = len(history)
        conversation.turns += 1
        history.append({"role": "user", "content": user_text})
        spoken: list[str] = []
        retried_empty = False
        try:
            for _ in range(self.max_rounds):
                now = self.now()
                messages = [{"role": "system", "content": system_prompt(now, conversation.language)}, *history]
                for attempt in (1, 2):
                    text, calls, streamed = "", [], False
                    try:
                        async for chunk in self.llm.stream(messages, self.tools):
                            streamed = True
                            if isinstance(chunk, str):
                                text += chunk
                                yield {"type": "text", "delta": chunk}
                            else:
                                calls = chunk
                        break
                    except Exception:
                        # Groq sometimes rejects a malformed tool call (400 tool_use_failed); if nothing reached
                        # the caller yet, one more try usually works
                        if streamed or attempt == 2:
                            raise
                        log.warning("Model call failed before any output, retrying", exc_info=True)
                if text.strip():
                    spoken.append(text.strip())
                if not calls and not spoken and not retried_empty:
                    # Models occasionally end a turn with no text at all; on a call that's dead air, so ask again
                    retried_empty = True
                    log.warning("Empty reply from the model, retrying once")
                    continue
                if not calls:
                    history.append({"role": "assistant", "content": text})
                    break
                history.append(
                    {
                        "role": "assistant",
                        "content": text or None,
                        "tool_calls": [
                            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                            for c in calls
                        ],
                    }
                )
                for call in calls:
                    yield {"type": "tool_call", "id": call.id, "name": call.name, "arguments": _loads(call.arguments)}
                    result = await asyncio.to_thread(self._run_tool, conversation, now, call.name, call.arguments)
                    yield {"type": "tool_result", "id": call.id, "name": call.name, "result": result}
                    history.append(
                        {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)}
                    )
            else:
                log.warning("Turn hit max_tool_rounds=%s without a final reply", self.max_rounds)
        except Exception as exc:
            log.exception("Model call failed")
            # Drop the half-finished turn: a tool call without its result would break the next request
            del history[turn_start:]
            for action_id in [a for a, p in conversation.pending.items() if p.turn == conversation.turns]:
                del conversation.pending[action_id]
            yield {"type": "error", "message": str(exc)}
            yield {"type": "done", "text": ""}
            return
        yield {"type": "done", "text": " ".join(spoken)}

    def _run_tool(self, conversation: Conversation, now: datetime, name: str, arguments: str) -> dict[str, Any]:
        with self.session_factory() as session:
            ctx = ToolContext(session, now, conversation.turns, conversation.pending)
            return run_tool(ctx, name, arguments)


def _loads(arguments: str) -> Any:
    try:
        return json.loads(arguments or "{}")
    except ValueError:
        return arguments
