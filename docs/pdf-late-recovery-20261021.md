# Late recovery captures — 21 October 2026

Six recovery captures represent four distinct PDFs requiring further corrections
after the afternoon audit. A fifth new PDF, a Cresol export, is tracked in #467.
This change starts from `main` and handles the four remaining document families.

| Document | Verified result | Controls |
| --- | --- | --- |
| Viacredi Alto Vale special statement, seven pages | 362 movements | Opening from `SALDO ANTERIOR`, explicit C/D amounts, negative balance suffixes, all 42 printed running balances, sequential pages and final notice. Credit limits and prospective charges are context. |
| Unicred credit-card invoice, six pages | 26 movements | Physical native-text rows restore the negative bonus/payment objects emitted later in the PDF stream. Summary credits/debits, previous invoice, invoice total, holder subtotals and page counters must agree. |
| Caixa Gerenciador, two pages | Six movements | Original retained OCR geometry is interpreted in its printed orientation; posting dates, amount magnitudes, successive balances and daily controls must agree. Effective-date timestamps and daily balance rows remain context. |
| Banco do Brasil, two pages | 29 movements | `00/00/0000` is accepted only on a printed daily balance control. Dated movements, split TED rows across pages, reversals, opening and final cash balance are preserved and checked. Additional-information balances do not enter the ledger. |

The [Ailos FAQ](https://www.ailos.coop.br/perguntas) confirms code 085 for its
cooperatives, including Viacredi and Viacredi Alto Vale. The existing bank catalog
gains that identity and its aliases, without changing stored analyses.

## Validation contract

Recognized layouts require complete monetary fields and consistent printed
controls, compared with `Decimal`. They cannot fall through to generic parsing
after financial validation fails. No source date is invented for a zero-date
control. Unknown layouts retain the existing parsing path. Invoice finance
charges without a supported transaction association are rejected instead of
receiving invented dates.

Regression fixtures use fictional accounts, people and amounts. Native tests
cover page loss, repeated page numbers, negative suffixes, split rows, late PDF
objects, tracking, year rollover and page scaling. OCR tests exercise normal and
quarter-turned coordinates, contradictory/missing fields, invalid dates and both
Textract text/table modes. Real local HTTP servers verify conversion, report
retrieval, CSV/XLSX/OFX exports, negative paths, CSV compatibility and the worker.
The production legacy `POST /analyze` contract remains HTTP 405; happy paths use
`POST /convert`.

Original PDFs, detailed arithmetic and replay results remain in private local
temporary storage. Retained Textract evidence is reused; there are no new OCR
calls or writes to production. The replay of all six affected captures confirms
their expected counts, totals, cash/card balances and zero balance warnings.

## Rollout and rollback

There is no schema migration, backfill or automatic reprocessing. Deploy API and
worker from the same revision. Monitor the new layout identifiers, bank identity,
financial-validation rejections and row/balance warnings. Rollback is a code
revert and redeploy; existing stored analyses are unchanged.
