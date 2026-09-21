"""
All configuration in one place, read from environment variables / .env.

Using pydantic-settings means every setting is typed and validated on
startup -- if GOOGLE_API_KEY is missing, the app fails fast with a clear
error instead of crashing later mid-request.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    google_api_key: str = "test-key"
    llm_model: str = "gemini-2.5-flash"

    # Voice: Groq Whisper (speech-to-text) + Azure Neural TTS (text-to-speech).
    # Both are optional at startup -- the /voice endpoint reports a clear,
    # recoverable error to the client instead of crashing the app if a key
    # is missing, so text chat keeps working without them.
    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3-turbo"
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    azure_tts_voice: str = "en-US-AvaMultilingualNeural"
    # Azure *Custom* Neural Voice: set the deployment's endpoint id and put the
    # custom voice's name in AZURE_TTS_VOICE. If the custom endpoint fails
    # (deployment suspended, wrong id, quota) speech falls back to the
    # prebuilt voice below instead of going silent.
    azure_tts_endpoint_id: str = ""
    azure_tts_fallback_voice: str = "hi-IN-SwaraNeural"
    # Voices a user may pick in the UI (comma-separated Azure voice names). Only
    # names listed here -- plus AZURE_TTS_VOICE itself -- are accepted from a client.
    azure_tts_voices: str = (
        "hi-IN-SwaraNeural,hi-IN-KavyaNeural,hi-IN-AartiNeural,hi-IN-AnanyaNeural,"
        "hi-IN-MadhurNeural,hi-IN-AaravNeural,hi-IN-KunalNeural,"
        "en-IN-NeerjaIndicNeural,en-IN-AartiIndicNeural,en-IN-PrabhatIndicNeural,en-IN-ArjunIndicNeural"
    )
    # SSML language tag. Prebuilt voice names encode it (hi-IN-...); a custom
    # voice name usually doesn't, so set it explicitly (default hi-IN).
    azure_tts_locale: str = ""

    # Language the assistant speaks/writes: "hinglish" (Hindi-English mix in
    # Roman script) or "english". Applies to text chat and voice.
    assistant_language: str = "hinglish"
    # Force Whisper's language ("hi", "en", ...); empty = auto-detect.
    groq_stt_language: str = ""

    # Voice session limits (cost + abuse protection, see voice_router.py)
    voice_max_concurrent_sessions: int = 20
    voice_max_session_seconds: int = 1800
    voice_idle_seconds: int = 300
    voice_max_utterance_seconds: int = 30
    voice_min_utterance_ms: int = 300
    voice_max_stt_failures: int = 3
    # Longest answer that is read aloud (characters); the rest stays on screen.
    # 0 = no limit. ~450 chars is roughly 30 seconds of speech.
    voice_max_spoken_chars: int = 450

    # Database
    database_url: str = "postgresql://support_app:support_app_password@localhost:5432/support_agent"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # Auth
    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Chroma
    chroma_persist_dir: str = "./chroma_data"

    # Observability
    log_level: str = "INFO"

    # Misc
    conversation_history_cache_size: int = 20


@lru_cache
def get_settings() -> Settings:
    """Cached so we parse the environment once, not on every request."""
    return Settings()
