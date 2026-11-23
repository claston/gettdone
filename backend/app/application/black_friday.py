"""Black Friday 2026 commercial terms; existing plan versions stay untouched."""
from calendar import monthrange
from datetime import datetime, timezone
from os import getenv

START = datetime(2026, 11, 25, 3, tzinfo=timezone.utc)
END = datetime(2026, 12, 1, 3, tzinfo=timezone.utc)
PREPAID_CODE = "black_friday_3_meses"
OFFERS = (
    ("essencial_black", "Essencial Black", 1990, 600, "monthly"),
    ("profissional_black", "Profissional Black", 2990, 1000, "monthly"),
    (PREPAID_CODE, "Black Friday 3 meses", 5990, 1000, "prepaid_quarter"),
)
CODES = tuple(offer[0] for offer in OFFERS)
PAID_PLAN_CODES = {"essencial", "profissional", "escritorio", "escritorio_documentos", *CODES}
UNAVAILABLE_MESSAGE = "As ofertas Black Friday estão disponíveis de 25/11 a 30/11/2026. Confira os planos disponíveis."


def is_available(now: datetime) -> bool:
    return getenv("BLACK_FRIDAY_2026_ENABLED", "true").strip().lower() in {"1", "true", "yes"} and START <= now < END


def catalog_cache_seconds(now: datetime, configured: int) -> int:
    future = [boundary for boundary in (START, END) if now < boundary]
    return min(configured, max(0, int((min(future) - now).total_seconds()) - 1)) if future else configured


def add_months(anchor: datetime, months: int) -> datetime:
    year, month_index = divmod(anchor.year * 12 + anchor.month - 1 + months, 12)
    month = month_index + 1
    return anchor.replace(year=year, month=month, day=min(anchor.day, monthrange(year, month)[1]))


def monthly_window(anchor: datetime, now: datetime) -> tuple[datetime, datetime]:
    months = max(0, (now.year - anchor.year) * 12 + now.month - anchor.month)
    if add_months(anchor, months) > now:
        months = max(0, months - 1)
    return add_months(anchor, months), add_months(anchor, months + 1)


def seed_offers(conn, *, execute, now_iso: str, true_value: bool | int) -> None:
    for code, name, price, quota, period in OFFERS:
        # Private rows stay invisible to old API versions during rolling deploys.
        execute(conn, """INSERT INTO plan_versions
            (id, code, name, version, currency, price_cents, billing_period, quota_mode, quota_limit,
             quota_window_days, max_upload_size_bytes, max_pages_per_file, max_pages_per_file_ocr,
             batch_conversion_enabled, batch_max_files, is_public, is_active, created_at)
            VALUES (?, ?, ?, 1, 'BRL', ?, ?, 'pages', ?, 30, 10485760, 100, 6, ?, 12, FALSE, ?, ?)
            ON CONFLICT(id) DO NOTHING""",
            (f"plan_{code}_v1", code, name, price, period, quota, true_value, true_value, now_iso))


def purchase_terms(code: str) -> str:
    if code == PREPAID_CODE:
        return "Pagamento único; 1.000 páginas por mês durante 3 meses a partir da ativação, sem renovação automática."
    if code in CODES:
        return "Preço e franquia mantidos nas renovações enquanto você permanecer neste plano."
    return ""
