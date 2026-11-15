"""Phoneme recognition with on-disk caching (Task 15).

Turns a cleaned recording into a canonical phoneme sequence plus per-frame
posteriors, caching both so an enrolled sample is never recognized twice.

Design notes
------------

**Injectable backend.** The heavy model sits behind the :class:`PhonemeBackend`
protocol. :class:`Wav2Vec2PhonemeBackend` is the real one and imports
``torch``/``transformers`` *lazily*, inside ``load()`` — importing this module
never pulls in a multi-gigabyte dependency, so the Task 13 send proxy and the
Task 17 coverage checker can import the phonetics package cheaply. It also
lets the tests exercise caching and symbol mapping without the weights.

**Input contract.** Audio must already be through Task 3's preprocessing:
16 kHz, mono, 16-bit PCM WAV. :func:`read_pcm_wav` enforces that with the
standard library's :mod:`wave` module, which keeps this module free of a
``soundfile``/``librosa`` dependency and doubles as a contract check on Task 3.

**Latency.** The PRD flags model latency on a laptop CPU as a risk and asks
for it to be measured early. Every result carries ``inference_sec``;
``scripts/measure_phoneme_latency.py`` reports percentiles over a batch.
"""

from __future__ import annotations

import json
import logging
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol, Sequence

import numpy as np

from app.config import get_settings
from app.phonetics import inventory, mapping

logger = logging.getLogger(__name__)

#: Candidate checkpoint from the PRD. Still an open question: its output
#: symbol set, license and CPU latency all need confirming before this is
#: locked in (see docs/tasks-13-14-15.md).
DEFAULT_MODEL = "facebook/wav2vec2-lv-60-espeak-cv-ft"

#: Task 3 normalizes every upload to this. Anything else is a bug upstream.
REQUIRED_SAMPLE_RATE = 16_000
REQUIRED_CHANNELS = 1
REQUIRED_SAMPLE_WIDTH = 2  # bytes, i.e. 16-bit


class PhonemeRecognizerError(RuntimeError):
    """Recognition could not be performed."""


class ModelUnavailableError(PhonemeRecognizerError):
    """The model or its dependencies are not installed locally."""


# ---------------------------------------------------------------------------
# Audio input
# ---------------------------------------------------------------------------

def read_pcm_wav(path) -> tuple[np.ndarray, int]:
    """Read a 16 kHz mono 16-bit PCM WAV into float32 samples in ``[-1, 1)``.

    Raises :class:`PhonemeRecognizerError` if the file is not in the format
    Task 3 guarantees, rather than silently mis-reading it.
    """
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except wave.Error as error:
        raise PhonemeRecognizerError(f"{path} is not a readable WAV file: {error}") from error
    except FileNotFoundError as error:
        raise PhonemeRecognizerError(f"no such audio file: {path}") from error

    problems = []
    if channels != REQUIRED_CHANNELS:
        problems.append(f"{channels} channels (want mono)")
    if width != REQUIRED_SAMPLE_WIDTH:
        problems.append(f"{width * 8}-bit samples (want 16-bit)")
    if rate != REQUIRED_SAMPLE_RATE:
        problems.append(f"{rate} Hz (want {REQUIRED_SAMPLE_RATE} Hz)")
    if problems:
        raise PhonemeRecognizerError(
            f"{path} is {', '.join(problems)}; audio must come through the "
            "Task 3 preprocessing pipeline first"
        )

    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return samples, rate


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------

@dataclass
class BackendResult:
    """Raw output of a phoneme recognition backend, before symbol mapping."""

    raw_text: str
    posteriors: Optional[np.ndarray] = None
    posterior_labels: Optional[List[str]] = None
    frame_rate_hz: Optional[float] = None


class PhonemeBackend(Protocol):
    """Anything that can turn a waveform into recognizer-native phonemes."""

    name: str

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> BackendResult:
        ...


class Wav2Vec2PhonemeBackend:
    """CTC phoneme recognition via a ``transformers`` wav2vec2 checkpoint.

    ``torch`` and ``transformers`` are imported inside :meth:`load`, so
    constructing this object is free and importing the module is free.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, device: Optional[str] = None):
        self.name = model_name
        self._requested_device = device
        self._model = None
        self._processor = None
        self._device = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Load weights into memory. Idempotent."""
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCTC, AutoProcessor
        except ImportError as error:
            raise ModelUnavailableError(
                "phoneme recognition needs torch and transformers: "
                "pip install torch transformers"
            ) from error

        try:
            # local_files_only keeps the demo off the network, per the PRD's
            # requirement that weights be pre-downloaded.
            offline = get_settings().PHONEME_MODEL_OFFLINE
            self._processor = AutoProcessor.from_pretrained(
                self.name, local_files_only=offline
            )
            self._model = AutoModelForCTC.from_pretrained(
                self.name, local_files_only=offline
            )
        except OSError as error:
            raise ModelUnavailableError(
                f"could not load {self.name!r}. Pre-download it before the demo "
                f"with: python -c \"from transformers import AutoProcessor, "
                f"AutoModelForCTC; AutoProcessor.from_pretrained('{self.name}'); "
                f"AutoModelForCTC.from_pretrained('{self.name}')\""
            ) from error

        self._device = self._requested_device or self._pick_device(torch)
        self._model = self._model.to(self._device)
        self._model.eval()
        logger.info("loaded phoneme model %s on %s", self.name, self._device)

    @staticmethod
    def _pick_device(torch) -> str:
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    # -- inference ---------------------------------------------------------

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> BackendResult:
        self.load()
        import torch

        inputs = self._processor(
            samples, sampling_rate=sample_rate, return_tensors="pt", padding=True
        )
        input_values = inputs.input_values.to(self._device)
        attention_mask = getattr(inputs, "attention_mask", None)
        if attention_mask is not None:
            attention_mask = attention_mask.to(self._device)

        with torch.no_grad():
            logits = self._model(input_values, attention_mask=attention_mask).logits

        predicted_ids = torch.argmax(logits, dim=-1)
        raw_text = self._processor.batch_decode(predicted_ids)[0]
        posteriors = torch.softmax(logits, dim=-1)[0].cpu().numpy()

        frames = posteriors.shape[0]
        duration = len(samples) / sample_rate
        frame_rate = frames / duration if duration > 0 else None

        return BackendResult(
            raw_text=raw_text,
            posteriors=posteriors,
            posterior_labels=self._vocabulary(),
            frame_rate_hz=frame_rate,
        )

    def _vocabulary(self) -> Optional[List[str]]:
        tokenizer = getattr(self._processor, "tokenizer", None)
        vocab = getattr(tokenizer, "get_vocab", None)
        if vocab is None:
            return None
        table = vocab()
        return [symbol for symbol, _ in sorted(table.items(), key=lambda item: item[1])]


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class PhonemeRecognition:
    """What the recognizer knows about one recording."""

    phonemes: List[str]
    """Canonical (ARPAbet) phoneme sequence. The input to Task 19's aligner."""

    raw_phonemes: List[str]
    """Recognizer-native symbols, kept so a mapping bug stays diagnosable."""

    model_name: str
    audio_duration_sec: float
    inference_sec: float
    mapping_revision: int = mapping.MAPPING_REVISION
    frame_rate_hz: Optional[float] = None
    posteriors: Optional[np.ndarray] = field(default=None, repr=False)
    posterior_labels: Optional[List[str]] = field(default=None, repr=False)
    from_cache: bool = False

    @property
    def real_time_factor(self) -> Optional[float]:
        """Inference seconds per second of audio. Below 1.0 is faster than real time."""
        if self.audio_duration_sec <= 0:
            return None
        return self.inference_sec / self.audio_duration_sec

    def to_metadata(self) -> dict:
        return {
            "model_name": self.model_name,
            "mapping_revision": self.mapping_revision,
            "phonemes": self.phonemes,
            "raw_phonemes": self.raw_phonemes,
            "audio_duration_sec": self.audio_duration_sec,
            "inference_sec": self.inference_sec,
            "frame_rate_hz": self.frame_rate_hz,
            "posterior_labels": self.posterior_labels,
            "has_posteriors": self.posteriors is not None,
        }


# ---------------------------------------------------------------------------
# Recognizer
# ---------------------------------------------------------------------------

class PhonemeRecognizer:
    """Recognize phonemes, caching by caller-supplied id.

    The cache id is the sample or attempt id, matching the PRD's
    ``phonemes/<sample_or_attempt_id>.json`` layout. Callers that have no
    stable id (an ad-hoc CLI run) pass ``cache_id=None`` and get no caching.
    """

    def __init__(
        self,
        backend: Optional[PhonemeBackend] = None,
        cache_dir=None,
        model_name: Optional[str] = None,
    ):
        self.model_name = model_name or get_settings().PHONEME_MODEL
        self.backend = backend if backend is not None else Wav2Vec2PhonemeBackend(self.model_name)
        self.cache_dir = Path(cache_dir) if cache_dir else get_settings().phoneme_cache_dir

    # -- cache paths -------------------------------------------------------

    def _metadata_path(self, cache_id: str) -> Path:
        return self.cache_dir / f"{cache_id}.json"

    def _posteriors_path(self, cache_id: str) -> Path:
        return self.cache_dir / f"{cache_id}.posteriors.npy"

    # -- public API --------------------------------------------------------

    def is_model_available(self) -> bool:
        """Whether the backend can run. Feeds ``/api/health``."""
        loaded = getattr(self.backend, "is_loaded", None)
        if loaded:
            return True
        load = getattr(self.backend, "load", None)
        if load is None:
            return True  # an injected backend with no load step is ready
        try:
            load()
        except PhonemeRecognizerError:
            return False
        return True

    def recognize(
        self,
        wav_path,
        cache_id: Optional[str] = None,
        want_posteriors: bool = True,
        use_cache: bool = True,
    ) -> PhonemeRecognition:
        """Recognize *wav_path*, reusing the cache when possible."""
        if cache_id and use_cache:
            cached = self._load_cached(cache_id, want_posteriors)
            if cached is not None:
                return cached

        samples, sample_rate = read_pcm_wav(wav_path)
        return self.recognize_samples(samples, sample_rate, cache_id=cache_id,
                                      want_posteriors=want_posteriors)

    def recognize_samples(
        self,
        samples: np.ndarray,
        sample_rate: int,
        cache_id: Optional[str] = None,
        want_posteriors: bool = True,
    ) -> PhonemeRecognition:
        """Recognize audio already in memory (16 kHz mono float32), caching by *cache_id*."""
        if sample_rate != REQUIRED_SAMPLE_RATE:
            raise PhonemeRecognizerError(f"audio must be {REQUIRED_SAMPLE_RATE} Hz, got {sample_rate}")
        samples = np.asarray(samples, dtype=np.float32)
        duration = len(samples) / sample_rate

        started = time.perf_counter()
        result = self.backend.transcribe(samples, sample_rate)
        inference_sec = time.perf_counter() - started

        # A symbol with no mapping is an error, not something to drop: see the
        # module docstring of app.phonetics.mapping.
        raw_phonemes = mapping.tokenize_espeak(result.raw_text)
        phonemes: List[str] = []
        for symbol in raw_phonemes:
            phonemes.extend(mapping.ESPEAK_TO_CANONICAL[symbol])
        inventory.validate_sequence(phonemes)

        recognition = PhonemeRecognition(
            phonemes=phonemes,
            raw_phonemes=raw_phonemes,
            model_name=getattr(self.backend, "name", self.model_name),
            audio_duration_sec=duration,
            inference_sec=inference_sec,
            frame_rate_hz=result.frame_rate_hz,
            posteriors=result.posteriors if want_posteriors else None,
            posterior_labels=result.posterior_labels if want_posteriors else None,
        )

        if cache_id:
            self._store(cache_id, recognition)
        return recognition

    # -- cache -------------------------------------------------------------

    def _load_cached(self, cache_id: str, want_posteriors: bool) -> Optional[PhonemeRecognition]:
        metadata_path = self._metadata_path(cache_id)
        if not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("discarding unreadable phoneme cache %s", metadata_path)
            return None

        expected_model = getattr(self.backend, "name", self.model_name)
        if metadata.get("model_name") != expected_model:
            logger.info(
                "phoneme cache %s was built with %s, want %s; recomputing",
                cache_id, metadata.get("model_name"), expected_model,
            )
            return None
        if metadata.get("mapping_revision") != mapping.MAPPING_REVISION:
            logger.info(
                "phoneme cache %s predates mapping revision %s; recomputing",
                cache_id, mapping.MAPPING_REVISION,
            )
            return None

        posteriors = None
        if want_posteriors and metadata.get("has_posteriors"):
            posteriors_path = self._posteriors_path(cache_id)
            if posteriors_path.exists():
                try:
                    posteriors = np.load(posteriors_path)
                except (OSError, ValueError):
                    logger.warning("discarding unreadable posteriors %s", posteriors_path)
                    return None
            else:
                return None  # metadata promises posteriors that are gone

        return PhonemeRecognition(
            phonemes=list(metadata["phonemes"]),
            raw_phonemes=list(metadata.get("raw_phonemes", [])),
            model_name=metadata["model_name"],
            audio_duration_sec=metadata.get("audio_duration_sec", 0.0),
            inference_sec=metadata.get("inference_sec", 0.0),
            mapping_revision=metadata.get("mapping_revision", mapping.MAPPING_REVISION),
            frame_rate_hz=metadata.get("frame_rate_hz"),
            posteriors=posteriors,
            posterior_labels=metadata.get("posterior_labels"),
            from_cache=True,
        )

    def _store(self, cache_id: str, recognition: PhonemeRecognition) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if recognition.posteriors is not None:
            np.save(self._posteriors_path(cache_id), recognition.posteriors)
        # Atomic write, as the PRD requires of every JSON write.
        metadata_path = self._metadata_path(cache_id)
        temporary = metadata_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(recognition.to_metadata(), indent=2))
        temporary.replace(metadata_path)

    def clear_cache(self, cache_id: str) -> None:
        """Drop one cached recognition, e.g. when its sample is deleted."""
        self._metadata_path(cache_id).unlink(missing_ok=True)
        self._posteriors_path(cache_id).unlink(missing_ok=True)


def recognize_phonemes(wav_path, cache_id: Optional[str] = None) -> Sequence[str]:
    """Convenience wrapper returning just the canonical phoneme sequence."""
    return PhonemeRecognizer().recognize(wav_path, cache_id=cache_id).phonemes
