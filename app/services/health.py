"""System status checks (Task 15).

``/api/health`` is what you look at thirty seconds before a demo, so every
check here has to be fast, has to never raise, and has to distinguish
"broken" from "not configured". ffmpeg missing means recording is dead; no
ElevenLabs key just means the browser voice speaks instead. Only the first
kind should make the overall status anything but ``ok``.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT_SEC = 5
PHOTON_TIMEOUT_SEC = 5.0
MIN_FREE_BYTES = 100 * 1024 * 1024  # 100 MB; recordings and caches are small

#: Checks that make the whole system unusable when they fail. The others are
#: reported but do not degrade the overall status.
CRITICAL = ("ffmpeg", "disk_space")


class HealthChecker:
    def __init__(self, settings, client_factory=None, stt_backend=None):
        self.settings = settings
        #: The app's speech-to-text backend, if one was built (reports "loaded").
        self.stt_backend = stt_backend
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=PHOTON_TIMEOUT_SEC)
        )

    async def check_all(self) -> Dict[str, Any]:
        checks = {
            "ffmpeg": self.check_ffmpeg(),
            "phoneme_model": self.check_phoneme_model(),
            "stt": self.check_stt(),
            "elevenlabs": self.check_elevenlabs(),
            "photon_service": await self.check_photon_service(),
            "disk_space": self.check_disk_space(),
        }
        degraded = [name for name in CRITICAL if not checks[name]["ok"]]
        return {
            "status": "ok" if not degraded else "degraded",
            "degraded": degraded,
            "checks": checks,
        }

    # -- individual checks -------------------------------------------------

    def check_ffmpeg(self) -> Dict[str, Any]:
        """ffmpeg converts every upload; without it nothing can be recorded."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                timeout=FFMPEG_TIMEOUT_SEC,
                check=False,
            )
        except FileNotFoundError:
            return {"ok": False, "error": "ffmpeg is not installed (brew install ffmpeg)"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ffmpeg did not respond"}
        except OSError as error:  # pragma: no cover - unusual platform failure
            return {"ok": False, "error": f"could not run ffmpeg: {error}"}

        if result.returncode != 0:
            return {"ok": False, "error": f"ffmpeg exited {result.returncode}"}
        first_line = result.stdout.decode("utf-8", "replace").splitlines()
        return {"ok": True, "version": first_line[0] if first_line else None}

    def check_phoneme_model(self) -> Dict[str, Any]:
        """Report readiness without importing torch.

        Importing transformers costs seconds and hundreds of MB of RSS, which
        a health check polled every minute must not do. So this only asks
        whether the packages are installed; actual weights are confirmed by
        ``scripts/measure_phoneme_latency.py``.
        """
        missing = [
            name
            for name in ("torch", "transformers")
            if importlib.util.find_spec(name) is None
        ]
        if missing:
            return {
                "ok": True,  # Phase 4 work; the MVP does not need it.
                "installed": False,
                "note": f"not installed: {', '.join(missing)} (Phase 4)",
            }
        return {"ok": True, "installed": True, "loaded": False, "note": "Phase 4"}

    def check_stt(self) -> Dict[str, Any]:
        """Speech-to-text is the fallback when no saved phrase matches.

        Missing it only removes that fallback (saved phrases still work), so
        it never degrades the overall status. Like the phoneme check, this
        never imports the heavy package.
        """
        enabled = bool(getattr(self.settings, "STT_ENABLED", False))
        model = getattr(self.settings, "STT_MODEL_SIZE", None)
        installed = importlib.util.find_spec("faster_whisper") is not None
        detail: Dict[str, Any] = {"ok": True, "enabled": enabled, "installed": installed, "model": model}
        if not enabled:
            detail["note"] = "disabled (STT_ENABLED=false)"
        elif not installed:
            detail["note"] = "not installed: make install-stt"
        else:
            backend = self.stt_backend
            detail["loaded"] = bool(getattr(backend, "is_loaded", False))
            if backend is not None and not backend.is_available():
                detail["note"] = "model failed to load; see server log"
        return detail

    def check_elevenlabs(self) -> Dict[str, Any]:
        """A missing key is fine: the browser voice takes over."""
        configured = bool(
            getattr(self.settings, "ELEVENLABS_API_KEY", "")
            and getattr(self.settings, "ELEVENLABS_VOICE_ID", "")
        )
        return {
            "ok": True,
            "configured": configured,
            "note": None if configured else "using browser speechSynthesis",
        }

    async def check_photon_service(self) -> Dict[str, Any]:
        """Probe the Photon relay. Unreachable only disables sending."""
        url = str(getattr(self.settings, "PHOTON_SERVICE_URL", "")).rstrip("/")
        if not url:
            return {"ok": True, "reachable": False, "note": "PHOTON_SERVICE_URL is not set"}
        try:
            async with self._client_factory() as client:
                response = await client.get(f"{url}/health")
        except httpx.TimeoutException:
            return {"ok": False, "reachable": False, "note": "timed out; sending unavailable"}
        except httpx.HTTPError:
            return {"ok": False, "reachable": False, "note": "unreachable; sending unavailable"}

        if response.status_code >= 400:
            return {
                "ok": False,
                "reachable": False,
                "note": f"HTTP {response.status_code}; sending unavailable",
            }
        return {"ok": True, "reachable": True, **_photon_detail(response)}

    def check_disk_space(self) -> Dict[str, Any]:
        """Recordings and caches are written on every utterance."""
        try:
            usage = shutil.disk_usage(str(getattr(self.settings, "DATA_DIR", ".")) or ".")
        except OSError:
            try:
                usage = shutil.disk_usage(".")
            except OSError as error:  # pragma: no cover
                return {"ok": False, "error": f"could not check disk space: {error}"}
        free_mb = usage.free // (1024 * 1024)
        return {"ok": usage.free > MIN_FREE_BYTES, "free_mb": free_mb}


def _photon_detail(response: httpx.Response) -> Dict[str, Any]:
    """Pull the relay's own status fields, if it reports any."""
    try:
        body = response.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    detail: Dict[str, Any] = {}
    for key in ("mode", "imessage_connected", "project_id"):
        if key in body:
            detail[key] = body[key]
    return detail
