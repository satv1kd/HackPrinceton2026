"""The phonetic decoder (Task 29): what the model heard -> words this person meant.

Given the phonemes the recognizer heard, find the most likely sequence of
common English words, using the person's sound profile:

    total cost =  sum over words of  profile cost of the word vs. its stretch
                                      of heard phonemes  (-log P, Task 27)
               +  LM_WEIGHT * (-log word frequency)
               -  bonus for words / whole phrases the user has saved
               (+ optional sound-bank acoustic rescoring of the top few)

So if this person's /r/ usually comes out as [w], hearing "W EH D" scores
"red" well, because P(heard W | meant R) is high for them.

Search: dynamic programming over positions in the heard sequence with a beam
of partial sentences. For each stretch of heard phonemes, candidate words are
pre-filtered (pronunciation length within reach, and a first phoneme that
could plausibly have produced what was heard at the start), then scored with
the profile-weighted aligner. Pure: no I/O, no model.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.phonetics.inventory import DELETION, PHONEMES
from app.phonetics.lexicon import Lexicon, normalize, tokenize
from app.phonetics.profile import SoundProfile

#: How many intended phonemes count as "plausible" for one heard phoneme.
PLAUSIBLE_PER_HEARD = 4
#: A word may cover this many more/fewer heard phonemes than it has.
LENGTH_SLACK = 2
#: Candidates kept per stretch, and partial sentences kept per position.
SPAN_CANDIDATES = 12
BEAM = 16
#: Cost discount for a word that appears in the user's saved phrases, and for
#: a hypothesis that equals one of them exactly.
PHRASE_WORD_BONUS = 1.0
PHRASE_BONUS = 3.0
#: Weight of the sound-bank distance when rescoring the top hypotheses.
ACOUSTIC_WEIGHT = 0.5


@dataclass(order=True)
class Hypothesis:
    cost: float
    words: Tuple[str, ...] = field(compare=False)
    #: Profile (acoustic-symbolic) part of the cost, before LM and bonuses.
    match_cost: float = field(default=0.0, compare=False)
    acoustic: Optional[float] = field(default=None, compare=False)

    @property
    def text(self) -> str:
        return " ".join(self.words)


class PhoneticDecoder:
    def __init__(self, lexicon: Lexicon, profile: SoundProfile, lm_weight: float = 0.3,
                 max_words: int = 6, phrase_texts: Iterable[str] = (), sound_bank=None):
        self.lexicon, self.profile = lexicon, profile
        self.lm_weight, self.max_words = lm_weight, max_words
        self.sound_bank = sound_bank
        self.phrase_texts = {" ".join(tokenize(t)) for t in phrase_texts if tokenize(t)}
        self.phrase_words = {w for t in self.phrase_texts for w in t.split()}
        # Saved phrases are scored whole against every utterance, so the beam
        # can never prune away something the user has said before.
        self._phrase_prons = []
        for text in self.phrase_texts:
            per_word = lexicon.transcribe_text(text)
            if per_word:
                prior = sum(self.lm_weight * (self.lexicon.entry(w).neg_log_freq if self.lexicon.entry(w) else 18.0)
                            - PHRASE_WORD_BONUS for w in text.split())
                self._phrase_prons.append((tuple(text.split()), tuple(p for w in per_word for p in w), prior))
        self._index = self._build_index()
        self._plausible = self._build_plausible()

    # -- precomputation ----------------------------------------------------------

    def _build_index(self) -> Dict[str, List[Tuple[str, Tuple[str, ...], float]]]:
        """first phoneme -> [(word, pronunciation, word prior cost)].

        Also indexed under ``"+" + second phoneme``, for when the speaker
        dropped the first sound ("can" heard as "an").
        """
        index: Dict[str, List[Tuple[str, Tuple[str, ...], float]]] = {}
        for entry in self.lexicon.words():
            prior = self.lm_weight * entry.neg_log_freq
            if entry.word in self.phrase_words:
                prior -= PHRASE_WORD_BONUS
            for pron in entry.pronunciations:
                item = (entry.word, tuple(pron), prior)
                index.setdefault(pron[0], []).append(item)
                if len(pron) > 1:
                    index.setdefault("+" + pron[1], []).append(item)
        return index

    def _build_plausible(self) -> Dict[str, Tuple[str, ...]]:
        """heard phoneme -> intended phonemes most likely to produce it."""
        out = {}
        for heard in PHONEMES:
            ranked = sorted(PHONEMES, key=lambda meant: -self.profile.probability(meant, heard))
            out[heard] = tuple(dict.fromkeys([heard] + ranked[:PLAUSIBLE_PER_HEARD]))
        return out

    # -- scoring ------------------------------------------------------------------

    def word_cost(self, pron: Sequence[str], heard: Sequence[str]) -> float:
        """Profile-weighted alignment cost of an intended pronunciation vs. a heard stretch."""
        return _align_cost(tuple(pron), tuple(heard), self.profile)

    def _span_candidates(self, heard: Tuple[str, ...]) -> List[Tuple[float, str, float]]:
        """Best (total, word, match) for one heard stretch."""
        keys = set(self._plausible.get(heard[0], (heard[0],)))
        # The word's first sound may have been dropped: match its *second*
        # sound against what was heard first.
        keys |= {"+" + p for p in self._plausible.get(heard[0], (heard[0],))}
        if len(heard) > 1:  # ...or an extra sound came before it
            keys |= set(self._plausible.get(heard[1], (heard[1],)))
        best: Dict[str, Tuple[float, str, float]] = {}
        n = len(heard)
        for key in keys:
            for word, pron, prior in self._index.get(key, ()):
                if abs(len(pron) - n) > LENGTH_SLACK:
                    continue
                match = self.word_cost(pron, heard)
                if word not in best or match + prior < best[word][0]:
                    best[word] = (match + prior, word, match)
        return heapq.nsmallest(SPAN_CANDIDATES, best.values())

    # -- search -------------------------------------------------------------------

    def decode(self, heard: Sequence[str], k: int = 3, query_features=None) -> List[Hypothesis]:
        heard = tuple(heard)
        n = len(heard)
        if n == 0:
            return []
        max_span = max((len(p) for entries in self._index.values() for _, p, _ in entries), default=1) + LENGTH_SLACK
        span_cache: Dict[Tuple[int, int], List[Tuple[float, str, float]]] = {}
        beams: List[List[Hypothesis]] = [[] for _ in range(n + 1)]
        beams[0] = [Hypothesis(0.0, ())]
        for j in range(1, n + 1):
            options: List[Hypothesis] = []
            # An extra heard sound that belongs to no word.
            for hyp in beams[j - 1]:
                ins = self.profile.insertion_cost(heard[j - 1])
                options.append(Hypothesis(hyp.cost + ins, hyp.words, hyp.match_cost + ins))
            for i in range(max(0, j - max_span), j):
                if not beams[i]:
                    continue
                key = (i, j)
                if key not in span_cache:
                    span_cache[key] = self._span_candidates(heard[i:j])
                for hyp in beams[i]:
                    if len(hyp.words) >= self.max_words:
                        continue
                    for total, word, match in span_cache[key]:
                        options.append(Hypothesis(hyp.cost + total, hyp.words + (word,), hyp.match_cost + match))
            beams[j] = _dedupe(heapq.nsmallest(BEAM * 2, options))[:BEAM]

        finals = [h for h in beams[n] if h.words]
        finals = [h for h in finals if " ".join(h.words) not in self.phrase_texts]
        for words, pron, prior in self._phrase_prons:
            match = self.word_cost(pron, heard)
            finals.append(Hypothesis(match + prior - PHRASE_BONUS, words, match))
        finals = _dedupe(sorted(finals))[: max(k, 5)]
        if self.sound_bank is not None and query_features is not None:
            finals = self._rescore(finals, query_features)
        return finals[:k]

    def _rescore(self, hyps: List[Hypothesis], query_features) -> List[Hypothesis]:
        for hyp in hyps:
            phonemes = self.lexicon.flat_phonemes(hyp.text)
            distance = self.sound_bank.score(query_features, phonemes) if phonemes else None
            if distance is not None:
                hyp.acoustic = distance
                hyp.cost += ACOUSTIC_WEIGHT * distance
        return sorted(hyps)


def _dedupe(hyps: Iterable[Hypothesis]) -> List[Hypothesis]:
    seen, out = set(), []
    for hyp in hyps:
        if hyp.words in seen:
            continue
        seen.add(hyp.words)
        out.append(hyp)
    return out


def _align_cost(intended: Tuple[str, ...], heard: Tuple[str, ...], profile: SoundProfile) -> float:
    """Min-cost alignment with -log P costs (inlined aligner, cost only)."""
    costs = _profile_costs(profile)
    sub, dele, ins = costs
    n, m = len(intended), len(heard)
    prev = [0.0] * (m + 1)
    for j in range(1, m + 1):
        prev[j] = prev[j - 1] + ins[heard[j - 1]]
    for i in range(1, n + 1):
        e = intended[i - 1]
        row = [prev[0] + dele[e]] + [0.0] * m
        sub_e = sub[e]
        for j in range(1, m + 1):
            r = heard[j - 1]
            a = prev[j - 1] + sub_e[r]
            b = prev[j] + dele[e]
            c = row[j - 1] + ins[r]
            row[j] = a if a <= b and a <= c else (b if b <= c else c)
        prev = row
    return prev[m]


def _profile_costs(profile: SoundProfile):
    """Cost tables for a profile, computed once per profile object."""
    cached = getattr(profile, "_decoder_costs", None)
    if cached is None:
        sub = {e: {r: profile.cost(e, r) for r in PHONEMES} for e in PHONEMES}
        dele = {e: profile.cost(e, DELETION) for e in PHONEMES}
        ins = {r: profile.insertion_cost(r) for r in PHONEMES}
        cached = (sub, dele, ins)
        object.__setattr__(profile, "_decoder_costs", cached)
    return cached


def confidence(hyps: List[Hypothesis], n_heard: int) -> Tuple[float, float]:
    """(margin, cost per heard phoneme) of the best hypothesis.

    margin = (second - best) / heard phonemes; large = the decoder is sure.
    """
    if not hyps or n_heard == 0:
        return 0.0, math.inf
    best = hyps[0].cost / n_heard
    margin = (hyps[1].cost - hyps[0].cost) / n_heard if len(hyps) > 1 else math.inf
    return margin, best
