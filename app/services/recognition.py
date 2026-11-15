"""Recognize browser audio by running the recognition cascade (Task 17).

The service owns what every recognizer shares - cleaning the upload,
extracting features, the attempt id and the attempt record - and delegates
the actual recognition to an ordered list of stages
(``app.services.recognition_stages``).
"""

import logging
import re
from typing import List, Optional
from uuid import uuid4

import numpy as np

from app.models.recognition import MatchCandidate, MatchResult
from app.services.audio import AudioProcessingError
from app.services.recognition_stages import RecognitionContext, RecognitionStage, StageResult

log = logging.getLogger(__name__)

MAX_CANDIDATES = 3


class InvalidAudio(ValueError):
    pass


class RecognitionService:
    def __init__(self, preprocessor, extractor, attempt_store,
                 stages: List[RecognitionStage], preference_store=None):
        if not stages:
            raise ValueError('At least one recognition stage is required')
        self.preprocessor, self.extractor = preprocessor, extractor
        self.attempt_store, self.stages = attempt_store, list(stages)
        self.preference_store = preference_store

    def recognize(self, content, mime_type) -> MatchResult:
        try:
            processed = self.preprocessor.process(content, mime_type)
            audio, sr = processed.samples, processed.sr
            if sr != 16000 or audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                raise ValueError('Expected finite 16 kHz mono audio')
            if np.max(np.abs(audio)) <= 1e-6:
                raise ValueError('Silent recording')
        except (ValueError, AudioProcessingError) as exc:
            raise InvalidAudio('Could not use this recording. Please try speaking again.') from exc

        ctx = RecognitionContext(
            attempt_id=str(uuid4()), audio=audio, sr=sr, wav_bytes=processed.wav_bytes,
            features=self.extractor.extract(audio, sr), phonetic_mode=self._phonetic_mode())
        result = self._run_cascade(ctx)
        self.attempt_store.save(result.attempt_id, processed.wav_bytes, ctx.features, result,
                                recognized_phonemes=ctx.extras.get('recognized_phonemes'),
                                extras=_record(ctx))
        return result

    # -- cascade ------------------------------------------------------------

    def _phonetic_mode(self) -> bool:
        if self.preference_store is None:
            return False
        try:
            return bool(self.preference_store.get().phonetic_mode)
        except Exception:
            log.exception('Could not read preferences; phonetic mode off for this attempt')
            return False

    def _run_cascade(self, ctx: RecognitionContext) -> MatchResult:
        ran: List[StageResult] = []
        for stage in self.stages:
            try:
                if not stage.enabled(ctx) or not stage.available():
                    continue
                outcome = stage.run(ctx)
            except Exception:
                # A missing model or a broken recognizer must never stop the
                # stages after it (least of all the saved-phrase match).
                log.exception('Recognition stage %r failed; skipping it', getattr(stage, 'name', stage))
                continue
            ran.append(outcome)
            ctx.extras.setdefault('stages_run', []).append(outcome.stage)
            if outcome.confident and getattr(stage, 'may_confirm', False) and outcome.candidates:
                return self._result(ctx, outcome.candidates, confident=True)
            if outcome.final and outcome.candidates:
                break  # sure enough to skip later stages, still confirmed by the user
        return self._result(ctx, merge_unconfident(ran), confident=False)

    def _result(self, ctx, candidates: List[MatchCandidate], confident: bool) -> MatchResult:
        candidates = candidates[:MAX_CANDIDATES]
        return MatchResult(
            attempt_id=ctx.attempt_id, confident=confident, candidates=candidates,
            stage=candidates[0].source if candidates else 'phrase',
            phonetic_mode=ctx.phonetic_mode,
            phonetic_skipped_reason=self._phonetic_skipped(ctx))

    def _phonetic_skipped(self, ctx) -> Optional[str]:
        """Why the user's-sounds stage did not run, when the switch is on."""
        if not ctx.phonetic_mode or 'phonetic' in ctx.extras.get('stages_run', []):
            return None
        stage = next((s for s in self.stages if getattr(s, 'name', '') == 'phonetic'), None)
        if stage is None:
            return "Sound learning isn't part of this server."
        return getattr(stage, 'unavailable_reason', lambda: None)() or None


def merge_unconfident(results: List[StageResult]) -> List[MatchCandidate]:
    """Order candidates when no stage was confident.

    1. Saved phrases that a generated stage *also* identified (e.g. the
       transcript matched the phrase text) lead: two independent recognizers
       agree.
    2. If DTW found its best phrase *plausible* (within ABSOLUTE_MAX), that
       phrase comes next, then the generated stages' best text, then DTW's
       other phrases. Interleaving guarantees the transcript is never pushed
       out of the top 3 by DTW near-misses.
    3. Otherwise the generated text leads (cascade order: phonetic before
       speech-to-text), followed by DTW's long shots.

    Duplicates are dropped - the same saved phrase found by DTW and by
    speech-to-text appears once, in its first position.
    """
    phrase = [r for r in results if r.stage == 'phrase']
    # Generated stages: those whose answer is believable first (speech-to-text
    # that heard words; the phonetic decoder only when it passed its own
    # check), then cascade order. Each stage's best answer is placed before
    # anyone's second, so one recognizer's guesses can never crowd out another.
    others = sorted((r for r in results if r.stage != 'phrase'), key=lambda r: not r.plausible)
    phrase_candidates = [c for r in phrase for c in r.candidates]
    other_candidates = ([r.candidates[0] for r in others if r.candidates]
                        + [c for r in others for c in r.candidates[1:]])
    corroborated = [c for c in other_candidates if c.type == 'phrase']
    other_candidates = [c for c in other_candidates if c.type != 'phrase']
    if any(r.plausible for r in phrase) and phrase_candidates:
        ordered = (corroborated + phrase_candidates[:1] + other_candidates[:1]
                   + phrase_candidates[1:] + other_candidates[1:])
    else:
        ordered = corroborated + other_candidates + phrase_candidates
    seen, merged = set(), []
    for candidate in ordered:
        key = (('phrase', candidate.phrase_id) if candidate.type == 'phrase' and candidate.phrase_id
               else ('text', normalize_text(candidate.text)))
        if key in seen:
            continue
        seen.add(key)
        merged.append(candidate)
    return merged[:MAX_CANDIDATES]


def normalize_text(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace (shared by STT matching)."""
    return ' '.join(re.sub(r"[^\w\s']", ' ', text.lower()).split())


def _record(ctx: RecognitionContext) -> Optional[dict]:
    """The JSON-safe parts of a cascade run worth keeping with the attempt."""
    record = {key: ctx.extras[key] for key in ('stages_run', 'transcript') if key in ctx.extras}
    return record or None
