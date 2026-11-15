import io
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.deps import reset_caches

SR = 16000


def tone(seconds: float, freq: float = 220.0, sr: int = SR, amp: float = 0.3) -> np.ndarray:
    """A harmonic, amplitude-modulated tone: crude but MFCC-friendly stand-in for speech."""
    t = np.arange(int(seconds * sr)) / sr
    sig = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 6))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)
    sig = sig * env
    return (amp * sig / np.max(np.abs(sig))).astype(np.float32)


def with_silence(audio: np.ndarray, before: float, after: float, sr: int = SR) -> np.ndarray:
    return np.concatenate(
        [np.zeros(int(before * sr), np.float32), audio, np.zeros(int(after * sr), np.float32)]
    )


def wav_bytes(audio: np.ndarray, sr: int = SR, channels: int = 1) -> bytes:
    data = np.stack([audio] * channels, axis=1) if channels > 1 else audio
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def encode(audio: np.ndarray, fmt: str, sr: int = 48000) -> bytes:
    """Encode like a browser would: 'webm' = Chrome (webm/opus), 'mp4' = iPhone Safari (mp4/aac)."""
    codec = {"webm": "libopus", "mp4": "aac"}[fmt]
    with tempfile.TemporaryDirectory() as tmp:
        # Regular (non-fragmented) files, like real browser recordings.
        out = Path(tmp) / f"out.{fmt}"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-c:a", codec, str(out)],
            input=wav_bytes(audio, sr=sr, channels=2),
            capture_output=True,
            check=True,
        )
        return out.read_bytes()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(d))
    # App tests must not depend on (or spend seconds loading) the real
    # speech-to-text model; tests that want it turn it back on.
    monkeypatch.setenv("STT_ENABLED", "false")
    # Nor on the 1.2 GB phoneme model; phonetics tests inject fakes.
    monkeypatch.setenv("PHONEMES_ENABLED", "false")
    reset_caches()
    yield d
    reset_caches()


@pytest.fixture
def client(data_dir):
    from app.main import app

    with TestClient(app) as c:
        yield c
