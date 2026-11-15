"""Recognize browser audio: transcribe what was said, and check saved phrases.

Two signals, merged by :func:`merge_results`:

* **Transcript** (``Transcriber``, local Whisper): the open answer to "what
  did the person say?". Leads the result whenever it is usable.
* **Phrase match** (``DTWMatcher``): "which saved phrase does this sound
  like?". Kept as backup, because it knows things Whisper cannot — that
  this person's "wawa" means water.

Safety is unchanged: generated text is never spoken automatically. The
client shows it with Yes/No and only speaks after a Yes.
"""

import difflib
import re

import numpy as np
from app.models.recognition import MatchCandidate, MatchResult
from app.services.audio import AudioProcessingError

from app.services.task_io import data_lock

MAX_CANDIDATES = 3
#: How close a transcript must be to a saved phrase's text to count as the
#: same thing ("water please" ~ "Water, please!").
AGREEMENT_RATIO = 0.85


class InvalidAudio(ValueError):
    pass


def normalize_text(text):
    text = re.sub(r"[^a-z0-9' ]+", " ", str(text).lower().replace("’", "'"))
    return " ".join(text.split())


def texts_agree(a, b):
    a, b = normalize_text(a), normalize_text(b)
    if not a or not b:
        return False
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= AGREEMENT_RATIO


def merge_results(match, transcript, phrases, absolute_max):
    """Combine the DTW phrase match with a Whisper transcript.

    :param match: ``MatchResult`` from the DTW matcher (phrase candidates only).
    :param transcript: ``Transcript`` or ``None`` (transcriber unavailable).
    :param phrases: every saved ``Phrase`` (to find agreement beyond the top 3,
        and to know which phrases are personal words).
    :param absolute_max: DTW distance beyond which a phrase is not a plausible
        alternative.
    """
    by_id = {p.id: p for p in phrases}
    phrase_cands = list(match.candidates)
    top = phrase_cands[0] if phrase_cands else None
    top_is_personal = bool(top and by_id.get(top.phrase_id) and by_id[top.phrase_id].personal_word)

    if transcript is None or not transcript.usable:
        # No usable words. A confident personal word can still stand on its
        # own; a confident *standard* phrase is downgraded to "unsure", so
        # noise never gets announced as "Water please" with certainty.
        confident = bool(match.confident and top_is_personal)
        return MatchResult(attempt_id=match.attempt_id, confident=confident,
                           candidates=phrase_cands[:MAX_CANDIDATES],
                           transcript=transcript.text.strip() if transcript and transcript.text.strip() else None)

    said = transcript.text.strip()
    plausible = [c for c in phrase_cands if c.score <= absolute_max]

    # 1. Transcript and a saved phrase agree -> show the phrase (it carries
    #    the user's picture and is auto-spoken as a confident phrase).
    agreeing = next((c for c in phrase_cands if texts_agree(c.text, said)), None)
    if agreeing is None:
        phrase = next((p for p in phrases if texts_agree(p.text, said)), None)
        if phrase is not None:
            agreeing = MatchCandidate(
                phrase_id=phrase.id, text=phrase.text, emoji=phrase.emoji,
                image=f'/api/phrases/{phrase.id}/image' if phrase.image else None,
                score=0.0)
    if agreeing is not None:
        rest = [c for c in plausible if c.phrase_id != agreeing.phrase_id]
        return MatchResult(attempt_id=match.attempt_id, confident=True,
                           candidates=[agreeing, *rest][:MAX_CANDIDATES], transcript=said)

    generated = MatchCandidate(type='generated', text=said,
                               score=round(max(0.0, -transcript.avg_logprob), 4))

    # 2. A personal word the sound matcher is sure about wins: Whisper can
    #    spell "wawa" but cannot know it means water.
    if match.confident and top_is_personal:
        rest = [c for c in plausible[1:]]
        return MatchResult(attempt_id=match.attempt_id, confident=True,
                           candidates=[top, generated, *rest][:MAX_CANDIDATES], transcript=said)

    # 3. Otherwise: what they actually said, first. Plausible saved phrases
    #    follow as alternatives, one tap away.
    return MatchResult(attempt_id=match.attempt_id, confident=True,
                       candidates=[generated, *plausible][:MAX_CANDIDATES], transcript=said)


class RecognitionService:
    def __init__(self, preprocessor, extractor, matcher, attempt_store, transcriber=None):
        self.preprocessor, self.extractor = preprocessor, extractor
        self.matcher, self.attempt_store = matcher, attempt_store
        self.transcriber = transcriber

    def recognize(self, content, mime_type):
        try:
            processed = self.preprocessor.process(content, mime_type)
            audio, sr = processed.samples, processed.sr
            if sr != 16000 or audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                raise ValueError('Expected finite 16 kHz mono audio')
            if np.max(np.abs(audio)) <= 1e-6:
                raise ValueError('Silent recording')
        except (ValueError, AudioProcessingError) as exc:
            raise InvalidAudio('Could not use this recording. Please try speaking again.') from exc
        features = self.extractor.extract(audio, sr)
        # Protect the template snapshot while feedback is mutating samples.
        with data_lock(self.attempt_store.data_dir):
            match = self.matcher.match(features)
            phrases = self.matcher.phrase_store.list_phrases()
        result = match
        if self.transcriber is not None:
            transcript = self.transcriber.transcribe(audio, hints=[p.text for p in phrases])
            result = merge_results(match, transcript, phrases, self.matcher.absolute_max)
        self.attempt_store.save(result.attempt_id, processed.wav_bytes, features, result)
        return result
