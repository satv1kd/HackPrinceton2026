"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Optional

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
    # A calibration sentence is a whole sentence read at the reader's own
    # pace, so it gets a far longer ceiling than a Talk phrase.
    CALIBRATION_MAX_CLIP_SEC: float = 45.0

    # --- Phonetic engine (PRD F6). Numeric defaults are starting points; the
    # evaluation task (34) replaces them with measured values.
    PHONEME_MODEL: str = "facebook/wav2vec2-lv-60-espeak-cv-ft"
    PHONEME_MODEL_OFFLINE: bool = False
    PHONEME_CACHE_DIR: Optional[Path] = None  # None means DATA_DIR / "phonemes"
    MIN_PHONEME_OCCURRENCES: int = 2
    LEXICON_SIZE: int = 5000
    MAX_DECODE_WORDS: int = 6
    PROFILE_SMOOTHING_ALPHA: float = 1.0
    LM_WEIGHT: float = 0.3
    RETRAIN_MIN_NEW_EXAMPLES: int = 10
    HOLDOUT_FRACTION: float = 0.2
    # Initial value of the user-facing "phonetic mode" switch; the live value
    # is stored in DATA_DIR/preferences.json once the user changes it.
    PHONETIC_MODE_DEFAULT: bool = False

    # --- Default speech-to-text (local faster-whisper), the fallback when no
    # saved phrase matches.
    STT_ENABLED: bool = True
    STT_MODEL_SIZE: str = "base.en"
    STT_DEVICE: str = "cpu"
    STT_COMPUTE_TYPE: str = "int8"
    # Never download the model at request time (pre-fetch it before a demo).
    STT_OFFLINE: bool = False
    # rapidfuzz score (0-100) at which a transcript counts as a saved phrase.
    STT_PHRASE_MATCH_MIN: float = 85.0
    # Segments faster-whisper marks as more likely silence than speech.
    STT_NO_SPEECH_MAX: float = 0.6
    # Measured on a 12-thread laptop CPU, per ~2 s clip:
    #   base.en,  4 threads, greedy  ~1.2 s   (this default)
    #   small.en, 4 threads, greedy  ~3.7 s   (try if accuracy falls short)
    # More threads was slower, not faster: the 30 s encoder pass dominates.
    STT_CPU_THREADS: int = 4

    @property
    def phoneme_cache_dir(self) -> Path:
        return self.PHONEME_CACHE_DIR or self.DATA_DIR / "phonemes"


@lru_cache
def get_settings() -> Settings:
    return Settings()
