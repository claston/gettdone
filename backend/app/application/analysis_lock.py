"""Bounded, reentrant locks for analyses sharing a local artifact cache."""

from functools import wraps
from threading import RLock

_LOCKS = tuple(RLock() for _ in range(128))


def locked_analysis(method):
    @wraps(method)
    def wrapped(self, analysis_id, *args, **kwargs):
        key = (str(self.root_dir.resolve()), analysis_id)
        with _LOCKS[hash(key) % len(_LOCKS)]:
            return method(self, analysis_id, *args, **kwargs)
    return wrapped
