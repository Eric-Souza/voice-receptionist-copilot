"""What the output rail adds to each spoken piece: Kokoro and the check run together, so a piece waits only when
its check outlasts its synthesis.

    python -m evals.first_audio

Replies are cut by the real sentence splitter and synthesized by the real Kokoro (run app.voice.download first);
the checks call Groq. Prints Markdown.
"""

import asyncio
import statistics
import time

from app.config import get_settings
from app.guard import NemoGuard, is_acknowledgement
from app.voice.speech_text import SentenceSplitter, speakable
from app.voice.tts import KokoroTTS

REPLIES = [
    (
        "Hi, I'd like to book a cleaning for next Tuesday.",
        "Sure! I have Tuesday at 8 am, 10:30 am or 2 pm. Which works best?",
    ),
    ("How much is teeth whitening?", "Whitening is $450, and it takes about an hour. Would you like to book it?"),
    (
        "Is there parking at the clinic?",
        "Yes! There's free parking in the garage behind the building, on levels 2 and 3.",
    ),
    (
        "My tooth is killing me and my face is swollen.",
        "I'm so sorry. That needs to be seen today. Can you come in at 2:30 pm?",
    ),
    ("Are you open on Saturdays?", "Of course. We're open on Saturdays from 8 am to noon."),
    ("Can I book my daughter in for a checkup?", "Absolutely, a checkup is free. What day works for her?"),
]


def pieces(reply: str) -> list[str]:
    splitter = SentenceSplitter()
    out = [p for word in reply.split(" ") for p in splitter.push(word + " ")]
    return out + ([splitter.buffer.strip()] if splitter.buffer.strip() else [])


async def main() -> None:
    settings = get_settings()
    tts = KokoroTTS(
        settings.models_dir / "kokoro-v1.0.int8.onnx",
        settings.models_dir / "voices-v1.0.bin",
        settings.tts_voice,
        settings.tts_speed,
    )
    guard = NemoGuard()
    await tts.synthesize("Warming up.")
    rows, first, later = [], [], []
    for user, reply in REPLIES:
        said: list[str] = []
        for n, text in enumerate(pieces(reply)):
            start = time.perf_counter()
            await tts.synthesize(speakable(text))
            synth = round((time.perf_counter() - start) * 1000)
            if n == 0 and is_acknowledgement(text):
                check, note = None, "skipped"
            else:
                verdict = await guard.check_output(user, " ".join([*said, text]))
                check, note = verdict.ms, "passed" if verdict.allowed else f"blocked ({verdict.rail})"
                await asyncio.sleep(2)
            added = max(0, (check or 0) - synth)
            (first if n == 0 else later).append(added)
            rows.append(f"| {text} | {synth} ms | {check if check is not None else '-'} ms | {added} ms | {note} |")
            said.append(text)
    print("| Piece | Kokoro | Output check | Added | Check |")
    print("|---|---|---|---|---|")
    print("\n".join(rows))
    print(
        f"\nAdded to the first piece: median {statistics.median(first)} ms, max {max(first)} ms. Later pieces are"
        f" synthesized while the one before plays, so their median {statistics.median(later)} ms isn't heard."
    )


if __name__ == "__main__":
    asyncio.run(main())
