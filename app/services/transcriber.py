"""Talk-mode speech-to-text: what did the person actually say?

Saved-phrase matching (DTW) can only ever answer "which of my phrases was
that?". This module answers the open question with a local Whisper model
(faster-whisper / CTranslate2), so audio never leaves the machine.

Design notes
------------

**Lazy, shared, optional.** The model is loaded on first use (or warmed in a
background thread at startup) and shared across requests behind a lock.
If faster-whisper is not installed or the model cannot be loaded, the
transcriber reports itself unavailable and recognition falls back to
phrase matching alone — the app keeps working.

**Biased toward the user's vocabulary.** The user's saved phrases are passed
as Whisper's ``initial_prompt``. That nudges spelling and word choice toward
things this person actually says, which matters most for names and personal
words.

**Honest about quality.** Every result carries Whisper's own confidence
signals (``avg_logprob``, ``no_speech_prob``). :meth:`Transcript.usable`
uses them to discard silence and the stock phrases Whisper is known to
hallucinate on near-silent audio ("Thank you.", "you").
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

#: Whisper's well-known outputs on silence or noise. Only discarded when the
#: model itself also thinks there was little speech.
HALLUCINATIONS = {
    "you", "thank you", "thanks for watching", "thank you for watching",
    "bye", "okay", "so", "uh", "um", "hmm",
}

#: Keep the vocabulary prompt short: Whisper reads at most ~224 tokens of it,
#: and a long prompt slows decoding for no benefit.
MAX_PROMPT_CHARS = 400

MIN_AVG_LOGPROB = -1.2
MAX_NO_SPEECH_PROB = 0.6


@dataclass(frozen=True)
class Transcript:
    text: str
    avg_logprob: float
    no_speech_prob: float
    inference_sec: float

    @property
    def usable(self) -> bool:
        """Is this worth showing the user as "what you said"?"""
        cleaned = self.text.strip().strip(".!?,").strip().lower()
        if not cleaned:
            return False
        if self.no_speech_prob > MAX_NO_SPEECH_PROB:
            return False
        if self.avg_logprob < MIN_AVG_LOGPROB:
            return False
        if cleaned in HALLUCINATIONS and self.no_speech_prob > 0.3:
            return False
        return True


def build_prompt(phrases: Iterable[str]) -> Optional[str]:
    """Join saved phrase texts into a short vocabulary hint."""
    seen, parts, length = set(), [], 0
    for text in phrases:
        text = " ".join(str(text).split())
        key = text.lower()
        if not text or key in seen:
            continue
        if length + len(text) + 2 > MAX_PROMPT_CHARS:
            break
        seen.add(key)
        parts.append(text)
        length += len(text) + 2
    if not parts:
        return None
    return ". ".join(parts) + "."


class Transcriber:
    """Thread-safe, lazily loaded faster-whisper wrapper."""

    def __init__(
        self,
        model_name: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 4,
        beam_size: int = 1,
        model_factory: Optional[Callable[[], object]] = None,
    ):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        self._model_factory = model_factory or self._load_faster_whisper
        self._model = None
        self._load_error: Optional[str] = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings) -> Optional["Transcriber"]:
        # Off unless the settings object opts in: test doubles that predate
        # transcription (plain namespaces) must not trigger a model download.
        if not getattr(settings, "TRANSCRIBE_ENABLED", False):
            return None
        return cls(
            model_name=getattr(settings, "WHISPER_MODEL", "base.en"),
            device=getattr(settings, "WHISPER_DEVICE", "cpu"),
            compute_type=getattr(settings, "WHISPER_COMPUTE_TYPE", "int8"),
            cpu_threads=getattr(settings, "WHISPER_CPU_THREADS", 4),
            beam_size=getattr(settings, "WHISPER_BEAM_SIZE", 1),
        )

    # -- loading -----------------------------------------------------------

    def _load_faster_whisper(self):
        from faster_whisper import WhisperModel  # heavy; import lazily

        return WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type,
                            cpu_threads=self.cpu_threads)

    def _ensure_model(self):
        if self._model is not None or self._load_error is not None:
            return self._model
        with self._lock:
            if self._model is None and self._load_error is None:
                started = time.perf_counter()
                try:
                    self._model = self._model_factory()
                    logger.info("loaded Whisper %s in %.1fs", self.model_name,
                                time.perf_counter() - started)
                except Exception as error:  # ImportError, download/network, bad weights
                    self._load_error = f"{type(error).__name__}: {error}"
                    logger.warning("Whisper unavailable (%s); Talk falls back to phrase matching",
                                   self._load_error)
        return self._model

    def warm_up(self) -> None:
        """Load the model now (call from a background thread at startup)."""
        self._ensure_model()

    @property
    def status(self) -> dict:
        if self._model is not None:
            return {"loaded": True, "model": self.model_name}
        if self._load_error is not None:
            return {"loaded": False, "model": self.model_name, "error": self._load_error}
        return {"loaded": False, "model": self.model_name, "note": "loads on first use"}

    @property
    def available(self) -> bool:
        return self._ensure_model() is not None

    # -- transcription -----------------------------------------------------

    def transcribe(self, audio: np.ndarray, hints: Sequence[str] = ()) -> Optional[Transcript]:
        """Transcribe 16 kHz mono float audio. ``None`` if the model is unavailable."""
        model = self._ensure_model()
        if model is None:
            return None
        audio = np.asarray(audio, dtype=np.float32)
        started = time.perf_counter()
        # One decode at a time: CTranslate2 parallelizes internally, and
        # interleaving requests on a laptop CPU only makes each one slower.
        with self._lock:
            segments, _info = model.transcribe(
                audio,
                language="en",
                beam_size=self.beam_size,
                initial_prompt=build_prompt(hints),
                condition_on_previous_text=False,
                vad_filter=False,  # Task 3 already trimmed silence
                without_timestamps=True,
            )
            segments = list(segments)  # the generator does the real work
        elapsed = time.perf_counter() - started
        if not segments:
            return Transcript("", avg_logprob=-10.0, no_speech_prob=1.0, inference_sec=elapsed)
        text = " ".join(s.text.strip() for s in segments).strip()
        weights = [max(1, len(s.text)) for s in segments]
        avg_logprob = float(np.average([s.avg_logprob for s in segments], weights=weights))
        no_speech = float(max(s.no_speech_prob for s in segments))
        return Transcript(text, avg_logprob=avg_logprob, no_speech_prob=no_speech, inference_sec=elapsed)
