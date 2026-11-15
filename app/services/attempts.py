import io
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.services.matcher import feature_matrix
from app.services.task_io import atomic_bytes, atomic_json, attempt_key, data_lock


def utcnow():
    return datetime.now(timezone.utc)


class AttemptStore:
    def __init__(self, data_dir='data'):
        self.data_dir = Path(data_dir)
        self.attempts_dir = self.data_dir / 'attempts'
        self.attempts_dir.mkdir(parents=True, exist_ok=True)

    def path(self, attempt_id):
        return self.attempts_dir / attempt_key(attempt_id)

    def save(self, attempt_id, audio_bytes, features, result, recognized_phonemes=None, extras=None):
        key = attempt_key(attempt_id)
        features = feature_matrix(features)
        if result.attempt_id != key or not audio_bytes:
            raise ValueError('Invalid attempt')
        with data_lock(self.data_dir):
            target = self.path(key)
            if target.exists():
                raise FileExistsError('Attempt already exists')
            temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=self.attempts_dir))
            try:
                atomic_bytes(temporary / 'audio.wav', audio_bytes)
                buffer = io.BytesIO()
                np.save(buffer, features, allow_pickle=False)
                atomic_bytes(temporary / 'features.npy', buffer.getvalue())
                atomic_json(temporary / 'metadata.json', dict(result.model_dump(),
                    timestamp=utcnow().isoformat(), recognized_phonemes=recognized_phonemes,
                    extras=extras or {}))
                os.rename(temporary, target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)

    def get(self, attempt_id):
        path = self.path(attempt_id) / 'metadata.json'
        return json.loads(path.read_text()) if path.exists() else None

    def cleanup_unlabeled(self, max_age=timedelta(hours=1)):
        """Called by FeedbackStore under its lock after pending transactions recover."""
        from app.services.feedback import active_feedback, read_events
        with data_lock(self.data_dir):
            if (self.data_dir / 'feedback-transaction.json').exists():
                return 0
            active = active_feedback(read_events(self.data_dir / 'feedback.jsonl'))
            cutoff = utcnow() - max_age
            removed = 0
            for path in self.attempts_dir.iterdir():
                if path.name.startswith('.'):
                    continue
                meta = self.get(path.name)
                feedback = active.get(path.name)
                # Keep labeled attempts, and every event during its undo window.
                if feedback and (feedback['label'] is not None or utcnow() <= datetime.fromisoformat(feedback['undo_until'])):
                    continue
                if meta and datetime.fromisoformat(meta['timestamp']) < cutoff:
                    shutil.rmtree(path)
                    removed += 1
            return removed
