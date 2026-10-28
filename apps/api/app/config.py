from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives in apps/api/app/
API_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = API_ROOT.parent.parent

PROVIDERS = {
    # name: (base URL, default model, default fallbacks)
    # Groq's free tier caps tokens per day per model, so a second model keeps the demo up after the first runs out
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "openai/gpt-oss-20b"),
    "openai": (None, "gpt-5-mini", ""),
}
# Speech-to-text model per provider (same API key as the chat model)
STT_MODELS = {"groq": "whisper-large-v3-turbo", "openai": "whisper-1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(API_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # "groq" (free tier) or "openai" (paid); both go through the openai SDK
    llm_provider: str = "groq"
    groq_api_key: str = ""
    openai_api_key: str = ""
    # "" uses the provider's default from PROVIDERS
    llm_model: str = ""
    # Comma-separated models to try, in order, when the main one is rate limited. Unset uses the provider's
    # default (see PROVIDERS); "" turns fallback off
    llm_fallback_models: str | None = None
    # Reasoning models (gpt-oss, gpt-5) think before answering; "low" keeps voice replies quick
    llm_reasoning_effort: str = "low"
    # Model calls per caller turn; stops a confused model from looping on tools
    max_tool_rounds: int = 6

    # Voice: model files come from `python -m app.voice.download`
    models_dir: Path = API_ROOT / "models"
    stt_model: str = ""
    tts_voice_pt: str = "pf_dora"
    tts_voice_en: str = "af_heart"
    tts_speed: float = 1.05
    # Silero speech probability that counts as talking, and the pause that ends a caller's turn
    vad_threshold: float = 0.5
    vad_min_silence_ms: int = 600
    # How long the caller must talk over Sofia before she stops; shorter sounds ("uhum", her own echo) don't count
    barge_in_ms: int = 300
    # One JSON line per voice turn with its timings; "" turns it off
    voice_turn_log: str = str(REPO_ROOT / "data" / "voice-turns.jsonl")

    database_url: str = f"sqlite:///{(REPO_ROOT / 'data' / 'clinic.db').as_posix()}"
    clinic_timezone: str = "America/Sao_Paulo"

    # Origins allowed to call the API from a browser (the Vite dev server by default)
    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def llm_api_key(self) -> str:
        return (self.openai_api_key if self.llm_provider == "openai" else self.groq_api_key).strip()

    @property
    def llm_base_url(self) -> str | None:
        return PROVIDERS[self.llm_provider][0]

    @property
    def resolved_llm_model(self) -> str:
        return self.llm_model.strip() or PROVIDERS[self.llm_provider][1]

    @property
    def resolved_stt_model(self) -> str:
        return self.stt_model.strip() or STT_MODELS[self.llm_provider]

    @property
    def resolved_fallback_models(self) -> list[str]:
        models = PROVIDERS[self.llm_provider][2] if self.llm_fallback_models is None else self.llm_fallback_models
        return [m.strip() for m in models.split(",") if m.strip() and m.strip() != self.resolved_llm_model]


@lru_cache
def get_settings() -> Settings:
    return Settings()
