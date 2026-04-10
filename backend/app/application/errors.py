class AnalysisNotFoundError(Exception):
    """Raised when an analysis id cannot be found."""


class UnsupportedFileTypeError(Exception):
    """Raised when the uploaded file extension is unsupported."""


class InvalidFileContentError(Exception):
    """Raised when a supported file has invalid or unreadable content."""
