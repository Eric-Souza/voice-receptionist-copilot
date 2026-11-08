"""Guardrails around Sofia, with NVIDIA NeMo Guardrails.

The agent loop stays in app/agent.py; NeMo only screens what goes in and out of it, through check_async():

- Input rails, on each caller turn: Prompt Guard 2 (a jailbreak classifier) and an LLM self check against the
  clinic's policy (other patients' data, off-topic tasks, abuse), in parallel. They run alongside the main
  model's first call, so they cost time only when they finish after its first words.
- Output rail, before each sentence is spoken: an LLM self check (medical advice, prompt leaks, other patients,
  made-up prices) on the reply up to that sentence. The voice layer runs it while the sentence is synthesized.

The rails config (flows, prompts and the Prompt Guard action) is in app/rails/. The self checks use a model
with its own free-tier quota, so they don't eat into the conversation's.
"""

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from openai import AsyncOpenAI

from app.config import PROVIDERS, get_settings

log = logging.getLogger(__name__)

RAILS_DIR = Path(__file__).parent / "rails"

# What Sofia says instead, per stage
INPUT_REFUSAL = "Sorry, I can only help with appointments and questions about Brightside Dental. What can I do for you?"
OUTPUT_REPLACEMENT = (
    "I'm not able to advise on that over the phone, but one of our dentists can take a look. "
    "Would you like me to find you an appointment?"
)
# A reply often opens with one of these as its own short piece. Kokoro has it ready in about 230 ms, sooner than
# a check, so checking it would only delay the first audio. Nothing here can break a rule, and the next sentence's
# check still sees it as part of the reply so far
ACKNOWLEDGEMENTS = set(
    "sure|yes|yeah|no|okay|ok|of course|absolutely|certainly|great|perfect|got it|no problem|thanks|thank you"
    "|i'm sorry|i'm so sorry|sorry|hi|hello|alright".split("|")
)


def is_acknowledgement(text: str) -> bool:
    return text.lower().replace("’", "'").strip(" ,.!") in ACKNOWLEDGEMENTS


@dataclass
class Verdict:
    allowed: bool
    rail: str | None = None  # the rail that blocked, if any
    ms: int = 0
    error: str | None = None  # the check failed and was skipped (fail open)


class Guard(Protocol):
    async def check_input(self, text: str) -> Verdict: ...

    async def check_output(self, user_text: str, reply: str) -> Verdict: ...


@lru_cache
def groq_client() -> AsyncOpenAI:
    settings = get_settings()
    # No SDK retry: a check that waits out a 429 holds up the caller, so it fails open instead
    return AsyncOpenAI(api_key=settings.groq_api_key.strip() or "unset", base_url=PROVIDERS["groq"][0], max_retries=0)


class NemoGuard:
    def __init__(self, input_flows: list[str] | None = None):
        """input_flows: a subset of the configured input rails (the eval compares them); all of them by default."""
        # Imported here: NeMo pulls in LangChain, which tests and guard-less runs don't need
        from langchain_openai import ChatOpenAI
        from nemoguardrails import LLMRails, RailsConfig

        settings = get_settings()
        llm = ChatOpenAI(
            model=settings.guard_model,
            api_key=settings.groq_api_key.strip() or "unset",
            base_url=PROVIDERS["groq"][0],
            temperature=0,
            max_tokens=5,  # "Yes" or "No"; NeMo replaces it with the prompt's max_tokens (prompts.yml)
            max_retries=0,
            reasoning_effort=settings.guard_reasoning_effort or None,
        )
        config = RailsConfig.from_path(str(RAILS_DIR))
        if input_flows is not None:
            config.rails.input.flows = input_flows
        self.rails = LLMRails(config, llm=llm)
        self.fail_open = settings.guardrails_fail_open

    async def check_input(self, text: str) -> Verdict:
        return await self._check([{"role": "user", "content": text}], "input")

    async def check_output(self, user_text: str, reply: str) -> Verdict:
        """reply: what Sofia has said this turn, up to and including the sentence she is about to say."""
        return await self._check(
            [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}], "output"
        )

    async def _check(self, messages: list[dict[str, str]], stage: str) -> Verdict:
        from nemoguardrails.rails.llm.options import RailStatus, RailType

        start = time.perf_counter()
        try:
            result = await self.rails.check_async(messages, rail_types=[RailType(stage)])
        except Exception as exc:
            ms = round((time.perf_counter() - start) * 1000)
            log.warning("The %s guardrail check failed after %s ms", stage, ms, exc_info=True)
            # Open: a receptionist that goes silent whenever a free-tier quota runs out is worse than an unchecked
            # turn, and the prompt and tool rules still apply. Closed: refuse when unsure.
            return Verdict(self.fail_open, None if self.fail_open else "check failed", ms, str(exc))
        ms = round((time.perf_counter() - start) * 1000)
        if result.status == RailStatus.BLOCKED:
            log.info("The %s rail %r blocked %r", stage, result.rail, messages[-1]["content"])
            return Verdict(False, result.rail, ms)
        return Verdict(True, None, ms)


def get_guard() -> Guard | None:
    settings = get_settings()
    if not settings.guardrails_enabled:
        return None
    if not settings.groq_api_key.strip():
        log.warning("Guardrails are on but GROQ_API_KEY is not set, so they are off")
        return None
    return NemoGuard()
