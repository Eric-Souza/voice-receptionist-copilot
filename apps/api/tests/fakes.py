import json
from typing import Any

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
