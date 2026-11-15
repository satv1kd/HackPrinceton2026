"""Cut a take into per-phoneme time spans by CTC forced alignment (Task 28).

The phoneme model gives, for every ~20 ms frame, a probability for each of its
symbols plus a CTC "blank". Knowing which phonemes the take *should* contain
(the enrollment phrase), Viterbi finds the most likely moment each one was
said - even when the model would not have *recognized* it on its own.

Implemented in numpy over the cached posteriors, so it needs no torchaudio and
never re-runs the model. A canonical phoneme's frame score is the summed
probability of every model symbol that maps to it alone (the model may write
an English /r/ as ``ɹ``, ``r`` or ``ɻ``).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.phonetics.inventory import validate_sequence
from app.phonetics.mapping import espeak_to_canonical_lenient

BLANK_LABELS = ("<pad>", "<blank>", "_")
_NEG = -1e30


@dataclass(frozen=True)
class Segment:
    phoneme: str
    start_sec: float
    end_sec: float
    #: Mean posterior of the phoneme over its frames (0..1): low = doubtful.
    confidence: float


@lru_cache(maxsize=8)
def _label_groups(labels: Tuple[str, ...]) -> Tuple[int, Dict[str, Tuple[int, ...]]]:
    """(blank index, canonical phoneme -> model label indices)."""
    blank = next((i for i, l in enumerate(labels) if l in BLANK_LABELS), 0)
    groups: Dict[str, List[int]] = {}
    for index, label in enumerate(labels):
        if index == blank or label.startswith("<"):
            continue
        phonemes, _tokens, _dropped = espeak_to_canonical_lenient(label)
        if len(phonemes) == 1:
            groups.setdefault(phonemes[0], []).append(index)
    return blank, {p: tuple(ix) for p, ix in groups.items()}


def phoneme_log_probs(posteriors: np.ndarray, labels: Sequence[str], phonemes: Sequence[str]):
    """(frames, len(phonemes)) log P(phoneme) and (frames,) log P(blank)."""
    blank, groups = _label_groups(tuple(labels))
    posteriors = np.asarray(posteriors, dtype=np.float64)
    with np.errstate(divide="ignore"):
        cols = []
        for p in phonemes:
            idx = groups.get(p)
            cols.append(np.log(posteriors[:, idx].sum(axis=1) + 1e-12) if idx else np.full(len(posteriors), -30.0))
        return np.stack(cols, axis=1), np.log(posteriors[:, blank] + 1e-12)


def force_align(posteriors: np.ndarray, labels: Sequence[str], phonemes: Sequence[str],
                frame_rate_hz: float, duration_sec: Optional[float] = None) -> List[Segment]:
    """Best CTC path for *phonemes*; one :class:`Segment` per phoneme.

    Each phoneme's span runs from its first frame to the first frame of the
    next phoneme (trailing blanks belong to the sound before them). Returns []
    when the take has fewer frames than phonemes (it cannot fit).
    """
    validate_sequence(phonemes)
    frames, n = len(posteriors), len(phonemes)
    if n == 0 or frames < n:
        return []
    emit, blank = phoneme_log_probs(posteriors, labels, phonemes)
    states = 2 * n + 1                     # blank, p1, blank, p2, ..., pn, blank
    def state_score(t, s):
        return blank[t] if s % 2 == 0 else emit[t, s // 2]

    score = np.full((frames, states), _NEG)
    back = np.zeros((frames, states), dtype=np.int8)   # 0 stay, 1 from s-1, 2 from s-2
    score[0, 0] = state_score(0, 0)
    score[0, 1] = state_score(0, 1)
    for t in range(1, frames):
        prev = score[t - 1]
        for s in range(states):
            best, move = prev[s], 0
            if s >= 1 and prev[s - 1] > best:
                best, move = prev[s - 1], 1
            # Skip a blank between two *different* phonemes.
            if s >= 2 and s % 2 == 1 and phonemes[s // 2] != phonemes[s // 2 - 1] and prev[s - 2] > best:
                best, move = prev[s - 2], 2
            if best <= _NEG / 2:
                continue
            score[t, s] = best + state_score(t, s)
            back[t, s] = move
    last = states - 1 if score[-1, states - 1] >= score[-1, states - 2] else states - 2
    if score[-1, last] <= _NEG / 2:
        return []
    path = [0] * frames
    s = last
    for t in range(frames - 1, -1, -1):
        path[t] = s
        s -= int(back[t, s])

    first = [None] * n
    frames_of: List[List[int]] = [[] for _ in range(n)]
    for t, s in enumerate(path):
        if s % 2 == 1:
            i = s // 2
            frames_of[i].append(t)
            if first[i] is None:
                first[i] = t
    total = duration_sec if duration_sec is not None else frames / frame_rate_hz
    segments = []
    for i, p in enumerate(phonemes):
        if first[i] is None:            # cannot happen on a valid CTC path
            return []
        start = first[i] / frame_rate_hz
        end = (first[i + 1] / frame_rate_hz) if i + 1 < n else total
        confidence = float(np.exp(emit[frames_of[i], i]).mean())
        segments.append(Segment(p, round(start, 3), round(max(end, start + 1 / frame_rate_hz), 3),
                                round(confidence, 4)))
    return segments
