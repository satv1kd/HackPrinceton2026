"""Phrase + sample management endpoints (PRD "APIs")."""

from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import Settings, get_settings
from app.deps import get_feature_cache, get_preprocessor, get_store
from app.models.schemas import PhraseOut, Sample
from app.services.audio import AudioPreprocessor, AudioProcessingError, AudioTooShortError
from app.services.features import FeatureCache
from app.services.storage import NotFoundError, PhraseStore

router = APIRouter(prefix="/api/phrases", tags=["phrases"])

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024

Store = Annotated[PhraseStore, Depends(get_store)]
Prep = Annotated[AudioPreprocessor, Depends(get_preprocessor)]
Cache = Annotated[FeatureCache, Depends(get_feature_cache)]
Config = Annotated[Settings, Depends(get_settings)]


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


@router.get("", response_model=list[PhraseOut])
def list_phrases(store: Store):
    return [PhraseOut.from_phrase(p) for p in store.list_phrases()]


@router.post("", response_model=PhraseOut, status_code=201)
def create_phrase(
    store: Store,
    text: Annotated[str, Form()],
    emoji: Annotated[Optional[str], Form()] = None,
    personal_word: Annotated[bool, Form()] = False,
    image: Annotated[Optional[UploadFile], File()] = None,
):
    image_bytes = ext = None
    if image is not None and image.filename:
        ext = Path(image.filename).suffix.lower()
        image_bytes = image.file.read(MAX_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image too large")
        if not image_bytes:
            image_bytes = None
    try:
        phrase = store.create_phrase(text=text, emoji=emoji, personal_word=personal_word)
        if image_bytes:
            try:
                phrase = store.save_image(phrase.id, image_bytes, ext)
            except ValueError:
                store.delete_phrase(phrase.id)
                raise
    except ValueError as e:
        raise HTTPException(422, str(e))
    return PhraseOut.from_phrase(phrase)


@router.get("/{phrase_id}", response_model=PhraseOut)
def get_phrase(phrase_id: str, store: Store):
    try:
        return PhraseOut.from_phrase(store.get_phrase(phrase_id))
    except NotFoundError:
        raise _not_found("Phrase")


@router.delete("/{phrase_id}", status_code=204)
def delete_phrase(phrase_id: str, store: Store, cache: Cache):
    try:
        store.delete_phrase(phrase_id)
    except NotFoundError:
        raise _not_found("Phrase")
    cache.invalidate_phrase(phrase_id)


@router.get("/{phrase_id}/image")
def get_image(phrase_id: str, store: Store):
    try:
        path = store.image_path(store.get_phrase(phrase_id))
    except NotFoundError:
        raise _not_found("Phrase")
    if not path or not path.exists():
        raise _not_found("Image")
    return FileResponse(path)


@router.post("/{phrase_id}/samples", response_model=Sample, status_code=201)
def add_sample(
    phrase_id: str,
    store: Store,
    prep: Prep,
    cache: Cache,
    settings: Config,
    audio: Annotated[UploadFile, File()],
):
    try:
        store.get_phrase(phrase_id)
    except NotFoundError:
        raise _not_found("Phrase")

    raw = audio.file.read(MAX_AUDIO_BYTES + 1)
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Recording too large")
    try:
        processed = prep.process(raw, audio.content_type)
    except AudioTooShortError as e:
        raise HTTPException(422, {"code": "too_short", "message": f"Didn't catch that: {e}"})
    except AudioProcessingError as e:
        raise HTTPException(422, {"code": "bad_audio", "message": str(e)})

    try:
        sample = store.add_sample(
            phrase_id, processed.wav_bytes, source="enroll", duration_sec=processed.duration_sec
        )
    except NotFoundError:  # deleted concurrently
        raise _not_found("Phrase")
    cache.put(phrase_id, sample.id, processed.samples, processed.sr)
    for evicted in store.evict_overflow(phrase_id, settings.MAX_SAMPLES_PER_PHRASE):
        cache.invalidate(phrase_id, evicted)
    return sample


@router.get("/{phrase_id}/samples/{sample_id}/audio")
def get_sample_audio(phrase_id: str, sample_id: str, store: Store):
    # Look the ids up in the store rather than trusting them as path components.
    try:
        phrase = store.get_phrase(phrase_id)
    except NotFoundError:
        raise _not_found("Phrase")
    if not any(s.id == sample_id for s in phrase.samples):
        raise _not_found("Sample")
    path = store.sample_path(phrase.id, sample_id)
    if not path.exists():
        raise _not_found("Sample")
    return FileResponse(path, media_type="audio/wav")


@router.delete("/{phrase_id}/samples/{sample_id}", status_code=204)
def delete_sample(phrase_id: str, sample_id: str, store: Store, cache: Cache):
    try:
        store.delete_sample(phrase_id, sample_id)
    except NotFoundError:
        raise _not_found("Sample")
    cache.invalidate(phrase_id, sample_id)
