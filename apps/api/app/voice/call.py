"""One voice call: mic audio in, the receptionist's spoken reply out.

The WebSocket protocol (/ws/voice?language=pt|en):

  client -> server   binary: mic audio, 16 kHz 16-bit mono PCM, any chunk size
  server -> client   binary: speech, 24 kHz 16-bit mono PCM, one message per sentence, each right after
                     its {"type": "audio"} event
                     JSON events:
    {"type": "ready", "language", "sample_rate"}
    {"type": "vad", "speaking": true | false}       the caller started or stopped talking
    {"type": "transcript", "text", "language"}      what Whisper heard ("" if it was just noise)
    {"type": "reply", "delta"}                      the reply text as the model streams it
    {"type": "tool_call", ...}, {"type": "tool_result", ...}
    {"type": "audio", "text", "bytes"}              the next binary message is this sentence
    {"type": "turn_done", "text", "timings"}        timings in ms, measured from the end of speech
    {"type": "error", "message"}

Half duplex for now: mic audio is dropped while a turn is being handled, and the browser stops sending while
it plays speech, so Sofia doesn't answer herself. Interrupting her (barge-in) is milestone 3.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.agent import Conversation, Receptionist
from app.config import get_settings
from app.prompts import GREETING
from app.voice.audio import pcm16_to_float
from app.voice.speech_text import SentenceSplitter, speakable
from app.voice.stt import Transcriber
from app.voice.tts import Synthesizer
from app.voice.vad import Segmenter, SpeechProb

log = logging.getLogger(__name__)

APOLOGY = {
    "pt": "Desculpe, tive um probleminha aqui. Pode repetir, por favor?",
    "en": "Sorry, I had a problem on my end. Could you say that again?",
}
# Whisper's language guess on "Sim" or "OK" is unreliable, so short utterances keep the current language
MIN_WORDS_TO_SWITCH_LANGUAGE = 3


@dataclass
class VoiceServices:
    vad: Callable[[], SpeechProb]  # a fresh scorer per call, since Silero keeps state
    stt: Transcriber
    tts: Synthesizer


def _ms(since: float) -> int:
    return round((time.perf_counter() - since) * 1000)


class VoiceCall:
    def __init__(
        self,
        receptionist: Receptionist,
        services: VoiceServices,
        language: str,
        send_json: Callable[[dict[str, Any]], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
    ):
        settings = get_settings()
        self.language = language if language in GREETING else "pt"
        self.receptionist = receptionist
        self.services = services
        self.conversation = Conversation([{"role": "assistant", "content": GREETING[self.language]}], self.language)
        self.segmenter = Segmenter(
            services.vad(), threshold=settings.vad_threshold, min_silence_ms=settings.vad_min_silence_ms
        )
        self._send_json, self._send_bytes = send_json, send_bytes
        # An audio event and its PCM must go out back to back, so all sends take this lock
        self._send_lock = asyncio.Lock()
        self.turn: asyncio.Task | None = None
        self._timings: dict[str, int] | None = None
        self._heard_at = 0.0

    @property
    def busy(self) -> bool:
        return self.turn is not None and not self.turn.done()

    async def start(self) -> None:
        await self.emit({"type": "ready", "language": self.language, "sample_rate": self.services.tts.sample_rate})
        self.turn = asyncio.create_task(self._speak(GREETING[self.language]))

    async def feed(self, data: bytes) -> None:
        if self.busy:
            return
        for event in self.segmenter.feed(pcm16_to_float(data)):
            await self.emit({"type": "vad", "speaking": event.kind == "start"})
            if event.kind == "end" and event.audio is not None:
                self.segmenter.reset()
                self.turn = asyncio.create_task(self._handle(event.audio))
                return

    async def close(self) -> None:
        if self.turn and not self.turn.done():
            self.turn.cancel()
        if self.turn:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.turn

    async def emit(self, event: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._send_json(event)

    async def _handle(self, audio: np.ndarray) -> None:
        self._heard_at = time.perf_counter()
        self._timings = timings = {}
        try:
            transcript = await self.services.stt.transcribe(audio)
        except Exception:
            log.exception("Transcription failed")
            await self.emit({"type": "error", "message": "Transcription failed"})
            await self._speak(APOLOGY[self.language])
            return
        timings["stt_ms"] = _ms(self._heard_at)
        if transcript.language and len(transcript.text.split()) >= MIN_WORDS_TO_SWITCH_LANGUAGE:
            self.language = transcript.language
        await self.emit({"type": "transcript", "text": transcript.text, "language": transcript.language})
        if not transcript.text:
            return

        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak_queue(sentences))
        splitter, reply, failed = SentenceSplitter(), "", False
        try:
            async for event in self.receptionist.respond(self.conversation, transcript.text):
                if event["type"] == "text":
                    timings.setdefault("llm_first_text_ms", _ms(self._heard_at))
                    await self.emit({"type": "reply", "delta": event["delta"]})
                    for sentence in splitter.push(event["delta"]):
                        sentences.put_nowait(sentence)
                elif event["type"] in ("tool_call", "tool_result"):
                    await self.emit(event)
                elif event["type"] == "error":
                    failed = True
                    await self.emit({"type": "error", "message": event["message"]})
                elif event["type"] == "done":
                    reply = event["text"]
            if rest := splitter.flush():
                sentences.put_nowait(rest)
            if failed or not reply:
                sentences.put_nowait(APOLOGY[self.language])
            sentences.put_nowait(None)
            await speaker
        except asyncio.CancelledError:
            speaker.cancel()
            raise
        timings["total_ms"] = _ms(self._heard_at)
        log.info("Turn timings %s", timings)
        await self.emit({"type": "turn_done", "text": reply, "timings": timings})

    async def _speak_queue(self, sentences: asyncio.Queue[str | None]) -> None:
        while (sentence := await sentences.get()) is not None:
            await self._speak(sentence)

    async def _speak(self, text: str) -> None:
        try:
            pcm = await self.services.tts.synthesize(speakable(text, self.language), self.language)
        except Exception:
            log.exception("Speech synthesis failed for %r", text)
            return
        async with self._send_lock:
            if self._timings is not None and "first_audio_ms" not in self._timings:
                self._timings["first_audio_ms"] = _ms(self._heard_at)
            await self._send_json({"type": "audio", "text": text, "bytes": len(pcm)})
            await self._send_bytes(pcm)
