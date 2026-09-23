from __future__ import annotations

import re
import unicodedata
from collections import Counter

from app.application.bank_catalog import resolve_bank_code_from_name
from app.application.document_extraction_models import ExtractedLine
from app.application.layout_profiles.registry import load_layout_profiles

# Only public document vocabulary belongs here. Never copy arbitrary OCR text into an artifact.
PUBLIC_LAYOUT_PHRASES = (
    "CONSULTA DE PAGAMENTOS TRANSFERENCIAS E PIX",
    "COMPROVANTE DE TRANSFERENCIA",
    "EXTRATO DE CONTA CORRENTE",
    "EXTRATO POR PERIODO",
    "PERIODO DO EXTRATO",
    "SALDO NESSE PERIODO",
    "ENTROU NA SUA CONTA",
    "SAIU DA SUA CONTA",
    "SUAS MOVIMENTACOES",
    "DATA DE LANCAMENTO",
    "DATA DE MOVIMENTO",
    "DADOS DA TRANSACAO",
    "DADOS DO RECEBEDOR",
    "DADOS DO PAGADOR",
    "SALDO ANTERIOR",
    "SALDO DO DIA",
    "SALDO FINAL",
    "EXTRATO COMPLETO",
    "EXTRATO MENSAL",
    "EXTRATO DE",
    "PIX RECEBIDO",
    "PIX ENVIADO",
    "CREDITO PIX",
    "DEBITO PIX",
    "DATA HORA",
    "DATA MOV",
    "NR DOC",
    "BANCO PAN",
    "BANCO DO BRASIL",
    "GERENCIADOR CAIXA",
)
PUBLIC_SINGLE_LABELS = frozenset(
    {
        "AGENCIA", "ANTERIOR", "BANCO", "BENEFICIARIO", "CAIXA", "CLIENTE", "CNPJ",
        "CONTA", "CORRENTE", "CPF", "CREDITO", "DATA", "DEBITO", "DESCRICAO",
        "DOCUMENTO", "ENTRADA", "EXTRATO", "FINAL", "HISTORICO", "HORA", "INICIAL",
        "ITAU", "LANCAMENTO", "LANCAMENTOS", "MOVIMENTACAO", "PAGAMENTO",
        "PAGAMENTOS", "PERIODO", "PIX", "SALDO", "SAIDA", "STATUS", "TARIFA",
        "TED", "TOTAL", "TRANSFERENCIA", "TRANSFERENCIAS", "VALOR",
    }
)
PUBLIC_LAYOUT_FINGERPRINTS = (
    "RENTAB.INVEST FACILCRED",
)
PLACEHOLDERS = frozenset(
    {"[TEXTO]", "[NUMERO]", "[DATA]", "[HORA]", "[VALOR]", "[VALOR_C]", "[VALOR_D]", "[VALOR_POS]", "[VALOR_NEG]", "[MOEDA]"}
)
_PHRASES = sorted(((tuple(item.split()), item) for item in PUBLIC_LAYOUT_PHRASES), key=lambda item: -len(item[0]))
_PRIVATE_FIELD_LABELS = {"CLIENTE", "TITULAR", "NOME", "CPF", "CNPJ", "AGENCIA", "CONTA"}
_DATE_PATTERN = re.compile(r"^\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?$")
_TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_AMOUNT_PATTERN = re.compile(r"^[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}[CD+-]?$")


def build_safe_layout_pages(
    pages: list[dict[str, object]],
    *,
    source_layout_lines: tuple[tuple[ExtractedLine, ...], ...] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    safe_pages: list[dict[str, object]] = []
    labels: set[str] = set()
    row_shapes: Counter[str] = Counter()
    geometry_sources: set[str] = set()
    for page_index, page in enumerate(pages):
        raw_lines = str(page["source_text"]).splitlines()
        ocr_lines = source_layout_lines[page_index] if source_layout_lines and page_index < len(source_layout_lines) else ()
        lines = [(line.text, line.bbox) for line in ocr_lines] if ocr_lines else [(line, None) for line in raw_lines]
        max_columns = max((len(line) for line, _ in lines), default=1)
        line_count = max(1, len(lines))
        elements: list[dict[str, object]] = []
        used_geometry = False
        for line_index, (raw_text, bbox) in enumerate(lines):
            safe_text, line_labels = sanitize_layout_line(raw_text)
            if not safe_text:
                continue
            labels.update(line_labels)
            labels.update(public_layout_fingerprint_labels(raw_text))
            row_shapes[_line_shape(safe_text)] += 1
            geometry = _safe_bbox(bbox)
            if geometry is None:
                x = (len(raw_text) - len(raw_text.lstrip())) / max(1, max_columns)
                y = line_index / line_count
                width = min(1.0 - x, max(0.02, len(raw_text.strip()) / max(1, max_columns)))
                height = min(0.05, 1.0 / line_count)
            else:
                x, y, width, height = geometry
                used_geometry = True
            elements.append(
                {
                    "line": line_index,
                    "x": round(x, 6),
                    "y": round(y, 6),
                    "width": round(width, 6),
                    "height": round(height, 6),
                    "role": "layout_line",
                    "text": safe_text,
                }
            )
        position_source = "textract" if used_geometry else "estimated"
        geometry_sources.add(position_source)
        safe_pages.append(
            {
                "width": round(float(page["width"]), 3),
                "height": round(float(page["height"]), 3),
                "line_count": line_count,
                "position_source": position_source,
                "elements": elements,
            }
        )
    geometry_source = next(iter(geometry_sources)) if len(geometry_sources) == 1 else "mixed"
    signals: dict[str, object] = {
        "labels": sorted(labels),
        "geometry_source": geometry_source,
        "page_count": len(safe_pages),
        "line_shape_counts": dict(sorted(row_shapes.items())),
    }
    return safe_pages, signals


def public_layout_fingerprint_labels(raw_text: str) -> tuple[str, ...]:
    normalized = _ascii_upper(raw_text)
    return tuple(fingerprint for fingerprint in PUBLIC_LAYOUT_FINGERPRINTS if fingerprint in normalized)


def sanitize_layout_line(raw_text: str) -> tuple[str, tuple[str, ...]]:
    tokens = _ascii_upper(raw_text).split()
    cores = [_word_core(token) for token in tokens]
    output: list[str] = []
    labels: list[str] = []
    private_field = bool(cores and cores[0] in _PRIVATE_FIELD_LABELS and cores[:2] != ["CONTA", "CORRENTE"])
    index = 0
    while index < len(tokens):
        core = cores[index]
        if private_field and index > 0:
            output.append(_placeholder(tokens[index]))
            index += 1
            continue
        matched = next(
            (
                (parts, label)
                for parts, label in _PHRASES
                if tuple(cores[index : index + len(parts)]) == parts
            ),
            None,
        )
        if matched is not None:
            parts, label = matched
            output.append(label)
            labels.append(label)
            index += len(parts)
            continue
        token = tokens[index]
        if core in PUBLIC_SINGLE_LABELS and not any(character.isdigit() for character in token):
            output.append(core)
            labels.append(core)
        elif token in {"R$", "R"} and index + 1 < len(tokens) and _AMOUNT_PATTERN.fullmatch(tokens[index + 1]):
            output.append("[MOEDA]")
        elif _AMOUNT_PATTERN.fullmatch(token) and index + 1 < len(tokens) and cores[index + 1] in {"C", "D"}:
            output.append(f"[VALOR_{cores[index + 1]}]")
            index += 1
        else:
            output.append(_placeholder(token))
        index += 1
    return " ".join(output), tuple(labels)


def match_layout_candidates(*, signals: dict[str, object], bank_code: str | None) -> dict[str, object]:
    safe_labels = tuple(_ascii_upper(label) for label in signals.get("labels") or ())
    candidates: list[dict[str, object]] = []
    for profile in load_layout_profiles():
        profile_bank_code = resolve_bank_code_from_name(profile.bank)
        if bank_code and profile_bank_code != bank_code:
            continue
        required_hits = [
            _ascii_upper(keyword)
            for keyword in profile.required_keywords
            if any(_ascii_upper(keyword) in label for label in safe_labels)
        ]
        header_hits = [
            _ascii_upper(keyword)
            for keyword in profile.header_keywords
            if any(_ascii_upper(keyword) in label for label in safe_labels)
        ]
        if not required_hits and not header_hits:
            continue
        required_ratio = len(required_hits) / max(1, len(profile.required_keywords))
        header_ratio = len(header_hits) / max(1, len(profile.header_keywords))
        score = round((required_ratio * 0.8) + (header_ratio * 0.2), 3)
        candidates.append(
            {
                "layout_name": profile.profile_name,
                "score": score,
                "matched_labels": sorted(set(required_hits + header_hits)),
            }
        )
    candidates.sort(key=lambda candidate: (-float(candidate["score"]), str(candidate["layout_name"])))
    top_score = float(candidates[0]["score"]) if candidates else 0.0
    second_score = float(candidates[1]["score"]) if len(candidates) > 1 else 0.0
    top_hits = len(candidates[0]["matched_labels"]) if candidates else 0
    if top_score >= 0.7 and top_hits >= 3 and top_score - second_score >= 0.12:
        status = "existing_candidate"
    elif top_score >= 0.4 and top_hits >= 3:
        status = "ambiguous"
    elif len(signals.get("labels") or ()) >= 4:
        status = "new_candidate"
    else:
        status = "insufficient_signals"
    return {"status": status, "candidates": candidates[:3]}


def _ascii_upper(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in decomposed if not unicodedata.combining(character)).upper()


def _word_core(token: str) -> str:
    return "".join(character for character in token if character.isalpha())


def _placeholder(token: str) -> str:
    if _DATE_PATTERN.fullmatch(token):
        return "[DATA]"
    if _TIME_PATTERN.fullmatch(token):
        return "[HORA]"
    if _AMOUNT_PATTERN.fullmatch(token):
        if token.endswith(("C", "D")):
            return f"[VALOR_{token[-1]}]"
        if token.startswith("-") or token.endswith("-"):
            return "[VALOR_NEG]"
        if token.startswith("+") or token.endswith("+"):
            return "[VALOR_POS]"
        return "[VALOR]"
    if any(character.isdigit() for character in token):
        return "[NUMERO]"
    return "[TEXTO]"


def _safe_bbox(bbox: dict[str, float] | None) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, dict):
        return None
    values = [bbox.get(key) for key in ("left", "top", "width", "height")]
    if not all(isinstance(value, int | float) for value in values):
        return None
    left, top, width, height = (float(value) for value in values)
    if not (0 <= left <= 1 and 0 <= top <= 1 and 0 < width <= 1 - left and 0 < height <= 1 - top):
        return None
    return left, top, width, height


def _line_shape(safe_text: str) -> str:
    parts = []
    if "[DATA]" in safe_text:
        parts.append("date")
    if "[HORA]" in safe_text:
        parts.append("time")
    if "[VALOR" in safe_text:
        parts.append("amount")
    if "[NUMERO]" in safe_text:
        parts.append("number")
    return "+".join(parts) or "text"
