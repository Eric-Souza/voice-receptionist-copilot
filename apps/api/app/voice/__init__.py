"""The voice layer: VAD, speech to text and text to speech around the same Receptionist the text API uses."""

from openai import AsyncOpenAI

from app.config import Settings, get_settings
from app.voice.call import VoiceServices

MODEL_FILES = {
    "silero_vad.onnx": "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
    "kokoro-v1.0.int8.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}


class VoiceUnavailable(RuntimeError):
    pass


def load_services(settings: Settings | None = None) -> VoiceServices:
    """Load the local models (about a second for Kokoro). Raises VoiceUnavailable with a fix if it can't."""
    from app.voice.stt import Whisper
    from app.voice.tts import KokoroTTS
    from app.voice.vad import SileroVAD

    settings = settings or get_settings()
    missing = [name for name in MODEL_FILES if not (settings.models_dir / name).exists()]
    if missing:
        raise VoiceUnavailable(f"Missing voice models ({', '.join(missing)}). Run: python -m app.voice.download")
    if not settings.llm_api_key:
        raise VoiceUnavailable("No API key for speech to text. Set GROQ_API_KEY in .env.")
    vad = SileroVAD(settings.models_dir / "silero_vad.onnx")
    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, max_retries=1)
    tts = KokoroTTS(
        settings.models_dir / "kokoro-v1.0.int8.onnx",
        settings.models_dir / "voices-v1.0.bin",
        {"pt": settings.tts_voice_pt, "en": settings.tts_voice_en},
        settings.tts_speed,
    )
    return VoiceServices(vad=vad.stream, stt=Whisper(client, settings.resolved_stt_model), tts=tts)
