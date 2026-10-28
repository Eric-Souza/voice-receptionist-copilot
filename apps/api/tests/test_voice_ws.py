import asyncio
import json

import numpy as np
from fastapi.testclient import TestClient

from app.main import create_app
from app.voice.audio import float_to_pcm16
from app.voice.call import VoiceServices
from app.voice.stt import Transcript
from app.voice.vad import WINDOW
from tests.fakes import ScriptedModel
from tests.test_vad import loudness, silence, tone


class FakeSTT:
    def __init__(self, *transcripts: Transcript):
        self.transcripts = list(transcripts)
        self.heard: list[np.ndarray] = []

    async def transcribe(self, audio):
        self.heard.append(audio)
        return self.transcripts.pop(0)


class FakeTTS:
    sample_rate = 24_000

    def __init__(self, seconds: float = 0):
        # How long each sentence plays for in the browser. No time by default: tests send mic audio much faster
        # than real time, and talking over audio that's still playing interrupts it
        self.seconds = seconds
        self.said: list[tuple[str, str]] = []

    async def synthesize(self, text, language):
        self.said.append((text, language))
        return b"\x01\x00" * int(self.seconds * self.sample_rate)


def receive(ws):
    """The next message: a dict for JSON events, bytes for audio."""
    message = ws.receive()
    return json.loads(message["text"]) if message.get("text") is not None else message["bytes"]


def until(ws, kind):
    """Messages up to and including the event of this type."""
    out = []
    while True:
        out.append(receive(ws))
        if isinstance(out[-1], dict) and out[-1]["type"] == kind:
            return out


def speak(ws, audio):
    pcm = float_to_pcm16(audio)
    for i in range(0, len(pcm), 3200):
        ws.send_bytes(pcm[i : i + 3200])


def test_a_spoken_turn():
    model = ScriptedModel(
        [[("get_clinic_info", {"topic": "parking"})]],
        ["Temos manobrista por R$ 25. ", "Posso ajudar em mais alguma coisa?"],
    )
    stt, tts = FakeSTT(Transcript("Tem estacionamento aí?", "pt")), FakeTTS()
    services = VoiceServices(vad=lambda: loudness, stt=stt, tts=tts)
    with TestClient(create_app(model, services)) as c, c.websocket_connect("/ws/voice?language=pt") as ws:
        assert receive(ws) == {"type": "ready", "language": "pt", "sample_rate": 24_000}
        greeting = receive(ws)
        assert greeting["type"] == "audio" and "Sofia" in greeting["text"]
        assert len(receive(ws)) == greeting["bytes"]

        speak(ws, np.concatenate([silence(0.5), tone(1), silence(1)]))
        events = until(ws, "turn_done")

    kinds = ["<pcm>" if isinstance(e, bytes) else e["type"] for e in events]
    assert kinds[:4] == ["vad", "vad", "transcript", "tool_call"]
    assert kinds.count("<pcm>") == 3 and kinds[-1] == "turn_done"
    # Every audio event is followed by its PCM
    for n, kind in enumerate(kinds):
        if kind == "audio":
            assert kinds[n + 1] == "<pcm>" and len(events[n + 1]) == events[n]["bytes"]

    assert events[2] == {"type": "transcript", "text": "Tem estacionamento aí?", "language": "pt"}
    # A filler while the tool runs, then the reply sentence by sentence, with the price spelled out for the TTS
    assert tts.said[1:] == [
        ("Só um instante.", "pt"),
        ("Temos manobrista por 25 reais.", "pt"),
        ("Posso ajudar em mais alguma coisa?", "pt"),
    ]
    assert [e["kind"] for e in events if isinstance(e, dict) and e["type"] == "audio"] == ["filler", "reply", "reply"]
    done = events[-1]
    assert done["text"] == "Temos manobrista por R$ 25. Posso ajudar em mais alguma coisa?"
    assert {"stt_ms", "llm_first_text_ms", "first_audio_ms", "total_ms"} <= set(done["timings"])
    assert done["timings"]["tools"] == ["get_clinic_info"]
    assert 1.0 < len(stt.heard[0]) / 16_000 < 2.0


def test_switches_language_and_skips_noise():
    model = ScriptedModel(["Sure, we have valet parking for 25 reais."])
    stt = FakeSTT(Transcript("", None), Transcript("Do you have parking there?", "en"))
    tts = FakeTTS()
    services = VoiceServices(vad=lambda: loudness, stt=stt, tts=tts)
    with TestClient(create_app(model, services)) as c, c.websocket_connect("/ws/voice") as ws:
        until(ws, "audio")
        receive(ws)
        speak(ws, np.concatenate([silence(0.5), tone(0.5), silence(1)]))
        noise = until(ws, "transcript")
        assert noise[-1]["text"] == ""

        speak(ws, np.concatenate([tone(1), silence(1)]))
        until(ws, "turn_done")
    assert len(model.calls) == 1
    assert tts.said[-1] == ("Sure, we have valet parking for 25 reais.", "en")


def windows(n: int, loud: bool) -> np.ndarray:
    """n VAD windows (32 ms each) of tone or silence, keeping the stream aligned to windows."""
    return tone(1)[: n * WINDOW] if loud else np.zeros(n * WINDOW, dtype=np.float32)


QUESTION = np.concatenate([windows(10, False), windows(30, True), windows(25, False)])


class StuckModel(ScriptedModel):
    """Thinks forever on its first call, so the caller can talk over it."""

    async def stream(self, messages, tools):
        if not self.calls:
            self.calls.append([dict(m) for m in messages])
            await asyncio.Event().wait()
        async for chunk in super().stream(messages, tools):
            yield chunk


def connect(model, stt, tts):
    return TestClient(create_app(model, VoiceServices(vad=lambda: loudness, stt=stt, tts=tts)))


def test_talking_over_sofia_stops_her():
    model = ScriptedModel(
        ["Temos três horários na quinta. ", "Às 8h, às 9h ou às 10h."],
        ["Claro, às 9h então."],
    )
    stt = FakeSTT(Transcript("Quero marcar uma limpeza na quinta.", "pt"), Transcript("Pode ser às 9h?", "pt"))
    tts = FakeTTS()
    with connect(model, stt, tts) as c, c.websocket_connect("/ws/voice") as ws:
        until(ws, "audio")
        receive(ws)
        tts.seconds = 5  # from here on each sentence plays for 5 s
        speak(ws, QUESTION)
        until(ws, "turn_done")

        # The first sentence is playing and the second is queued behind it when the caller talks over her
        speak(ws, np.concatenate([windows(30, True), windows(25, False)]))
        assert until(ws, "interrupt")[-1]["heard"] == "Temos três horários na quinta."
        until(ws, "turn_done")

    # The model saw only what the caller heard, marked as cut off
    assert model.calls[1][-2] == {"role": "assistant", "content": "Temos três horários na quinta.…"}
    assert model.calls[1][-1] == {"role": "user", "content": "Pode ser às 9h?"}


def test_short_sounds_dont_interrupt():
    model = ScriptedModel(["Temos três horários na quinta."])
    stt = FakeSTT(Transcript("Quero marcar uma limpeza.", "pt"))
    tts = FakeTTS()
    with connect(model, stt, tts) as c, c.websocket_connect("/ws/voice") as ws:
        until(ws, "audio")
        receive(ws)
        tts.seconds = 5
        speak(ws, QUESTION)
        until(ws, "turn_done")
        # An "uhum" (8 windows, a quarter second) while Sofia talks: long enough to be speech, too short to interrupt
        speak(ws, np.concatenate([windows(8, True), windows(25, False)]))
        events = until(ws, "vad") + until(ws, "vad")
    assert [e["speaking"] for e in events] == [True, False]
    assert len(stt.heard) == 1


def test_talking_while_sofia_thinks_resends_both_parts():
    model = StuckModel(["Claro, quinta às 8h com a Dra. Ana."])
    stt = FakeSTT(Transcript("Quero marcar uma limpeza", "pt"), Transcript("Quero marcar uma limpeza na quinta.", "pt"))
    with connect(model, stt, FakeTTS()) as c, c.websocket_connect("/ws/voice") as ws:
        until(ws, "audio")
        receive(ws)
        speak(ws, QUESTION)
        until(ws, "transcript")
        # The pause was long enough to end the turn, but the caller goes on while the model is still thinking
        speak(ws, np.concatenate([windows(20, True), windows(25, False)]))
        assert until(ws, "interrupt")[-1]["heard"] == ""
        until(ws, "turn_done")

    # Whisper got both parts together the second time, and the model saw a single caller message
    assert len(stt.heard[1]) > len(stt.heard[0]) + 40 * WINDOW
    assert [m["role"] for m in model.calls[1][1:]] == ["assistant", "user"]
    assert model.calls[1][-1]["content"] == "Quero marcar uma limpeza na quinta."
