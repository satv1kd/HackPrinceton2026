"""Shared service instances, built once from settings and injected into routers."""

from functools import lru_cache

from app.config import get_settings
from app.phonetics.service import PhonemeService
from app.services.audio import AudioPreprocessor
from app.services.calibration import CalibrationStore
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
def get_calibration_preprocessor() -> AudioPreprocessor:
    """Same cleaning as a Talk recording, with room for a whole sentence read
    slowly: MAX_CLIP_SEC is sized for a short phrase."""
    settings = get_settings()
    return AudioPreprocessor(settings.TRIM_TOP_DB, settings.MIN_CLIP_SEC,
                             settings.CALIBRATION_MAX_CLIP_SEC)


@lru_cache
def get_calibration_store() -> CalibrationStore:
    return CalibrationStore(get_settings().DATA_DIR)


@lru_cache
def get_feature_cache() -> FeatureCache:
    settings = get_settings()
    return FeatureCache(settings.DATA_DIR, get_extractor(settings.FEATURE_EXTRACTOR))


@lru_cache
def get_preference_store() -> PreferenceStore:
    settings = get_settings()
    return PreferenceStore(settings.DATA_DIR, Preferences(phonetic_mode=settings.PHONETIC_MODE_DEFAULT))


@lru_cache
def get_phoneme_service() -> PhonemeService:
    """One phoneme model per process; loaded in the background at startup."""
    return PhonemeService.from_settings(get_settings())


@lru_cache
def get_phonetic_readiness() -> PhoneticReadiness:
    # Checked in order; the first unmet one is the reason shown by the switch.
    from app.services.preferences import _engine_not_built

    return PhoneticReadiness([get_phoneme_service().unavailable_reason, _engine_not_built])


@lru_cache
def get_stt_backend() -> FasterWhisperBackend:
    """One speech-to-text backend per process; the model loads on first use."""
    return FasterWhisperBackend.from_settings(get_settings())


def reset_caches() -> None:
    """Forget all singletons (used by tests that change settings)."""
    for fn in (get_settings, get_store, get_preprocessor, get_feature_cache,
               get_preference_store, get_phonetic_readiness, get_stt_backend,
               get_calibration_preprocessor, get_calibration_store):
        fn.cache_clear()
    if get_phoneme_service.cache_info().currsize:
        get_phoneme_service().shutdown()
    get_phoneme_service.cache_clear()
