"""Phoneme alignment (Task 26): what was meant vs. what was heard.

Lines up the *expected* phonemes of a known phrase with the phonemes the
recognizer *heard*, as (intended, recognized) pairs:

    expected  W  AO  T  ER        "water"
    heard     W  AA  -  ER        (the T was not produced)
    pairs     (W,W) (AO,AA) (T,<DEL>) (ER,ER)

An extra sound with no target is ``(<INS>, X)``. These pairs are what the
sound profile counts (Task 27) and what the decoder scores against (Task 29).

Weighted Needleman-Wunsch: a substitution costs ``1 - feature_similarity``
by default, so /L/->/W/ (both voiced approximants) is cheaper than /L/->/K/.
The decoder passes its own costs (``-log P`` from the profile) through the
same function. Ties break deterministically (diagonal, then deletion, then
insertion), so retraining on the same data is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from app.phonetics.inventory import DELETION, INSERTION, feature_similarity, validate_sequence

SubCost = Callable[[str, str], float]


@dataclass(frozen=True)
class AlignedPair:
    intended: str      # canonical phoneme, or INSERTION
    recognized: str    # canonical phoneme, or DELETION


@dataclass
class Alignment:
    pairs: List[AlignedPair]
    cost: float

    @property
    def matches(self) -> int:
        return sum(1 for p in self.pairs if p.intended == p.recognized)

    def accuracy(self) -> float:
        """Share of intended phonemes produced as themselves (0..1)."""
        intended = [p for p in self.pairs if p.intended != INSERTION]
        return self.matches / len(intended) if intended else 0.0


def default_sub_cost(a: str, b: str) -> float:
    return 0.0 if a == b else 1.0 - feature_similarity(a, b)


def align(expected: Sequence[str], recognized: Sequence[str],
          sub_cost: Optional[SubCost] = None,
          del_cost: Callable[[str], float] | float = 1.0,
          ins_cost: Callable[[str], float] | float = 1.0,
          validate: bool = True) -> Alignment:
    """Minimum-cost alignment of *expected* (intended) against *recognized*."""
    if validate:
        validate_sequence(expected)
        validate_sequence(recognized)
    sub = sub_cost or default_sub_cost
    dele = del_cost if callable(del_cost) else (lambda _p, c=float(del_cost): c)
    ins = ins_cost if callable(ins_cost) else (lambda _p, c=float(ins_cost): c)
    n, m = len(expected), len(recognized)

    # cost[i][j]: best cost aligning expected[:i] with recognized[:j]
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]   # 1 diag, 2 deletion, 3 insertion
    for i in range(1, n + 1):
        cost[i][0] = cost[i - 1][0] + dele(expected[i - 1])
        back[i][0] = 2
    for j in range(1, m + 1):
        cost[0][j] = cost[0][j - 1] + ins(recognized[j - 1])
        back[0][j] = 3
    for i in range(1, n + 1):
        e = expected[i - 1]
        row, prev = cost[i], cost[i - 1]
        for j in range(1, m + 1):
            r = recognized[j - 1]
            diag = prev[j - 1] + sub(e, r)
            deletion = prev[j] + dele(e)
            insertion = row[j - 1] + ins(r)
            best, move = diag, 1
            if deletion < best - 1e-12:
                best, move = deletion, 2
            if insertion < best - 1e-12:
                best, move = insertion, 3
            row[j], back[i][j] = best, move

    pairs: List[AlignedPair] = []
    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j]
        if move == 1:
            pairs.append(AlignedPair(expected[i - 1], recognized[j - 1]))
            i, j = i - 1, j - 1
        elif move == 2:
            pairs.append(AlignedPair(expected[i - 1], DELETION))
            i -= 1
        else:
            pairs.append(AlignedPair(INSERTION, recognized[j - 1]))
            j -= 1
    pairs.reverse()
    return Alignment(pairs, cost[n][m])
