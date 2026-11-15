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
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
from fastdtw import fastdtw

log = logging.getLogger(__name__)

SR = 16000
HOP = 160            # 10 ms
N_FFT = 400          # 25 ms
#: Segments the aligner was unsure of (mean posterior below this) are skipped.
MIN_SEGMENT_CONFIDENCE = 0.05
#: Units kept per phoneme (most typical first).
MAX_UNITS = 5


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
    total, path = fastdtw(a, b, dist=lambda x, y: float(np.linalg.norm(x - y)))
    return float(total / len(path))


@dataclass
class Unit:
    phoneme: str
    source: str          # take cache id
    features: np.ndarray


class SoundBank:
    def __init__(self, units: Optional[Dict[str, List[Unit]]] = None):
        self.units: Dict[str, List[Unit]] = units or {}

    # -- building ---------------------------------------------------------------

    def add_take(self, source: str, audio: np.ndarray, sr: int, segments) -> int:
        """Add the units of one take; returns how many were kept."""
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

    def remove_source(self, source: str) -> None:
        for phoneme in list(self.units):
            self.units[phoneme] = [u for u in self.units[phoneme] if u.source != source]
            if not self.units[phoneme]:
                del self.units[phoneme]

    def finalize(self) -> None:
        """Order each phoneme's units by typicality (medoid first) and cap them."""
        for phoneme, units in self.units.items():
            if len(units) > 2:
                totals = [sum(_distance(u.features, v.features) for v in units if v is not u) for u in units]
                units = [u for _, u in sorted(zip(totals, units), key=lambda pair: pair[0])]
            self.units[phoneme] = units[:MAX_UNITS]

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
        atomic_write_json(folder / "index.json", index)

    @classmethod
    def load(cls, folder: Path) -> Optional["SoundBank"]:
        folder = Path(folder)
        try:
            index = json.loads((folder / "index.json").read_text())
            arrays = np.load(folder / "units.npz")
        except (OSError, ValueError):
            return None
        units: Dict[str, List[Unit]] = {}
        for item in index:
            if item["key"] in arrays:
                units.setdefault(item["phoneme"], []).append(
                    Unit(item["phoneme"], item["source"], arrays[item["key"]]))
        return cls(units)
