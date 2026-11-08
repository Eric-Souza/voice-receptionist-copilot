"""The receptionist's turn loop: stream the model's reply, run its tool calls, repeat until it just talks.

respond() yields events as they happen, so the voice layer can start speaking the first sentence while
the model is still writing the rest:

    {"type": "text", "delta": "..."}                        part of the spoken reply
    {"type": "tool_call", "id", "name", "arguments"}       the model called a tool
    {"type": "tool_result", "id", "name", "result"}        what the tool returned
    {"type": "guardrail", "stage": "input", "allowed", "rail", "ms", "waited_ms"[, "error"]}
                                                           the input rails' verdict (see app/guard.py)
    {"type": "error", "message"}                           the model call failed; the turn was dropped
    {"type": "done", "text"}                               the whole spoken reply for this turn

The voice layer may cancel a turn midway when the caller talks over Sofia. Tools still run to the end and
their results are recorded, so the history never disagrees with the database; Conversation.interrupt()
then trims the reply to what the caller actually heard.

With a guard, the input rails check the caller's words while the model starts on its reply. Its text is held
back until they pass and no tool runs before then; if they block, the reply is dropped and Sofia declines.
Output rails are up to whoever consumes respond(), since only it knows when a sentence is about to be said.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.config import get_settings
from app.db import new_session
from app.guard import INPUT_REFUSAL, Guard, Verdict
from app.llm import ChatModel, ToolCall
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
    turns: int = 0  # caller messages so far
    pending: dict[str, Pending] = field(default_factory=dict)  # changes waiting for the caller's yes
    turn_start: int = 0  # where the latest caller turn begins in messages

    def interrupt(self, heard: str) -> bool:
        """The caller talked over the latest reply after hearing only `heard` (maybe nothing).

        Rewrites the turn so the model knows where it was cut off, and drops changes proposed in it, since the
        caller may not have heard them read back. Returns True if the turn was removed altogether because
        nothing was said or done yet; the caller's words should then go in front of what they say next.
        """
        turn = self.messages[self.turn_start :]
        if not turn:
            return False
        if not heard and not any(m["role"] == "tool" for m in turn):
            self._drop_pending()
            del self.messages[self.turn_start :]
            self.turns -= 1
            return True
        self._end_turn(f"{heard}…" if heard else "…")
        return False

    def withdraw(self, said: str, replacement: str) -> None:
        """An output rail blocked a sentence of the latest reply: the caller heard `said`, then `replacement`."""
        if self.messages[self.turn_start :]:
            self._end_turn(f"{said} {replacement}".strip())

    def _end_turn(self, reply: str) -> None:
        # What the model wrote is replaced by what the caller heard. Changes it proposed are dropped, since the
        # caller may not have heard them read back
        turn = [
            {**m, "content": None} if m.get("tool_calls") else m
            for m in _close_open_calls(self.messages[self.turn_start :])
            if m["role"] != "assistant" or m.get("tool_calls")
        ]
        self._drop_pending()
        self.messages[self.turn_start :] = [*turn, {"role": "assistant", "content": reply}]

    def _drop_pending(self) -> None:
        for action_id in [a for a, p in self.pending.items() if p.turn == self.turns]:
            del self.pending[action_id]


class Receptionist:
    def __init__(
        self,
        llm: ChatModel,
        session_factory: Callable[[], Session] = new_session,
        now: Callable[[], datetime] = clinic_now,
        max_rounds: int | None = None,
        guard: Guard | None = None,
    ):
        self.llm = llm
        self.guard = guard
        self.session_factory = session_factory
        self.now = now
        self.max_rounds = max_rounds or get_settings().max_tool_rounds
        self.tools = tool_schemas()

    async def respond(self, conversation: Conversation, user_text: str) -> AsyncIterator[Event]:
        history = conversation.messages
        turn_start = conversation.turn_start = len(history)
        conversation.turns += 1
        history.append({"role": "user", "content": user_text})
        spoken: list[str] = []
        retried_empty = False
        # Until the input check passes, the model's text is held here and its tool calls wait
        check = asyncio.ensure_future(self.guard.check_input(user_text)) if self.guard else None
        held: list[str] = []
        first_text_at = 0.0
        blocked = False

        def verdict_event(verdict: Verdict) -> Event:
            # waited_ms: how long the check held up words the model had already written
            waited = round((time.perf_counter() - first_text_at) * 1000) if first_text_at else 0
            event = {"type": "guardrail", "stage": "input", "allowed": verdict.allowed, "rail": verdict.rail}
            event |= {"ms": verdict.ms, "waited_ms": waited}
            return event | ({"error": verdict.error} if verdict.error else {})

        try:
            for _ in range(self.max_rounds):
                now = self.now()
                messages = [{"role": "system", "content": system_prompt(now)}, *history]
                for attempt in (1, 2):
                    text, calls, streamed = "", [], False
                    held.clear()
                    try:
                        async with contextlib.aclosing(self.llm.stream(messages, self.tools)) as stream:
                            chunks, next_chunk = aiter(stream), None
                            try:
                                while True:
                                    next_chunk = asyncio.ensure_future(anext(chunks))
                                    if check is not None and not check.done():
                                        # Wait on both, so a block is acted on as soon as it's known, not when the
                                        # model's first words (or the end of a tool call) arrive
                                        await asyncio.wait({next_chunk, check}, return_when=asyncio.FIRST_COMPLETED)
                                    if check is not None and check.done():
                                        verdict = check.result()
                                        check = None
                                        yield verdict_event(verdict)
                                        if not verdict.allowed:
                                            blocked = True
                                            break
                                        for delta in held:
                                            yield {"type": "text", "delta": delta}
                                    try:
                                        chunk = await next_chunk
                                    except StopAsyncIteration:
                                        break
                                    streamed = True
                                    if not isinstance(chunk, str):
                                        calls = chunk
                                        continue
                                    text += chunk
                                    if check is None:
                                        yield {"type": "text", "delta": chunk}
                                        continue
                                    held.append(chunk)
                                    first_text_at = first_text_at or time.perf_counter()
                            finally:
                                if next_chunk is not None and not next_chunk.done():
                                    # Let the stream settle before aclosing closes it
                                    next_chunk.cancel()
                                    await asyncio.wait({next_chunk})
                        break
                    except Exception:
                        # Groq sometimes rejects a malformed tool call (400 tool_use_failed); if nothing reached
                        # the caller yet, one more try usually works
                        if (streamed and check is None) or attempt == 2:
                            raise
                        log.warning("Model call failed before any output, retrying", exc_info=True)
                if check is not None:
                    # The model finished its first call before the check did: wait for it before speaking or acting
                    verdict = await check
                    check = None
                    yield verdict_event(verdict)
                    blocked = not verdict.allowed
                    if not blocked:
                        for delta in held:
                            yield {"type": "text", "delta": delta}
                if blocked:
                    # The blocked words stay out of the history, so an attack can't build up over several turns
                    history[turn_start:] = [
                        {"role": "user", "content": "(withheld: blocked by the guardrails)"},
                        {"role": "assistant", "content": INPUT_REFUSAL},
                    ]
                    yield {"type": "text", "delta": INPUT_REFUSAL}
                    yield {"type": "done", "text": INPUT_REFUSAL}
                    return
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
                    result = await _uninterrupted(self._record_tool(conversation, now, call))
                    yield {"type": "tool_result", "id": call.id, "name": call.name, "result": result}
            else:
                log.warning("Turn hit max_tool_rounds=%s without a final reply", self.max_rounds)
        except Exception as exc:
            log.exception("Model call failed")
            if any(m["role"] == "tool" for m in history[turn_start:]):
                # Keep what the tools did (a booking may already be confirmed), so history matches the database
                history[turn_start:] = _close_open_calls(history[turn_start:])
            else:
                del history[turn_start:]
            for action_id in [a for a, p in conversation.pending.items() if p.turn == conversation.turns]:
                del conversation.pending[action_id]
            yield {"type": "error", "message": str(exc)}
            yield {"type": "done", "text": ""}
            return
        finally:
            if check is not None:
                check.cancel()
        yield {"type": "done", "text": " ".join(spoken)}

    async def _record_tool(self, conversation: Conversation, now: datetime, call: ToolCall) -> dict[str, Any]:
        result = await asyncio.to_thread(self._run_tool, conversation, now, call.name, call.arguments)
        conversation.messages.append(
            {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)}
        )
        return result

    def _run_tool(self, conversation: Conversation, now: datetime, name: str, arguments: str) -> dict[str, Any]:
        with self.session_factory() as session:
            ctx = ToolContext(session, now, conversation.turns, conversation.pending)
            return run_tool(ctx, name, arguments)


def _close_open_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give the tool calls that never ran a result saying so. A batch without all its results breaks the next
    request, and dropping the batch instead would lose what the calls that did run changed."""
    answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    missing = [c["id"] for m in messages for c in m.get("tool_calls") or [] if c["id"] not in answered]
    not_run = json.dumps({"error": "Not run: the call was cut off before this tool ran"})
    return [*messages, *({"role": "tool", "tool_call_id": i, "content": not_run} for i in missing)]


async def _uninterrupted(coro: Awaitable[Any]) -> Any:
    """Await coro to the end even if this task is cancelled meanwhile, then pass the cancellation on."""
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def _loads(arguments: str) -> Any:
    try:
        return json.loads(arguments or "{}")
    except ValueError:
        return arguments
