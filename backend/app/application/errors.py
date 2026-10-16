class AnalysisNotFoundError(Exception):
    """Raised when an analysis id cannot be found."""


class UnsupportedFileTypeError(Exception):
    """Raised when the uploaded file extension is unsupported."""


class InvalidFileContentError(Exception):
    """Raised when a supported file has invalid or unreadable content."""


class PdfFinancialValidationError(InvalidFileContentError):
    """Readable invoice fields could not be reconciled with its printed totals."""

    def __init__(self) -> None:
        super().__init__(
            "Não foi possível conferir todos os lançamentos com o total da fatura. "
            "Para evitar valores incorretos, este arquivo não foi convertido. "
            "Envie a fatura original completa baixada da instituição."
        )


class OcrFinancialAmbiguityError(InvalidFileContentError):
    """Raised when OCR succeeded but financial fields cannot be associated safely."""


class CrossPageTransactionSplitError(InvalidFileContentError):
    """Raised when transaction fields were printed on different PDF pages."""

    USER_MESSAGE = (
        "Identificamos que algumas transações estão divididas entre páginas, com informações "
        "como descrição e valor em páginas diferentes. Para evitar lançamentos incorretos, este "
        "arquivo não foi convertido. Envie o extrato original baixado da instituição ou gere "
        "novamente o PDF mantendo todas as colunas na mesma página."
    )

    def __init__(self, *, split_kind: str, description_page: int, amount_page: int) -> None:
        self.split_kind = str(split_kind or "unknown").strip() or "unknown"
        self.description_page = max(1, int(description_page))
        self.amount_page = max(1, int(amount_page))
        super().__init__(self.USER_MESSAGE)


class UnsupportedDocumentContentError(InvalidFileContentError):
    """Raised when readable content is not a supported financial document."""

    def __init__(self, *, document_type: str, message: str) -> None:
        self.document_type = str(document_type or "unknown").strip() or "unknown"
        super().__init__(message)


class MaxPagesPerFileExceededError(Exception):
    """Raised when a PDF exceeds the maximum allowed pages for current identity."""

    def __init__(
        self,
        *,
        pages_count: int,
        max_pages_per_file: int,
        ocr_context: str | None = None,
    ) -> None:
        self.pages_count = max(0, int(pages_count))
        self.max_pages_per_file = max(1, int(max_pages_per_file))
        self.ocr_context = str(ocr_context or "").strip() or None
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


class EmailVerificationRequiredError(Exception):
    """Raised when a local account must verify its email before authentication."""


class InvalidEmailVerificationTokenError(Exception):
    """Raised when an email verification token is missing, expired, or already used."""


class EmailVerificationRateLimitedError(Exception):
    """Raised when verification email resend limits are exceeded."""


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


class GoogleOAuthAccountNotFoundError(Exception):
    """Raised when Google login flow requires an existing account but none matches."""
