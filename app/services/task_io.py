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
_held = threading.local()


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
    """Reentrant across shared services; serializes threads and local processes."""
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        held = getattr(_held, 'paths', set())
        if data_dir in held:
            yield
            return
        with (data_dir / '.tasks-5-8.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _held.paths = held | {data_dir}
            try:
                yield
            finally:
                _held.paths = held
                fcntl.flock(lock, fcntl.LOCK_UN)
