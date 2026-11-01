"""Server-side eligibility and bounded, idempotent paid OCR attempts."""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

from pypdf import PdfReader

from app.application.pdf_representation_classifier import classify_pdf_representation


class ConversionProAccessError(ValueError):
    pass


class ConversionProQuotaError(ValueError):
    pass


@dataclass(frozen=True)
class ProConversionPolicy:
    enabled: bool = False
    max_pages: int = 6
    monthly_pages: int = 20

    @classmethod
    def from_environment(cls):
        return cls(
            os.getenv("CONVERSION_PRO_ENABLED", "false").lower() in {"true", "1"},
            min(6, max(1, int(os.getenv("CONVERSION_PRO_MAX_PAGES", "6")))),
            max(1, int(os.getenv("CONVERSION_PRO_MONTHLY_PAGES", "20"))),
        )


def inspect_pro_pdf(raw, *, max_pages):
    try:
        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("A Conversão PRO não aceita PDFs protegidos por senha.")
        pages = len(reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Não foi possível ler este PDF.") from exc
    if not 1 <= pages <= max_pages:
        raise ValueError(f"A Conversão PRO permite até {max_pages} páginas por PDF.")
    classification = classify_pdf_representation(raw)
    if not classification.requires_visual_extraction:
        raise ValueError("Este PDF permite leitura de texto. Use a conversão Padrão.")
    return pages


class ConversionProQuota:
    def __init__(self, access, policy=None):
        self.access = access
        self.policy = policy or ProConversionPolicy.from_environment()

    def eligible(self, identity):
        if (
            not self.policy.enabled
            or identity.identity_type != "user"
            or identity.plan_code not in {"essencial", "profissional", "escritorio"}
        ):
            return False
        plan = self.access._read_active_user_plan(user_id=identity.identity_id)
        return bool(plan and plan["code"] in {"essencial", "profissional", "escritorio"})

    def require_access(self, identity):
        if not self.eligible(identity):
            raise ConversionProAccessError("A Conversão PRO está disponível somente para assinaturas pagas ativas, quando habilitada.")

    def _period(self):
        return self.access.now_provider().astimezone(timezone.utc).strftime("%Y-%m")

    def reset_at(self):
        now = self.access.now_provider().astimezone(timezone.utc)
        return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc).isoformat()

    def _used(self, conn, identity):
        row = self.access._fetchone(
            conn,
            """SELECT COALESCE(SUM(CASE WHEN status='reserved'
            THEN reserved_pages ELSE attempted_pages END),0) AS used FROM conversion_pro_usage
            WHERE owner_id=? AND period=?""",
            (identity.identity_id, self._period()),
        )
        return int(row["used"])

    def remaining(self, identity):
        self.require_access(identity)
        with self.access._connect() as conn:
            return max(0, self.policy.monthly_pages - self._used(conn, identity))

    def reserve(self, job_id, identity, pages):
        self.require_access(identity)
        if not 1 <= pages <= min(self.policy.max_pages, identity.max_pages_per_file_ocr or self.policy.max_pages):
            raise ValueError("Quantidade de páginas fora do limite da Conversão PRO.")
        with self.access._lock, self.access._connect() as conn:
            if self.access._use_postgres:
                self.access._execute(conn, "SELECT pg_advisory_xact_lock(hashtext(?))", ("conversion-pro:" + identity.identity_id,))
            else:
                conn.execute("BEGIN IMMEDIATE")
            existing = self.access._fetchone(conn, "SELECT * FROM conversion_pro_usage WHERE job_id=?", (job_id,))
            if existing:
                if (
                    existing["owner_id"] != identity.identity_id
                    or existing["reserved_pages"] != pages
                    or existing["status"] != "reserved"
                    or existing["attempted_pages"] != 0
                ):
                    raise ConversionProAccessError("Esta Conversão PRO já foi iniciada. Envie uma nova solicitação para tentar novamente.")
            else:
                if self._used(conn, identity) + pages > self.policy.monthly_pages:
                    raise ConversionProQuotaError("O limite mensal da Conversão PRO é insuficiente para este PDF.")
                self.access._execute(
                    conn,
                    """INSERT INTO conversion_pro_usage
                    (job_id,owner_id,period,reserved_pages,status,created_at) VALUES (?,?,?,?,'reserved',?)""",
                    (job_id, identity.identity_id, self._period(), pages, self.access.now_provider().isoformat()),
                )
            remaining = max(0, self.policy.monthly_pages - self._used(conn, identity))
            conn.commit()
            return remaining

    def before_page(self, job_id, identity, page):
        self.require_access(identity)
        with self.access._lock, self.access._connect() as conn:
            cursor = self.access._execute(
                conn,
                """UPDATE conversion_pro_usage SET attempted_pages=?
                WHERE job_id=? AND owner_id=? AND status='reserved' AND attempted_pages=? AND reserved_pages>=?""",
                (page, job_id, identity.identity_id, page - 1, page),
            )
            if cursor.rowcount != 1:
                raise ConversionProAccessError("A tentativa da Conversão PRO já foi iniciada ou não pertence a esta conta.")
            conn.commit()

    def before_document(self, job_id, identity, pages):
        self.require_access(identity)
        with self.access._lock, self.access._connect() as conn:
            cursor = self.access._execute(
                conn,
                """UPDATE conversion_pro_usage SET attempted_pages=reserved_pages
                WHERE job_id=? AND owner_id=? AND status='reserved' AND attempted_pages=0 AND reserved_pages=?""",
                (job_id, identity.identity_id, pages),
            )
            if cursor.rowcount != 1:
                raise ConversionProAccessError("Esta Conversão PRO já foi iniciada ou não pertence a esta conta.")
            conn.commit()

    def reject_document(self, job_id, identity, pages):
        with self.access._lock, self.access._connect() as conn:
            self.access._execute(
                conn,
                """UPDATE conversion_pro_usage SET attempted_pages=0,status='finished'
                WHERE job_id=? AND owner_id=? AND status='reserved' AND reserved_pages=? AND attempted_pages=?""",
                (job_id, identity.identity_id, pages, pages),
            )
            conn.commit()

    def finish(self, job_id, identity):
        with self.access._lock, self.access._connect() as conn:
            self.access._execute(
                conn, "UPDATE conversion_pro_usage SET status='finished' WHERE job_id=? AND owner_id=?", (job_id, identity.identity_id)
            )
            conn.commit()

    def reject_page(self, job_id, identity, page):
        """A definite authorization/input rejection never started billable analysis."""
        with self.access._lock, self.access._connect() as conn:
            self.access._execute(
                conn,
                """UPDATE conversion_pro_usage SET attempted_pages=?,status='finished'
                WHERE job_id=? AND owner_id=? AND status='reserved' AND attempted_pages=?""",
                (page - 1, job_id, identity.identity_id, page),
            )
            conn.commit()
