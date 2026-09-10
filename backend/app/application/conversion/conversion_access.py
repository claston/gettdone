"""Compatibility exports; new callers use explicit contract or adapter modules."""

from importlib import import_module

_EXPORTS = {
    "ConversionAccessPort": ("app.application.conversion.contracts.access", "ConversionAccessPort"),
    "PostgresConversionAccessService": ("app.adapters.conversion.access", "PostgresConversionAccessService"),
}

__all__ = sorted(name for name in _EXPORTS if not name.startswith("_"))


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
