"""Recognize browser audio using the shared preprocessing and feature services."""

import numpy as np
from app.services.audio import AudioProcessingError

from app.services.task_io import data_lock


class InvalidAudio(ValueError):
    pass


class RecognitionService:
    def __init__(self, preprocessor, extractor, matcher, attempt_store):
        self.preprocessor, self.extractor = preprocessor, extractor
        self.matcher, self.attempt_store = matcher, attempt_store

    def recognize(self, content, mime_type):
        try:
            processed = self.preprocessor.process(content, mime_type)
            audio, sr = processed.samples, processed.sr
            if sr != 16000 or audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                raise ValueError('Expected finite 16 kHz mono audio')
            if np.max(np.abs(audio)) <= 1e-6:
                raise ValueError('Silent recording')
        except (ValueError, AudioProcessingError) as exc:
            raise InvalidAudio('Could not use this recording. Please try speaking again.') from exc
        features = self.extractor.extract(audio, sr)
        # Protect the template snapshot while feedback is mutating samples.
        with data_lock(self.attempt_store.data_dir):
            result = self.matcher.match(features)
        self.attempt_store.save(result.attempt_id, processed.wav_bytes, features, result)
        return result
