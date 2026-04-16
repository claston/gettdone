class AnalysisNotFoundError(Exception):
    """Raised when an analysis id cannot be found."""


class UnsupportedFileTypeError(Exception):
    """Raised when the uploaded file extension is unsupported."""


class InvalidFileContentError(Exception):
    """Raised when a supported file has invalid or unreadable content."""


class QuotaExceededError(Exception):
    """Raised when the identity has no conversion quota remaining."""


class FileTooLargeError(Exception):
    """Raised when uploaded file exceeds maximum allowed size."""


class InvalidUserTokenError(Exception):
    """Raised when a user token cannot be validated."""


class UserAlreadyExistsError(Exception):
    """Raised when trying to register an already existing user."""
