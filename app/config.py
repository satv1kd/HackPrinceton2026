"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env also holds Taskmaster/LLM keys that the app doesn't use.
        extra="ignore",
    )

    DATA_DIR: Path = Path("data")

    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"
    PHOTON_SERVICE_URL: str = "http://localhost:3001"

    FEATURE_EXTRACTOR: str = "mfcc"
    MARGIN: float = 0.15
    ABSOLUTE_MAX: float = 50.0
    MAX_SAMPLES_PER_PHRASE: int = 10

    TRIM_TOP_DB: float = 20
    MIN_CLIP_SEC: float = 0.3
    MAX_CLIP_SEC: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
