"""Shared disk lock and atomic writes for the single-user, disk-backed MVP."""
import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

_thread_lock = threading.RLock()


def attempt_key(value):
    return str(UUID(str(value)))


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value, allow_nan=False).encode())


@contextmanager
def data_lock(data_dir):
    """All phrase mutations must share this lock at integration (not nest it)."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    with _thread_lock, (data_dir / '.tasks-5-8.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
