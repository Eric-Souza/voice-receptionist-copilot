"""The LLM behind the agent, reduced to one streaming call so tests can swap in a scripted fake.

Groq and OpenAI both speak the OpenAI chat-completions API, so one adapter covers both.
"""

import logging
import time
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
        # Rate-limited models are skipped until this time (monotonic), rather than asked again every call
        self.cooling_until: dict[str, float] = {}

    async def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AsyncIterator[Chunk]:
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        now = time.monotonic()
        models = [m for m in self.models if self.cooling_until.get(m, 0) <= now] or self.models[-1:]
        for n, model in enumerate(models):
            last = n == len(models) - 1
            # Only the last model gets the SDK's retry: waiting out a 429 is slower than the next model
            client = self.client if last else self.client.with_options(max_retries=0)
            try:
                response = await client.chat.completions.create(
                    model=model, messages=messages, tools=tools, stream=True, **extra
                )
                break
            except RateLimitError as exc:
                self.cooling_until[model] = time.monotonic() + _retry_after(exc)
                if last:
                    raise
                log.warning("%s is rate limited, falling back to %s", model, models[n + 1])
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


def _retry_after(exc: RateLimitError) -> float:
    """Seconds until the model takes requests again, per the 429's retry-after header (a minute if absent)."""
    try:
        return max(1.0, float(exc.response.headers.get("retry-after", 60)))
    except ValueError:
        return 60.0


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
