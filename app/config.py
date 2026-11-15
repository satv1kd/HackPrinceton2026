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
    PHOTON_TIMEOUT_SEC: float = 10.0

    FEATURE_EXTRACTOR: str = "mfcc"
    MARGIN: float = 0.15
    # Path-normalized cosine distance is in [0, 2]; tune on real recordings.
    ABSOLUTE_MAX: float = 0.75
    MAX_SAMPLES_PER_PHRASE: int = 10
    DTW_METRIC: str = 'cosine'
    UNDO_WINDOW_SECONDS: float = 10

    TRIM_TOP_DB: float = 20
    MIN_CLIP_SEC: float = 0.3
    MAX_CLIP_SEC: float = 10.0

    # Talk-mode transcription (local faster-whisper; audio never leaves the
    # machine). Measured on a 12-thread laptop CPU, per ~2 s clip:
    #   base.en, 4 threads, greedy  ~1.2 s   (default)
    #   small.en, 4 threads, greedy ~3.7 s   (try if accuracy falls short)
    # More threads was slower, not faster; the 30 s encoder pass dominates.
    # Set TRANSCRIBE_ENABLED=false to fall back to saved-phrase matching only.
    TRANSCRIBE_ENABLED: bool = True
    WHISPER_MODEL: str = "base.en"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"
    WHISPER_CPU_THREADS: int = 4
    WHISPER_BEAM_SIZE: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
