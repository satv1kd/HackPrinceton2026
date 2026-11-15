"""Tests for the health endpoint (Task 15)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.health import router
from app.services import health as health_module
from app.services.health import HealthChecker


#: Friendly test kwargs -> real setting names.
_ALIASES = {"api_key": "ELEVENLABS_API_KEY", "voice_id": "ELEVENLABS_VOICE_ID"}


class FakeSettings:
    def __init__(self, **kwargs):
        self.DATA_DIR = Path(".")
        self.ELEVENLABS_API_KEY = "key"
        self.ELEVENLABS_VOICE_ID = "voice"
        self.PHOTON_SERVICE_URL = "http://photon.test"
        for name, value in kwargs.items():
            setattr(self, _ALIASES.get(name, name), value)


def client_factory(handler):
    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)

    return factory


def photon_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "mode": "live"})


def build(handler=photon_ok, **settings_kwargs) -> HealthChecker:
    return HealthChecker(FakeSettings(**settings_kwargs), client_factory=client_factory(handler))


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def ffmpeg_present(monkeypatch):
    """Pretend ffmpeg is installed.

    Whether this machine actually has ffmpeg is not what these tests are
    about, and letting it leak in makes the aggregate-status assertions pass
    or fail depending on the developer's laptop. Tests that care about ffmpeg
    patch it themselves.
    """
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"ffmpeg version 7.1\n", stderr=b""
        ),
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_reports_every_check():
    result = await build().check_all()
    assert set(result["checks"]) == {
        "ffmpeg", "phoneme_model", "stt", "elevenlabs", "photon_service", "disk_space",
    }
    assert result["status"] in {"ok", "degraded"}


@pytest.mark.anyio
async def test_status_is_degraded_only_for_critical_failures(monkeypatch):
    """A missing ElevenLabs key or phoneme model must not read as broken:
    both have working fallbacks, ffmpeg does not."""
    result = await build(api_key="", voice_id="").check_all()
    assert result["checks"]["elevenlabs"]["configured"] is False
    assert "elevenlabs" not in result["degraded"]

    monkeypatch.setattr(health_module, "CRITICAL", ("ffmpeg", "disk_space"))
    monkeypatch.setattr(
        HealthChecker, "check_ffmpeg", lambda self: {"ok": False, "error": "gone"}
    )
    degraded = await build().check_all()
    assert degraded["status"] == "degraded"
    assert "ffmpeg" in degraded["degraded"]


@pytest.mark.anyio
async def test_photon_unreachable_does_not_degrade_the_whole_system():
    """Sending is a bonus; being understood in person is the core feature."""

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    result = await build(handler).check_all()
    assert result["checks"]["photon_service"]["reachable"] is False
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------

def test_ffmpeg_missing_is_reported_with_the_fix(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", boom)
    result = build().check_ffmpeg()
    assert result["ok"] is False
    assert "brew install ffmpeg" in result["error"]


def test_ffmpeg_timeout_is_not_fatal(monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)

    monkeypatch.setattr(subprocess, "run", boom)
    assert build().check_ffmpeg()["ok"] is False


def test_ffmpeg_nonzero_exit_is_reported(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b""),
    )
    assert build().check_ffmpeg()["ok"] is False


def test_ffmpeg_version_is_the_first_line(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"ffmpeg version 7.1\nbuilt with clang\n", stderr=b""
        ),
    )
    result = build().check_ffmpeg()
    assert result == {"ok": True, "version": "ffmpeg version 7.1"}


# ---------------------------------------------------------------------------
# Phoneme model
# ---------------------------------------------------------------------------

def test_phoneme_check_never_imports_torch():
    """This endpoint is polled every minute; importing transformers would
    cost seconds and hundreds of megabytes of RSS."""
    import sys

    build().check_phoneme_model()
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_phoneme_model_absent_is_still_ok():
    result = build().check_phoneme_model()
    assert result["ok"] is True  # Phase 4; the MVP does not need it
    if not result.get("installed"):
        assert "Phase 4" in result["note"]


# ---------------------------------------------------------------------------
# ElevenLabs and Photon
# ---------------------------------------------------------------------------

def test_elevenlabs_without_a_key_names_the_fallback():
    result = build(api_key="").check_elevenlabs()
    assert result == {"ok": True, "configured": False, "note": "using browser speechSynthesis"}


def test_elevenlabs_configured_requires_both_key_and_voice():
    assert build().check_elevenlabs()["configured"] is True
    assert build(voice_id="").check_elevenlabs()["configured"] is False


@pytest.mark.anyio
async def test_photon_detail_is_passed_through():
    result = await build().check_photon_service()
    assert result["reachable"] is True
    assert result["mode"] == "live"


@pytest.mark.anyio
async def test_photon_non_json_response_is_still_reachable():
    def handler(request):
        return httpx.Response(200, text="OK")

    assert (await build(handler).check_photon_service())["reachable"] is True


@pytest.mark.anyio
async def test_photon_http_error_is_unreachable():
    def handler(request):
        return httpx.Response(500)

    result = await build(handler).check_photon_service()
    assert result["ok"] is False
    assert "500" in result["note"]


@pytest.mark.anyio
async def test_photon_timeout_is_unreachable():
    def handler(request):
        raise httpx.TimeoutException("slow", request=request)

    assert (await build(handler).check_photon_service())["note"].startswith("timed out")


@pytest.mark.anyio
async def test_unset_photon_url_is_not_a_failure():
    result = await build(PHOTON_SERVICE_URL="").check_photon_service()
    assert result["ok"] is True
    assert result["reachable"] is False


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------

def test_disk_space_reports_free_megabytes():
    result = build().check_disk_space()
    assert result["ok"] is True
    assert result["free_mb"] > 0


def test_disk_space_fails_when_nearly_full(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: type("U", (), {"free": 1024, "total": 1, "used": 1})()
    )
    result = build().check_disk_space()
    assert result["ok"] is False
    assert result["free_mb"] == 0


def test_disk_space_falls_back_when_data_dir_is_missing(monkeypatch):
    checker = build(DATA_DIR=Path("/nonexistent/path/xyz"))
    assert checker.check_disk_space()["ok"] in {True, False}  # must not raise


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

def test_endpoint_returns_200_even_when_degraded(monkeypatch):
    """A monitoring endpoint that 503s tells you less than one that explains."""
    monkeypatch.setattr(
        HealthChecker, "check_ffmpeg", lambda self: {"ok": False, "error": "gone"}
    )
    app = FastAPI()
    app.include_router(router)
    app.state.health_checker = build()
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["ffmpeg"]["ok"] is False
