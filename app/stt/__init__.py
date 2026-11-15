"""Local speech-to-text (Task 18): the default fallback when no saved phrase matches.

Runs offline with faster-whisper: no key, no per-use cost, and the user's
audio never leaves the machine.

Design mirrors ``app/phonetics/recognizer.py``:

* **Optional install.** ``faster_whisper`` (and its ctranslate2/onnxruntime
  stack) is imported lazily inside :meth:`FasterWhisperBackend.load`, so
  ``import app.stt`` is cheap and the app runs without it
  (``make install-stt`` adds it).
* **Injectable backend.** Recognition code depends on the
  :class:`SpeechToTextBackend` protocol; tests use a fake.
* **Input contract.** Audio must already be through Task 3's preprocessing:
  16 kHz mono float32.

Latency is logged per call (``Transcript.inference_sec``); see the README of
``scripts/download_models.py`` for pre-fetching the model before a demo.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Protocol

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

#: Phrases Whisper emits when it is fed near-silence. Dropped only when the
#: no-speech probability agrees, so a user who really did say "okay" keeps it.
HALLUCINATIONS = frozenset({
    "you", "thank you", "thanks for watching", "thank you for watching",
    "bye", "okay", "so", "uh", "um", "hmm",
})
#: ``initial_prompt`` is a vocabulary hint, not a transcript: past a few
#: hundred characters it starts competing with the audio.
MAX_PROMPT_CHARS = 400
#: Mean token log-prob below which the decode is guesswork.
MIN_AVG_LOGPROB = -1.2
#: Above this, the segment is more likely silence than speech.
MAX_NO_SPEECH_PROB = 0.6


class SpeechToTextError(RuntimeError):
    pass


class SpeechToTextUnavailable(SpeechToTextError):
    """faster-whisper is not installed, or the model cannot be loaded."""


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: float


@dataclass
class Transcript:
    text: str
    words: List[Word] = field(default_factory=list)
    #: Mean token log-probability over segments (higher = more sure; <= 0).
    avg_logprob: Optional[float] = None
    #: Highest per-segment probability that the audio is not speech.
    no_speech_prob: Optional[float] = None
    inference_sec: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    @property
    def usable(self) -> bool:
        """Worth showing the user at all.

        Whisper never returns "I did not hear anything": on silence or noise it
        returns a confident-looking stock phrase. The log-prob and no-speech
        numbers are how we tell those apart.
        """
        cleaned = self.text.strip().strip(".!?,").strip().lower()
        if not cleaned:
            return False
        if (self.no_speech_prob or 0.0) > MAX_NO_SPEECH_PROB:
            return False
        if self.avg_logprob is not None and self.avg_logprob < MIN_AVG_LOGPROB:
            return False
        if cleaned in HALLUCINATIONS and (self.no_speech_prob or 0.0) > 0.3:
            return False
        return True


class SpeechToTextBackend(Protocol):
    def is_available(self) -> bool: ...

    def transcribe(self, audio: np.ndarray, sr: int,
                   vocabulary: Optional[Iterable[str]] = None) -> Transcript: ...


def build_prompt(phrases: Iterable[str]) -> Optional[str]:
    """Join the user's saved phrase texts into a short vocabulary hint.

    Biases the decoder towards words this user actually says - names, places,
    "nebulizer" - which is most of the accuracy win on a small model.
    """
    seen: set = set()
    parts: List[str] = []
    length = 0
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


def validate_audio(audio: np.ndarray, sr: int) -> np.ndarray:
    if sr != SAMPLE_RATE:
        raise SpeechToTextError(f"expected {SAMPLE_RATE} Hz audio, got {sr} Hz")
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim != 1 or audio.size == 0 or not np.isfinite(audio).all():
        raise SpeechToTextError("expected non-empty, finite mono audio")
    return audio


def transcript_from_segments(segments, inference_sec: float = 0.0) -> Transcript:
    """Build a :class:`Transcript` from faster-whisper segments (or look-alikes)."""
    segments = list(segments)
    words: List[Word] = []
    for segment in segments:
        for word in getattr(segment, "words", None) or []:
            words.append(Word(word.word.strip(), float(word.start), float(word.end),
                              float(word.probability)))
    text = " ".join(s.text.strip() for s in segments if s.text and s.text.strip())
    logprobs = [float(s.avg_logprob) for s in segments if getattr(s, "avg_logprob", None) is not None]
    no_speech = [float(s.no_speech_prob) for s in segments if getattr(s, "no_speech_prob", None) is not None]
    return Transcript(
        text=" ".join(text.split()),
        words=words,
        avg_logprob=sum(logprobs) / len(logprobs) if logprobs else None,
        no_speech_prob=max(no_speech) if no_speech else None,
        inference_sec=inference_sec,
    )


class FasterWhisperBackend:
    def __init__(self, model_size: str = "base.en", device: str = "cpu",
                 compute_type: str = "int8", offline: bool = False, download_root=None,
                 cpu_threads: int = 4):
        self.model_size, self.device, self.compute_type = model_size, device, compute_type
        self.offline, self.download_root = offline, download_root
        self.cpu_threads = cpu_threads
        self._model = None
        self._load_lock = threading.Lock()
        # ctranslate2 models are not documented as safe for concurrent
        # transcribe calls from many threads; one at a time is plenty here.
        self._run_lock = threading.Lock()
        self._load_error: Optional[str] = None

    @classmethod
    def from_settings(cls, settings) -> "FasterWhisperBackend":
        return cls(settings.STT_MODEL_SIZE, settings.STT_DEVICE, settings.STT_COMPUTE_TYPE,
                   offline=settings.STT_OFFLINE, cpu_threads=settings.STT_CPU_THREADS)

    @staticmethod
    def package_installed() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def is_available(self) -> bool:
        """Installed and not known to be unloadable. Does not load the model."""
        return self.package_installed() and self._load_error is None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise SpeechToTextUnavailable(
                    "speech-to-text needs faster-whisper: make install-stt") from error
            try:
                self._model = WhisperModel(
                    self.model_size, device=self.device, compute_type=self.compute_type,
                    download_root=self.download_root, local_files_only=self.offline,
                    cpu_threads=self.cpu_threads)
            except Exception as error:  # download/offline/corrupt-model errors vary
                self._load_error = str(error)
                raise SpeechToTextUnavailable(
                    f"could not load faster-whisper model {self.model_size!r}: {error}. "
                    "Pre-download it with: python scripts/download_models.py --stt") from error
            log.info("loaded faster-whisper %s (%s/%s)", self.model_size, self.device, self.compute_type)

    def warm_up(self) -> None:
        """Load the model and run one tiny decode, so the first real recording
        does not pay for lazy initialisation (~1 s) on top of its own decode."""
        self.load()
        try:
            self.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), SAMPLE_RATE)
        except SpeechToTextError as error:
            log.warning("stt warm-up failed: %s", error)

    def transcribe(self, audio: np.ndarray, sr: int,
                   vocabulary: Optional[Iterable[str]] = None) -> Transcript:
        audio = validate_audio(audio, sr)
        self.load()
        prompt = build_prompt(vocabulary) if vocabulary else None
        started = time.perf_counter()
        with self._run_lock:
            segments, _info = self._model.transcribe(
                audio, language="en", beam_size=5, word_timestamps=True, vad_filter=True,
                initial_prompt=prompt)
            # segments is a lazy generator: decoding happens while iterating.
            transcript = transcript_from_segments(segments)
        transcript.inference_sec = time.perf_counter() - started
        log.info("stt %.2fs for %.2fs of audio: %r", transcript.inference_sec,
                 len(audio) / sr, transcript.text)
        return transcript
