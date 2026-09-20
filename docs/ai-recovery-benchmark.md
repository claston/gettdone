# AI recovery offline benchmark

The AI recovery benchmark sends labeled PDF statements to Amazon Bedrock. It is intentionally separate from the user conversion flow and makes no calls unless `--allow-bedrock-call` is present.

## Data handling

- Keep PDFs and manifests in approved private storage, outside this public repository.
- Use technical case IDs only. Do not include customer names, account numbers, or document text in IDs or paths.
- The generated report contains aggregate accuracy, token, latency, validator, and safe error-code metrics. It does not contain PDF bytes, extracted transactions, or expected transactions.
- Each run accepts at most 50 PDFs, and each PDF must contain between 1 and 15 pages.

## Manifest

PDF paths are relative to the manifest and cannot escape its directory. The optional `page_count` is checked against the real PDF.

```json
{
  "cases": [
    {
      "id": "case-001",
      "pdf": "case-001.pdf",
      "page_count": 1,
      "expected": {
        "transactions": [
          {
            "date": "2026-08-02",
            "description": "PIX recebido",
            "amount": "500.00",
            "direction": "credit",
            "running_balance": "1500.00",
            "source_page": 1,
            "source_line": 8
          }
        ],
        "opening_balance": "1000.00",
        "closing_balance": "1500.00",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "warnings": []
      }
    }
  ]
}
```

## Run

Configure AWS credentials with `bedrock:InvokeModel` permission. The default inference profile is `us.amazon.nova-2-lite-v1:0` through `us-east-1`.

```powershell
backend\venv\Scripts\python.exe backend\scripts\ai_recovery_benchmark.py `
  --manifest C:\private\ai-recovery\manifest.json `
  --output C:\private\ai-recovery\report.json `
  --allow-bedrock-call
```

Optional token rates can add a cost estimate without hard-coding time-sensitive AWS pricing:

```powershell
  --input-cost-per-million-usd 0.00 `
  --output-cost-per-million-usd 0.00
```

Replace the example rates with the current contracted rates before running. Both rate arguments must be provided together.

The command exits with code `3` when extraction failures or critical false accepts are detected. A critical false accept means the deterministic validator approved an output that differs financially from the human-reviewed ground truth.
