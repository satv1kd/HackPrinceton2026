"""DTW whole-phrase ranking. Scores are distances, not probabilities.

DTW is exact and compiled (librosa's numba implementation over a cost matrix
built in one numpy step). It replaced ``fastdtw`` with a per-frame Python
distance, which made ~90k Python calls per Talk request and held the GIL
throughout. On the saved phrases and 116 real Talk attempts the two agreed on
every confident/unsure decision and on all but one (unsure) top phrase, while
this is about 13x faster.
"""
import logging
from uuid import uuid4

import numpy as np

from app.models.recognition import MatchCandidate, MatchResult

log = logging.getLogger(__name__)


def feature_matrix(value):
    value = np.asarray(value, dtype=np.float64)
    if value.ndim != 2 or min(value.shape) == 0 or not np.isfinite(value).all():
        raise ValueError('Features must be a nonempty finite frames × dimensions matrix')
    return value


def cosine_distance(a, b):
    # scipy cosine yields NaN on zero vectors. Silent frames must remain finite.
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0 if na == nb else 1.0
    return float(np.clip(1 - np.dot(a, b) / (na * nb), 0, 2))


def distance_matrix(a, b, metric='cosine'):
    """Frame-to-frame costs (a frames × b frames), as ``cosine_distance`` would give them."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if metric == 'euclidean':
        return np.sqrt(np.maximum(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2), 0.0))
    na, nb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    with np.errstate(invalid='ignore', divide='ignore'):
        cost = np.clip(1 - (a @ b.T) / np.outer(na, nb), 0, 2)
    za, zb = na == 0, nb == 0
    cost[za[:, None] | zb[None, :]] = 1.0          # silent vs. sound: as different as it gets
    cost[za[:, None] & zb[None, :]] = 0.0          # silent vs. silent: the same
    return cost


def dtw_distance(a, b, metric='cosine'):
    """Path-normalised DTW distance between two (frames × dims) feature matrices."""
    from librosa.sequence import dtw

    total, path = dtw(C=distance_matrix(a, b, metric), backtrack=True)
    return float(total[-1, -1] / len(path))


class DTWMatcher:
    def __init__(self, config, phrase_store, feature_cache):
        self.margin = float(config.MARGIN)
        self.absolute_max = float(config.ABSOLUTE_MAX)
        self.metric = getattr(config, 'DTW_METRIC', 'cosine')
        if not 0 <= self.margin <= 1 or not np.isfinite(self.absolute_max) or self.absolute_max < 0:
            raise ValueError('Invalid confidence thresholds')
        if self.metric not in ('cosine', 'euclidean'):
            raise ValueError('DTW_METRIC must be cosine or euclidean')
        self.phrase_store, self.feature_cache = phrase_store, feature_cache

    def match(self, query_features):
        query = feature_matrix(query_features)
        candidates = []
        for phrase in self.phrase_store.list_phrases():
            scores = []
            for sample in phrase.samples:
                try:
                    template = feature_matrix(self.feature_cache.get_or_extract(
                        phrase.id, sample.id, self.phrase_store.sample_path(phrase.id, sample.id)))
                    if query.shape[1] != template.shape[1]:
                        raise ValueError('Feature dimensions differ; invalidate old caches')
                    score = dtw_distance(query, template, self.metric)
                    if np.isfinite(score):
                        scores.append(score)
                except (OSError, ValueError):
                    log.warning('Skipping invalid template %s/%s', phrase.id, sample.id, exc_info=True)
            if scores:
                candidates.append(MatchCandidate(phrase_id=phrase.id, text=phrase.text,
                    emoji=phrase.emoji, image=f'/api/phrases/{phrase.id}/image' if phrase.image else None, score=min(scores)))
        candidates.sort(key=lambda item: (item.score, item.phrase_id))
        return MatchResult(attempt_id=str(uuid4()), confident=self._is_confident(candidates), candidates=candidates[:3])

    def _is_confident(self, candidates):
        if not candidates or candidates[0].type != 'phrase' or candidates[0].score > self.absolute_max:
            return False
        if len(candidates) == 1:
            return True
        best, second = candidates[0].score, candidates[1].score
        return second > best and (second - best) / second >= self.margin
