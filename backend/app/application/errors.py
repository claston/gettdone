class AnalysisNotFoundError(Exception):
    """Raised when an analysis id cannot be found."""


class UnsupportedFileTypeError(Exception):
    """Raised when the uploaded file extension is unsupported."""


class InvalidFileContentError(Exception):
    """Raised when a supported file has invalid or unreadable content."""


class MaxPagesPerFileExceededError(Exception):
    """Raised when a PDF exceeds the maximum allowed pages for current identity."""

    def __init__(self, *, pages_count: int, max_pages_per_file: int) -> None:
        self.pages_count = max(0, int(pages_count))
        self.max_pages_per_file = max(1, int(max_pages_per_file))
        super().__init__(
            f"PDF has {self.pages_count} pages, exceeding the max of {self.max_pages_per_file} pages per file."
        )


class QuotaExceededError(Exception):
    """Raised when the identity has no conversion quota remaining."""


class FileTooLargeError(Exception):
    """Raised when uploaded file exceeds maximum allowed size."""


class InvalidUserTokenError(Exception):
    """Raised when a user token cannot be validated."""


class InvalidSessionTokenError(Exception):
    """Raised when a session access/refresh token cannot be validated."""


class ReusedSessionTokenError(Exception):
    """Raised when a rotated refresh token is reused (possible token theft)."""


class UserAlreadyExistsError(Exception):
    """Raised when trying to register an already existing user."""


class InvalidCredentialsError(Exception):
    """Raised when user login credentials are invalid."""


class AnalysisAccessDeniedError(Exception):
    """Raised when an identity attempts to access another identity's analysis."""


class AnalysisEditConflictError(Exception):
    """Raised when edit request is based on an outdated analysis version."""


class ContactProviderNotConfiguredError(Exception):
    """Raised when contact provider credentials are not configured."""


class ContactDeliveryError(Exception):
    """Raised when contact message delivery fails at provider level."""


class GoogleOAuthNotConfiguredError(Exception):
    """Raised when Google OAuth env/config is missing."""


class GoogleOAuthStateError(Exception):
    """Raised when OAuth state is invalid, missing, or expired."""


class GoogleOAuthExchangeError(Exception):
    """Raised when token exchange or profile fetch fails."""
