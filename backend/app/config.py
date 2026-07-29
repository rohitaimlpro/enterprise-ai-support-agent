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
