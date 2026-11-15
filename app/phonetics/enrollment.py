"""The built-in enrollment phrases (Task 23).

Short everyday phrases that are useful in Talk mode on their own *and*
together contain every sound of English in the positions that matter. They
are recorded on the calibration page (two takes each), saved as the user's
first Talk phrases, and are the training data for the personal sound profile.

The list lives in ``enrollment_set.json`` next to this module, with each
word's phonemes pre-computed, so the app needs no dictionary at runtime.
``scripts/choose_enrollment_phrases.py`` regenerates it from a candidate pool
and ``tests/test_enrollment_set.py`` fails if it ever stops covering every
sound.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List

from app.phonetics.inventory import validate_sequence

SET_PATH = Path(__file__).with_name("enrollment_set.json")
#: Takes recorded per phrase: two let the profile tell a habit from a slip.
TAKES_PER_PHRASE = 2


@dataclass(frozen=True)
class EnrollmentPhrase:
    id: str
    text: str
    emoji: str
    #: Canonical phonemes per word, aligned with ``text.split()``.
    words: tuple

    @property
    def phonemes(self) -> List[str]:
        return [p for word in self.words for p in word]

    def to_json(self) -> dict:
        return {"id": self.id, "text": self.text, "emoji": self.emoji,
                "words": [list(w) for w in self.words], "phonemes": self.phonemes}


@lru_cache
def load_enrollment_set(path: Path = SET_PATH) -> tuple:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    phrases = []
    for item in raw["phrases"]:
        words = tuple(tuple(w) for w in item["words"])
        for word in words:
            validate_sequence(word)
        phrases.append(EnrollmentPhrase(item["id"], item["text"], item["emoji"], words))
    ids = [p.id for p in phrases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate enrollment phrase id")
    return tuple(phrases)


def enrollment_phrase(phrase_id: str):
    return next((p for p in load_enrollment_set() if p.id == phrase_id), None)
