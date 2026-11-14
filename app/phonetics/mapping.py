"""Symbol mapping into the canonical inventory (Task 14).

Two symbol sources have to meet in the middle:

* the **phoneme recognizer** (Task 15), whose checkpoint emits espeak-style
  IPA, and
* the **pronunciation lexicon** (Task 16), built from CMUdict (ARPAbet) with
  a ``phonemizer``/espeak-ng fallback for out-of-dictionary words.

Both are converted here into the ARPAbet inventory defined in
:mod:`app.phonetics.inventory`. The PRD's risk register calls a symbol
mismatch between these two sources out explicitly; a single tested table is
the mitigation.

Unknown symbols raise :class:`UnknownPhonemeError` rather than being dropped.
Silently discarding a symbol would corrupt the alignment in Task 19 — a
dropped phone looks exactly like a deletion by the speaker, which would teach
the confusion profile that the user omits sounds they actually produced.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from app.phonetics.inventory import PHONEME_SET, PHONEMES


MAPPING_REVISION = 1
"""Bump this whenever the tables below change meaning.

Cached phoneme recognitions (``data/phonemes/*.json``) record the revision
they were produced under and are recomputed when it moves. Without that, a
corrected mapping would leave stale, wrongly-mapped sequences in the cache,
and those feed the confusion profile.
"""


class UnknownPhonemeError(ValueError):
    """An input symbol has no mapping into the canonical inventory."""


# ---------------------------------------------------------------------------
# espeak-style IPA -> canonical
# ---------------------------------------------------------------------------
# Values are tuples because a handful of inputs expand to two canonical
# phonemes (the centering diphthongs). Everything else is 1:1.
#
# Covers the English-relevant portion of the espeak phoneme set as emitted by
# wav2vec2 espeak checkpoints. Several entries are deliberate *normalizations*
# rather than identities; each is commented, because these are the judgement
# calls a reviewer should check.

ESPEAK_TO_CANONICAL: Dict[str, Tuple[str, ...]] = {
    # --- stops ---
    "p": ("P",), "b": ("B",), "t": ("T",), "d": ("D",), "k": ("K",),
    "ɡ": ("G",),   # U+0261 LATIN SMALL LETTER SCRIPT G, what espeak emits
    "g": ("G",),   # U+0067, in case a tokenizer normalizes it
    "ɾ": ("T",),   # alveolar flap: the American "butter" realization of /t/
    "ʔ": ("T",),   # glottal stop: usually a /t/ allophone ("mountain")
    # --- affricates ---
    "tʃ": ("CH",), "dʒ": ("JH",),
    "ʧ": ("CH",), "ʤ": ("JH",),  # precomposed ligature forms
    # --- fricatives ---
    "f": ("F",), "v": ("V",), "θ": ("TH",), "ð": ("DH",),
    "s": ("S",), "z": ("Z",), "ʃ": ("SH",), "ʒ": ("ZH",), "h": ("HH",),
    "x": ("K",),   # loanword velar fricative, nearest English target
    # --- nasals ---
    "m": ("M",), "n": ("N",), "ŋ": ("NG",),
    "m̩": ("M",), "n̩": ("N",),  # syllabic nasals ("rhythm", "button")
    # --- liquids and glides ---
    "l": ("L",), "ɫ": ("L",), "l̩": ("L",),  # dark and syllabic /l/
    "ɹ": ("R",), "r": ("R",), "ɻ": ("R",),
    "j": ("Y",), "w": ("W",), "ʍ": ("W",),
    # --- monophthongs ---
    "ɑ": ("AA",), "ɑː": ("AA",), "ɒ": ("AA",), "a": ("AA",),
    "æ": ("AE",),
    "ʌ": ("AH",), "ə": ("AH",), "ɐ": ("AH",),  # CMUdict spells schwa AH0
    "ɔ": ("AO",), "ɔː": ("AO",),
    "ɛ": ("EH",), "ɛː": ("EH",), "e": ("EH",),
    "ɪ": ("IH",), "ɪː": ("IH",),
    "i": ("IY",), "iː": ("IY",),
    "ʊ": ("UH",),
    "u": ("UW",), "uː": ("UW",),
    # --- r-coloured ---
    "ɚ": ("ER",), "ɝ": ("ER",), "ɜ": ("ER",), "ɜː": ("ER",), "ɹ̩": ("ER",),
    # --- diphthongs ---
    "eɪ": ("EY",), "oʊ": ("OW",), "əʊ": ("OW",), "o": ("OW",), "oː": ("OW",),
    "aɪ": ("AY",), "aʊ": ("AW",), "ɔɪ": ("OY",),
    # --- centering diphthongs (non-rhotic voices only) ---
    # en-us is rhotic and emits e.g. ɪɹ, so these should not appear; mapped
    # for robustness if the recognizer is run with a non-rhotic voice.
    "ɪə": ("IH", "R"),
    "eə": ("EH", "R"),
    "ʊə": ("UH", "R"),
}

#: Diacritics and prosodic marks carried by espeak output that are not
#: phonemes. Stress and length are dropped because the canonical inventory is
#: stress-free and length-free (see :mod:`app.phonetics.inventory`).
IGNORED_MARKS: frozenset = frozenset({
    "ˈ",  # primary stress
    "ˌ",  # secondary stress
    "ː",  # length (only reached when not part of a mapped key such as "iː")
    "ˑ",  # half-length
    "ʰ",  # aspiration
    "ʲ",  # palatalization
    "ʷ",  # labialization
    "̃",   # nasalization (combining)
    "̩",   # syllabicity (combining, when not part of a mapped key)
    "̯",   # non-syllabicity (combining)
    "̥",   # voicelessness (combining)
    "ˤ",  # pharyngealization
    ".",  # syllable break
    "|",  # phrase break
    "‖",  # phrase break
    "‿",  # linking
    "-",  # espeak separator
    "_",  # espeak separator
})

_MAX_ESPEAK_KEY = max(len(key) for key in ESPEAK_TO_CANONICAL)

#: One representative IPA spelling per canonical phoneme, for display only
#: (the coverage table in Task 17 and the coverage screen in Task 22).
CANONICAL_TO_IPA: Dict[str, str] = {
    "P": "p", "B": "b", "T": "t", "D": "d", "K": "k", "G": "ɡ",
    "CH": "tʃ", "JH": "dʒ",
    "F": "f", "V": "v", "TH": "θ", "DH": "ð", "S": "s", "Z": "z",
    "SH": "ʃ", "ZH": "ʒ", "HH": "h",
    "M": "m", "N": "n", "NG": "ŋ", "L": "l", "R": "ɹ", "Y": "j", "W": "w",
    "IY": "i", "IH": "ɪ", "EY": "eɪ", "EH": "ɛ", "AE": "æ",
    "AA": "ɑ", "AO": "ɔ", "OW": "oʊ", "UH": "ʊ", "UW": "u",
    "AH": "ʌ", "ER": "ɚ", "AW": "aʊ", "AY": "aɪ", "OY": "ɔɪ",
}


def tokenize_espeak(text: str) -> List[str]:
    """Split an espeak IPA string into recognizer symbols.

    Handles both output shapes seen from espeak-based checkpoints: symbols
    separated by whitespace, and a run-together string. Matching is
    longest-first, so ``"tʃ"`` is one affricate rather than ``t`` + ``ʃ``.

    Raises :class:`UnknownPhonemeError` on a symbol with no mapping.
    """
    tokens: List[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        matched = False
        # Longest-first match so multi-character symbols win.
        for size in range(min(_MAX_ESPEAK_KEY, length - index), 0, -1):
            candidate = text[index:index + size]
            if candidate in ESPEAK_TO_CANONICAL:
                tokens.append(candidate)
                index += size
                matched = True
                break
        if matched:
            continue
        if char in IGNORED_MARKS:
            index += 1
            continue
        raise UnknownPhonemeError(
            f"{char!r} (U+{ord(char):04X}) at index {index} of {text!r} is "
            "neither a mapped espeak symbol nor an ignorable mark; add it to "
            "ESPEAK_TO_CANONICAL or IGNORED_MARKS in app/phonetics/mapping.py"
        )
    return tokens


def espeak_to_canonical(text: str) -> List[str]:
    """Convert an espeak IPA string to canonical phonemes."""
    canonical: List[str] = []
    for token in tokenize_espeak(text):
        canonical.extend(ESPEAK_TO_CANONICAL[token])
    return canonical


def arpabet_to_canonical(sequence: Iterable[str]) -> List[str]:
    """Convert CMUdict-style ARPAbet symbols to canonical phonemes.

    Strips the trailing stress digit (``AH0`` -> ``AH``) and upper-cases.
    Raises :class:`UnknownPhonemeError` on anything outside the inventory.
    """
    canonical: List[str] = []
    for raw in sequence:
        symbol = raw.strip().upper()
        if not symbol:
            continue
        if symbol[-1].isdigit():
            symbol = symbol[:-1]
        if symbol not in PHONEME_SET:
            raise UnknownPhonemeError(
                f"{raw!r} is not an ARPAbet symbol in the canonical inventory"
            )
        canonical.append(symbol)
    return canonical


def to_canonical(value, source: str) -> List[str]:
    """Convert *value* to canonical phonemes.

    *source* is ``"espeak"`` (``value`` is a string) or ``"arpabet"``
    (``value`` is a sequence of symbols, or a space-separated string).
    """
    if source == "espeak":
        if not isinstance(value, str):
            raise TypeError("espeak input must be a string")
        return espeak_to_canonical(value)
    if source == "arpabet":
        symbols: Sequence[str] = value.split() if isinstance(value, str) else value
        return arpabet_to_canonical(symbols)
    raise ValueError(f"unknown source {source!r}; expected 'espeak' or 'arpabet'")


# Guard rails checked at import, so a bad edit to the tables fails loudly and
# immediately rather than at recognition time.
for _key, _value in ESPEAK_TO_CANONICAL.items():
    if not _value:
        raise AssertionError(f"espeak symbol {_key!r} maps to an empty sequence")
    for _phoneme in _value:
        if _phoneme not in PHONEME_SET:
            raise AssertionError(
                f"espeak symbol {_key!r} maps to {_phoneme!r}, which is not canonical"
            )

_reachable = {p for value in ESPEAK_TO_CANONICAL.values() for p in value}
if _reachable != PHONEME_SET:
    raise AssertionError(
        "espeak table cannot produce these canonical phonemes: "
        f"{sorted(PHONEME_SET - _reachable)}"
    )

if set(CANONICAL_TO_IPA) != PHONEME_SET:
    raise AssertionError("CANONICAL_TO_IPA must cover exactly the inventory")

if not IGNORED_MARKS.isdisjoint(ESPEAK_TO_CANONICAL):
    raise AssertionError("a symbol cannot be both mapped and ignored")

del _key, _value, _phoneme, _reachable
