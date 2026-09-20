from datetime import date
from decimal import Decimal

from app.application.ai_recovery.benchmark import AIRecoveryBenchmarkCase, run_ai_recovery_benchmark
from app.application.ai_recovery.contracts import AIExtractionResult, AIExtractionUsage
from app.application.ai_recovery.models import AIStatement, AITransaction, TransactionDirection


class _FakeExtractor:
    def __init__(self, results: dict[str, AIExtractionResult]) -> None:
        self.results = results

    def extract(self, *, filename: str, raw_bytes: bytes, page_count: int) -> AIExtractionResult:
        del raw_bytes, page_count
        return self.results[filename]


def _statement(*, amount: str = "500.00", direction: TransactionDirection = TransactionDirection.CREDIT) -> AIStatement:
    return AIStatement(
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("1500.00"),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        transactions=[
            AITransaction(
                date=date(2026, 8, 2),
                description="PIX recebido",
                amount=Decimal(amount),
                direction=direction,
                running_balance=Decimal("1500.00"),
                source_page=1,
                source_line=8,
            )
        ],
    )


def _result(statement: AIStatement, *, input_tokens: int = 1000, output_tokens: int = 200) -> AIExtractionResult:
    return AIExtractionResult(
        statement=statement,
        model_id="test-model",
        prompt_version="test-prompt",
        latency_ms=125.5,
        usage=AIExtractionUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def test_benchmark_aggregates_exact_accuracy_validation_tokens_and_latency() -> None:
    expected = _statement()
    cases = [
        AIRecoveryBenchmarkCase(
            case_id="case-001",
            filename="case-001.pdf",
            raw_bytes=b"%PDF-one",
            page_count=1,
            expected=expected,
        ),
        AIRecoveryBenchmarkCase(
            case_id="case-002",
            filename="case-002.pdf",
            raw_bytes=b"%PDF-two",
            page_count=1,
            expected=expected,
        ),
    ]
    extractor = _FakeExtractor(
        {
            "case-001.pdf": _result(expected),
            "case-002.pdf": _result(_statement(amount="50.00")),
        }
    )

    report = run_ai_recovery_benchmark(cases=cases, extractor=extractor)

    assert report.total_cases == 2
    assert report.completed_cases == 2
    assert report.extraction_failures == 0
    assert report.exact_cases == 1
    assert report.approved_cases == 1
    assert report.rejected_cases == 1
    assert report.inconclusive_cases == 0
    assert report.critical_false_accepts == 0
    assert report.date_accuracy == Decimal("1.000000")
    assert report.amount_accuracy == Decimal("0.500000")
    assert report.direction_accuracy == Decimal("1.000000")
    assert report.description_accuracy == Decimal("1.000000")
    assert report.input_tokens == 2000
    assert report.output_tokens == 400
    assert report.latency_p50_ms == 125.5
    assert report.latency_p95_ms == 125.5
    assert [item.case_id for item in report.cases] == ["case-001", "case-002"]
    assert all(not hasattr(item, "actual_statement") for item in report.cases)


def test_benchmark_flags_financially_wrong_output_that_validator_approves() -> None:
    expected = _statement()
    actual = _statement(direction=TransactionDirection.DEBIT)
    actual.opening_balance = Decimal("2000.00")
    actual.closing_balance = Decimal("1500.00")
    case = AIRecoveryBenchmarkCase(
        case_id="case-false-accept",
        filename="false-accept.pdf",
        raw_bytes=b"%PDF",
        page_count=1,
        expected=expected,
    )

    report = run_ai_recovery_benchmark(
        cases=[case],
        extractor=_FakeExtractor({"false-accept.pdf": _result(actual)}),
    )

    assert report.approved_cases == 1
    assert report.critical_false_accepts == 1
    assert report.cases[0].financially_exact is False
    assert report.cases[0].critical_false_accept is True


def test_benchmark_counts_safe_extraction_error_without_exposing_message() -> None:
    from app.application.ai_recovery.errors import AIExtractionError, AIExtractionErrorCode

    class _FailingExtractor:
        def extract(self, **kwargs):
            del kwargs
            raise AIExtractionError(AIExtractionErrorCode.PROVIDER_TIMEOUT, "private failure details")

    case = AIRecoveryBenchmarkCase(
        case_id="case-timeout",
        filename="timeout.pdf",
        raw_bytes=b"%PDF",
        page_count=1,
        expected=_statement(),
    )

    report = run_ai_recovery_benchmark(cases=[case], extractor=_FailingExtractor())

    assert report.completed_cases == 0
    assert report.extraction_failures == 1
    assert report.cases[0].error_code == "provider_timeout"
    assert "private" not in str(report.to_dict())


def test_benchmark_penalizes_missing_and_extra_rows_in_field_denominators() -> None:
    expected = _statement()
    expected.transactions.append(
        AITransaction(
            date=date(2026, 8, 3),
            description="Pagamento",
            amount=Decimal("100.00"),
            direction=TransactionDirection.DEBIT,
            source_page=1,
            source_line=9,
        )
    )
    case = AIRecoveryBenchmarkCase(
        case_id="case-missing",
        filename="missing.pdf",
        raw_bytes=b"%PDF",
        page_count=1,
        expected=expected,
    )

    report = run_ai_recovery_benchmark(
        cases=[case],
        extractor=_FakeExtractor({"missing.pdf": _result(_statement())}),
    )

    assert report.date_accuracy == Decimal("0.500000")
    assert report.amount_accuracy == Decimal("0.500000")
    assert report.cases[0].expected_transactions == 2
    assert report.cases[0].actual_transactions == 1
