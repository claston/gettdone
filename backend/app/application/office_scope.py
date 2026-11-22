"""Request-local PRO document policy; legacy/Free is always the default."""

from contextlib import contextmanager
from contextvars import ContextVar

_documents = ContextVar('pro_documents', default=False)


def pro_documents_enabled():
    return _documents.get()


@contextmanager
def document_scope(enabled):
    token = _documents.set(bool(enabled))
    try:
        yield
    finally:
        _documents.reset(token)
