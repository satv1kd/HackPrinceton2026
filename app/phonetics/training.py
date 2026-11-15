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
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable, List, Optional

from app.phonetics.aligner import Alignment, align
from app.phonetics.enrollment import enrollment_phrase, load_enrollment_set
from app.phonetics.inventory import PHONEMES
from app.phonetics.profile import ProfileStore, SoundProfile
from app.phonetics.service import take_cache_id
from app.phonetics.sound_bank import SoundBank

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
    text: str = ""               # the words meant (for decoding evaluation)

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
        examples.append(TrainingExample("take", cache_id, phrase.phonemes, list(recognition.phonemes),
                                        phrase.text))
    return examples


def is_holdout(attempt_id: str, fraction: float) -> bool:
    """Stable split: the same attempt is always held out (or never)."""
    import hashlib

    bucket = int(hashlib.sha1(attempt_id.encode()).hexdigest()[:8], 16) % 1000
    return bucket < fraction * 1000


def feedback_examples(data_dir, phrase_store, lexicon_loader) -> List[TrainingExample]:
    """Confirmed Talk attempts with recognized phonemes and a label we can pronounce.

    Phrase labels use the phrase's phonemes; typed / generated text uses the
    word list. Personal words and labels with unknown words are skipped. Undone
    feedback is already gone from ``active_feedback``.
    """
    from app.services.feedback import active_feedback, read_events
    from app.services.storage import NotFoundError

    lexicon = lexicon_loader()
    out = []
    for attempt_id, event in active_feedback(read_events(Path(data_dir) / "feedback.jsonl")).items():
        heard, label = event.get("recognized_phonemes"), event.get("label")
        if not heard or not label:
            continue
        expected, text = None, ""
        if label.get("type") == "phrase":
            try:
                phrase = phrase_store.get_phrase(label["phrase_id"])
            except (NotFoundError, KeyError):
                continue
            if phrase is None or phrase.personal_word:
                continue
            text = phrase.text
            expected = phrase.phonemes or (lexicon.flat_phonemes(text) if lexicon else None)
        elif label.get("type") == "text":
            text = label.get("text", "")
            expected = lexicon.flat_phonemes(text) if lexicon else None
        if expected:
            out.append(TrainingExample("feedback", attempt_id, list(expected), list(heard), text))
    return out


def take_segments(record: dict, calibration_store, phoneme_service):
    """(cache id, audio, sr, phoneme segments) for one recognized enrollment take, else None."""
    from app.phonetics.recognizer import read_pcm_wav
    from app.phonetics.segmenter import force_align

    phrase = enrollment_phrase(record["id"])
    if phrase is None:
        return None
    cache_id = take_cache_id(record["id"], int(record.get("take", 1) or 1))
    recognition = phoneme_service.cached(cache_id, want_posteriors=True)
    path = calibration_store.resolve(record.get("audio_path") or "")
    if (recognition is None or recognition.posteriors is None or not recognition.posterior_labels
            or not recognition.frame_rate_hz or path is None):
        return None
    try:
        audio, sr = read_pcm_wav(path)
    except Exception:
        return None
    segments = force_align(recognition.posteriors, recognition.posterior_labels, phrase.phonemes,
                           recognition.frame_rate_hz, duration_sec=len(audio) / sr)
    return cache_id, audio, sr, segments


def build_sound_bank(calibration_store, phoneme_service) -> SoundBank:
    """Cut every recognized enrollment take into phoneme units (Task 28)."""
    bank = SoundBank()
    for record in calibration_store.list_sentences():
        found = take_segments(record, calibration_store, phoneme_service)
        if found is not None:
            cache_id, audio, sr, segments = found
            bank.add_take(cache_id, audio, sr, segments)
    bank.finalize()
    return bank


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
                 min_occurrences: int, feedback_examples=None, lexicon_loader=None,
                 retrain_after: int = 10, holdout_fraction: float = 0.2, lm_weight: float = 0.3,
                 max_words: int = 6, refresh_delay: float = 1.5):
        self.store = ProfileStore(data_dir)
        self.calibration_store = calibration_store
        self.phoneme_service = phoneme_service
        self.alpha = alpha
        self.min_occurrences = min_occurrences
        #: Callable returning extra examples from confirmed Talk attempts (Task 31).
        self.feedback_examples = feedback_examples
        self.lexicon_loader = lexicon_loader
        self.retrain_after, self.holdout_fraction = retrain_after, holdout_fraction
        self.lm_weight, self.max_words = lm_weight, max_words
        self.log_path = Path(data_dir) / "profile" / "retrain_log.json"
        self._retraining = threading.Lock()
        self.bank_dir = Path(data_dir) / "sound_bank"
        self._bank: Optional[SoundBank] = None
        self._lock = threading.Lock()
        #: Takes saved or deleted since the last refresh, and the timer that
        #: batches them (a burst of saves costs one refresh, not one each).
        self.refresh_delay = refresh_delay
        self._changed: set = set()
        self._timer: Optional[threading.Timer] = None
        self._pending = threading.Lock()

    @classmethod
    def from_settings(cls, settings, calibration_store, phoneme_service, feedback_examples=None,
                      lexicon_loader=None):
        return cls(settings.DATA_DIR, calibration_store, phoneme_service,
                   settings.PROFILE_SMOOTHING_ALPHA, settings.MIN_PHONEME_OCCURRENCES,
                   feedback_examples, lexicon_loader, settings.RETRAIN_MIN_NEW_EXAMPLES,
                   settings.HOLDOUT_FRACTION, settings.LM_WEIGHT, settings.MAX_DECODE_WORDS)

    # -- examples -------------------------------------------------------------

    def _feedback(self) -> List[TrainingExample]:
        return list(self.feedback_examples()) if self.feedback_examples is not None else []

    def split_feedback(self):
        """(training, held-out) feedback examples; held-out ones never train."""
        train, holdout = [], []
        for example in self._feedback():
            (holdout if is_holdout(example.key, self.holdout_fraction) else train).append(example)
        return train, holdout

    def examples(self) -> List[TrainingExample]:
        return take_examples(self.calibration_store, self.phoneme_service) + self.split_feedback()[0]

    # -- building -------------------------------------------------------------

    def _build(self, examples) -> SoundProfile:
        profile = build_profile(examples, self.alpha)
        profile.trained_on = sorted(e.key for e in examples if e.source == "feedback")
        return profile

    def rebuild(self) -> Optional[SoundProfile]:
        """Build from every example and make it current. None when there is no data."""
        with self._lock:
            examples = self.examples()
            if not examples:
                return None
            profile = self._build(examples)
            self.store.save(profile)
            try:
                bank = build_sound_bank(self.calibration_store, self.phoneme_service)
                bank.save(self.bank_dir)
                self._bank = bank
            except Exception:  # the profile is still useful without the bank
                log.exception("could not build the sound bank")
            log.info("sound profile v%s built from %s", profile.version, profile.sources)
            return profile

    # -- after a calibration take is saved or deleted ----------------------------

    def take_changed(self, cache_id: str) -> None:
        """A take was (re)recognized or deleted: refresh the profile and bank soon.

        Runs on a timer thread, never on the phoneme model's worker, so Talk
        recognitions are not queued behind it; takes that change within
        ``refresh_delay`` of each other are handled in one refresh.
        """
        with self._pending:
            self._changed.add(cache_id)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.refresh_delay, self.flush)
            self._timer.daemon = True
            self._timer.name = "profile-refresh"
            self._timer.start()

    def flush(self) -> Optional[SoundProfile]:
        """Apply the pending take changes now (the timer calls this; tests may too)."""
        with self._pending:
            changed, self._changed = self._changed, set()
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if not changed:
            return None
        try:
            return self._refresh(changed)
        except Exception:
            log.exception("could not refresh the sound profile")
            return None

    def _refresh(self, changed: set) -> Optional[SoundProfile]:
        with self._lock:
            examples = self.examples()
            if not examples:
                return None
            profile = self._build(examples)
            self.store.save(profile)
            try:
                self._update_bank(changed)
            except Exception:  # the profile is still useful without the bank
                log.exception("could not update the sound bank")
            log.info("sound profile v%s built from %s (%d take(s) changed)",
                     profile.version, profile.sources, len(changed))
            return profile

    def _update_bank(self, changed: set) -> None:
        bank = self.sound_bank()
        if bank is None or not bank.complete:
            bank = build_sound_bank(self.calibration_store, self.phoneme_service)
        else:
            records = {take_cache_id(r["id"], int(r.get("take", 1) or 1)): r
                       for r in self.calibration_store.list_sentences()}
            for cache_id in sorted(changed):
                found = (take_segments(records[cache_id], self.calibration_store, self.phoneme_service)
                         if cache_id in records else None)
                if found is None:
                    bank = bank.update(cache_id)            # deleted, or not recognized yet
                else:
                    _, audio, sr, segments = found
                    bank = bank.update(cache_id, audio, sr, segments)
        bank.save(self.bank_dir)
        self._bank = bank

    def sound_bank(self) -> Optional[SoundBank]:
        if self._bank is None:
            self._bank = SoundBank.load(self.bank_dir)
        return self._bank

    def current(self) -> Optional[SoundProfile]:
        return self.store.load()

    # -- retraining from feedback (Task 31) --------------------------------------

    def pending_counts(self) -> dict:
        """New (not yet learned) training examples per intended phoneme."""
        current = self.current()
        seen = set(current.trained_on) if current else set()
        counts: dict = {}
        for example in self.split_feedback()[0]:
            if example.key in seen:
                continue
            for phoneme in example.expected:
                counts[phoneme] = counts.get(phoneme, 0) + 1
        return counts

    def due(self) -> bool:
        pending = self.pending_counts()
        return bool(pending) and max(pending.values()) >= self.retrain_after

    def on_feedback(self) -> bool:
        """Called after each Yes/No/correction. Starts a background retrain when due."""
        if self.current() is None or not self.due() or self._retraining.locked():
            return False
        threading.Thread(target=self.retrain, kwargs={"trigger": "feedback"}, daemon=True,
                         name="profile-retrain").start()
        return True

    def evaluate(self, profile: SoundProfile, examples: List[TrainingExample]) -> Optional[dict]:
        """Decode each held-out attempt with *profile*; top-1 / top-3 sentence accuracy."""
        from app.phonetics.decoder import PhoneticDecoder
        from app.phonetics.lexicon import tokenize

        lexicon = self.lexicon_loader() if self.lexicon_loader else None
        usable = [e for e in examples if e.text]
        if lexicon is None or not usable:
            return None
        decoder = PhoneticDecoder(lexicon, profile, self.lm_weight, self.max_words)
        top1 = top3 = 0
        for example in usable:
            want = tuple(tokenize(example.text))
            got = [h.words for h in decoder.decode(example.heard, k=3)]
            top1 += bool(got) and got[0] == want
            top3 += want in got
        n = len(usable)
        return {"n": n, "top1": round(top1 / n, 4), "top3": round(top3 / n, 4)}

    def retrain(self, trigger: str = "manual") -> dict:
        """Rebuild with all new feedback; keep it only if held-out accuracy does not drop."""
        if not self._retraining.acquire(blocking=False):
            return {"status": "busy"}
        try:
            current = self.current()
            examples = self.examples()
            if not examples:
                return {"status": "no_data"}
            _train, holdout = self.split_feedback()
            candidate = self._build(examples)
            before = self.evaluate(current, holdout) if current else None
            after = self.evaluate(candidate, holdout)
            better = (before is None or after is None
                      or (after["top3"], after["top1"]) >= (before["top3"], before["top1"]))
            # The sound bank comes from calibration takes only, so feedback
            # never changes it: no need to rebuild it here.
            with self._lock:
                saved = self.store.save(candidate, activate=better)
            entry = {"at": saved.created_at, "trigger": trigger, "candidate_version": saved.version,
                     "promoted": better, "current_version": self.store.current_version(),
                     "holdout": len(holdout), "before": before, "after": after,
                     "learned_from": saved.sources}
            self._log(entry)
            log.info("profile retrain (%s): v%s %s", trigger, saved.version,
                     "promoted" if better else "kept previous")
            return {"status": "promoted" if better else "rolled_back", **entry}
        finally:
            self._retraining.release()

    def _log(self, entry: dict) -> None:
        import json

        from app.services.storage import atomic_write_json
        try:
            history = json.loads(self.log_path.read_text())
        except (OSError, ValueError):
            history = []
        atomic_write_json(self.log_path, (history + [entry])[-50:])

    def last_retrain(self) -> Optional[dict]:
        import json
        try:
            history = json.loads(self.log_path.read_text())
        except (OSError, ValueError):
            return None
        return history[-1] if history else None

    def readiness(self) -> Optional[str]:
        return profile_readiness(self.current(), self.min_occurrences)
