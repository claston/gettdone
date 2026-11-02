"""Small adapters used by existing HTTP routes; no parallel financial writer."""
from fastapi import HTTPException

from app.application.errors import AnalysisAccessDeniedError, AnalysisEditConflictError, AnalysisNotFoundError
from app.dependencies import get_canonical_pro_service


def canonical_record_payload(record, owner):
    payload = record.result.payload if record.result else None
    if not payload or payload.get("authority") != "postgres_v1":
        return payload
    service = get_canonical_pro_service()
    if service is None:
        raise HTTPException(503, "A revisão está temporariamente indisponível.")
    try:
        scope = service.resolve(owner, record.result.analysis_id)
        if scope is None:
            raise AnalysisNotFoundError
        analysis = service.read(scope)
        return {**payload, "analysis": analysis, "reviewed_at": analysis.get("reviewed_at")}
    except AnalysisNotFoundError:
        raise HTTPException(404, "Analysis not found") from None
    except AnalysisAccessDeniedError:
        raise HTTPException(403, "O acesso ativo ao PRO é necessário.") from None
    except AnalysisEditConflictError:
        raise HTTPException(409, "A leitura desta análise está suspensa.") from None


def export_canonical_record(record, owner, file_format, checks, expected_versions=None):
    if not record.result or (record.result.payload or {}).get("authority") != "postgres_v1":
        return None
    service = get_canonical_pro_service()
    if service is None:
        raise HTTPException(503, "A exportação está temporariamente indisponível.")
    try:
        scope = service.resolve(owner, record.result.analysis_id)
    except AnalysisNotFoundError:
        raise HTTPException(404, "Analysis not found") from None
    except AnalysisAccessDeniedError:
        raise HTTPException(403, "O acesso ativo ao PRO é necessário.") from None
    if scope is None:
        raise HTTPException(404, "Analysis not found")
    expected = (expected_versions or {}).get(scope.analysis_id, (record.result.payload["analysis"])["version"])
    data, version = canonical_bytes(service, scope, f"converted.{file_format}", expected_version=expected)
    checks.append((service, scope, version))
    return data


def validate_export_checks(checks):
    for service, scope, version in checks:
        try:
            if service.read(scope)["version"] != version:
                raise AnalysisEditConflictError
        except (AnalysisEditConflictError, AnalysisNotFoundError):
            raise HTTPException(409, "O lote mudou durante a exportação. Recarregue e tente novamente.") from None
        except AnalysisAccessDeniedError:
            raise HTTPException(403, "O acesso ativo ao PRO é necessário.") from None


def resolve_canonical(identity, analysis_id):
    # Anonymous and Free reads do not depend on the canonical schema or service.
    if identity.identity_type != "user" or not identity.batch_conversion_enabled:
        return None
    service = get_canonical_pro_service()
    if service is None:
        return None
    try:
        scope = service.resolve(identity.identity_id, analysis_id)
        return CanonicalReportAdapter(service, scope) if scope else None
    except AnalysisNotFoundError:
        raise HTTPException(404, "Analysis not found") from None
    except AnalysisAccessDeniedError:
        raise HTTPException(403, "O acesso ativo ao PRO é necessário.") from None


class CanonicalReportAdapter:
    def __init__(self, service, scope):
        self.canonical, self.scope = service, scope

    def get_convert_edit_operation(self, analysis_id, operation_id):
        self.canonical.read(self.scope)
        return self.canonical.repository.confirm(self.scope, operation_id)

    def apply_convert_edits(self, analysis_id, edits, **options):
        # Old endpoints may use stable aliases and updated_at. Their translation
        # happens under the SQL lock. New clients always submit UUID + version.
        return self.canonical.edit(self.scope, edits, **options)

    def get_upload_filename(self, analysis_id):
        return self.canonical.read(self.scope).get("upload_filename")


def canonical_bytes(service, scope, name, *, expected_version=None):
    try:
        return service.download(scope, name, expected_version=expected_version)
    except AnalysisNotFoundError:
        raise HTTPException(404, "Analysis not found") from None
    except AnalysisAccessDeniedError:
        raise HTTPException(403, "O acesso ativo ao PRO é necessário.") from None
    except AnalysisEditConflictError:
        raise HTTPException(409, "A revisão mudou ou está suspensa. Recarregue antes de exportar.") from None
    except ValueError:
        raise HTTPException(400, "Não foi possível gerar o formato solicitado.") from None
    except Exception:
        raise HTTPException(503, "A revisão está salva. Tente gerar o arquivo novamente.") from None
