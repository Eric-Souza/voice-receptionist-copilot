"""Speech to text: Whisper on Groq (free tier), or on OpenAI, through the same SDK as the chat model."""

import re
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from openai import AsyncOpenAI

from app.clinic import CLINIC_NAME, DENTISTS
from app.voice.audio import to_wav

_LANGUAGES = {"portuguese": "pt", "english": "en", "pt": "pt", "en": "en"}
# Whisper "hears" these in noise and silence, from the subtitles it was trained on
_HALLUCINATIONS = re.compile(r"amara\.org|legendas pela comunidade|subtitles by|thanks for watching", re.I)
# Above this, a segment is probably not speech at all
NO_SPEECH = 0.6


@dataclass
class Transcript:
    text: str
    language: str | None = None  # "pt", "en", or None if Whisper heard something else


class Transcriber(Protocol):
    async def transcribe(self, audio: np.ndarray) -> Transcript: ...


class Whisper:
    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model
        # A prompt with the names it will hear makes Whisper spell them right
        self.prompt = f"{CLINIC_NAME}. " + ", ".join(d.name for d in DENTISTS.values()) + "."

    async def transcribe(self, audio: np.ndarray) -> Transcript:
        # No language is passed, so the caller can switch between Portuguese and English mid-call
        result = await self.client.audio.transcriptions.create(
            model=self.model,
            file=("speech.wav", to_wav(audio), "audio/wav"),
            response_format="verbose_json",
            prompt=self.prompt,
        )
        return clean(result.model_dump())


def clean(result: dict) -> Transcript:
    segments = [s for s in result.get("segments") or [] if (s.get("no_speech_prob") or 0) < NO_SPEECH and s.get("text")]
    text = " ".join(s["text"].strip() for s in segments) if result.get("segments") else result.get("text", "")
    text = text.strip()
    if _HALLUCINATIONS.search(text):
        text = ""
    return Transcript(text, _LANGUAGES.get((result.get("language") or "").lower()))
