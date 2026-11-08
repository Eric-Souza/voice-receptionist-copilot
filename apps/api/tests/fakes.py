import asyncio
import json
from typing import Any

from app.guard import Verdict
from app.llm import Chunk, ToolCall


class ScriptedModel:
    """Replays canned replies, one per model call, and records what it was sent.

    Each reply is a list of chunks: strings are streamed text, a list of (name, args) pairs is a tool call batch.
    """

    def __init__(self, *replies: list[str | list[tuple[str, dict[str, Any]]]]):
        self.replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []

    async def stream(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("ScriptedModel ran out of replies")
        for n, chunk in enumerate(self.replies.pop(0)):
            yield _convert(chunk, len(self.calls), n)


def _convert(chunk, call_no: int, n: int) -> Chunk:
    if isinstance(chunk, str):
        return chunk
    return [ToolCall(f"call_{call_no}_{n}_{i}", name, json.dumps(args)) for i, (name, args) in enumerate(chunk)]


class FailingModel:
    async def stream(self, messages, tools):
        raise RuntimeError("503 UNAVAILABLE")
        yield  # makes this an async generator


class FlakyModel(ScriptedModel):
    """Fails on the first call, then replays its replies."""

    async def stream(self, messages, tools):
        if not self.calls:
            self.calls.append([])
            raise RuntimeError("400 tool_use_failed")
        async for chunk in super().stream(messages, tools):
            yield chunk


class FakeGuard:
    """Blocks caller turns and reply text containing a marker word, after an optional delay."""

    def __init__(self, block_input: str = "", block_output: str = "", delay: float = 0):
        self.block_input, self.block_output, self.delay = block_input, block_output, delay
        self.inputs: list[str] = []
        self.outputs: list[str] = []

    async def check_input(self, text):
        self.inputs.append(text)
        return await self._verdict(text, self.block_input, "self check input")

    async def check_output(self, user_text, reply):
        self.outputs.append(reply)
        return await self._verdict(reply, self.block_output, "self check output")

    async def _verdict(self, text, marker, rail):
        await asyncio.sleep(self.delay)
        blocked = bool(marker) and marker in text
        return Verdict(not blocked, rail if blocked else None, round(self.delay * 1000))
