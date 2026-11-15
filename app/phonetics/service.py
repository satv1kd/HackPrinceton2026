"""The app's one phoneme recognizer, shared by calibration and Talk (Task 25).

Wraps :class:`app.phonetics.recognizer.PhonemeRecognizer` with what the app
needs around it:

* **Optional.** ``available()`` answers from the package list and the
  Hugging Face cache, never by loading 1.2 GB of weights, so health checks
  and readiness stay instant and the app runs without the phonetics install.
* **One model, one at a time.** A single worker thread owns every inference,
  so background calibration jobs and Talk requests never run the model
  concurrently (and never load it twice).
* **Background for calibration.** A saved take is recognized after the
  upload returns; the profile builder reads the cache.
* **Warm.** ``warm_up()`` loads the weights in the background at startup, so
  the first Talk attempt in phonetic mode is not the one that waits.

Cache ids are flat strings: ``cal-<phrase>-t<take>`` for calibration takes,
``attempt-<uuid>`` for Talk attempts.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from app.phonetics.recognizer import (
    ModelUnavailableError,
    PhonemeRecognition,
    PhonemeRecognizer,
    PhonemeRecognizerError,
)

log = logging.getLogger(__name__)


def take_cache_id(phrase_id: str, take: int) -> str:
    return f"cal-{phrase_id}-t{int(take)}"


def attempt_cache_id(attempt_id: str) -> str:
    return f"attempt-{attempt_id}"


def model_in_cache(model_name: str) -> bool:
    """Whether the model's files are on disk, without loading them."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    try:
        found = try_to_load_from_cache(model_name, "config.json")
    except Exception:
        return False
    return isinstance(found, str)


class PhonemeService:
    def __init__(self, recognizer: PhonemeRecognizer, model_name: str,
                 packages_installed: Optional[Callable[[], bool]] = None,
                 model_present: Optional[Callable[[], bool]] = None, enabled: bool = True):
        self.recognizer = recognizer
        self.model_name = model_name
        self.enabled = enabled
        self._packages_installed = packages_installed or (
            lambda: all(importlib.util.find_spec(m) for m in ("torch", "transformers")))
        self._model_present = model_present or (lambda: model_in_cache(model_name))
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="phonemes")
        self._failed: Optional[str] = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings) -> "PhonemeService":
        recognizer = PhonemeRecognizer(cache_dir=settings.phoneme_cache_dir,
                                       model_name=settings.PHONEME_MODEL)
        return cls(recognizer, settings.PHONEME_MODEL, enabled=settings.PHONEMES_ENABLED)

    # -- availability -------------------------------------------------------

    def unavailable_reason(self) -> Optional[str]:
        if not self.enabled:
            return "The sound engine is turned off (PHONEMES_ENABLED=false)."
        if not self._packages_installed():
            return "The sound engine isn't installed (make install-phonetics)."
        if not self._model_present():
            return "The phoneme model isn't downloaded (scripts/download_models.py --phonemes)."
        if self._failed:
            return f"The phoneme model failed to load: {self._failed}"
        return None

    def available(self) -> bool:
        return self.unavailable_reason() is None

    @property
    def is_loaded(self) -> bool:
        return bool(getattr(self.recognizer.backend, "is_loaded", False))

    # -- work ----------------------------------------------------------------

    def _run(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ModelUnavailableError as error:
            self._failed = str(error).split(".")[0]
            raise

    def recognize_samples(self, samples: np.ndarray, sr: int, cache_id: Optional[str] = None,
                          timeout: Optional[float] = None) -> PhonemeRecognition:
        """Recognize now (on the model's thread) and wait for the result."""
        future = self._executor.submit(self._run, self.recognizer.recognize_samples,
                                       samples, sr, cache_id=cache_id)
        return future.result(timeout=timeout)

    def recognize_file_async(self, wav_path: Path, cache_id: str,
                             on_done: Optional[Callable[[PhonemeRecognition], None]] = None) -> Optional[Future]:
        """Recognize a stored take in the background, replacing any cached result."""
        if not self.available():
            return None

        def job():
            try:
                self.recognizer.clear_cache(cache_id)
                result = self._run(self.recognizer.recognize, wav_path, cache_id=cache_id)
            except (PhonemeRecognizerError, OSError) as error:
                log.warning("phoneme recognition failed for %s: %s", cache_id, error)
                return None
            if on_done:
                try:
                    on_done(result)
                except Exception:
                    log.exception("phoneme callback failed for %s", cache_id)
            return result

        return self._executor.submit(job)

    def cached(self, cache_id: str, want_posteriors: bool = False) -> Optional[PhonemeRecognition]:
        """A stored recognition, or None. Never runs the model."""
        return self.recognizer._load_cached(cache_id, want_posteriors)

    def forget(self, cache_id: str) -> None:
        self.recognizer.clear_cache(cache_id)

    def warm_up(self) -> Optional[Future]:
        """Load the weights in the background (no-op when unavailable)."""
        if not self.available() or self.is_loaded:
            return None
        load = getattr(self.recognizer.backend, "load", None)
        return self._executor.submit(self._run, load) if load else None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def wait_idle(self, timeout: float = 30.0) -> None:
        """Block until queued work is done (tests and scripts)."""
        self._executor.submit(lambda: None).result(timeout=timeout)
