"""Custom NeMo action: Meta's Prompt Guard 2, a small classifier for jailbreaks and prompt injection.

Groq serves it through the chat-completions API; the reply is the probability that the text is an attack.
NeMo loads this file because it sits next to config.yml.
"""

from nemoguardrails.actions import action
from nemoguardrails.exceptions import LLMCallException

from app.config import get_settings
from app.guard import groq_client


@action(name="prompt_guard_check")
async def prompt_guard_check(text: str) -> bool:
    settings = get_settings()
    try:
        response = await groq_client().chat.completions.create(
            model=settings.prompt_guard_model, messages=[{"role": "user", "content": text}]
        )
    except Exception as exc:
        # NeMo swallows other errors from an action and reports the rail as having blocked, so a 429 (the free
        # tier allows 30 requests a minute) would refuse every caller. This one it passes on to app/guard.py,
        # which applies guardrails_fail_open
        raise LLMCallException(exc, detail="Prompt Guard call failed") from exc
    return float(response.choices[0].message.content or 0) >= settings.prompt_guard_threshold
