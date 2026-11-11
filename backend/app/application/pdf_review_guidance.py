"""Explain extraction evidence without changing transactions or review decisions."""
import re
from math import isfinite

from app.application.conversion_quality import assess_conversion_quality


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def build_pdf_review_guidance(analysis):
    if _get(analysis, "file_type") != "pdf" or _get(analysis, "financial_document") is not None:
        return None
    metrics = _get(analysis, "pdf_processing_metrics") or {}

    def metric(key, default=0):
        return _get(metrics, key, default) or default

    codes = metric('canonical_warning_types_list', [])
    warnings = set(re.split(r'[,|]', codes) if isinstance(codes, str) else codes)
    warnings.update(filter(None, re.split(r'[,|]', metric('canonical_warning_types', ''))))
    warnings.discard('')
    rows = [*(_get(analysis, "report_transactions") or []), *(_get(analysis, "preview_transactions") or [])]
    for row in rows:
        if not _get(row, "is_deleted", False):
            warnings.update(_get(row, "warning_types", []) or [])
    reason = metric("export_recommendation_reason", "")
    comparison = _get(analysis, "statement_balance_check")
    located = _located_details(analysis, rows)
    mismatch = bool(
        metric("balance_consistency_failed")
        or _get(comparison, "difference", 0)
        or any(_balance_description(_get(row, 'balance_check')) for row in rows if not _get(row, 'is_deleted', False))
        or warnings
        & {
            "balance_consistency_failed",
            "financial_validation_failed",
            "printed_summary_balance_mismatch",
            "printed_totals_differ_from_extracted_movements",
        }
    )
    if mismatch:
        return {
            "category": "financial_discrepancy",
            "title": "Divergência financeira encontrada",
            "message": "Os lançamentos e os saldos ou totais de referência não conferem. "
            "Confira a comparação no PDF antes de alterar valores ou concluir a revisão." + _suffix(located),
        }
    shared = "shared_operation_reference" in warnings
    contextual = any(
        metric(key)
        for key in (
            "partial_statement",
            "document_period_unavailable",
            "opening_balance_ambiguous",
            "balance_bounds_unavailable",
            "ocr_unresolved_rows",
            "ocr_review_fields_count",
            "ocr_review_rows_count",
            "ocr_annotations_detected",
            "statement_validation_failed",
        )
    ) or (metric('statement_rows_expected') > 0 and
          (metric('statement_rows_parsed') < metric('statement_rows_expected') or metric('statement_row_coverage_rate', 1) < 1))
    contextual = contextual or metric('balance_validation_status', '') == 'review_required' or any(
        _get(part, 'requires_review', False) for part in (_get(analysis, 'document_parts') or []))
    if (not shared and not warnings and not contextual and not located
            and metric("export_recommendation", "") != "review_recommended"):
        return None
    if (reason == 'medium_confidence_band' and metric('confidence_band', '') == 'medium'
            and not warnings and not contextual and not located
            and metric('balance_validation_status', '') not in {'opening_inferred_from_closing', 'not_available'}):
        quality = assess_conversion_quality(status='success', conversion_type='pdf',
            transactions_count=_get(analysis, 'transactions_total'),
            layout_name=_get(analysis, 'layout_inference_name'),
            layout_confidence=_get(analysis, 'layout_inference_confidence'),
            selected_parser=metric('selected_parser', ''),
            warning_count=max(metric('canonical_warning_count'), metric('canonical_warning_transactions_count')),
            balance_failed=metric('balance_consistency_failed'))
        if quality.status == 'clean':
            return {'category': 'information', 'title': 'Conversão concluída',
                    'message': 'Os lançamentos foram extraídos sem alertas de revisão.'}
    controls = (
        metric("financial_controls_validated") == 1
        and metric("balance_validation_status", "") == "validated"
        and metric("printed_balance_checks") > 0
        and metric("statement_row_coverage_rate") == 1
    )
    only_reference = warnings <= {"shared_operation_reference", "manual_review_recommended"}
    known_reason = reason in {
        "",
        "low_confidence_band",
        "medium_confidence_band",
        "high_confidence_band",
        "complete_movement_section_and_printed_controls_validated",
    }
    if shared and controls and only_reference and not contextual and known_reason:
        return {
            "category": "information",
            "title": "Referências de operação compartilhadas",
            "message": "Na leitura original, os controles financeiros impressos e a cobertura dos movimentos conferiram. "
            "Há lançamentos com a mesma referência de operação; isso não comprova duplicidade. "
            "Eles foram preservados como aparecem no PDF. Não exclua movimentos apenas pela referência.",
        }
    details = []
    if metric('partial_statement') or 'partial_statement' in warnings:
        details.append('A cobertura do documento está incompleta ou não foi confirmada. Confira as páginas e os períodos incluídos.')
    if metric('document_period_unavailable') or 'document_period_unavailable' in warnings:
        details.append('O período completo não foi confirmado no documento; confira as datas inicial e final.')
    if 'statement_blocks_not_contiguous' in warnings:
        details.append('Os blocos têm lacunas, períodos sobrepostos ou saldos sem continuidade confirmada.')
    if reason == 'opening_inferred_from_closing' or metric('balance_validation_status', '') == 'opening_inferred_from_closing':
        details.append('O saldo inicial foi inferido a partir do fechamento; confira o saldo impresso no documento.')
    if metric('opening_balance_ambiguous'):
        details.append('O saldo inicial tem mais de uma interpretação possível e precisa ser conferido.')
    if metric('balance_bounds_unavailable') or reason == 'no_printed_balance_controls':
        details.append('Os saldos ou controles impressos necessários para conferir o período não estão disponíveis.')
    if reason == "unverified_visual_recovery":
        details.append("A leitura por OCR foi concluída, mas sua validação financeira está incompleta.")
    # Counts describe their original, limited scope. Neither one proves a global balance.
    if "financial_validation_failed" not in warnings:
        if metric("balance_consistency_checked"):
            details.append(f"Na leitura original, {metric('balance_consistency_checked')} verificações entre saldos conferiram.")
        elif metric("printed_balance_checks"):
            details.append(f"Na leitura original, foram conferidos {metric('printed_balance_checks')} controles impressos.")
    if (metric("balance_consistency_checked") or metric("printed_balance_checks")) and "financial_validation_failed" not in warnings:
        details.append("Essas verificações não certificam o saldo global nem a cobertura completa do período. Confira os limites no PDF.")
    else:
        details.append(
            "A validação automática não dispõe de controles suficientes para confirmar todos os dados. "
            "Confira os lançamentos e a cobertura do período no PDF; "
            "confiança técnica reduzida, por si só, não indica erro financeiro."
        )
    if shared:
        details.append("Uma referência de operação compartilhada não comprova duplicidade; os movimentos foram preservados.")
    details.extend(located)
    return {"category": "validation_incomplete", "title": "Validação automática incompleta", "message": " ".join(details)}


def _suffix(details):
    return ' ' + ' '.join(details) if details else ''


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _balance_description(check):
    values = [_get(check, key) for key in ('expected_balance', 'reported_balance', 'difference')]
    if not all(isinstance(n, (float, int)) and not isinstance(n, bool) and isfinite(n) for n in values) or not values[-1]:
        return None
    formatted = [f'{value:,.2f}'.replace(',', '|').replace('.', ',').replace('|', '.') for value in values]
    return f'saldo calculado {formatted[0]}, saldo informado {formatted[1]}, diferença {formatted[2]}.'


def _located_details(analysis, rows):
    details = []
    comparison = _balance_description(_get(analysis, 'statement_balance_check'))
    if comparison:
        details.append('Comparação do período: ' + comparison)
    fields = {'ocr_amount_confidence_unconfirmed': 'O valor precisa ser conferido no PDF.',
              'ocr_date_confidence_unconfirmed': 'A data precisa ser conferida no PDF.',
              'ocr_control_confidence_unconfirmed': 'O controle financeiro precisa ser conferido no PDF.'}
    for row in rows:
        if _get(row, 'is_deleted', False):
            continue
        page, line = _get(row, 'source_page'), _get(row, 'source_line')
        if not _positive_int(page):
            continue
        location = f'Página {page}' + (f', linha {line}' if _positive_int(line) else '') + ': '
        balance = _balance_description(_get(row, 'balance_check'))
        if balance:
            details.append(location + balance)
        for code in _get(row, 'warning_types', []) or []:
            if code in fields:
                details.append(location + fields[code])
    for index, part in enumerate(_get(analysis, 'document_parts', []) or [], 1):
        if not _get(part, 'requires_review', False):
            continue
        pages = [str(page) for page in (_get(part, 'pages', []) or []) if _positive_int(page)]
        if pages:
            label = f'Bloco {index}' if _get(part, 'included_in_statement', False) else 'Trecho separado'
            details.append(f'{label}, páginas {", ".join(pages)}: confira a cobertura e os controles desse trecho no PDF.')
    unique = list(dict.fromkeys(details))
    overflow = [f'Há mais {len(unique)-6} ocorrências; consulte os detalhes dos lançamentos e blocos.'] if len(unique) > 6 else []
    return unique[:6] + overflow
