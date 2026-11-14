"""ElevenLabs text-to-speech with an on-disk cache (Task 10).

Spoken output is the moment the PRD is built around: the user is understood
by the person in front of them. So the failure path matters as much as the
happy one — if ElevenLabs has no key, no network or no quota left, the client
falls back to the browser's own `speechSynthesis` and the user still gets a
voice. :meth:`TTSService.synthesize` returns ``None`` for every such case and
the router turns that into a 503, which is the signal the client listens for.

The cache is keyed on ``(text, voice_id)`` and lives in ``data/tts_cache``.
Enrolled phrases are spoken over and over, so in practice almost every
utterance after the first is served from disk: instant, and free of quota.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API_ROOT = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_MODEL = "eleven_monolingual_v1"
REQUEST_TIMEOUT_SEC = 10.0

#: ElevenLabs bills by character and the result card holds one short phrase.
#: Anything longer means something upstream went wrong, so reject it rather
#: than spend quota on it.
MAX_TEXT_LENGTH = 1_000


class TextTooLongError(ValueError):
    """The text exceeds :data:`MAX_TEXT_LENGTH`."""


class TTSService:
    """Synthesize speech, preferring the disk cache, then ElevenLabs."""

    def __init__(
        self,
        api_key: str = "",
        voice_id: str = "",
        cache_dir: Path | str = "data/tts_cache",
        model_id: str = DEFAULT_MODEL,
        client_factory=None,
    ):
        self.api_key = api_key or ""
        self.voice_id = voice_id or ""
        self.model_id = model_id
        self.cache_dir = Path(cache_dir)
        # Injectable so tests can drive this without touching the network.
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC)
        )

    @classmethod
    def from_settings(cls, settings, client_factory=None) -> "TTSService":
        return cls(
            api_key=settings.ELEVENLABS_API_KEY,
            voice_id=settings.ELEVENLABS_VOICE_ID,
            cache_dir=Path(settings.DATA_DIR) / "tts_cache",
            client_factory=client_factory,
        )

    # -- cache -------------------------------------------------------------

    def cache_key(self, text: str) -> str:
        """SHA-1 of text and voice, per the PRD's ``sha1(text+voice_id)``."""
        return hashlib.sha1(f"{text}:{self.voice_id}".encode("utf-8")).hexdigest()

    def cache_path(self, text: str) -> Path:
        return self.cache_dir / f"{self.cache_key(text)}.mp3"

    def get_cached(self, text: str) -> Optional[bytes]:
        path = self.cache_path(text)
        try:
            if path.exists() and path.stat().st_size > 0:
                return path.read_bytes()
        except OSError as error:
            logger.warning("could not read TTS cache %s: %s", path, error)
        return None

    def _store(self, text: str, audio: bytes) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            # Write then rename, so a crash mid-write cannot leave a truncated
            # mp3 that would be served as a cache hit forever after.
            path = self.cache_path(text)
            temporary = path.with_suffix(".mp3.tmp")
            temporary.write_bytes(audio)
            temporary.replace(path)
        except OSError as error:
            logger.warning("could not cache TTS audio: %s", error)

    # -- synthesis ---------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """Whether a key and voice are present. Reported by ``/api/health``."""
        return bool(self.api_key and self.voice_id)

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Return mp3 bytes, or ``None`` if the client should fall back.

        ``None`` is not an error: it is the documented route to the browser's
        own voice. Raises only for input the caller got wrong.
        """
        text = text.strip()
        if not text:
            raise ValueError("text is empty")
        if len(text) > MAX_TEXT_LENGTH:
            raise TextTooLongError(
                f"text is {len(text)} characters; the limit is {MAX_TEXT_LENGTH}"
            )

        cached = self.get_cached(text)
        if cached is not None:
            return cached

        if not self.is_configured:
            logger.info("no ElevenLabs key or voice configured; using browser fallback")
            return None

        try:
            async with self._client_factory() as client:
                response = await client.post(
                    f"{API_ROOT}/{self.voice_id}",
                    headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                    json={
                        "text": text,
                        "model_id": self.model_id,
                        "voice_settings": {"stability": 0.75, "similarity_boost": 0.75},
                    },
                )
        except httpx.HTTPError as error:
            logger.warning("ElevenLabs request failed (%s); using browser fallback",
                           type(error).__name__)
            return None

        if response.status_code != 200:
            # 401 bad key, 429 quota, 5xx outage: all fall back the same way.
            logger.warning(
                "ElevenLabs returned HTTP %s; using browser fallback", response.status_code
            )
            return None

        audio = response.content
        if not audio:
            logger.warning("ElevenLabs returned an empty body; using browser fallback")
            return None

        self._store(text, audio)
        return audio
