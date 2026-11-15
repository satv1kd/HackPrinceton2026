"""Pluggable recognition stages (Task 17).

Recognition is a cascade: each stage looks at the same cleaned recording and
either answers or hands over to the next one.

    saved phrase (DTW)  ->  phonetic decoder  ->  speech-to-text
                           (phonetic mode on)

Only the saved-phrase stage may end the cascade with a *confident* answer
(auto-spoken). Every other stage produces text the user confirms before it is
spoken or sent: a wrong guess said out loud is worse than one extra tap.

Adding a recognizer means writing one class with ``name``, ``available``,
``enabled`` and ``run``; the router, the response schema and the client do not
change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import numpy as np

from app.models.recognition import MatchCandidate
from app.services.task_io import data_lock

log = logging.getLogger(__name__)


@dataclass
class RecognitionContext:
    attempt_id: str
    audio: np.ndarray        # cleaned 16 kHz mono float32 (Task 3 output)
    sr: int
    wav_bytes: bytes
    features: np.ndarray     # from the configured FeatureExtractor
    phonetic_mode: bool = False
    #: Outputs earlier stages leave for later ones and for the attempt record
    #: (e.g. "phrase_result", "transcript", "recognized_phonemes").
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    stage: str
    candidates: List[MatchCandidate]
    #: May this result end the cascade and be spoken without a tap? The
    #: cascade honours it only for stages with ``may_confirm = True``.
    confident: bool = False
    #: The best candidate is believable even if not confident (for DTW: within
    #: ABSOLUTE_MAX). Used to order the merged list when nobody is confident.
    plausible: bool = False
    #: Stop the cascade here without being confident: the answer is good
    #: enough not to ask later stages, but the user still confirms it.
    final: bool = False


class RecognitionStage(Protocol):
    name: str
    may_confirm: bool

    def available(self) -> bool:
        """Dependencies installed, models present, data ready."""

    def enabled(self, ctx: RecognitionContext) -> bool:
        """Whether this attempt should use the stage (e.g. phonetic mode on)."""

    def run(self, ctx: RecognitionContext) -> StageResult:
        ...


class PhraseMatchStage:
    """Whole-phrase DTW against the user's saved recordings (Task 5)."""

    name = "phrase"
    may_confirm = True

    def __init__(self, matcher, data_dir: Optional[Path] = None):
        self.matcher = matcher
        self.data_dir = Path(data_dir) if data_dir is not None else None

    def available(self) -> bool:
        return True

    def enabled(self, ctx: RecognitionContext) -> bool:
        return True

    def run(self, ctx: RecognitionContext) -> StageResult:
        # Templates are read under the shared data lock so feedback cannot
        # add/evict samples mid-match (unchanged from the pre-cascade code).
        if self.data_dir is not None:
            with data_lock(self.data_dir):
                result = self.matcher.match(ctx.features)
        else:
            result = self.matcher.match(ctx.features)
        candidates = [c.model_copy(update={"source": "phrase"}) for c in result.candidates]
        absolute_max = getattr(self.matcher, "absolute_max", float("inf"))
        plausible = bool(candidates) and candidates[0].score <= absolute_max
        stage_result = StageResult(self.name, candidates, result.confident, plausible)
        ctx.extras["phrase_result"] = stage_result
        return stage_result


class SpeechToTextStage:
    """Default speech-to-text with whole-utterance phrase substitution (Task 19).

    Transcribes the recording locally (faster-whisper). If the transcript
    matches one of the user's saved phrases, that phrase is returned - with
    its emoji/photo and exact wording - instead of the raw transcript
    ("phrase recordings are subbed in if possible"). Otherwise the transcript
    itself is offered as generated text.

    Never confident: the user confirms before anything is spoken or sent.
    Substituting phrases *inside* a longer sentence is Task 33.
    """

    name = "stt"
    may_confirm = False

    def __init__(self, backend, phrase_store, enabled: bool = True,
                 phrase_match_min: float = 85.0, no_speech_max: float = 0.6):
        self.backend, self.phrase_store = backend, phrase_store
        self._enabled = enabled
        self.phrase_match_min, self.no_speech_max = phrase_match_min, no_speech_max

    @classmethod
    def from_settings(cls, settings, backend, phrase_store) -> "SpeechToTextStage":
        return cls(backend, phrase_store, settings.STT_ENABLED,
                   settings.STT_PHRASE_MATCH_MIN, settings.STT_NO_SPEECH_MAX)

    def available(self) -> bool:
        return self._enabled and self.backend.is_available()

    def enabled(self, ctx: RecognitionContext) -> bool:
        return True

    def run(self, ctx: RecognitionContext) -> StageResult:
        phrases = list(self.phrase_store.list_phrases())
        # The user's own phrases bias the decoder towards the words they say
        # (names, "nebulizer"), which is most of the accuracy win on base.en.
        transcript = self.backend.transcribe(
            ctx.audio, ctx.sr, vocabulary=[p.text for p in phrases])
        ctx.extras["transcript"] = transcript.text
        ctx.extras["transcript_words"] = transcript.words
        # ``usable`` also rejects Whisper's stock phrases for silence; the
        # no_speech ceiling stays configurable on top of it.
        if not transcript.usable or (transcript.no_speech_prob or 0) > self.no_speech_max:
            return StageResult(self.name, [])

        matches = self._phrase_matches(transcript.text, ctx, phrases)
        candidates = [_phrase_candidate(phrase, score) for phrase, score in matches]
        exact = bool(matches) and matches[0][1] >= 100.0
        if not exact:
            candidates.append(MatchCandidate(
                type="generated", source=self.name, text=transcript.text,
                score=_logprob_score(transcript.avg_logprob)))
        # Words were heard: believable enough to lead over an unsure decoder.
        return StageResult(self.name, candidates, plausible=bool(candidates))

    def _phrase_matches(self, text: str, ctx: RecognitionContext, phrases):
        from rapidfuzz import fuzz

        from app.services.recognition import normalize_text

        spoken = normalize_text(text)
        dtw_rank = _dtw_rank(ctx)
        scored = []
        for phrase in phrases:
            target = normalize_text(phrase.text)
            if not target:
                continue
            ratio = float(fuzz.ratio(spoken, target))
            if ratio >= self.phrase_match_min:
                scored.append((phrase, ratio))
        # Best text match first; DTW's ranking breaks ties (and nudges the
        # phrase the user's own recordings also favoured).
        scored.sort(key=lambda item: (-item[1], dtw_rank.get(item[0].id, 99), item[0].id))
        return scored[:2]


def _dtw_rank(ctx: RecognitionContext) -> dict:
    result = ctx.extras.get("phrase_result")
    if result is None:
        return {}
    return {c.phrase_id: i for i, c in enumerate(result.candidates) if c.phrase_id}


def _phrase_candidate(phrase, ratio: float) -> MatchCandidate:
    return MatchCandidate(
        type="phrase", source="stt", phrase_id=phrase.id, text=phrase.text,
        emoji=phrase.emoji, image=f"/api/phrases/{phrase.id}/image" if phrase.image else None,
        score=round(1 - ratio / 100.0, 4))


def _logprob_score(avg_logprob) -> float:
    """Distance-like score (>= 0, lower = more sure) from Whisper's log-prob."""
    if avg_logprob is None or not np.isfinite(avg_logprob):
        return 1.0
    return round(max(0.0, -float(avg_logprob)), 4)


class PhoneticDecoderStage:
    """The user's own sounds (Task 30): phonemes -> profile-weighted decoding.

    Runs only when the "Learn from my sounds" switch is on and the engine is
    ready (model installed, profile built, word list present). Recognizes the
    attempt's phonemes, decodes them into the top 3 sentences with the
    personal sound profile, and:

    * a decoded sentence equal to a saved phrase comes back as that phrase;
    * when the decoder is sure (margin and cost thresholds), the cascade stops
      here (``final``) - speech-to-text is skipped - but the answer still
      needs the user's Yes before it is spoken;
    * when it is unsure, speech-to-text runs too and both are offered.
    """

    name = "phonetic"
    may_confirm = False

    def __init__(self, phoneme_service, trainer, lexicon_loader, phrase_store,
                 lm_weight: float = 0.3, max_words: int = 6,
                 margin: float = 0.15, max_cost: float = 3.0):
        self.phonemes, self.trainer = phoneme_service, trainer
        self.lexicon_loader, self.phrase_store = lexicon_loader, phrase_store
        self.lm_weight, self.max_words = lm_weight, max_words
        self.margin, self.max_cost = margin, max_cost
        self._decoder = None
        self._decoder_key = None

    @classmethod
    def from_settings(cls, settings, phoneme_service, trainer, lexicon_loader, phrase_store):
        return cls(phoneme_service, trainer, lexicon_loader, phrase_store,
                   settings.LM_WEIGHT, settings.MAX_DECODE_WORDS,
                   settings.PHONETIC_MARGIN, settings.PHONETIC_MAX_COST)

    def unavailable_reason(self):
        return (self.phonemes.unavailable_reason() or self.trainer.readiness()
                or (None if self.lexicon_loader() is not None
                    else "The word list isn't built (python scripts/build_lexicon.py)."))

    def available(self) -> bool:
        return self.unavailable_reason() is None

    def enabled(self, ctx: RecognitionContext) -> bool:
        return ctx.phonetic_mode

    def _get_decoder(self, phrases):
        from app.phonetics.decoder import PhoneticDecoder

        profile = self.trainer.current()
        lexicon = self.lexicon_loader()
        texts = tuple(sorted(p.text for p in phrases if not p.personal_word))
        key = (profile.version, id(lexicon), texts)
        if key != self._decoder_key:
            self._decoder = PhoneticDecoder(lexicon, profile, self.lm_weight, self.max_words,
                                            phrase_texts=texts, sound_bank=self.trainer.sound_bank())
            self._decoder_key = key
        return self._decoder

    def run(self, ctx: RecognitionContext) -> StageResult:
        from app.phonetics.decoder import confidence
        from app.phonetics.service import attempt_cache_id
        from app.phonetics.sound_bank import fine_features
        from app.services.recognition import normalize_text

        recognition = self.phonemes.recognize_samples(ctx.audio, ctx.sr,
                                                      cache_id=attempt_cache_id(ctx.attempt_id))
        heard = list(recognition.phonemes)
        ctx.extras["recognized_phonemes"] = heard
        if not heard:
            return StageResult(self.name, [])
        phrases = self.phrase_store.list_phrases()
        decoder = self._get_decoder(phrases)
        query = fine_features(ctx.audio, ctx.sr) if decoder.sound_bank is not None else None
        hyps = decoder.decode(heard, k=3, query_features=query)
        if not hyps:
            return StageResult(self.name, [])
        margin, cost = confidence(hyps, len(heard))
        sure = margin >= self.margin and cost <= self.max_cost
        ctx.extras["phonetic"] = {"margin": round(margin, 3) if margin != float("inf") else None,
                                  "cost": round(cost, 3), "sure": sure}
        by_text = {normalize_text(p.text): p for p in phrases if not p.personal_word}
        candidates = []
        for hyp in hyps:
            score = round(max(0.0, hyp.cost / len(heard)), 4)
            phrase = by_text.get(normalize_text(hyp.text))
            if phrase is not None:
                candidates.append(MatchCandidate(
                    type="phrase", source="phonetic", phrase_id=phrase.id, text=phrase.text,
                    emoji=phrase.emoji, image=f"/api/phrases/{phrase.id}/image" if phrase.image else None,
                    score=score))
            else:
                candidates.append(MatchCandidate(type="generated", source="phonetic",
                                                 text=_sentence_case(hyp.text), score=score))
        return StageResult(self.name, candidates, confident=False, plausible=sure, final=sure)


def _sentence_case(text: str) -> str:
    text = " ".join("I" if w == "i" else ("I" + w[1:] if w.startswith("i'") else w) for w in text.split())
    return text[:1].upper() + text[1:]
