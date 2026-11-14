"""Canonical phoneme inventory for General American English (Task 14).

The PRD requires that the phoneme recognizer's output symbols and the
pronunciation lexicon's symbols end up in *one* canonical inventory. This
module defines that inventory; :mod:`app.phonetics.mapping` defines the
conversions into it.

Choice of canonical set: **ARPAbet without stress digits** (39 phonemes, the
CMUdict inventory).

Rationale:

* The lexicon (Task 16) is built from CMUdict, which is natively ARPAbet, so
  the larger of the two symbol sources needs no conversion at all.
* The symbols are ASCII and filename-safe. Phoneme sequences are written into
  ``phrases.json``, ``feedback.jsonl``, ``data/phonemes/*.json`` and the
  confusion-matrix keys of ``profile/profile_v<N>.json``; ASCII keeps those
  files greppable and avoids Unicode normalization hazards (IPA ``ɡ`` U+0261
  versus ASCII ``g`` is exactly the kind of bug this averts).
* Stress is dropped because the confusion profile (Task 20) models *segment*
  production, not prosody. ``AH0`` and ``AH1`` are the same target sound for
  our purposes, and keeping them apart would split already-scarce counts.

Downstream consumers: the aligner (Task 19), the confusion profile (Task 20),
the decoder (Task 21) and the enrollment coverage checker (Task 17).
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

# ---------------------------------------------------------------------------
# Alignment sentinels
# ---------------------------------------------------------------------------
# The aligner (Task 19) emits (intended, recognized) pairs where either side
# may be absent, and the profile (Task 20) stores a DELETION column and an
# INSERTION row. Both live here so the two modules cannot drift apart on the
# spelling of these keys.

DELETION = "<DEL>"
"""Recognized side of a pair where the intended phoneme was not produced."""

INSERTION = "<INS>"
"""Intended side of a pair where a phoneme was produced with no target."""

SENTINELS: FrozenSet[str] = frozenset({DELETION, INSERTION})


# ---------------------------------------------------------------------------
# The inventory
# ---------------------------------------------------------------------------

CONSONANTS: Tuple[str, ...] = (
    "P", "B", "T", "D", "K", "G",
    "CH", "JH",
    "F", "V", "TH", "DH", "S", "Z", "SH", "ZH", "HH",
    "M", "N", "NG",
    "L", "R",
    "Y", "W",
)

VOWELS: Tuple[str, ...] = (
    "IY", "IH", "EY", "EH", "AE",
    "AA", "AO", "OW", "UH", "UW",
    "AH", "ER",
    "AW", "AY", "OY",
)

PHONEMES: Tuple[str, ...] = CONSONANTS + VOWELS
PHONEME_SET: FrozenSet[str] = frozenset(PHONEMES)

assert len(PHONEMES) == 39, "CMUdict inventory is 39 phonemes"
assert len(PHONEME_SET) == len(PHONEMES), "duplicate phoneme in inventory"


# ---------------------------------------------------------------------------
# Phonotactic constraints
# ---------------------------------------------------------------------------
# Used by the enrollment coverage checker (Task 17), which requires every
# consonant to appear word-initially and word-finally *where English allows
# it*. These sets are the documented exemption list the PRD calls for.

NO_WORD_INITIAL: FrozenSet[str] = frozenset({
    "NG",  # /ŋ/ never begins an English word.
    "ZH",  # word-initial /ʒ/ occurs only in loanwords ("genre") and is too
           # rare to demand of a 25-30 phrase enrollment set.
})

NO_WORD_FINAL: FrozenSet[str] = frozenset({
    "HH",  # /h/ never ends an English syllable.
    "W",   # word-final [w] is transcribed as part of a diphthong in CMUdict
    "Y",   # ("cow" is K AW, not K AW W), so it never appears finally.
})


def allows_word_initial(phoneme: str) -> bool:
    """Whether English phonotactics permit *phoneme* to start a word."""
    _require_consonant(phoneme)
    return phoneme not in NO_WORD_INITIAL


def allows_word_final(phoneme: str) -> bool:
    """Whether English phonotactics permit *phoneme* to end a word."""
    _require_consonant(phoneme)
    return phoneme not in NO_WORD_FINAL


# ---------------------------------------------------------------------------
# Articulatory features
# ---------------------------------------------------------------------------
# The confusion prior in Task 20 is "identity-heavy, optionally adjusted by
# phonetic-feature similarity such as place/manner/voicing". These tables and
# `feature_similarity` supply that adjustment, so the prior can express that
# /p/->/b/ is a likelier confusion than /p/->/iy/ before any user data exists.

CONSONANT_FEATURES: Dict[str, Dict[str, object]] = {
    "P":  {"manner": "stop",      "place": "bilabial",     "voiced": False},
    "B":  {"manner": "stop",      "place": "bilabial",     "voiced": True},
    "T":  {"manner": "stop",      "place": "alveolar",     "voiced": False},
    "D":  {"manner": "stop",      "place": "alveolar",     "voiced": True},
    "K":  {"manner": "stop",      "place": "velar",        "voiced": False},
    "G":  {"manner": "stop",      "place": "velar",        "voiced": True},
    "CH": {"manner": "affricate", "place": "postalveolar", "voiced": False},
    "JH": {"manner": "affricate", "place": "postalveolar", "voiced": True},
    "F":  {"manner": "fricative", "place": "labiodental",  "voiced": False},
    "V":  {"manner": "fricative", "place": "labiodental",  "voiced": True},
    "TH": {"manner": "fricative", "place": "dental",       "voiced": False},
    "DH": {"manner": "fricative", "place": "dental",       "voiced": True},
    "S":  {"manner": "fricative", "place": "alveolar",     "voiced": False},
    "Z":  {"manner": "fricative", "place": "alveolar",     "voiced": True},
    "SH": {"manner": "fricative", "place": "postalveolar", "voiced": False},
    "ZH": {"manner": "fricative", "place": "postalveolar", "voiced": True},
    "HH": {"manner": "fricative", "place": "glottal",      "voiced": False},
    "M":  {"manner": "nasal",     "place": "bilabial",     "voiced": True},
    "N":  {"manner": "nasal",     "place": "alveolar",     "voiced": True},
    "NG": {"manner": "nasal",     "place": "velar",        "voiced": True},
    "L":  {"manner": "liquid",    "place": "alveolar",     "voiced": True},
    "R":  {"manner": "liquid",    "place": "postalveolar", "voiced": True},
    "Y":  {"manner": "glide",     "place": "palatal",      "voiced": True},
    "W":  {"manner": "glide",     "place": "labiovelar",   "voiced": True},
}

VOWEL_FEATURES: Dict[str, Dict[str, object]] = {
    "IY": {"height": "high",      "backness": "front",   "rounded": False, "diphthong": False, "rhotic": False},
    "IH": {"height": "near-high", "backness": "front",   "rounded": False, "diphthong": False, "rhotic": False},
    "EY": {"height": "mid",       "backness": "front",   "rounded": False, "diphthong": True,  "rhotic": False},
    "EH": {"height": "mid",       "backness": "front",   "rounded": False, "diphthong": False, "rhotic": False},
    "AE": {"height": "low",       "backness": "front",   "rounded": False, "diphthong": False, "rhotic": False},
    "AA": {"height": "low",       "backness": "back",    "rounded": False, "diphthong": False, "rhotic": False},
    "AO": {"height": "mid",       "backness": "back",    "rounded": True,  "diphthong": False, "rhotic": False},
    "OW": {"height": "mid",       "backness": "back",    "rounded": True,  "diphthong": True,  "rhotic": False},
    "UH": {"height": "near-high", "backness": "back",    "rounded": True,  "diphthong": False, "rhotic": False},
    "UW": {"height": "high",      "backness": "back",    "rounded": True,  "diphthong": False, "rhotic": False},
    "AH": {"height": "mid",       "backness": "central", "rounded": False, "diphthong": False, "rhotic": False},
    "ER": {"height": "mid",       "backness": "central", "rounded": False, "diphthong": False, "rhotic": True},
    "AW": {"height": "low",       "backness": "central", "rounded": False, "diphthong": True,  "rhotic": False},
    "AY": {"height": "low",       "backness": "central", "rounded": False, "diphthong": True,  "rhotic": False},
    "OY": {"height": "mid",       "backness": "back",    "rounded": True,  "diphthong": True,  "rhotic": False},
}

_CONSONANT_WEIGHTS = {"manner": 0.4, "place": 0.4, "voiced": 0.2}
_VOWEL_WEIGHTS = {"height": 0.4, "backness": 0.3, "rounded": 0.2, "diphthong": 0.1}


def is_vowel(phoneme: str) -> bool:
    """Whether *phoneme* is a vowel. Raises on unknown symbols."""
    _require_phoneme(phoneme)
    return phoneme in VOWEL_FEATURES


def is_consonant(phoneme: str) -> bool:
    """Whether *phoneme* is a consonant. Raises on unknown symbols."""
    _require_phoneme(phoneme)
    return phoneme in CONSONANT_FEATURES


def feature_similarity(a: str, b: str) -> float:
    """Articulatory similarity of two phonemes, in ``[0.0, 1.0]``.

    ``1.0`` for a phoneme with itself, ``0.0`` across the vowel/consonant
    divide. Intended as the shape of the generic confusion prior in Task 20,
    not as a claim about any particular speaker.
    """
    _require_phoneme(a)
    _require_phoneme(b)
    if a == b:
        return 1.0
    if is_consonant(a) and is_consonant(b):
        fa, fb, weights = CONSONANT_FEATURES[a], CONSONANT_FEATURES[b], _CONSONANT_WEIGHTS
    elif is_vowel(a) and is_vowel(b):
        fa, fb, weights = VOWEL_FEATURES[a], VOWEL_FEATURES[b], _VOWEL_WEIGHTS
    else:
        return 0.0
    return sum(w for key, w in weights.items() if fa[key] == fb[key])


def validate_sequence(sequence) -> None:
    """Raise :class:`ValueError` unless every item is a canonical phoneme.

    Sentinels are rejected: they belong in alignment output, never in a
    phoneme sequence representing speech.
    """
    for index, phoneme in enumerate(sequence):
        if phoneme not in PHONEME_SET:
            raise ValueError(
                f"{phoneme!r} at index {index} is not a canonical phoneme; "
                f"expected one of the {len(PHONEMES)} ARPAbet symbols"
            )


def _require_phoneme(phoneme: str) -> None:
    if phoneme not in PHONEME_SET:
        raise ValueError(f"{phoneme!r} is not a canonical phoneme")


def _require_consonant(phoneme: str) -> None:
    _require_phoneme(phoneme)
    if phoneme not in CONSONANT_FEATURES:
        raise ValueError(f"{phoneme!r} is a vowel; phonotactic position does not apply")


# Every phoneme must carry features, or the Task 20 prior would have holes.
assert set(CONSONANT_FEATURES) == set(CONSONANTS)
assert set(VOWEL_FEATURES) == set(VOWELS)
assert NO_WORD_INITIAL <= set(CONSONANTS)
assert NO_WORD_FINAL <= set(CONSONANTS)
