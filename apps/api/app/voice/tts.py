"""Text to speech with Kokoro-82M, run locally on the CPU through ONNX Runtime (no GPU, no API key)."""

import asyncio
import threading
from pathlib import Path
from typing import Protocol

from app.voice.audio import float_to_pcm16


class Synthesizer(Protocol):
    sample_rate: int

    async def synthesize(self, text: str) -> bytes:
        """16-bit PCM, mono, at sample_rate."""
        ...


class KokoroTTS:
    sample_rate = 24_000

    def __init__(self, model_path: Path, voices_path: Path, voice: str, speed: float = 1.0):
        from kokoro_onnx import Kokoro  # imported here so the text-only API doesn't need it

        self.kokoro = Kokoro(str(model_path), str(voices_path))
        self.voice = voice
        self.speed = speed
        # One synthesis at a time: it already uses every core, so running two only slows both down
        self.lock = threading.Lock()

    async def synthesize(self, text: str) -> bytes:
        return await asyncio.to_thread(self._run, text)

    def _run(self, text: str) -> bytes:
        with self.lock:
            samples, _ = self.kokoro.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        return float_to_pcm16(samples)
