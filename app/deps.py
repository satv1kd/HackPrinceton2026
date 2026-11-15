"""Shared service instances, built once from settings and injected into routers."""

from functools import lru_cache

from app.config import get_settings
from app.services.audio import AudioPreprocessor
from app.services.features import FeatureCache, get_extractor
from app.services.preferences import PhoneticReadiness, Preferences, PreferenceStore
from app.services.storage import PhraseStore
from app.stt import FasterWhisperBackend


@lru_cache
def get_store() -> PhraseStore:
    return PhraseStore(get_settings().DATA_DIR)


@lru_cache
def get_preprocessor() -> AudioPreprocessor:
    return AudioPreprocessor.from_settings(get_settings())


@lru_cache
def get_feature_cache() -> FeatureCache:
    settings = get_settings()
    return FeatureCache(settings.DATA_DIR, get_extractor(settings.FEATURE_EXTRACTOR))


@lru_cache
def get_preference_store() -> PreferenceStore:
    settings = get_settings()
    return PreferenceStore(settings.DATA_DIR, Preferences(phonetic_mode=settings.PHONETIC_MODE_DEFAULT))


@lru_cache
def get_phonetic_readiness() -> PhoneticReadiness:
    return PhoneticReadiness()


@lru_cache
def get_stt_backend() -> FasterWhisperBackend:
    """One speech-to-text backend per process; the model loads on first use."""
    return FasterWhisperBackend.from_settings(get_settings())


def reset_caches() -> None:
    """Forget all singletons (used by tests that change settings)."""
    for fn in (get_settings, get_store, get_preprocessor, get_feature_cache,
               get_preference_store, get_phonetic_readiness, get_stt_backend):
        fn.cache_clear()
