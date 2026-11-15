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
def get_profile_trainer():
    from app.phonetics.training import ProfileTrainer

    return ProfileTrainer.from_settings(get_settings(), get_calibration_store(), get_phoneme_service())


_LEXICON_UNSET = object()
_lexicon = _LEXICON_UNSET


def get_lexicon():
    """The decoder's word list (data/lexicon/words.tsv), or None.

    Built on first use when missing and the phonetics packages are installed,
    so a fresh checkout does not need a manual step.
    """
    global _lexicon
    if _lexicon is not _LEXICON_UNSET:
        return _lexicon
    import importlib.util
    import logging

    from app.phonetics.lexicon import Lexicon, build_lexicon, default_path

    settings = get_settings()
    path = default_path(settings.DATA_DIR)
    lexicon = None
    try:
        if path.exists():
            lexicon = Lexicon.load(path)
        elif importlib.util.find_spec("cmudict") and importlib.util.find_spec("wordfreq"):
            from app.phonetics.enrollment import load_enrollment_set
            from app.phonetics.lexicon import tokenize

            extra = [w for p in load_enrollment_set() for w in tokenize(p.text)]
            extra += [w for p in get_store().list_phrases() if not p.personal_word for w in tokenize(p.text)]
            lexicon = build_lexicon(settings.LEXICON_SIZE, extra)
            lexicon.save(path)
    except Exception:
        logging.getLogger(__name__).exception("could not load or build the word list")
        lexicon = None
    _lexicon = lexicon
    return lexicon


def lexicon_reason():
    return None if get_lexicon() is not None else "The word list isn't built (python scripts/build_lexicon.py)."


@lru_cache
def get_phonetic_readiness() -> PhoneticReadiness:
    # Checked in order; the first unmet one is the reason shown by the switch.
    return PhoneticReadiness([get_phoneme_service().unavailable_reason,
                              get_profile_trainer().readiness, lexicon_reason])


@lru_cache
def get_stt_backend() -> FasterWhisperBackend:
    """One speech-to-text backend per process; the model loads on first use."""
    return FasterWhisperBackend.from_settings(get_settings())


def reset_caches() -> None:
    """Forget all singletons (used by tests that change settings)."""
    for fn in (get_settings, get_store, get_preprocessor, get_feature_cache,
               get_preference_store, get_phonetic_readiness, get_stt_backend,
               get_calibration_preprocessor, get_calibration_store, get_profile_trainer):
        fn.cache_clear()
    if get_phoneme_service.cache_info().currsize:
        get_phoneme_service().shutdown()
    get_phoneme_service.cache_clear()
    global _lexicon
    _lexicon = _LEXICON_UNSET
