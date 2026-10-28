"""The LLM behind the agent, reduced to one streaming call so tests can swap in a scripted fake.

Groq and OpenAI both speak the OpenAI chat-completions API, so one adapter covers both.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from openai import AsyncOpenAI, RateLimitError

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, as the model wrote it


# One streamed piece of a reply: a text delta, or the tool calls the model made (always last)
Chunk = str | list[ToolCall]


class ChatModel(Protocol):
    def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AsyncIterator[Chunk]: ...


class OpenAIChat:
    def __init__(self, client: AsyncOpenAI, model: str, reasoning_effort: str = "", fallbacks: list[str] | None = None):
        self.client = client
        self.models = [model, *(fallbacks or [])]
        self.reasoning_effort = reasoning_effort

    async def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AsyncIterator[Chunk]:
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        for n, model in enumerate(self.models):
            try:
                response = await self.client.chat.completions.create(
                    model=model, messages=messages, tools=tools, stream=True, **extra
                )
                break
            except RateLimitError:
                if n == len(self.models) - 1:
                    raise
                log.warning("%s is rate limited, falling back to %s", model, self.models[n + 1])
        # Tool calls arrive in fragments keyed by index: id and name once, arguments piece by piece
        calls: dict[int, ToolCall] = {}
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
            for part in delta.tool_calls or []:
                call = calls.setdefault(part.index, ToolCall("", "", ""))
                call.id = part.id or call.id
                if part.function:
                    call.name += part.function.name or ""
                    call.arguments += part.function.arguments or ""
        if calls:
            yield [calls[i] for i in sorted(calls)]


@lru_cache
def get_chat_model() -> OpenAIChat:
    settings = get_settings()
    if not settings.llm_api_key:
        key = "OPENAI_API_KEY" if settings.llm_provider == "openai" else "GROQ_API_KEY"
        raise RuntimeError(f"{key} is not set. Add it to .env at the repo root.")
    # One SDK retry: a daily-quota 429 won't clear in seconds, and the fallback model is a better bet
    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, max_retries=1)
    return OpenAIChat(
        client, settings.resolved_llm_model, settings.llm_reasoning_effort, settings.resolved_fallback_models
    )
