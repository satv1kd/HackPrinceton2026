"""Pluggable recognition stages (Task 17).

Recognition is a cascade: each stage looks at the same cleaned recording and
either answers or hands over to the next one.

    saved phrase (DTW)  ->  phonetic decoder  ->  speech-to-text
                           (phonetic mode on)

Only the saved-phrase stage may end the cascade with a *confident* answer
(auto-spoken). Every other stage produces text the user confirms before it is
spoken or sent: a wrong guess said out loud is worse than one extra tap.

Adding a recognizer means writing one class with ``name``, ``available``,
``enabled`` and ``run``; the router, the response schema and the client do not
change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import numpy as np

from app.models.recognition import MatchCandidate
from app.services.task_io import data_lock

log = logging.getLogger(__name__)


@dataclass
class RecognitionContext:
    attempt_id: str
    audio: np.ndarray        # cleaned 16 kHz mono float32 (Task 3 output)
    sr: int
    wav_bytes: bytes
    features: np.ndarray     # from the configured FeatureExtractor
    phonetic_mode: bool = False
    #: Outputs earlier stages leave for later ones and for the attempt record
    #: (e.g. "phrase_result", "transcript", "recognized_phonemes").
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    stage: str
    candidates: List[MatchCandidate]
    #: May this result end the cascade and be spoken without a tap? The
    #: cascade honours it only for stages with ``may_confirm = True``.
    confident: bool = False
    #: The best candidate is believable even if not confident (for DTW: within
    #: ABSOLUTE_MAX). Used to order the merged list when nobody is confident.
    plausible: bool = False


class RecognitionStage(Protocol):
    name: str
    may_confirm: bool

    def available(self) -> bool:
        """Dependencies installed, models present, data ready."""

    def enabled(self, ctx: RecognitionContext) -> bool:
        """Whether this attempt should use the stage (e.g. phonetic mode on)."""

    def run(self, ctx: RecognitionContext) -> StageResult:
        ...


class PhraseMatchStage:
    """Whole-phrase DTW against the user's saved recordings (Task 5)."""

    name = "phrase"
    may_confirm = True

    def __init__(self, matcher, data_dir: Optional[Path] = None):
        self.matcher = matcher
        self.data_dir = Path(data_dir) if data_dir is not None else None

    def available(self) -> bool:
        return True

    def enabled(self, ctx: RecognitionContext) -> bool:
        return True

    def run(self, ctx: RecognitionContext) -> StageResult:
        # Templates are read under the shared data lock so feedback cannot
        # add/evict samples mid-match (unchanged from the pre-cascade code).
        if self.data_dir is not None:
            with data_lock(self.data_dir):
                result = self.matcher.match(ctx.features)
        else:
            result = self.matcher.match(ctx.features)
        candidates = [c.model_copy(update={"source": "phrase"}) for c in result.candidates]
        absolute_max = getattr(self.matcher, "absolute_max", float("inf"))
        plausible = bool(candidates) and candidates[0].score <= absolute_max
        stage_result = StageResult(self.name, candidates, result.confident, plausible)
        ctx.extras["phrase_result"] = stage_result
        return stage_result
