"""The personal sound profile (Task 27): how this person's sounds come out.

For every intended phoneme, a probability distribution over what the phoneme
recognizer hears when *this* person says it - itself, another phoneme, or
nothing (deletion) - plus how often extra sounds appear (insertion):

    P(recognized | intended)

Built by aligning (Task 26) the known phonemes of each enrollment take with
what the model heard, and counting. It models the speaker *and* the
recognizer together: if the model always hears this person's /r/ as [w], the
decoder learns to read [w] as a likely /r/, whichever of them is "at fault".

Smoothing: each row mixes the counts with a generic prior worth
``alpha`` pseudo-observations::

    P(r | p) = (C[p][r] + alpha * Prior(r | p)) / (sum_r C[p][r] + alpha)

The prior is identity-heavy, spreads the rest by articulatory similarity
(``inventory.feature_similarity``) and keeps a little mass for deletion, so a
sound with few examples stays close to "it sounds like itself", and nothing
ever has probability zero.

Stored versioned (``DATA_DIR/profile/profile_v<N>.json`` + ``current.json``)
so retraining (Task 31) can roll back.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app.phonetics.aligner import Alignment
from app.phonetics.inventory import DELETION, INSERTION, PHONEMES, feature_similarity

#: Prior: P(heard as itself), the share spread over similar phonemes, P(deleted).
PRIOR_SELF = 0.80
PRIOR_DELETE = 0.03
PRIOR_SIMILAR = 1.0 - PRIOR_SELF - PRIOR_DELETE
#: Prior chance of an extra (inserted) phoneme per intended phoneme.
PRIOR_INSERT_RATE = 0.05
OUTCOMES = PHONEMES + (DELETION,)


def _prior_row(phoneme: str) -> Dict[str, float]:
    sims = {r: feature_similarity(phoneme, r) for r in PHONEMES if r != phoneme}
    total = sum(sims.values())
    row = {r: (PRIOR_SIMILAR * s / total if total else PRIOR_SIMILAR / len(sims)) for r, s in sims.items()}
    # A floor so no outcome is impossible, even across the vowel/consonant divide.
    floor = 1e-4
    row = {r: max(v, floor) for r, v in row.items()}
    row[phoneme] = PRIOR_SELF
    row[DELETION] = PRIOR_DELETE
    norm = sum(row.values())
    return {r: v / norm for r, v in row.items()}


PRIOR: Dict[str, Dict[str, float]] = {p: _prior_row(p) for p in PHONEMES}


@dataclass
class SoundProfile:
    alpha: float = 1.0
    #: intended phoneme -> recognized outcome (phoneme or DELETION) -> count
    counts: Dict[str, Dict[str, float]] = field(default_factory=dict)
    #: recognized phoneme -> count, for sounds heard with no intended target
    insertions: Dict[str, float] = field(default_factory=dict)
    version: int = 0
    created_at: str = ""
    #: Where the data came from: {"takes": n, "feedback": n}
    sources: Dict[str, int] = field(default_factory=dict)

    # -- building -------------------------------------------------------------

    @classmethod
    def build(cls, alignments: Iterable[Alignment], alpha: float = 1.0, sources=None) -> "SoundProfile":
        profile = cls(alpha=alpha, sources=dict(sources or {}))
        for alignment in alignments:
            profile.add(alignment)
        return profile

    def add(self, alignment: Alignment, weight: float = 1.0) -> None:
        for pair in alignment.pairs:
            if pair.intended == INSERTION:
                self.insertions[pair.recognized] = self.insertions.get(pair.recognized, 0.0) + weight
            else:
                row = self.counts.setdefault(pair.intended, {})
                row[pair.recognized] = row.get(pair.recognized, 0.0) + weight

    # -- reading --------------------------------------------------------------

    def examples(self, intended: str) -> float:
        return sum(self.counts.get(intended, {}).values())

    def coverage(self) -> Dict[str, int]:
        return {p: int(round(self.examples(p))) for p in PHONEMES}

    def probability(self, intended: str, recognized: str) -> float:
        prior = PRIOR[intended].get(recognized, 0.0)
        row = self.counts.get(intended, {})
        return (row.get(recognized, 0.0) + self.alpha * prior) / (sum(row.values()) + self.alpha)

    def cost(self, intended: str, recognized: str) -> float:
        """-log P(recognized | intended); use DELETION for 'not produced'."""
        return -math.log(self.probability(intended, recognized))

    def insertion_rate(self) -> float:
        intended_total = sum(sum(row.values()) for row in self.counts.values())
        inserted = sum(self.insertions.values())
        return (inserted + self.alpha * PRIOR_INSERT_RATE) / (intended_total + self.alpha)

    def insertion_cost(self, recognized: str) -> float:
        """-log P(an extra `recognized` sound appears)."""
        total = sum(self.insertions.values())
        share = (self.insertions.get(recognized, 0.0) + self.alpha / len(PHONEMES)) / (total + self.alpha)
        return -math.log(self.insertion_rate() * share)

    def top_confusions(self, limit: int = 10, min_examples: float = 2) -> List[dict]:
        """The strongest learned patterns: intended -> most likely other outcome."""
        found = []
        for intended in PHONEMES:
            if self.examples(intended) < min_examples:
                continue
            best = max((r for r in OUTCOMES if r != intended), key=lambda r: self.probability(intended, r))
            found.append({"intended": intended, "heard": best,
                          "probability": round(self.probability(intended, best), 3),
                          "self": round(self.probability(intended, intended), 3),
                          "examples": int(round(self.examples(intended)))})
        found.sort(key=lambda f: -f["probability"])
        return found[:limit]

    # -- persistence ----------------------------------------------------------

    def to_json(self) -> dict:
        return {"version": self.version, "created_at": self.created_at, "alpha": self.alpha,
                "counts": self.counts, "insertions": self.insertions, "sources": self.sources}

    @classmethod
    def from_json(cls, data: dict) -> "SoundProfile":
        return cls(alpha=float(data.get("alpha", 1.0)),
                   counts={p: {r: float(n) for r, n in row.items()} for p, row in data.get("counts", {}).items()},
                   insertions={r: float(n) for r, n in data.get("insertions", {}).items()},
                   version=int(data.get("version", 0)), created_at=data.get("created_at", ""),
                   sources=dict(data.get("sources", {})))


class ProfileStore:
    """Versioned profiles on disk; ``current.json`` names the active one."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "profile"

    @property
    def pointer(self) -> Path:
        return self.root / "current.json"

    def path(self, version: int) -> Path:
        return self.root / f"profile_v{version}.json"

    def versions(self) -> List[int]:
        if not self.root.exists():
            return []
        out = []
        for path in self.root.glob("profile_v*.json"):
            try:
                out.append(int(path.stem.split("_v", 1)[1]))
            except ValueError:
                continue
        return sorted(out)

    def save(self, profile: SoundProfile, activate: bool = True) -> SoundProfile:
        from app.services.storage import atomic_write_json

        profile.version = (self.versions() or [0])[-1] + 1
        profile.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        atomic_write_json(self.path(profile.version), profile.to_json())
        if activate:
            self.activate(profile.version)
        return profile

    def activate(self, version: int) -> None:
        from app.services.storage import atomic_write_json

        if not self.path(version).exists():
            raise FileNotFoundError(self.path(version))
        atomic_write_json(self.pointer, {"version": version})

    def current_version(self) -> Optional[int]:
        try:
            return int(json.loads(self.pointer.read_text())["version"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def load(self, version: Optional[int] = None) -> Optional[SoundProfile]:
        version = version if version is not None else self.current_version()
        if version is None:
            return None
        try:
            return SoundProfile.from_json(json.loads(self.path(version).read_text()))
        except (OSError, ValueError):
            return None
