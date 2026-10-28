import json

import numpy as np
from fastapi.testclient import TestClient

from app.main import create_app
from app.voice.audio import float_to_pcm16
from app.voice.call import VoiceServices
from app.voice.stt import Transcript
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

    def __init__(self):
        self.said: list[tuple[str, str]] = []

    async def synthesize(self, text, language):
        self.said.append((text, language))
        return b"\x01\x00" * len(text)


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

    kinds = [e if isinstance(e, bytes) else e["type"] for e in events]
    kinds = ["<pcm>" if isinstance(k, bytes) else k for k in kinds]
    assert kinds[:5] == ["vad", "vad", "transcript", "tool_call", "tool_result"]
    assert kinds.count("<pcm>") == 2 and kinds[-1] == "turn_done"
    # Every audio event is followed by its PCM
    for n, kind in enumerate(kinds):
        if kind == "audio":
            assert kinds[n + 1] == "<pcm>" and len(events[n + 1]) == events[n]["bytes"]

    assert events[2] == {"type": "transcript", "text": "Tem estacionamento aí?", "language": "pt"}
    # Spoken sentence by sentence, with the price spelled out for the TTS
    assert tts.said[1:] == [("Temos manobrista por 25 reais.", "pt"), ("Posso ajudar em mais alguma coisa?", "pt")]
    done = events[-1]
    assert done["text"] == "Temos manobrista por R$ 25. Posso ajudar em mais alguma coisa?"
    assert {"stt_ms", "llm_first_text_ms", "first_audio_ms", "total_ms"} <= set(done["timings"])
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
