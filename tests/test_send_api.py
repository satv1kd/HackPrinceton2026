"""Tests for the contacts and send-proxy routes (Task 13).

The Photon service (Task 12) is replaced with an ``httpx.MockTransport``, so
these run with no Mac, no Messages and no Node process.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.send import get_contact_store, get_photon_client, router
from app.stores.contacts import ContactStore

MOM = {"id": "mom", "name": "Mom", "handle": "+15551234567", "emoji": "👩"}
CAFE = {"id": "cafe", "name": "Café", "handle": "cafe@example.com"}


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


@pytest.fixture
def build(tmp_path):
    """Build a TestClient with a stubbed Photon service and contacts file."""

    def _build(handler=ok_handler, contacts=(MOM, CAFE), contacts_raw=None):
        path = tmp_path / "contacts.json"
        if contacts_raw is not None:
            path.write_text(contacts_raw)
        elif contacts is not None:
            path.write_text(json.dumps(list(contacts)))

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_contact_store] = lambda: ContactStore(path=path)

        async def _client():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://photon.test"
            ) as client:
                yield client

        app.dependency_overrides[get_photon_client] = _client
        return TestClient(app)

    return _build


# ---------------------------------------------------------------------------
# GET /api/contacts
# ---------------------------------------------------------------------------

def test_lists_contacts_without_raw_handles(build):
    response = build().get("/api/contacts")
    assert response.status_code == 200
    body = response.json()
    assert [c["id"] for c in body] == ["mom", "cafe"]
    assert body[0]["handle_hint"] == "…4567"
    assert "+15551234567" not in response.text


def test_no_contacts_file_gives_an_empty_list(build):
    assert build(contacts=None).get("/api/contacts").json() == []


def test_a_broken_contacts_file_is_a_loud_500(build):
    """Rendering zero send buttons would hide a setup error."""
    response = build(contacts_raw="{ not json").get("/api/contacts")
    assert response.status_code == 500
    assert "contacts.json is invalid" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/send — happy path
# ---------------------------------------------------------------------------

def test_forwards_text_and_handle_to_photon(build):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    response = build(handler).post(
        "/api/send", json={"text": "I want water", "contact_id": "mom"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "error": None, "contact_id": "mom"}
    assert seen["url"].endswith("/send")
    # The resolved handle, not the contact id, reaches Photon.
    assert seen["body"] == {"to": "+15551234567", "text": "I want water"}


def test_text_is_trimmed_before_sending(build):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    build(handler).post("/api/send", json={"text": "  hello  ", "contact_id": "mom"})
    assert seen["body"]["text"] == "hello"


def test_email_handles_are_sent_as_is(build):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    build(handler).post("/api/send", json={"text": "table for two", "contact_id": "cafe"})
    assert seen["body"]["to"] == "cafe@example.com"


# ---------------------------------------------------------------------------
# POST /api/send — caller mistakes are HTTP errors
# ---------------------------------------------------------------------------

def test_unknown_contact_is_404(build):
    response = build().post("/api/send", json={"text": "hi", "contact_id": "nobody"})
    assert response.status_code == 404
    assert "nobody" in response.json()["detail"]


def test_empty_text_is_rejected(build):
    assert build().post("/api/send", json={"text": "", "contact_id": "mom"}).status_code == 422


def test_whitespace_only_text_is_rejected(build):
    response = build().post("/api/send", json={"text": "   ", "contact_id": "mom"})
    assert response.status_code == 422
    assert "empty after trimming" in response.json()["detail"]


def test_missing_fields_are_rejected(build):
    assert build().post("/api/send", json={"text": "hi"}).status_code == 422


def test_absurdly_long_text_is_rejected(build):
    response = build().post("/api/send", json={"text": "x" * 5000, "contact_id": "mom"})
    assert response.status_code == 422


def test_an_unsent_message_never_reaches_photon(build):
    """A 404 on the contact must not have already sent the text somewhere."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    build(handler).post("/api/send", json={"text": "hi", "contact_id": "nobody"})
    assert calls == []


# ---------------------------------------------------------------------------
# POST /api/send — downstream failures are {ok: false}, not exceptions
# ---------------------------------------------------------------------------

def test_photon_reporting_failure_surfaces_its_error(build):
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "Messages not signed in"})

    body = build(handler).post(
        "/api/send", json={"text": "hi", "contact_id": "mom"}
    ).json()
    assert body["ok"] is False
    assert body["error"] == "Messages not signed in"


def test_timeout_is_reported_as_a_failed_send(build):
    def handler(request):
        raise httpx.TimeoutException("too slow", request=request)

    response = build(handler).post("/api/send", json={"text": "hi", "contact_id": "mom"})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "did not respond" in response.json()["error"]


def test_connection_failure_tells_the_user_the_service_may_be_down(build):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    body = build(handler).post("/api/send", json={"text": "hi", "contact_id": "mom"}).json()
    assert body["ok"] is False
    assert "Is it running?" in body["error"]


@pytest.mark.parametrize("status_code", [400, 404, 500, 503])
def test_photon_http_errors_are_failed_sends(build, status_code):
    def handler(request):
        return httpx.Response(status_code, text="nope")

    response = build(handler).post("/api/send", json={"text": "hi", "contact_id": "mom"})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert str(status_code) in response.json()["error"]


def test_non_json_response_is_a_failed_send(build):
    def handler(request):
        return httpx.Response(200, text="<html>oops</html>")

    body = build(handler).post("/api/send", json={"text": "hi", "contact_id": "mom"}).json()
    assert body["ok"] is False
    assert "not JSON" in body["error"]


def test_unexpected_response_shape_is_a_failed_send(build):
    def handler(request):
        return httpx.Response(200, json=["ok"])

    body = build(handler).post("/api/send", json={"text": "hi", "contact_id": "mom"}).json()
    assert body["ok"] is False
    assert "unexpected response shape" in body["error"]


def test_missing_ok_field_is_not_treated_as_success(build):
    def handler(request):
        return httpx.Response(200, json={"status": "maybe"})

    assert build(handler).post(
        "/api/send", json={"text": "hi", "contact_id": "mom"}
    ).json()["ok"] is False


# ---------------------------------------------------------------------------
# GET /api/photon/health
# ---------------------------------------------------------------------------

def test_health_ok(build):
    def handler(request):
        assert request.url.path == "/health"
        return httpx.Response(200, json={"ok": True})

    body = build(handler).get("/api/photon/health").json()
    assert body["reachable"] is True
    assert body["detail"] == "ok"


def test_health_404_still_means_the_process_is_listening(build):
    def handler(request):
        return httpx.Response(404)

    body = build(handler).get("/api/photon/health").json()
    assert body["reachable"] is True
    assert "no /health route" in body["detail"]


def test_health_timeout_is_unreachable(build):
    def handler(request):
        raise httpx.TimeoutException("slow", request=request)

    body = build(handler).get("/api/photon/health").json()
    assert body["reachable"] is False
    assert body["detail"] == "timed out"


def test_health_connect_error_is_unreachable(build):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    body = build(handler).get("/api/photon/health").json()
    assert body["reachable"] is False
    assert "unreachable" in body["detail"]


def test_health_server_error_is_unreachable(build):
    def handler(request):
        return httpx.Response(500)

    body = build(handler).get("/api/photon/health").json()
    assert body["reachable"] is False
    assert "500" in body["detail"]
