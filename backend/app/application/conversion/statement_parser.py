from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.application.conversion.document_extractor import ExtractedDocument, resolve_legacy_parsed_document
from app.application.models import NormalizedTransaction
from app.application.parsers.service import ParsedDocument


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    date: str
    description: str
    amount: float
    type: str | None = None
    running_balance: float | None = None
    warning_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedBankStatement:
    file_type: str
    transactions: list[ParsedTransaction]
    extracted_document: ExtractedDocument
    extracted_text: str | None = None
    layout_inference_name: str | None = None
    layout_inference_confidence: float | None = None
    metadata: dict[str, Any] | None = None


class StatementParser(Protocol):
    def parse(self, *, extracted_document: ExtractedDocument) -> ParsedBankStatement: ...


class LegacyExtractedDocumentStatementParser:
    """Transitional adapter that keeps the legacy ParsedDocument bridge alive."""

    def parse(self, *, extracted_document: ExtractedDocument) -> ParsedBankStatement:
        parsed_document = resolve_legacy_parsed_document(extracted_document)
        if parsed_document is None:
            raise RuntimeError("LegacyExtractedDocumentStatementParser requires a legacy parsed document adapter.")

        warning_types = parsed_document.warning_types or [[] for _ in parsed_document.transactions]
        running_balances = parsed_document.running_balances or [None for _ in parsed_document.transactions]
        transactions = [
            ParsedTransaction(
                date=item.date,
                description=item.description,
                amount=item.amount,
                type=item.type,
                running_balance=running_balances[idx] if idx < len(running_balances) else None,
                warning_types=tuple(warning_types[idx] if idx < len(warning_types) else []),
            )
            for idx, item in enumerate(parsed_document.transactions)
        ]
        return ParsedBankStatement(
            file_type=parsed_document.file_type,
            transactions=transactions,
            extracted_document=extracted_document,
            extracted_text=parsed_document.extracted_text,
            layout_inference_name=parsed_document.layout_inference_name,
            layout_inference_confidence=parsed_document.layout_inference_confidence,
            metadata={
                "legacy_parsed_document": parsed_document,
                "parse_metrics": parsed_document.parse_metrics,
                "canonical_transactions": parsed_document.canonical_transactions,
                "warning_types": parsed_document.warning_types,
                "running_balances": parsed_document.running_balances,
            },
        )


def resolve_legacy_parsed_statement(parsed_statement: ParsedBankStatement) -> ParsedDocument:
    metadata = parsed_statement.metadata or {}
    legacy_parsed_document = metadata.get("legacy_parsed_document")
    if isinstance(legacy_parsed_document, ParsedDocument):
        return legacy_parsed_document

    warning_types = metadata.get("warning_types")
    running_balances = metadata.get("running_balances")
    canonical_transactions = metadata.get("canonical_transactions")
    return ParsedDocument(
        file_type=parsed_statement.file_type,
        transactions=[
            NormalizedTransaction(
                date=item.date,
                description=item.description,
                amount=item.amount,
                type=_resolve_transaction_type(item),
            )
            for item in parsed_statement.transactions
        ],
        layout_inference_name=parsed_statement.layout_inference_name,
        layout_inference_confidence=parsed_statement.layout_inference_confidence,
        extracted_text=parsed_statement.extracted_text,
        parse_metrics=metadata.get("parse_metrics"),
        canonical_transactions=canonical_transactions if isinstance(canonical_transactions, list) else None,
        warning_types=warning_types if isinstance(warning_types, list) else None,
        running_balances=running_balances if isinstance(running_balances, list) else None,
    )


def _resolve_transaction_type(transaction: ParsedTransaction) -> str:
    if transaction.type:
        return transaction.type
    return "credit" if float(transaction.amount) >= 0 else "debit"
