from pathlib import Path

FRONTEND_SOURCE = Path(__file__).resolve().parents[2] / "frontend" / "ofx-convert.js"


def test_runtime_discovery_uses_http_only_cookie_session() -> None:
    source = FRONTEND_SOURCE.read_text(encoding="utf-8")
    runtime_block = source[
        source.index("async function syncConversionRuntime()") : source.index("let anonymousSessionPromise")
    ]

    assert "session.request" in runtime_block
    assert "authorization" not in runtime_block


def test_runtime_discovery_enables_multiple_selection_only_above_one_file() -> None:
    source = FRONTEND_SOURCE.read_text(encoding="utf-8")
    runtime_block = source[
        source.index("async function syncConversionRuntime()") : source.index("let anonymousSessionPromise")
    ]

    assert "const multiFileEnabled = directBatchEnabled && batchMaxFiles > 1;" in runtime_block
    assert "input.multiple = multiFileEnabled;" in runtime_block
    assert "emptyHint.textContent = multiFileEnabled" in runtime_block


def test_batch_review_resets_bank_code_to_the_selected_job_analysis() -> None:
    source = FRONTEND_SOURCE.read_text(encoding="utf-8")
    apply_block = source[source.index("function applyConversionPayload(payload)") : source.index("async function runLegacyConvert")]

    assert 'state.bankCodeOverride = resolveInitialBankCode(analysis, "");' in apply_block
    assert "resolveInitialBankCode(analysis, state.bankCodeOverride)" not in apply_block
