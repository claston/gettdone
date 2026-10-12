from dataclasses import dataclass

DEFAULT_MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_PAGES_PER_FILE = 10
DEFAULT_QUOTA_WINDOW_DAYS = 7


@dataclass(frozen=True)
class IdentityContext:
    identity_type: str
    identity_id: str
    quota_limit: int
    quota_mode: str = "conversion"
    quota_window_days: int = DEFAULT_QUOTA_WINDOW_DAYS
    max_upload_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES
    max_pages_per_file: int = DEFAULT_MAX_PAGES_PER_FILE
    max_pages_per_file_ocr: int | None = None
    plan_code: str | None = None
    plan_name: str | None = None
    batch_conversion_enabled: bool = False
    batch_max_files: int = 1
