"""DTW whole-phrase ranking. Scores are distances, not probabilities."""
import logging
from uuid import uuid4

import numpy as np
from fastdtw import fastdtw

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
        distance = cosine_distance if self.metric == 'cosine' else lambda a, b: float(np.linalg.norm(a - b))
        for phrase in self.phrase_store.list_phrases():
            scores = []
            for sample in phrase.samples:
                try:
                    template = feature_matrix(self.feature_cache.get_or_extract(
                        phrase.id, sample.id, self.phrase_store.get_audio_path(phrase.id, sample.id)))
                    if query.shape[1] != template.shape[1]:
                        raise ValueError('Feature dimensions differ; invalidate old caches')
                    total, path = fastdtw(query, template, dist=distance)
                    score = float(total / len(path))
                    if np.isfinite(score):
                        scores.append(score)
                except (OSError, ValueError):
                    log.warning('Skipping invalid template %s/%s', phrase.id, sample.id, exc_info=True)
            if scores:
                candidates.append(MatchCandidate(phrase_id=phrase.id, text=phrase.text,
                    emoji=phrase.emoji, image=phrase.image, score=min(scores)))
        candidates.sort(key=lambda item: (item.score, item.phrase_id))
        return MatchResult(attempt_id=str(uuid4()), confident=self._is_confident(candidates), candidates=candidates[:3])

    def _is_confident(self, candidates):
        if not candidates or candidates[0].type != 'phrase' or candidates[0].score > self.absolute_max:
            return False
        if len(candidates) == 1:
            return True
        best, second = candidates[0].score, candidates[1].score
        return second > best and (second - best) / second >= self.margin
