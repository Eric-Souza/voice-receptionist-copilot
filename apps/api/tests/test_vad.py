import wave

import numpy as np
import pytest

from app.config import get_settings
from app.voice.audio import MIC_RATE, pcm16_to_float, resample
from app.voice.vad import WINDOW, Segmenter, SileroVAD


def loudness(window: np.ndarray) -> float:
    """A stand-in for Silero: anything louder than a whisper is speech."""
    return 1.0 if np.abs(window).mean() > 0.05 else 0.0


def tone(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * MIC_RATE)) / MIC_RATE
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * MIC_RATE), dtype=np.float32)


def feed_in_chunks(segmenter: Segmenter, audio: np.ndarray, chunk: int = 1600):
    events = []
    for i in range(0, len(audio), chunk):
        events += segmenter.feed(audio[i : i + chunk])
    return events


def test_one_utterance_with_pre_roll():
    segmenter = Segmenter(loudness, min_silence_ms=600, pre_roll_ms=300)
    events = feed_in_chunks(segmenter, np.concatenate([silence(1), tone(1), silence(1)]))
    assert [e.kind for e in events] == ["start", "end"]
    seconds = len(events[1].audio) / MIC_RATE
    # 1 s of speech, plus up to 300 ms before it and the 600 ms of silence that ended it
    assert 1.8 < seconds < 2.0


def test_short_pause_does_not_end_the_utterance():
    segmenter = Segmenter(loudness, min_silence_ms=600)
    events = feed_in_chunks(segmenter, np.concatenate([tone(0.5), silence(0.3), tone(0.5), silence(1)]))
    assert [e.kind for e in events] == ["start", "end"]


def test_a_click_is_not_speech():
    segmenter = Segmenter(loudness, min_speech_ms=250)
    events = feed_in_chunks(segmenter, np.concatenate([tone(0.05), silence(1)]))
    assert [e.kind for e in events] == ["start", "end"]
    assert events[1].audio is None


def test_long_speech_is_cut():
    segmenter = Segmenter(loudness, max_utterance_s=2)
    events = feed_in_chunks(segmenter, tone(5))
    assert [e.kind for e in events][:2] == ["start", "end"]
    assert len(events[1].audio) <= 2 * MIC_RATE + WINDOW


def test_reset_forgets_half_heard_speech():
    segmenter = Segmenter(loudness)
    feed_in_chunks(segmenter, tone(0.5))
    segmenter.reset()
    assert feed_in_chunks(segmenter, silence(1)) == []


MODELS = get_settings().models_dir
SAMPLE = MODELS / "samples" / "en-us.wav"


@pytest.mark.skipif(not (MODELS / "silero_vad.onnx").exists() or not SAMPLE.exists(), reason="voice models missing")
def test_silero_finds_real_speech():
    with wave.open(str(SAMPLE)) as w:
        speech = resample(pcm16_to_float(w.readframes(w.getnframes())), w.getframerate(), MIC_RATE)
    vad = SileroVAD(MODELS / "silero_vad.onnx")
    segmenter = Segmenter(vad.stream())
    events = feed_in_chunks(segmenter, np.concatenate([silence(1), speech, silence(1.5)]))
    ends = [e for e in events if e.kind == "end" and e.audio is not None]
    assert ends, "Silero heard no speech"
    assert sum(len(e.audio) for e in ends) > 0.7 * len(speech)
    # And nothing in plain silence
    assert feed_in_chunks(Segmenter(vad.stream()), silence(2)) == []
