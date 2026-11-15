"""Enrollment coverage checker (Task 23).

The enrollment phrases are only useful for learning someone's sound pattern
if together they contain every sound of English - enough times, and for
consonants in the positions where they behave differently (a word-initial /t/
and a word-final /t/ are often produced differently, and final consonant
deletion is one of the most common patterns in atypical speech).

Requirements (PRD F1):
* every phoneme in the inventory occurs at least ``min_occurrences`` times;
* every consonant occurs word-initially and word-finally, except where
  English phonotactics forbid it (``inventory.allows_word_initial/final``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set

from app.phonetics.inventory import (
    CONSONANTS,
    PHONEMES,
    allows_word_final,
    allows_word_initial,
    is_consonant,
)


@dataclass
class CoverageReport:
    counts: Dict[str, int]
    initial: Set[str]
    final: Set[str]
    min_occurrences: int
    missing: List[str] = field(default_factory=list)          # below min_occurrences
    missing_initial: List[str] = field(default_factory=list)
    missing_final: List[str] = field(default_factory=list)
    word_count: int = 0
    phrase_count: int = 0

    @property
    def ok(self) -> bool:
        return not (self.missing or self.missing_initial or self.missing_final)

    def table(self) -> str:
        """Human-readable coverage table for the checker script."""
        lines = [f"{self.phrase_count} phrases, {self.word_count} words; "
                 f"each sound needs >= {self.min_occurrences}", ""]
        for phoneme in PHONEMES:
            positions = ""
            if is_consonant(phoneme):
                positions = (" initial" if phoneme in self.initial else "  ------") + \
                            (" final" if phoneme in self.final else "  -----")
            flag = "  <-- too few" if phoneme in self.missing else ""
            lines.append(f"  {phoneme:<3} {self.counts.get(phoneme, 0):>3}{positions}{flag}")
        if self.missing_initial:
            lines.append(f"\nmissing word-initial: {' '.join(self.missing_initial)}")
        if self.missing_final:
            lines.append(f"missing word-final:   {' '.join(self.missing_final)}")
        lines.append("\nOK" if self.ok else "\nNOT COVERED")
        return "\n".join(lines)


def check_coverage(phrases: Sequence[Sequence[Sequence[str]]], min_occurrences: int = 2) -> CoverageReport:
    """Check a set of phrases given as per-word phoneme lists.

    ``phrases[i][j]`` is the canonical phoneme list of word *j* of phrase *i*.
    """
    counts: Counter = Counter()
    initial: Set[str] = set()
    final: Set[str] = set()
    words = 0
    for phrase in phrases:
        for word in phrase:
            if not word:
                continue
            words += 1
            counts.update(word)
            initial.add(word[0])
            final.add(word[-1])
    report = CoverageReport(dict(counts), initial, final, min_occurrences,
                            word_count=words, phrase_count=len(phrases))
    report.missing = [p for p in PHONEMES if counts.get(p, 0) < min_occurrences]
    report.missing_initial = [c for c in CONSONANTS if allows_word_initial(c) and c not in initial]
    report.missing_final = [c for c in CONSONANTS if allows_word_final(c) and c not in final]
    return report


def requirements_met(per_word_phrases, min_occurrences: int) -> int:
    """How many requirement units a set satisfies (for greedy selection)."""
    report = check_coverage(per_word_phrases, min_occurrences)
    satisfied = sum(min(report.counts.get(p, 0), min_occurrences) for p in PHONEMES)
    satisfied += sum(1 for c in CONSONANTS if allows_word_initial(c) and c in report.initial)
    satisfied += sum(1 for c in CONSONANTS if allows_word_final(c) and c in report.final)
    return satisfied


def total_requirements(min_occurrences: int) -> int:
    return (len(PHONEMES) * min_occurrences
            + sum(1 for c in CONSONANTS if allows_word_initial(c))
            + sum(1 for c in CONSONANTS if allows_word_final(c)))
