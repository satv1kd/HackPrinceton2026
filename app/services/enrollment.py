"""Enrollment takes become Talk phrases (Task 24).

Each built-in enrollment phrase recorded on the calibration page is also a
useful everyday phrase, so its takes are saved as samples of a matching Talk
phrase (``origin="enrollment"``, ``enrollment_key=<phrase id>``). Saying
"I want water" in Talk then matches immediately, and the same recordings feed
the personal sound profile.

Re-recording a take replaces that take's sample, never the other take's, and
never anything the user recorded themselves in Setup.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.phonetics.enrollment import EnrollmentPhrase
from app.services.storage import NotFoundError, PhraseStore

log = logging.getLogger(__name__)


class EnrollmentLinker:
    def __init__(self, phrase_store: PhraseStore, feature_cache=None):
        self.phrase_store = phrase_store
        self.feature_cache = feature_cache

    def ensure_phrase(self, phrase: EnrollmentPhrase):
        """The Talk phrase for *phrase*, created on first use."""
        existing = self.phrase_store.find_enrollment_phrase(phrase.id)
        if existing:
            return existing
        try:
            return self.phrase_store.create_phrase(
                text=phrase.text, emoji=phrase.emoji, origin="enrollment",
                phonemes=phrase.phonemes, enrollment_key=phrase.id)
        except ValueError:  # created concurrently by another take
            return self.phrase_store.find_enrollment_phrase(phrase.id)

    def save_take(self, phrase: EnrollmentPhrase, wav_bytes: bytes, samples, sr: int,
                  duration_sec: float, previous_sample_id: Optional[str] = None):
        """Add one take as a Talk sample; drop the sample it replaces. Returns (phrase_id, sample_id)."""
        talk_phrase = self.ensure_phrase(phrase)
        if previous_sample_id:
            self.remove_sample(talk_phrase.id, previous_sample_id)
        sample = self.phrase_store.add_sample(talk_phrase.id, wav_bytes, source="enroll",
                                              duration_sec=duration_sec)
        if self.feature_cache is not None:
            try:
                self.feature_cache.put(talk_phrase.id, sample.id, samples, sr)
            except Exception:  # features are recomputed lazily if this fails
                log.warning("could not cache features for %s/%s", talk_phrase.id, sample.id, exc_info=True)
        return talk_phrase.id, sample.id

    def remove_sample(self, phrase_id: str, sample_id: str) -> None:
        try:
            self.phrase_store.delete_sample(phrase_id, sample_id)
        except NotFoundError:
            return  # already gone (the user may have deleted it in Setup)
        if self.feature_cache is not None:
            self.feature_cache.invalidate(phrase_id, sample_id)
