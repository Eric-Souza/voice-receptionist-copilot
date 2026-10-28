"""Finding where the caller starts and stops talking.

SileroVAD scores each 32 ms window of 16 kHz audio for speech. Segmenter turns those scores into
"started talking" and "finished an utterance" events, keeping a little audio from before the start so the
first syllable isn't clipped.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.voice.audio import MIC_RATE

WINDOW = 512  # samples per Silero window at 16 kHz: 32 ms
WINDOW_MS = WINDOW * 1000 // MIC_RATE

SpeechProb = Callable[[np.ndarray], float]


class SileroVAD:
    """Loads the ONNX model once; each call to stream() gives a scorer with its own recurrent state."""

    CONTEXT = 64  # Silero v5 expects the last 64 samples of the previous window in front of each one

    def __init__(self, model_path: Path):
        import onnxruntime  # imported here so the text-only API doesn't need it

        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def stream(self) -> SpeechProb:
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros(self.CONTEXT, dtype=np.float32)
        rate = np.array(MIC_RATE, dtype=np.int64)

        def prob(window: np.ndarray) -> float:
            nonlocal state, context
            x = np.concatenate([context, window.astype(np.float32)])[None, :]
            out, state = self.session.run(None, {"input": x, "state": state, "sr": rate})
            context = x[0, -self.CONTEXT :]
            return float(out[0][0])

        return prob


@dataclass
class VadEvent:
    kind: str  # "start" or "end"
    audio: np.ndarray | None = None  # on "end": the utterance, or None if it was too short to be speech


class Segmenter:
    def __init__(
        self,
        prob: SpeechProb,
        threshold: float = 0.5,
        min_silence_ms: int = 600,
        min_speech_ms: int = 250,
        pre_roll_ms: int = 300,
        max_utterance_s: int = 30,
    ):
        self.prob = prob
        self.threshold = threshold
        # Hysteresis: once talking, a window has to score clearly lower to count as silence
        self.release = max(threshold - 0.15, 0.05)
        self.min_silence = max(1, min_silence_ms // WINDOW_MS)
        self.min_speech = max(1, min_speech_ms // WINDOW_MS)
        self.max_windows = max_utterance_s * 1000 // WINDOW_MS
        self.pre_roll: deque[np.ndarray] = deque(maxlen=max(1, pre_roll_ms // WINDOW_MS))
        self.pending = np.zeros(0, dtype=np.float32)
        self.frames: list[np.ndarray] = []
        self.speaking = False
        self.voiced = self.silence = 0

    def reset(self) -> None:
        """Forget any half-heard utterance (audio that arrived while the agent was busy)."""
        self.pending = np.zeros(0, dtype=np.float32)
        self.pre_roll.clear()
        self.frames, self.speaking, self.voiced, self.silence = [], False, 0, 0

    def feed(self, samples: np.ndarray) -> list[VadEvent]:
        self.pending = np.concatenate([self.pending, samples])
        events = []
        while len(self.pending) >= WINDOW:
            window, self.pending = self.pending[:WINDOW], self.pending[WINDOW:]
            p = self.prob(window)
            if not self.speaking:
                self.pre_roll.append(window)
                if p >= self.threshold:
                    self.speaking, self.voiced, self.silence = True, 1, 0
                    self.frames = list(self.pre_roll)
                    events.append(VadEvent("start"))
                continue
            self.frames.append(window)
            if p < self.release:
                self.silence += 1
            else:
                self.voiced, self.silence = self.voiced + 1, 0
            if self.silence >= self.min_silence or len(self.frames) >= self.max_windows:
                speech = self.voiced >= self.min_speech
                events.append(VadEvent("end", np.concatenate(self.frames) if speech else None))
                self.pre_roll.clear()
                self.frames, self.speaking = [], False
        return events
