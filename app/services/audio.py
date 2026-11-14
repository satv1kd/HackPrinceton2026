"""Audio preprocessing: any browser upload -> clean 16 kHz mono WAV.

Pipeline (PRD "Audio pipeline"):
  1. ffmpeg converts webm/opus (Chrome), mp4/aac (iPhone Safari), wav, ... to
     16 kHz mono 16-bit PCM WAV.
  2. Trim leading/trailing silence (librosa.effects.trim, TRIM_TOP_DB).
  3. Validate length: reject < MIN_CLIP_SEC, cap at MAX_CLIP_SEC.
  4. Peak-normalize volume.
"""

import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

TARGET_SR = 16000
FFMPEG_TIMEOUT_SEC = 30
# Peak amplitude after normalization. Slightly below 1.0 so 16-bit PCM
# quantization never clips.
NORMALIZE_PEAK = 0.99
# Anything quieter than this after trimming is treated as "no speech".
SILENCE_PEAK = 1e-4

_MIME_EXT = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
}


class AudioProcessingError(Exception):
    """The upload could not be decoded or converted."""


class AudioTooShortError(AudioProcessingError):
    """No usable speech: the clip is shorter than MIN_CLIP_SEC after trimming, or silent."""


@dataclass
class ProcessedAudio:
    wav_bytes: bytes  # 16 kHz mono PCM_16 WAV file contents
    samples: np.ndarray  # float32, shape (n,)
    sr: int
    duration_sec: float  # duration after trimming and capping
    truncated: bool  # True if the clip was capped at MAX_CLIP_SEC


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ext_for_mime(mime_type: str | None) -> str:
    """File extension hint for ffmpeg. ffmpeg sniffs the real format anyway."""
    base = (mime_type or "").split(";")[0].strip().lower()
    return _MIME_EXT.get(base, ".bin")


class AudioPreprocessor:
    def __init__(self, trim_top_db: float, min_clip_sec: float, max_clip_sec: float):
        self.trim_top_db = trim_top_db
        self.min_clip_sec = min_clip_sec
        self.max_clip_sec = max_clip_sec

    @classmethod
    def from_settings(cls, settings) -> "AudioPreprocessor":
        return cls(settings.TRIM_TOP_DB, settings.MIN_CLIP_SEC, settings.MAX_CLIP_SEC)

    def convert_to_wav(self, input_bytes: bytes, mime_type: str | None = None) -> bytes:
        """Convert any audio container/codec ffmpeg understands to 16 kHz mono PCM WAV."""
        if not input_bytes:
            raise AudioProcessingError("Empty upload")
        # Temp files rather than pipes: mp4/m4a from Safari needs a seekable input.
        with tempfile.TemporaryDirectory(prefix="heard-") as tmp:
            src = Path(tmp) / f"input{ext_for_mime(mime_type)}"
            dst = Path(tmp) / "output.wav"
            src.write_bytes(input_bytes)
            cmd = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(src),
                "-vn", "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le",
                str(dst),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=FFMPEG_TIMEOUT_SEC)
            except FileNotFoundError as e:
                raise AudioProcessingError("ffmpeg is not installed") from e
            except subprocess.TimeoutExpired as e:
                raise AudioProcessingError("ffmpeg timed out") from e
            except subprocess.CalledProcessError as e:
                detail = e.stderr.decode(errors="replace").strip().splitlines()
                raise AudioProcessingError(
                    f"Could not decode audio: {detail[-1] if detail else 'unknown error'}"
                ) from e
            if not dst.exists() or dst.stat().st_size == 0:
                raise AudioProcessingError("Could not decode audio: no output")
            return dst.read_bytes()

    def clean(self, wav_bytes: bytes) -> ProcessedAudio:
        """Trim silence, validate/cap length and peak-normalize a 16 kHz mono WAV."""
        try:
            audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
        except Exception as e:  # soundfile raises several error types
            raise AudioProcessingError(f"Could not read WAV: {e}") from e
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != TARGET_SR:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=TARGET_SR)
            sr = TARGET_SR

        if audio.size == 0 or np.max(np.abs(audio)) < SILENCE_PEAK:
            raise AudioTooShortError("No speech detected")

        trimmed, _ = librosa.effects.trim(audio, top_db=self.trim_top_db)

        duration = len(trimmed) / sr
        if duration < self.min_clip_sec:
            raise AudioTooShortError(
                f"Recording too short ({duration:.2f}s < {self.min_clip_sec}s)"
            )
        truncated = duration > self.max_clip_sec
        if truncated:
            trimmed = trimmed[: int(sr * self.max_clip_sec)]
            duration = len(trimmed) / sr

        peak = float(np.max(np.abs(trimmed)))
        normalized = (trimmed * (NORMALIZE_PEAK / peak)).astype(np.float32)

        buf = io.BytesIO()
        sf.write(buf, normalized, sr, format="WAV", subtype="PCM_16")
        return ProcessedAudio(
            wav_bytes=buf.getvalue(),
            samples=normalized,
            sr=sr,
            duration_sec=duration,
            truncated=truncated,
        )

    def process(self, input_bytes: bytes, mime_type: str | None = None) -> ProcessedAudio:
        """Full pipeline: decode/convert, then trim/validate/normalize."""
        return self.clean(self.convert_to_wav(input_bytes, mime_type))
