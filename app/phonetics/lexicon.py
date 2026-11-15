"""Pronunciation lexicon (Task 22): common English words and how they sound.

The phonetic decoder can only ever output words in this lexicon, and the
enrollment coverage checker uses it to know which sounds a phrase contains.

Built by ``scripts/build_lexicon.py`` from two sources:

* **CMUdict** (``cmudict`` package) for pronunciations, converted to the
  canonical stress-free ARPAbet inventory by
  :func:`app.phonetics.mapping.arpabet_to_canonical`;
* **wordfreq** for how common each word is. wordfreq's code is Apache-2.0 and
  its data CC-BY-SA 4.0; the generated list lives in ``data/lexicon/`` (git
  ignored) rather than being redistributed in the repo.

Runtime needs neither package: it reads ``data/lexicon/words.tsv``. When a
word is missing (a custom phrase), :meth:`Lexicon.pronounce` falls back to
CMUdict if the package happens to be installed.

File format, one word per line::

    word<TAB>frequency<TAB>P1 P2 P3|ALT1 ALT2
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from app.phonetics.inventory import validate_sequence
from app.phonetics.mapping import UnknownPhonemeError, arpabet_to_canonical

log = logging.getLogger(__name__)

LEXICON_FILENAME = "words.tsv"
#: Floor for words with no frequency (forced-in phrase words), so -log stays finite.
MIN_FREQUENCY = 1e-8
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def normalize(word: str) -> str:
    """Lowercase, keep letters and inner apostrophes ("don't")."""
    match = _WORD.search(word.lower().replace("’", "'"))
    return match.group(0) if match else ""


def tokenize(text: str) -> List[str]:
    return [w for w in (normalize(t) for t in re.split(r"[\s\-–—/]+", text)) if w]


@dataclass
class LexiconEntry:
    word: str
    frequency: float
    pronunciations: List[List[str]] = field(default_factory=list)

    @property
    def neg_log_freq(self) -> float:
        return -math.log(max(self.frequency, MIN_FREQUENCY))


class Lexicon:
    def __init__(self, entries: Iterable[LexiconEntry] = ()):
        self._entries: Dict[str, LexiconEntry] = {}
        for entry in entries:
            self.add(entry)
        self._cmudict = None  # lazily loaded fallback

    # -- construction -------------------------------------------------------

    def add(self, entry: LexiconEntry) -> None:
        for pron in entry.pronunciations:
            validate_sequence(pron)
        existing = self._entries.get(entry.word)
        if existing:
            for pron in entry.pronunciations:
                if pron not in existing.pronunciations:
                    existing.pronunciations.append(pron)
            existing.frequency = max(existing.frequency, entry.frequency)
        else:
            self._entries[entry.word] = entry

    @classmethod
    def load(cls, path: Path) -> "Lexicon":
        entries = []
        for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.startswith("#"):
                continue
            try:
                word, freq, prons = line.split("\t")
                entries.append(LexiconEntry(
                    word, float(freq), [p.split() for p in prons.split("|") if p.strip()]))
            except ValueError as error:
                raise ValueError(f"{path}:{line_no}: malformed lexicon line") from error
        return cls(entries)

    def save(self, path: Path) -> None:
        from app.services.storage import atomic_write_bytes

        lines = ["# word\tfrequency\tpronunciations (canonical ARPAbet, '|' separates variants)"]
        for entry in sorted(self._entries.values(), key=lambda e: (-e.frequency, e.word)):
            prons = "|".join(" ".join(p) for p in entry.pronunciations)
            lines.append(f"{entry.word}\t{entry.frequency:.6g}\t{prons}")
        atomic_write_bytes(Path(path), ("\n".join(lines) + "\n").encode("utf-8"))

    # -- lookups ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, word: str) -> bool:
        return normalize(word) in self._entries

    def entry(self, word: str) -> Optional[LexiconEntry]:
        return self._entries.get(normalize(word))

    def words(self) -> Iterator[LexiconEntry]:
        return iter(self._entries.values())

    def pronounce(self, word: str) -> List[List[str]]:
        """All known pronunciations of *word* ([] if unknown)."""
        key = normalize(word)
        entry = self._entries.get(key)
        if entry:
            return [list(p) for p in entry.pronunciations]
        return cmudict_pronunciations(key, self)

    def transcribe_text(self, text: str) -> Optional[List[List[str]]]:
        """Per-word canonical phonemes (first pronunciation), or None if any word is unknown."""
        words = tokenize(text)
        if not words:
            return None
        out = []
        for word in words:
            prons = self.pronounce(word)
            if not prons:
                return None
            out.append(prons[0])
        return out

    def flat_phonemes(self, text: str) -> Optional[List[str]]:
        per_word = self.transcribe_text(text)
        return None if per_word is None else [p for word in per_word for p in word]


def cmudict_pronunciations(word: str, cache_holder=None) -> List[List[str]]:
    """Look *word* up in CMUdict if the package is installed, else []."""
    holder = cache_holder if cache_holder is not None else _MODULE_CACHE
    if getattr(holder, "_cmudict", None) is None:
        try:
            import cmudict  # type: ignore
            holder._cmudict = cmudict.dict()
        except Exception:  # not installed, or its data is missing
            holder._cmudict = {}
    prons = []
    for raw in holder._cmudict.get(word, []):
        try:
            canonical = arpabet_to_canonical(raw)
        except UnknownPhonemeError:
            continue
        if canonical and canonical not in prons:
            prons.append(canonical)
    return prons


class _Cache:
    _cmudict = None


_MODULE_CACHE = _Cache()


def default_path(data_dir: Path) -> Path:
    return Path(data_dir) / "lexicon" / LEXICON_FILENAME


def build_lexicon(size: int, extra_words: Sequence[str] = ()) -> Lexicon:
    """Top *size* frequent English words that CMUdict can pronounce, plus *extra_words*.

    Needs the ``cmudict`` and ``wordfreq`` packages (``make install-phonetics``).
    """
    import cmudict  # type: ignore
    from wordfreq import top_n_list, word_frequency  # type: ignore

    pron_dict = cmudict.dict()
    lexicon = Lexicon()
    holder = _Cache()
    holder._cmudict = pron_dict
    # Over-fetch: some frequent tokens are not in CMUdict (names, fragments).
    for word in top_n_list("en", size * 3, wordlist="best"):
        if len(lexicon) >= size:
            break
        key = normalize(word)
        if not key or key != word.lower().replace("’", "'") or key in lexicon:
            continue
        prons = cmudict_pronunciations(key, holder)
        if prons:
            lexicon.add(LexiconEntry(key, word_frequency(key, "en"), prons))
    for word in extra_words:
        key = normalize(word)
        if key and key not in lexicon:
            prons = cmudict_pronunciations(key, holder)
            if prons:
                lexicon.add(LexiconEntry(key, max(word_frequency(key, "en"), MIN_FREQUENCY), prons))
            else:
                log.warning("no pronunciation for %r; it is left out of the lexicon", key)
    return lexicon
