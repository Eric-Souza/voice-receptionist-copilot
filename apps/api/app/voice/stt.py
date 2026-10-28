"""Speech to text: Whisper on Groq (free tier), or on OpenAI, through the same SDK as the chat model."""

import re
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from openai import AsyncOpenAI

from app.clinic import CLINIC_NAME, DENTISTS
from app.voice.audio import to_wav

# Whisper "hears" these in noise and silence, from the subtitles it was trained on
_HALLUCINATIONS = re.compile(r"amara\.org|subtitles by|thanks for watching|please subscribe", re.I)
# Above this, a segment is probably not speech at all
NO_SPEECH = 0.6


@dataclass
class Transcript:
    text: str


class Transcriber(Protocol):
    async def transcribe(self, audio: np.ndarray) -> Transcript: ...


class Whisper:
    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model
        # A prompt with the names it will hear makes Whisper spell them right. It's written as a sentence a caller
        # might hear: a bare list of "Dr. ..." names leaked into transcripts ("Dr. C. 9 am, if you have it").
        names = [d.name.removeprefix("Dr. ") for d in DENTISTS.values()]
        self.prompt = f"Thanks for calling {CLINIC_NAME}. Our dentists are {', '.join(names[:-1])} and {names[-1]}."

    async def transcribe(self, audio: np.ndarray) -> Transcript:
        # Fixing the language skips Whisper's detection, which misfires on short answers like "yeah"
        result = await self.client.audio.transcriptions.create(
            model=self.model,
            file=("speech.wav", to_wav(audio), "audio/wav"),
            response_format="verbose_json",
            language="en",
            prompt=self.prompt,
        )
        return clean(result.model_dump())


def clean(result: dict) -> Transcript:
    segments = [s for s in result.get("segments") or [] if (s.get("no_speech_prob") or 0) < NO_SPEECH and s.get("text")]
    text = " ".join(s["text"].strip() for s in segments) if result.get("segments") else result.get("text", "")
    text = text.strip()
    if _HALLUCINATIONS.search(text):
        text = ""
    return Transcript(text)
