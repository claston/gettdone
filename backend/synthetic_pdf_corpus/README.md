# Synthetic PDF corpus

This package keeps synthetic statement generation and evaluation independent from production parser modules.

## Modules

- `models.py`: immutable scenario and evaluation contracts.
- `catalog.py`: versioned JSON fixture loading and validation.
- `native_text_pdf.py`: searchable PDF generation.
- `scanned_pdf.py`: image-only PDF generation.
- `generator.py`: variant dispatch without parser dependencies.
- `evaluator.py`: recall, precision, missing-row, and false-positive comparison.
- `runner.py`: parser execution adapter and blocking/known-gap policy.

Scenario definitions live in `backend/tests/fixtures/pdf_scenarios`. Generated PDFs are temporary artifacts and are not committed.

## Generate PDFs for manual upload

From the repository root:

```powershell
backend\venv\Scripts\python.exe backend\scripts\evaluate_synthetic_pdf_corpus.py `
  --output-dir backend\tmp\synthetic_pdf_corpus_upload
```

This generates both searchable and scanned variants.

## Evaluate searchable PDFs

```powershell
backend\venv\Scripts\python.exe backend\scripts\evaluate_synthetic_pdf_corpus.py `
  --variants native_text `
  --evaluate `
  --output-dir backend\tmp\synthetic_pdf_corpus_evaluation
```

The command writes `evaluation.json` and returns a non-zero exit code only for enforced scenarios. Known gaps remain visible without blocking unrelated changes.

Real scanned-PDF evaluation requires the Tesseract binary and `PDF_OCR_ENABLED=1`. The optional pytest coverage uses the `pdf_ocr` marker and skips when Tesseract is unavailable.
