"""Contacts and iMessage send proxy (Task 13).

Routes
------
``GET  /api/contacts``      list contacts for the send buttons
``POST /api/send``          forward text to the Photon service (Task 12)
``GET  /api/photon/health`` Photon reachability, for Task 1's ``/api/health``

The Photon service is a separate TypeScript process on a Mac signed into
Messages, exposing ``POST /send {to, text} -> {ok, error?}`` on localhost. All
knowledge of Photon's SDK stays inside that service; this module only knows
that one HTTP shape, which is what lets the rest of the system be developed
and demoed without a Mac.

Wiring (Task 1)::

    from app.api.send import router as send_router
    app.include_router(send_router)

Failure policy: a *caller* mistake is an HTTP error (404 for an unknown
contact, 422 for empty text). A *downstream* failure — Photon down, timeout,
Messages not signed in — returns 200 with ``{"ok": false, "error": ...}``, so
the UI renders a "failed" state on the send button instead of an exception.
The user has already been understood at that point; the send is the part that
failed, and the distinction should be visible to them.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app import settings_bridge
from app.stores.contacts import Contact, ContactsError, ContactStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["send"])

DEFAULT_PHOTON_URL = "http://127.0.0.1:3001"
DEFAULT_TIMEOUT_SEC = 10.0

#: iMessage has no hard limit this low, but a result card that produced
#: thousands of characters means something upstream went wrong.
MAX_TEXT_LENGTH = 2_000


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class SendRequest(BaseModel):
    """``POST /api/send`` body, per the PRD."""

    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    contact_id: str = Field(..., min_length=1)


class SendResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    contact_id: Optional[str] = None


class PhotonHealth(BaseModel):
    reachable: bool
    detail: str
    url: str


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def photon_base_url() -> str:
    return str(settings_bridge.get("PHOTON_SERVICE_URL", DEFAULT_PHOTON_URL)).rstrip("/")


def photon_timeout() -> float:
    return settings_bridge.get("PHOTON_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC, cast=float)


def get_contact_store() -> ContactStore:
    """Override in tests; Task 1 may override to share one instance."""
    return ContactStore()


async def get_photon_client() -> httpx.AsyncClient:
    """An HTTP client pointed at the Photon service.

    A FastAPI dependency so tests can override it with a ``MockTransport``
    and so Task 1 can swap in a long-lived pooled client.
    """
    async with httpx.AsyncClient(
        base_url=photon_base_url(), timeout=photon_timeout()
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/contacts")
def list_contacts(store: ContactStore = Depends(get_contact_store)) -> list[dict]:
    """Contacts for the send buttons.

    Returns the public projection: no raw phone numbers or Apple IDs. See
    :meth:`app.stores.contacts.Contact.to_public_json`.
    """
    try:
        contacts = store.load()
    except ContactsError as error:
        # The file exists but is broken: that is a setup error worth showing
        # loudly rather than rendering zero send buttons.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"contacts.json is invalid: {error}",
        ) from error
    return [contact.to_public_json() for contact in contacts]


@router.post("/send", response_model=SendResponse)
async def send_message(
    request: SendRequest,
    store: ContactStore = Depends(get_contact_store),
    client: httpx.AsyncClient = Depends(get_photon_client),
) -> SendResponse:
    """Send *text* to *contact_id* as an iMessage via the Photon service.

    The UI must only offer this after the result has been confirmed (a Yes tap
    or a picker tap), so unconfirmed text is never sent. That gate is in
    ``static/js/send.js``; the server cannot see confirmation state, because
    the feedback store (Task 6) owns it.
    """
    text = request.text.strip()
    if not text:
        # Literal 422 rather than the `status` constant: Starlette renamed it
        # (UNPROCESSABLE_ENTITY -> UNPROCESSABLE_CONTENT) and the number is
        # stable across both.
        raise HTTPException(status_code=422, detail="text is empty after trimming")

    try:
        contact = store.get(request.contact_id)
    except ContactsError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"contacts.json is invalid: {error}",
        ) from error

    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no contact with id {request.contact_id!r}",
        )

    ok, error = await _photon_send(client, contact, text)
    if ok:
        logger.info("sent %d chars to contact %s", len(text), contact.id)
    else:
        logger.warning("send to contact %s failed: %s", contact.id, error)
    return SendResponse(ok=ok, error=error, contact_id=contact.id)


@router.get("/photon/health", response_model=PhotonHealth)
async def photon_health_route(
    client: httpx.AsyncClient = Depends(get_photon_client),
) -> PhotonHealth:
    """Photon reachability. Task 1's ``/api/health`` should fold this in."""
    return await photon_health(client)


# ---------------------------------------------------------------------------
# Photon transport
# ---------------------------------------------------------------------------

async def _photon_send(
    client: httpx.AsyncClient, contact: Contact, text: str
) -> tuple[bool, Optional[str]]:
    """POST to the Photon service, translating every failure into a message.

    Never raises: the caller turns the pair into ``{ok, error}``.
    """
    try:
        response = await client.post("/send", json={"to": contact.handle, "text": text})
    except httpx.TimeoutException:
        return False, (
            f"the iMessage service did not respond within {photon_timeout():.0f}s"
        )
    except httpx.HTTPError as error:
        return False, (
            "could not reach the iMessage service at "
            f"{photon_base_url()} ({type(error).__name__}). Is it running?"
        )

    if response.status_code >= 500:
        return False, f"the iMessage service returned an error (HTTP {response.status_code})"
    if response.status_code >= 400:
        return False, f"the iMessage service rejected the request (HTTP {response.status_code})"

    try:
        payload = response.json()
    except ValueError:
        return False, "the iMessage service returned a response that was not JSON"

    if not isinstance(payload, dict):
        return False, "the iMessage service returned an unexpected response shape"
    if payload.get("ok") is True:
        return True, None
    return False, str(payload.get("error") or "the iMessage service reported a failure")


async def photon_health(client: httpx.AsyncClient) -> PhotonHealth:
    """Probe the Photon service. Used by ``/api/health``."""
    url = photon_base_url()
    try:
        response = await client.get("/health")
    except httpx.TimeoutException:
        return PhotonHealth(reachable=False, detail="timed out", url=url)
    except httpx.HTTPError as error:
        return PhotonHealth(reachable=False, detail=f"unreachable ({type(error).__name__})", url=url)
    if response.status_code == status.HTTP_404_NOT_FOUND:
        # The service is up but predates a /health route; that still proves
        # the process is listening, which is what this check is for.
        return PhotonHealth(reachable=True, detail="listening (no /health route)", url=url)
    if response.status_code >= 400:
        return PhotonHealth(
            reachable=False, detail=f"HTTP {response.status_code}", url=url
        )
    return PhotonHealth(reachable=True, detail="ok", url=url)
