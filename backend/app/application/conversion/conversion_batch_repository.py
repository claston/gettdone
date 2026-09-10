"""Compatibility exports; new callers use explicit contract or adapter modules."""

from importlib import import_module

_EXPORTS = {
    "ConversionBatchRepository": ("app.application.conversion.contracts.batches", "ConversionBatchRepository"),
    "ConversionBatchSnapshot": ("app.application.conversion.contracts.batches", "ConversionBatchSnapshot"),
    "ConversionBatchSubmission": ("app.application.conversion.contracts.batches", "ConversionBatchSubmission"),
    "ConversionOutboxEvent": ("app.application.conversion.contracts.batches", "ConversionOutboxEvent"),
    "InMemoryConversionBatchRepository": ("app.adapters.conversion.memory_batches", "InMemoryConversionBatchRepository"),
}

__all__ = sorted(name for name in _EXPORTS if not name.startswith("_"))


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
