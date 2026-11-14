from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.models.recognition import MatchResult
from app.services.recognition import InvalidAudio

router = APIRouter(prefix='/api', tags=['recognition'])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {'audio/webm', 'video/webm', 'audio/mp4', 'video/mp4',
                'audio/wav', 'audio/x-wav', 'audio/ogg', 'application/octet-stream'}


@router.post('/recognize', response_model=MatchResult)
async def recognize(request: Request, audio: UploadFile = File(...)):
    service = getattr(request.app.state, 'recognition_service', None)
    if service is None:
        raise HTTPException(503, 'Recognition services have not been connected')
    try:
        mime = (audio.content_type or 'application/octet-stream').split(';')[0].strip().lower()
        if mime not in ALLOWED_MIME:
            raise HTTPException(415, 'Unsupported audio format')
        content = await audio.read(MAX_UPLOAD_BYTES + 1)
        if not content:
            raise HTTPException(422, 'Recording is empty')
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, 'Recording exceeds the 10 MB limit')
        return await run_in_threadpool(service.recognize, content, mime)
    except InvalidAudio as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await audio.close()
