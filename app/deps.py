"""Shared service instances, built once from settings and injected into routers."""

from functools import lru_cache

from app.config import get_settings
from app.services.audio import AudioPreprocessor
from app.services.features import FeatureCache, get_extractor
from app.services.storage import PhraseStore


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


def reset_caches() -> None:
    """Forget all singletons (used by tests that change settings)."""
    for fn in (get_settings, get_store, get_preprocessor, get_feature_cache):
        fn.cache_clear()
