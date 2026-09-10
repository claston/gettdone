"""Compatibility exports; new callers use explicit contract or adapter modules."""

from importlib import import_module

_EXPORTS = {
    "ConversionDocumentReference": ("app.application.conversion.contracts.documents", "ConversionDocumentReference"),
    "ConversionDocumentStore": ("app.application.conversion.contracts.documents", "ConversionDocumentStore"),
    "FilesystemConversionDocumentStore": ("app.adapters.conversion.document_store", "FilesystemConversionDocumentStore"),
    "S3ConversionDocumentStore": ("app.adapters.conversion.document_store", "S3ConversionDocumentStore"),
    "_content_type": ("app.adapters.conversion.document_store", "_content_type"),
    "_load_boto3": ("app.adapters.conversion.document_store", "_load_boto3"),
    "_validate_document_reference": ("app.adapters.conversion.document_store", "_validate_document_reference"),
}

__all__ = sorted(name for name in _EXPORTS if not name.startswith("_"))


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
