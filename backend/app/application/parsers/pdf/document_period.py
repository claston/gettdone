"""Document coverage is optional; transaction dates and accounting controls are not."""

from datetime import date, datetime

from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.statement_review import document_period_unavailable


def _date(value):
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError) as exc:
        raise PdfFinancialValidationError("O período impresso contém uma data inválida.") from exc


def optional_document_period(match, *, parse_date=_date):
    if match is None:
        document_period_unavailable()
        return None, None
    values = match.groups() if hasattr(match, "groups") else match
    if len(values) != 2:
        raise PdfFinancialValidationError("Não foi possível interpretar o período impresso.")
    start, end = map(parse_date, values)
    if start > end:
        raise PdfFinancialValidationError("O período impresso tem início posterior ao fim.")
    return start, end


def unique_document_period(matches, *, parse_date=_date):
    values = set(matches)
    if len(values) > 1:
        raise PdfFinancialValidationError("Há períodos conflitantes; confira a separação dos extratos.")
    return optional_document_period(next(iter(values)) if values else None, parse_date=parse_date)


def period_contains(value, start, end):
    # Bounds never supply an invented year to a partially legible transaction.
    if not isinstance(value, date):
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise PdfFinancialValidationError("Um lançamento está sem data completa e inequívoca.") from exc
    return (start is None or start <= value) and (end is None or value <= end)


def require_period_context(start, end):
    if start is None or end is None:
        raise PdfFinancialValidationError("Falta o período necessário para completar o ano ou mês das datas dos lançamentos.")
    return start, end
