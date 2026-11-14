"""Tests for ElevenLabs TTS and its fallback contract (Task 10)."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.tts import router
from app.services.tts import MAX_TEXT_LENGTH, TextTooLongError, TTSService

MP3 = b"ID3\x04\x00fake-mp3-bytes"


def client_factory(handler):
    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)

    return factory


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=MP3)


@pytest.fixture
def service(tmp_path):
    def build(handler=ok_handler, api_key="key", voice_id="voice"):
        return TTSService(
            api_key=api_key,
            voice_id=voice_id,
            cache_dir=tmp_path / "tts_cache",
            client_factory=client_factory(handler),
        )

    return build


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_synthesizes_and_caches(service):
    svc = service()
    assert await svc.synthesize("I want water") == MP3
    assert svc.cache_path("I want water").read_bytes() == MP3


@pytest.mark.anyio
async def test_second_call_is_served_from_cache_without_a_request(service):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=MP3)

    svc = service(handler)
    await svc.synthesize("hello")
    await svc.synthesize("hello")
    assert len(calls) == 1, "a repeated phrase must not spend quota"


@pytest.mark.anyio
async def test_cache_key_depends_on_voice(service):
    a = service(voice_id="voice-a")
    b = service(voice_id="voice-b")
    assert a.cache_key("hello") != b.cache_key("hello")


@pytest.mark.anyio
async def test_request_carries_the_key_and_voice(service):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("xi-api-key")
        return httpx.Response(200, content=MP3)

    await service(handler, voice_id="v123").synthesize("hello")
    assert seen["url"].endswith("/v123")
    assert seen["key"] == "key"


# ---------------------------------------------------------------------------
# Fallback: None means "use the browser voice"
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_key_falls_back(service):
    assert await service(api_key="").synthesize("hello") is None


@pytest.mark.anyio
async def test_no_voice_falls_back(service):
    assert await service(voice_id="").synthesize("hello") is None


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [401, 422, 429, 500, 503])
async def test_api_errors_fall_back(service, status_code):
    """A bad key, spent quota and an outage all have the same remedy."""

    def handler(request):
        return httpx.Response(status_code, json={"detail": "nope"})

    assert await service(handler).synthesize("hello") is None


@pytest.mark.anyio
async def test_timeout_falls_back(service):
    def handler(request):
        raise httpx.TimeoutException("slow", request=request)

    assert await service(handler).synthesize("hello") is None


@pytest.mark.anyio
async def test_network_error_falls_back(service):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    assert await service(handler).synthesize("hello") is None


@pytest.mark.anyio
async def test_empty_body_falls_back_and_is_not_cached(service):
    def handler(request):
        return httpx.Response(200, content=b"")

    svc = service(handler)
    assert await svc.synthesize("hello") is None
    assert not svc.cache_path("hello").exists()


@pytest.mark.anyio
async def test_a_zero_byte_cache_file_is_ignored(service):
    """Otherwise a truncated write would serve silence forever."""
    svc = service()
    svc.cache_dir.mkdir(parents=True)
    svc.cache_path("hello").write_bytes(b"")
    assert await svc.synthesize("hello") == MP3


@pytest.mark.anyio
async def test_no_temp_files_are_left_behind(service):
    svc = service()
    await svc.synthesize("hello")
    assert not list(svc.cache_dir.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_empty_text_is_a_caller_error(service):
    with pytest.raises(ValueError):
        await service().synthesize("   ")


@pytest.mark.anyio
async def test_overlong_text_is_rejected_before_spending_quota(service):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=MP3)

    with pytest.raises(TextTooLongError):
        await service(handler).synthesize("x" * (MAX_TEXT_LENGTH + 1))
    assert calls == []


@pytest.mark.anyio
async def test_text_is_trimmed_for_the_cache_key(service):
    svc = service()
    assert svc.cache_key("hello") == svc.cache_key("hello")
    await svc.synthesize("  hello  ")
    assert svc.cache_path("hello").exists()


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def api(service):
    def build(handler=ok_handler, **kwargs):
        app = FastAPI()
        app.include_router(router)
        app.state.tts_service = service(handler, **kwargs)
        return TestClient(app)

    return build


def test_endpoint_returns_audio(api):
    response = api().get("/api/tts", params={"text": "I want water"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == MP3
    assert "max-age" in response.headers["cache-control"]


def test_endpoint_503s_so_the_client_falls_back(api):
    """503 is the documented signal for speechSynthesis, not a bug."""
    response = api(api_key="").get("/api/tts", params={"text": "hello"})
    assert response.status_code == 503


def test_endpoint_rejects_missing_and_empty_text(api):
    assert api().get("/api/tts").status_code == 422
    assert api().get("/api/tts", params={"text": ""}).status_code == 422


def test_endpoint_rejects_overlong_text(api):
    response = api().get("/api/tts", params={"text": "x" * (MAX_TEXT_LENGTH + 1)})
    assert response.status_code == 422


def test_endpoint_handles_unicode_and_emoji(api):
    assert api().get("/api/tts", params={"text": "Café ☕ please"}).status_code == 200


@pytest.fixture(scope="module")
def anyio_backend():
    """Run the async tests on asyncio only (no trio installed)."""
    return "asyncio"
