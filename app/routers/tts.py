"""Speech output endpoint (Task 10).

``GET /api/tts?text=...`` returns mp3 audio, or **503** when speech must come
from the browser instead. The 503 is a contract, not a fault: the client in
``static/js/tts.js`` treats it as "use speechSynthesis" and the user still
hears their words.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from app.services.tts import MAX_TEXT_LENGTH, TextTooLongError, TTSService

router = APIRouter(prefix="/api", tags=["tts"])


def service_for(request: Request) -> TTSService:
    """The app-wide service, built once at startup in ``app.main``."""
    service = getattr(request.app.state, "tts_service", None)
    if service is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(status_code=500, detail="TTS service is not configured")
    return service


@router.get("/tts")
async def get_tts(
    request: Request,
    text: str = Query(..., min_length=1, max_length=MAX_TEXT_LENGTH),
) -> Response:
    service = service_for(request)
    try:
        audio = await service.synthesize(text)
    except TextTooLongError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if audio is None:
        raise HTTPException(
            status_code=503, detail="Speech is unavailable; use the browser voice."
        )

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            # The same text always yields the same audio, so let the browser
            # keep it too: a repeated phrase then costs no request at all.
            "Cache-Control": "public, max-age=86400",
            "Content-Length": str(len(audio)),
        },
    )
