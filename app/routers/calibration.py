"""Calibration endpoints: a sentence is recorded whole, then cut into words.

The page uploads one sentence at a time. The server cleans the audio, asks
speech-to-text for word timings, slices a clip per word and keeps the lot next
to the words the passage asked for. Nothing is judged and nothing is scored;
the response just says how much of the sentence could be placed in time.
"""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.deps import (get_calibration_preprocessor, get_calibration_store, get_feature_cache,
                      get_store, get_stt_backend)
from app.phonetics.enrollment import TAKES_PER_PHRASE, enrollment_phrase, load_enrollment_set
from app.services.audio import AudioPreprocessor, AudioProcessingError, AudioTooShortError
from app.services.calibration import MAX_TAKES, CalibrationStore
from app.services.enrollment import EnrollmentLinker
from app.stt import SpeechToTextError

router = APIRouter(prefix="/api/calibration", tags=["calibration"])

Store = Annotated[CalibrationStore, Depends(get_calibration_store)]
Preprocessor = Annotated[AudioPreprocessor, Depends(get_calibration_preprocessor)]
Backend = Annotated[object, Depends(get_stt_backend)]


def get_linker() -> EnrollmentLinker:
    return EnrollmentLinker(get_store(), get_feature_cache())


Linker = Annotated[EnrollmentLinker, Depends(get_linker)]
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
async def save_sentence(store: Store, preprocessor: Preprocessor, backend: Backend, linker: Linker,
                        sentence_id: Annotated[str, Form(min_length=1, max_length=40)],
                        text: Annotated[str, Form(min_length=1, max_length=400)],
                        take: Annotated[int, Form(ge=1, le=MAX_TAKES)] = 1,
                        audio: UploadFile = File(...)):
    phrase = enrollment_phrase(sentence_id)
    if phrase is not None:
        text = phrase.text  # the server's copy is authoritative for enrollment phrases
    raw = await audio.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Recording too large")
    try:
        wav = preprocessor.convert_to_wav(raw, audio.content_type)
        processed = preprocessor.clean(wav)
    except AudioTooShortError as error:
        raise HTTPException(422, str(error)) from error
    except AudioProcessingError as error:
        raise HTTPException(400, str(error)) from error

    transcript_text, words = "", []
    if backend.is_available():
        try:
            transcript = backend.transcribe(processed.samples, processed.sr, vocabulary=[text])
            transcript_text, words = transcript.text, transcript.words
        except SpeechToTextError as error:
            # A sentence read aloud is worth keeping even when the cutting
            # step cannot run; the words simply stay unplaced.
            transcript_text, words = "", []
            _log_unavailable(error)

    try:
        previous = store.get_sentence(sentence_id, take)
        record = store.save_sentence(sentence_id, text, processed.samples, processed.sr,
                                     transcript=transcript_text, heard=words, take=take)
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
def delete_sentence(sentence_id: str, store: Store, linker: Linker,
                    take: Annotated[Optional[int], Query(ge=1, le=MAX_TAKES)] = None):
    """Delete one take (``?take=n``) or all of them, with their Talk samples."""
    try:
        deleted = store.delete_sentence(sentence_id, take)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not deleted:
        raise HTTPException(404, "No recording for that sentence")
    for record in deleted:
        if record.get("phrase_id") and record.get("sample_id"):
            linker.remove_sample(record["phrase_id"], record["sample_id"])


def _log_unavailable(error: Exception) -> None:
    import logging

    logging.getLogger(__name__).warning("calibration could not transcribe: %s", error)
