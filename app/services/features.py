"""Feature extraction for whole-phrase (DTW) matching, with on-disk caching.

Features are cached under `<DATA_DIR>/features/<extractor.name>/<phrase_id>/<sample_id>.npy`.
The extractor name encodes its parameters, so switching extractors (or their
settings) never reads stale features from another configuration.
"""

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

import librosa
import numpy as np

from app.services.audio import TARGET_SR

CMVN_EPS = 1e-8


class FeatureExtractor(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique, parameter-encoding name used as the cache directory."""

    @abstractmethod
    def extract(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Return a (frames, dims) float32 feature matrix."""


class MFCCExtractor(FeatureExtractor):
    """13 MFCCs (+ delta + delta-delta = 39 dims) with per-utterance CMVN."""

    def __init__(self, n_mfcc: int = 13, include_deltas: bool = True):
        self.n_mfcc = n_mfcc
        self.include_deltas = include_deltas

    @property
    def name(self) -> str:
        return f"mfcc_{self.n_mfcc}_{'deltas' if self.include_deltas else 'nodeltas'}"

    def extract(self, audio: np.ndarray, sr: int) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float32)
        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=self.n_mfcc)  # (n_mfcc, frames)
        if self.include_deltas:
            # librosa.feature.delta needs width <= frames; pick the largest odd width that fits.
            width = min(9, mfcc.shape[1] if mfcc.shape[1] % 2 == 1 else mfcc.shape[1] - 1)
            if width >= 3:
                d1 = librosa.feature.delta(mfcc, width=width)
                d2 = librosa.feature.delta(mfcc, width=width, order=2)
            else:
                d1 = np.zeros_like(mfcc)
                d2 = np.zeros_like(mfcc)
            mfcc = np.vstack([mfcc, d1, d2])
        feats = mfcc.T  # (frames, dims)
        feats = (feats - feats.mean(axis=0)) / (feats.std(axis=0) + CMVN_EPS)
        return feats.astype(np.float32)


_EXTRACTORS = {
    "mfcc": MFCCExtractor,
    # Future (PRD N6): "hubert", "wav2vec2"
}


def get_extractor(name: str) -> FeatureExtractor:
    try:
        return _EXTRACTORS[name.lower()]()
    except KeyError:
        raise ValueError(
            f"Unknown feature extractor {name!r}; choose one of {sorted(_EXTRACTORS)}"
        ) from None


class FeatureCache:
    def __init__(self, data_dir: Path, extractor: FeatureExtractor):
        self.root = Path(data_dir) / "features"
        self.extractor = extractor
        self.cache_dir = self.root / extractor.name

    def path_for(self, phrase_id: str, sample_id: str) -> Path:
        return self.cache_dir / phrase_id / f"{sample_id}.npy"

    def put(self, phrase_id: str, sample_id: str, audio: np.ndarray, sr: int) -> np.ndarray:
        """Extract features from in-memory audio and cache them."""
        feats = self.extractor.extract(audio, sr)
        path = self.path_for(phrase_id, sample_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, feats)
        return feats

    def get_or_extract(self, phrase_id: str, sample_id: str, audio_path: Path) -> np.ndarray:
        path = self.path_for(phrase_id, sample_id)
        if path.exists():
            return np.load(path)
        audio, sr = librosa.load(audio_path, sr=TARGET_SR, mono=True)
        return self.put(phrase_id, sample_id, audio, sr)

    def invalidate(self, phrase_id: str, sample_id: str) -> None:
        """Remove a sample's cached features for every extractor."""
        if not self.root.exists():
            return
        for extractor_dir in self.root.iterdir():
            (extractor_dir / phrase_id / f"{sample_id}.npy").unlink(missing_ok=True)

    def invalidate_phrase(self, phrase_id: str) -> None:
        """Remove all cached features of a phrase for every extractor."""
        if not self.root.exists():
            return
        for extractor_dir in self.root.iterdir():
            shutil.rmtree(extractor_dir / phrase_id, ignore_errors=True)
