"""PCM helpers. On the wire, audio is 16-bit little-endian mono: 16 kHz from the mic, 24 kHz from the TTS."""

import io
import wave

import numpy as np

MIC_RATE = 16_000


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768


def float_to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def to_wav(samples: np.ndarray, rate: int = MIC_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(float_to_pcm16(samples))
    return buffer.getvalue()


def resample(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Linear interpolation; good enough for speech going into VAD and Whisper."""
    if from_rate == to_rate or not len(samples):
        return samples
    count = round(len(samples) * to_rate / from_rate)
    positions = np.arange(count) * (from_rate / to_rate)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
