"""Turning recordings into a sound profile (Tasks 27 and 31).

Training examples come from two places:

* **Enrollment takes** (calibration page): the phrase's known phonemes vs.
  the phonemes the model heard in that take.
* **Confirmed Talk attempts** (Task 31): when the user taps Yes / picks /
  types what they meant, that text's phonemes vs. what the model heard.

Personal words ("wawa") never train the profile: they do not follow
standard pronunciation, so aligning them would teach the wrong pattern.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Iterable, List, Optional

from app.phonetics.aligner import Alignment, align
from app.phonetics.enrollment import enrollment_phrase, load_enrollment_set
from app.phonetics.inventory import PHONEMES
from app.phonetics.profile import ProfileStore, SoundProfile
from app.phonetics.service import take_cache_id

log = logging.getLogger(__name__)

#: Share of phonemes that must have MIN_PHONEME_OCCURRENCES examples before
#: phonetic mode turns on. Not 100%: a skipped phrase should not block it, and
#: smoothing keeps sounds with few examples close to the generic prior.
READY_COVERAGE = 0.8


@dataclass
class TrainingExample:
    source: str                  # "take" | "feedback"
    key: str                     # take cache id or attempt id
    expected: List[str]
    heard: List[str]

    def alignment(self) -> Alignment:
        return align(self.expected, self.heard)


def take_examples(calibration_store, phoneme_service) -> List[TrainingExample]:
    """One example per enrollment take whose phonemes have been recognized."""
    examples = []
    for record in calibration_store.list_sentences():
        phrase = enrollment_phrase(record["id"])
        if phrase is None:
            continue  # a free-text calibration sentence: no trusted phonemes
        cache_id = take_cache_id(record["id"], int(record.get("take", 1) or 1))
        recognition = phoneme_service.cached(cache_id)
        if recognition is None or not recognition.phonemes:
            continue
        examples.append(TrainingExample("take", cache_id, phrase.phonemes, list(recognition.phonemes)))
    return examples


def build_profile(examples: Iterable[TrainingExample], alpha: float) -> SoundProfile:
    examples = list(examples)
    sources = {}
    for example in examples:
        sources[example.source] = sources.get(example.source, 0) + 1
    return SoundProfile.build((e.alignment() for e in examples), alpha=alpha, sources=sources)


def profile_readiness(profile: Optional[SoundProfile], min_occurrences: int) -> Optional[str]:
    """None when the profile is good enough to use, else a reason for the user."""
    if profile is None:
        total = len(load_enrollment_set())
        return f"Record the {total} calibration phrases first (Calibration page)."
    covered = sum(1 for p in PHONEMES if profile.examples(p) >= min_occurrences)
    if covered < READY_COVERAGE * len(PHONEMES):
        missing = len(PHONEMES) - covered
        return f"Record a few more calibration phrases — {missing} sounds still need examples."
    return None


class ProfileTrainer:
    """Rebuilds and stores the profile; serialised so rebuilds never overlap."""

    def __init__(self, data_dir, calibration_store, phoneme_service, alpha: float,
                 min_occurrences: int, feedback_examples=None):
        self.store = ProfileStore(data_dir)
        self.calibration_store = calibration_store
        self.phoneme_service = phoneme_service
        self.alpha = alpha
        self.min_occurrences = min_occurrences
        #: Callable returning extra examples from confirmed Talk attempts (Task 31).
        self.feedback_examples = feedback_examples
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings, calibration_store, phoneme_service, feedback_examples=None):
        return cls(settings.DATA_DIR, calibration_store, phoneme_service,
                   settings.PROFILE_SMOOTHING_ALPHA, settings.MIN_PHONEME_OCCURRENCES,
                   feedback_examples)

    def examples(self) -> List[TrainingExample]:
        examples = take_examples(self.calibration_store, self.phoneme_service)
        if self.feedback_examples is not None:
            examples += list(self.feedback_examples())
        return examples

    def rebuild(self) -> Optional[SoundProfile]:
        """Build from every example and make it current. None when there is no data."""
        with self._lock:
            examples = self.examples()
            if not examples:
                return None
            profile = build_profile(examples, self.alpha)
            self.store.save(profile)
            log.info("sound profile v%s built from %s", profile.version, profile.sources)
            return profile

    def current(self) -> Optional[SoundProfile]:
        return self.store.load()

    def readiness(self) -> Optional[str]:
        return profile_readiness(self.current(), self.min_occurrences)
