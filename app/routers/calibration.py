"""Calibration endpoints: each phrase is recorded whole, two takes each.

The page uploads one take at a time. The server cleans the audio, keeps it
(also as a sample of the matching Talk phrase) and answers straight away.
The person was reading the phrase on screen, so there is nothing to
transcribe: the sound engine lines the take up against the phrase's known
pronunciation in the background (phoneme model + forced alignment), and
that is where the per-sound timing comes from. Nothing is judged and
nothing is scored.

Older takes may still carry per-word clips from when speech-to-text cut
them; ``/words/{index}`` keeps serving those.
"""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.deps import (get_calibration_preprocessor, get_calibration_store, get_feature_cache,
                      get_phoneme_service, get_profile_trainer, get_store)
from app.phonetics.service import PhonemeService, take_cache_id
from app.phonetics.enrollment import TAKES_PER_PHRASE, enrollment_phrase, load_enrollment_set
from app.services.audio import AudioPreprocessor, AudioProcessingError, AudioTooShortError
from app.services.calibration import MAX_TAKES, CalibrationStore
from app.services.enrollment import EnrollmentLinker

router = APIRouter(prefix="/api/calibration", tags=["calibration"])

Store = Annotated[CalibrationStore, Depends(get_calibration_store)]
Preprocessor = Annotated[AudioPreprocessor, Depends(get_calibration_preprocessor)]


def get_linker() -> EnrollmentLinker:
    return EnrollmentLinker(get_store(), get_feature_cache())


Linker = Annotated[EnrollmentLinker, Depends(get_linker)]
Phonemes = Annotated[PhonemeService, Depends(get_phoneme_service)]
Trainer = Annotated[object, Depends(get_profile_trainer)]
TakeQuery = Annotated[int, Query(ge=1, le=MAX_TAKES)]

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class WordOut(BaseModel):
    index: int
    expected: str
    heard: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    match: Optional[str] = None
    probability: Optional[float] = None
    #: Whether a clip was written for this word (the path itself stays private).
    clip: bool = False


class SentenceOut(BaseModel):
    id: str
    text: str
    recorded_at: str
    duration_sec: float
    take: int = 1
    #: The Talk phrase this take was saved into (enrollment phrases only).
    phrase_id: Optional[str] = None
    transcript: str = ""
    words: List[WordOut] = []
    placed_count: int = 0
    word_count: int = 0
    #: False when faster-whisper is not installed: the take is still kept, it
    #: just has no word clips yet.
    transcribed: bool = True


def _out(record: dict) -> SentenceOut:
    words = [
        WordOut(index=w["index"], expected=w["expected"], heard=w.get("heard"),
                start=w.get("start"), end=w.get("end"), match=w.get("match"),
                probability=w.get("probability"), clip=bool(w.get("path")))
        for w in record.get("words", [])
    ]
    return SentenceOut(
        id=record["id"], text=record["text"], recorded_at=record["recorded_at"],
        duration_sec=record.get("duration_sec", 0.0), take=int(record.get("take", 1) or 1),
        phrase_id=record.get("phrase_id"),
        transcript=record.get("transcript", ""), words=words,
        placed_count=sum(1 for w in words if w.clip), word_count=len(words),
        transcribed=bool(record.get("transcript")) or not words)


class EnrollmentPhraseOut(BaseModel):
    id: str
    text: str
    emoji: str
    phonemes: List[str]
    #: Takes already recorded (1-based take numbers).
    recorded_takes: List[int] = []


class EnrollmentSetOut(BaseModel):
    takes_per_phrase: int
    phrases: List[EnrollmentPhraseOut]


@router.get("/enrollment-set", response_model=EnrollmentSetOut)
def enrollment_set(store: Store):
    """The built-in phrases the calibration page asks for, and what is recorded."""
    takes = {}
    for record in store.list_sentences():
        takes.setdefault(record["id"], []).append(int(record.get("take", 1) or 1))
    return EnrollmentSetOut(
        takes_per_phrase=TAKES_PER_PHRASE,
        phrases=[EnrollmentPhraseOut(id=p.id, text=p.text, emoji=p.emoji, phonemes=p.phonemes,
                                     recorded_takes=sorted(takes.get(p.id, [])))
                 for p in load_enrollment_set()])


@router.post("/sentences", response_model=SentenceOut)
async def save_sentence(store: Store, preprocessor: Preprocessor, linker: Linker,
                        phonemes: Phonemes, trainer: Trainer,
                        sentence_id: Annotated[str, Form(min_length=1, max_length=40)],
                        text: Annotated[str, Form(min_length=1, max_length=400)],
                        take: Annotated[int, Form(ge=1, le=MAX_TAKES)] = 1,
                        audio: UploadFile = File(...)):
    raw = await audio.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Recording too large")
    # ffmpeg, disk writes and features are blocking work: run them on a worker
    # thread so one save never stalls every other request.
    return await run_in_threadpool(_save_take, store, preprocessor, linker, phonemes, trainer,
                                   sentence_id, text, take, raw, audio.content_type)


def _save_take(store, preprocessor, linker, phonemes, trainer, sentence_id: str, text: str,
               take: int, raw: bytes, content_type: Optional[str]) -> SentenceOut:
    phrase = enrollment_phrase(sentence_id)
    if phrase is not None:
        text = phrase.text  # the server's copy is authoritative for enrollment phrases
    try:
        wav = preprocessor.convert_to_wav(raw, content_type)
        processed = preprocessor.clean(wav)
    except AudioTooShortError as error:
        raise HTTPException(422, str(error)) from error
    except AudioProcessingError as error:
        raise HTTPException(400, str(error)) from error

    try:
        previous = store.get_sentence(sentence_id, take)
        record = store.save_sentence(sentence_id, text, processed.samples, processed.sr, take=take)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    result = record.to_json()
    if phrase is not None:
        # An enrollment phrase is also an everyday Talk phrase: keep this take
        # as one of its samples, replacing only the sample of the take redone.
        phrase_id, sample_id = linker.save_take(
            phrase, processed.wav_bytes, processed.samples, processed.sr, processed.duration_sec,
            previous_sample_id=(previous or {}).get("sample_id"))
        result = store.update_record(record.id, take, phrase_id=phrase_id, sample_id=sample_id) or result
    # The sound profile needs to know which phonemes the model heard in this
    # take; work it out in the background so the page is not kept waiting,
    # then fold it into the profile and sound bank (batched, also background).
    take_path = store.resolve(result.get("audio_path") or "")
    if take_path is not None:
        cache_id = take_cache_id(record.id, take)
        phonemes.recognize_file_async(take_path, cache_id,
                                      on_done=lambda _result: trainer.take_changed(cache_id))
    return _out(result)


@router.get("/sentences", response_model=List[SentenceOut])
def list_sentences(store: Store):
    return [_out(record) for record in store.list_sentences()]


@router.get("/sentences/{sentence_id}/audio")
def sentence_audio(sentence_id: str, store: Store, take: TakeQuery = 1):
    record = store.get_sentence(sentence_id, take)
    path = store.resolve(record.get("audio_path") or "") if record else None
    if path is None:
        raise HTTPException(404, "No recording for that sentence")
    return FileResponse(path, media_type="audio/wav")


@router.get("/sentences/{sentence_id}/words/{index}")
def word_audio(sentence_id: str, index: int, store: Store, take: TakeQuery = 1):
    record = store.get_sentence(sentence_id, take)
    word = next((w for w in (record or {}).get("words", []) if w["index"] == index), None)
    path = store.resolve(word.get("path") or "") if word else None
    if path is None:
        raise HTTPException(404, "No clip for that word")
    return FileResponse(path, media_type="audio/wav")


@router.delete("/sentences/{sentence_id}", status_code=204)
def delete_sentence(sentence_id: str, store: Store, linker: Linker, phonemes: Phonemes, trainer: Trainer,
                    take: Annotated[Optional[int], Query(ge=1, le=MAX_TAKES)] = None):
    """Delete one take (``?take=n``) or all of them, with their Talk samples."""
    try:
        deleted = store.delete_sentence(sentence_id, take)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not deleted:
        raise HTTPException(404, "No recording for that sentence")
    for record in deleted:
        cache_id = take_cache_id(record["id"], int(record.get("take", 1) or 1))
        phonemes.forget(cache_id)
        if record.get("phrase_id") and record.get("sample_id"):
            linker.remove_sample(record["phrase_id"], record["sample_id"])
        if phonemes.available():
            trainer.take_changed(cache_id)
