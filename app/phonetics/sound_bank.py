"""The sound bank (Task 28): this person's own sounds, ready to chain into words.

Every enrollment take is cut into phoneme segments (``segmenter``) and each
segment's acoustic features are kept as a *unit* of that phoneme. For any
dictionary word, chaining the most typical unit of each of its phonemes gives
a template of how *this person* would probably say it - so a candidate word
the decoder proposes can be checked against the recording acoustically
(``score``), not just by symbols.

Features are fine-grained MFCCs (10 ms hop, 13 + deltas, CMVN per take),
separate from the 32 ms whole-phrase features, because phonemes are short.
Stored as one ``.npz`` (units) + ``.json`` (index) under DATA_DIR/sound_bank.

Every unit is kept (ordered most typical first), so one take can be swapped
in or out with :meth:`SoundBank.update` without rebuilding the rest.
Distances use librosa's compiled DTW: exact, and an order of magnitude faster
than ``fastdtw`` with a Python distance function, which matters because
ordering a sound's units compares every pair of them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
log = logging.getLogger(__name__)

SR = 16000
HOP = 160            # 10 ms
N_FFT = 400          # 25 ms
#: Segments the aligner was unsure of (mean posterior below this) are skipped.
MIN_SEGMENT_CONFIDENCE = 0.05
#: Index format: 2 keeps every unit; 1 (a bare list) kept only the top 5.
FORMAT = 2


def fine_features(audio: np.ndarray, sr: int = SR) -> np.ndarray:
    """(frames, 39) MFCC + deltas at 10 ms, CMVN-normalised."""
    import librosa

    audio = np.asarray(audio, dtype=np.float32)
    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=13, n_fft=N_FFT, hop_length=HOP)
    width = min(9, mfcc.shape[1] if mfcc.shape[1] % 2 else mfcc.shape[1] - 1)
    if width >= 3:
        feats = np.vstack([mfcc, librosa.feature.delta(mfcc, width=width),
                           librosa.feature.delta(mfcc, width=width, order=2)])
    else:
        feats = np.vstack([mfcc, np.zeros_like(mfcc), np.zeros_like(mfcc)])
    feats = feats.T
    return ((feats - feats.mean(axis=0)) / (feats.std(axis=0) + 1e-8)).astype(np.float32)


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    """Path-normalised DTW distance (Euclidean frames) between (frames, dims) arrays."""
    from librosa.sequence import dtw

    cost, path = dtw(X=np.asarray(a, dtype=np.float32).T, Y=np.asarray(b, dtype=np.float32).T,
                     metric="euclidean", backtrack=True)
    return float(cost[-1, -1] / len(path))


@dataclass
class Unit:
    phoneme: str
    source: str          # take cache id
    features: np.ndarray


class SoundBank:
    def __init__(self, units: Optional[Dict[str, List[Unit]]] = None, complete: bool = True):
        self.units: Dict[str, List[Unit]] = units or {}
        #: False for a bank saved in the old capped format: it lacks units, so
        #: it must be rebuilt from the takes before it can be updated in place.
        self.complete = complete

    # -- building ---------------------------------------------------------------

    def add_take(self, source: str, audio: np.ndarray, sr: int, segments) -> int:
        """Add the units of one take (call :meth:`finalize` after); returns how many were kept."""
        feats = fine_features(audio, sr)
        rate = sr / HOP
        kept = 0
        for seg in segments:
            if seg.confidence < MIN_SEGMENT_CONFIDENCE:
                continue
            a, b = int(seg.start_sec * rate), max(int(seg.end_sec * rate), int(seg.start_sec * rate) + 1)
            chunk = feats[a:b]
            if len(chunk) == 0:
                continue
            self.units.setdefault(seg.phoneme, []).append(Unit(seg.phoneme, source, chunk))
            kept += 1
        return kept

    def remove_source(self, source: str) -> set:
        """Drop one take's units; returns the phonemes that lost any."""
        touched = set()
        for phoneme in list(self.units):
            kept = [u for u in self.units[phoneme] if u.source != source]
            if len(kept) != len(self.units[phoneme]):
                touched.add(phoneme)
            if kept:
                self.units[phoneme] = kept
            else:
                del self.units[phoneme]
        return touched

    def finalize(self, phonemes: Optional[Iterable[str]] = None) -> None:
        """Order units by typicality (medoid first): all phonemes, or just *phonemes*."""
        for phoneme in (self.units if phonemes is None else [p for p in phonemes if p in self.units]):
            units = self.units[phoneme]
            if len(units) <= 2:
                continue
            n = len(units)
            dist = np.zeros((n, n))
            for i in range(n):
                for j in range(i + 1, n):
                    dist[i, j] = dist[j, i] = _distance(units[i].features, units[j].features)
            order = np.argsort(dist.sum(axis=1), kind="stable")
            self.units[phoneme] = [units[i] for i in order]

    def update(self, source: str, audio: Optional[np.ndarray] = None, sr: int = SR,
               segments=()) -> "SoundBank":
        """A new bank with *source*'s units replaced by this take's (or removed when
        *audio* is None). Only the phonemes that changed are re-ordered. The bank
        itself is left alone, so readers holding it never see a half-done update."""
        bank = SoundBank({p: list(units) for p, units in self.units.items()}, complete=self.complete)
        touched = bank.remove_source(source)
        if audio is not None:
            before = {p: len(u) for p, u in bank.units.items()}
            bank.add_take(source, audio, sr, segments)
            touched |= {p for p, u in bank.units.items() if len(u) != before.get(p, 0)}
        bank.finalize(touched)
        return bank

    # -- using ------------------------------------------------------------------

    def coverage(self) -> Dict[str, int]:
        return {p: len(u) for p, u in self.units.items()}

    def has(self, phonemes: Sequence[str]) -> bool:
        return all(p in self.units for p in phonemes)

    def chain(self, phonemes: Sequence[str]) -> Optional[np.ndarray]:
        """Template for a phoneme sequence from the most typical units, or None."""
        if not phonemes or not self.has(phonemes):
            return None
        return np.vstack([self.units[p][0].features for p in phonemes])

    def score(self, query: np.ndarray, phonemes: Sequence[str]) -> Optional[float]:
        """Path-normalised DTW distance between a query and the chained template
        (lower = closer); None when a phoneme has no unit."""
        template = self.chain(phonemes)
        if template is None or len(query) == 0:
            return None
        return _distance(np.asarray(query, dtype=np.float32), template)

    # -- persistence --------------------------------------------------------------

    def save(self, folder: Path) -> None:
        from app.services.storage import atomic_write_json

        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        arrays, index = {}, []
        for phoneme, units in sorted(self.units.items()):
            for i, unit in enumerate(units):
                key = f"{phoneme}_{i}"
                arrays[key] = unit.features
                index.append({"key": key, "phoneme": phoneme, "source": unit.source})
        tmp = folder / "units.tmp.npz"
        np.savez(tmp, **arrays)
        tmp.replace(folder / "units.npz")
        atomic_write_json(folder / "index.json", {"format": FORMAT, "complete": self.complete,
                                                  "units": index})

    @classmethod
    def load(cls, folder: Path) -> Optional["SoundBank"]:
        folder = Path(folder)
        try:
            index = json.loads((folder / "index.json").read_text())
            arrays = np.load(folder / "units.npz")
        except (OSError, ValueError):
            return None
        if isinstance(index, dict):
            items, complete = index.get("units", []), bool(index.get("complete", True))
        else:
            items, complete = index, False      # format 1: capped at 5 units per sound
        units: Dict[str, List[Unit]] = {}
        for item in items:
            if item["key"] in arrays:
                units.setdefault(item["phoneme"], []).append(
                    Unit(item["phoneme"], item["source"], arrays[item["key"]]))
        return cls(units, complete=complete)
