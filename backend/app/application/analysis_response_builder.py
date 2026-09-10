"""Compatibility exports; new callers use explicit contract or adapter modules."""

from importlib import import_module

_EXPORTS = {
    "_build_export_review_insight": ("app.application.conversion.result_payload", "_build_export_review_insight"),
    "_metrics_get": ("app.application.conversion.result_payload", "_metrics_get"),
    "build_analyze_response": ("app.application.conversion.result_payload", "build_analyze_response"),
    "build_convert_response_payload": ("app.application.conversion.result_payload", "build_convert_response_payload"),
    "persist_and_build_analyze_response": ("app.application.conversion.result_payload", "persist_and_build_analyze_response"),
    "persist_conversion_result": ("app.application.conversion.result_persistence", "persist_conversion_result"),
}

__all__ = sorted(name for name in _EXPORTS if not name.startswith("_"))


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
