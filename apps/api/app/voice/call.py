"""One voice call: mic audio in, the receptionist's spoken reply out.

The WebSocket protocol (/ws/voice?language=pt|en):

  client -> server   binary: mic audio, 16 kHz 16-bit mono PCM, any chunk size, sent all the time
  server -> client   binary: speech, 24 kHz 16-bit mono PCM, one message per sentence, each right after
                     its {"type": "audio"} event
                     JSON events:
    {"type": "ready", "language", "sample_rate"}
    {"type": "vad", "speaking": true | false}       the caller started or stopped talking
    {"type": "transcript", "text", "language"}      what Whisper heard ("" if it was just noise)
    {"type": "reply", "delta"}                      the reply text as the model streams it
    {"type": "tool_call", ...}, {"type": "tool_result", ...}
    {"type": "audio", "text", "bytes", "kind"}      the next binary message is this sentence; kind is "reply",
                                                    "greeting", "apology" or "filler" (a "one moment" said
                                                    while a tool runs)
    {"type": "interrupt", "heard"}                  the caller talked over Sofia: stop playback now
    {"type": "turn_done", "text", "timings"}        timings in ms, measured from the end of speech
    {"type": "error", "message"}

Barge-in: the server keeps listening while Sofia thinks and talks. Once the caller has talked over her for
barge_in_ms, the reply is cancelled, the browser is told to stop playing, and the history keeps only the
sentences that had started playing. The browser's echo cancellation keeps Sofia from interrupting herself.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.agent import Conversation, Receptionist
from app.config import get_settings
from app.prompts import GREETING
from app.voice.audio import pcm16_to_float
from app.voice.speech_text import SentenceSplitter, speakable
from app.voice.stt import Transcriber
from app.voice.tts import Synthesizer
from app.voice.vad import WINDOW_MS, Segmenter, SpeechProb

log = logging.getLogger(__name__)

APOLOGY = {
    "pt": "Desculpe, tive um probleminha aqui. Pode repetir, por favor?",
    "en": "Sorry, I had a problem on my end. Could you say that again?",
}
# Said when the model goes to a tool before saying anything, so the caller isn't left in silence
FILLERS = {
    "pt": ["Só um instante.", "Um momento.", "Deixa eu ver."],
    "en": ["One moment.", "Let me check.", "Just a second."],
}
# Whisper's language guess on "Sim" or "OK" is unreliable, so short utterances keep the current language
MIN_WORDS_TO_SWITCH_LANGUAGE = 3


@dataclass
class VoiceServices:
    vad: Callable[[], SpeechProb]  # a fresh scorer per call, since Silero keeps state
    stt: Transcriber
    tts: Synthesizer


@dataclass
class Sent:
    starts: float  # when the browser should start playing it (estimated)
    text: str
    kind: str  # "reply" (the model's words), "greeting", "filler" or "apology"


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
        self.barge_in_windows = max(1, settings.barge_in_ms // WINDOW_MS)
        self.turn_log = Path(settings.voice_turn_log) if settings.voice_turn_log else None
        self._send_json, self._send_bytes = send_json, send_bytes
        # An audio event and its PCM must go out back to back, so all sends take this lock
        self._send_lock = asyncio.Lock()
        self.turn: asyncio.Task | None = None
        # What has been sent for the current turn, and when the browser will be done playing it
        self.sent: list[Sent] = []
        self.playing_until = 0.0
        self.replying = False  # the receptionist has started on the current turn
        self.reply_done = True
        self.utterance: np.ndarray | None = None  # what the caller said this turn
        self.carried: np.ndarray | None = None  # an utterance cut off before Sofia said anything, to resend
        self.speech_started = 0.0
        self.barged = False  # the caller's current utterance has already interrupted Sofia
        self.fillers = 0
        self._timings: dict[str, Any] | None = None
        self._heard_at = 0.0

    @property
    def sofia_active(self) -> bool:
        """Working on a reply, or still talking in the browser.

        Tracked with flags rather than the turn task being done: the task finishes a little after its last
        message goes out, and audio the caller sends in between mustn't count as talking over her.
        """
        return not self.reply_done or time.perf_counter() < self.playing_until

    async def start(self) -> None:
        await self.emit({"type": "ready", "language": self.language, "sample_rate": self.services.tts.sample_rate})
        self.turn = asyncio.create_task(self._speak(GREETING[self.language], "greeting"))

    async def feed(self, data: bytes) -> None:
        for event in self.segmenter.feed(pcm16_to_float(data)):
            await self.emit({"type": "vad", "speaking": event.kind == "start"})
            if event.kind == "start":
                self.speech_started, self.barged = time.perf_counter(), False
            elif event.audio is not None and not (self.sofia_active and not self.barged):
                # (While Sofia is active, speech too short to interrupt her is a "uhum" or her own echo)
                self._start_turn(event.audio)
        talking_over = self.segmenter.speaking and self.segmenter.voiced >= self.barge_in_windows
        if talking_over and not self.barged and self.sofia_active:
            await self._barge_in()

    async def close(self) -> None:
        await self._cancel_turn()

    async def emit(self, event: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._send_json(event)

    async def _cancel_turn(self) -> None:
        if self.turn and not self.turn.done():
            self.turn.cancel()
        if self.turn:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.turn

    async def _barge_in(self) -> None:
        self.barged = True
        await self._cancel_turn()
        # A sentence counts as heard if it had started playing when the caller started talking
        heard = [s for s in self.sent if s.starts < self.speech_started]
        heard_all = self.reply_done and len(heard) == len(self.sent)
        heard_text = " ".join(s.text for s in heard if s.kind == "reply")
        self.playing_until = 0.0
        await self.emit({"type": "interrupt", "heard": heard_text})
        if self.replying and not heard_all and self.conversation.interrupt(heard_text):
            self.carried = self.utterance
        elif not self.replying and self.utterance is not None and not self.reply_done:
            self.carried = self.utterance  # cut off while transcribing
        if self._timings is not None and not self.reply_done:
            self._log_turn(self._timings, interrupted=True)
        self.reply_done = True
        log.info("Caller interrupted; they heard %r", heard_text)

    def _start_turn(self, audio: np.ndarray) -> None:
        if self.carried is not None:
            audio, self.carried = np.concatenate([self.carried, audio]), None
        self._heard_at = time.perf_counter()
        self._timings = {}
        self.sent, self.replying, self.reply_done, self.utterance = [], False, False, audio
        self.turn = asyncio.create_task(self._handle(audio, self._timings))

    async def _handle(self, audio: np.ndarray, timings: dict[str, Any]) -> None:
        try:
            transcript = await self.services.stt.transcribe(audio)
        except Exception:
            log.exception("Transcription failed")
            await self.emit({"type": "error", "message": "Transcription failed"})
            await self._speak(APOLOGY[self.language], "apology")
            self.reply_done = True
            return
        timings["stt_ms"] = _ms(self._heard_at)
        if transcript.language and len(transcript.text.split()) >= MIN_WORDS_TO_SWITCH_LANGUAGE:
            self.language = transcript.language
        if not transcript.text:
            self.reply_done = True
        await self.emit({"type": "transcript", "text": transcript.text, "language": transcript.language})
        if not transcript.text:
            return

        sentences: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak_queue(sentences))
        splitter, reply, failed, tools = SentenceSplitter(), "", False, []
        self.replying = True
        try:
            async with contextlib.aclosing(self.receptionist.respond(self.conversation, transcript.text)) as events:
                async for event in events:
                    if event["type"] == "text":
                        timings.setdefault("llm_first_text_ms", _ms(self._heard_at))
                        await self.emit({"type": "reply", "delta": event["delta"]})
                        for sentence in splitter.push(event["delta"]):
                            sentences.put_nowait((sentence, "reply"))
                    elif event["type"] in ("tool_call", "tool_result"):
                        if event["type"] == "tool_call":
                            tools.append(event["name"])
                            if "llm_first_text_ms" not in timings and len(tools) == 1:
                                sentences.put_nowait((self._filler(), "filler"))
                        await self.emit(event)
                    elif event["type"] == "error":
                        failed = True
                        await self.emit({"type": "error", "message": event["message"]})
                    elif event["type"] == "done":
                        reply = event["text"]
            if rest := splitter.flush():
                sentences.put_nowait((rest, "reply"))
            if failed or not reply:
                sentences.put_nowait((APOLOGY[self.language], "apology"))
            sentences.put_nowait(None)
            await speaker
        except asyncio.CancelledError:
            speaker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await speaker
            raise
        self.reply_done = True
        timings["total_ms"] = _ms(self._heard_at)
        timings["tools"] = tools
        self._log_turn(timings, interrupted=False)
        await self.emit({"type": "turn_done", "text": reply, "timings": timings})

    def _filler(self) -> str:
        options = FILLERS[self.language]
        self.fillers += 1
        return options[(self.fillers - 1) % len(options)]

    async def _speak_queue(self, sentences: asyncio.Queue[tuple[str, str] | None]) -> None:
        while (item := await sentences.get()) is not None:
            await self._speak(*item)

    async def _speak(self, text: str, kind: str) -> None:
        try:
            pcm = await self.services.tts.synthesize(speakable(text, self.language), self.language)
        except Exception:
            log.exception("Speech synthesis failed for %r", text)
            return
        async with self._send_lock:
            now = time.perf_counter()
            if self._timings is not None and "first_audio_ms" not in self._timings:
                self._timings["first_audio_ms"] = _ms(self._heard_at)
            # The browser queues each sentence after the last, so this tracks what it's playing when
            starts = max(now, self.playing_until)
            self.playing_until = starts + len(pcm) / 2 / self.services.tts.sample_rate
            self.sent.append(Sent(starts, text, kind))
            await self._send_json({"type": "audio", "text": text, "bytes": len(pcm), "kind": kind})
            await self._send_bytes(pcm)

    def _log_turn(self, timings: dict[str, Any], interrupted: bool) -> None:
        if not self.turn_log:
            return
        record = {"at": datetime.now().isoformat(timespec="seconds"), "language": self.language}
        record |= {"interrupted": interrupted, **timings}
        try:
            with self.turn_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            log.warning("Couldn't write the turn log %s", self.turn_log, exc_info=True)
