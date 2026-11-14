"""Adapter for the task 3 and 4 interfaces, with no imports of unfinished modules."""
import io
import subprocess

import numpy as np
import soundfile as sf

from app.services.task_io import data_lock


class InvalidAudio(ValueError):
    pass


class RecognitionService:
    def __init__(self, preprocessor, extractor, matcher, attempt_store, audio_errors=()):
        self.preprocessor, self.extractor = preprocessor, extractor
        self.matcher, self.attempt_store = matcher, attempt_store
        self.audio_errors = tuple(audio_errors)

    def recognize(self, content, mime_type):
        try:
            converted = self.preprocessor.convert_to_wav(content, mime_type)
            cleaned, duration = self.preprocessor.process(converted)
            audio, sr = sf.read(io.BytesIO(cleaned), dtype='float32')
            if sr != 16000 or audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                raise ValueError('Expected finite 16 kHz mono audio')
            if np.max(np.abs(audio)) <= 1e-6:
                raise ValueError('Silent recording')
        except (ValueError, sf.LibsndfileError, subprocess.CalledProcessError, subprocess.TimeoutExpired, *self.audio_errors) as exc:
            raise InvalidAudio('Could not use this recording. Please try speaking again.') from exc
        features = self.extractor.extract(audio, sr)
        # Protect the template snapshot while feedback is mutating samples.
        with data_lock(self.attempt_store.data_dir):
            result = self.matcher.match(features)
        self.attempt_store.save(result.attempt_id, cleaned, features, result)
        return result
