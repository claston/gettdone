# PDF recovery audit — 21 October 2026, afternoon

The audit covered 20 additional recovery captures, representing 17 different
PDFs, through 14:54 Brasília time. These are recovery captures, not a sample of
all production conversions or an application failure-rate measurement. The
morning audit is handled separately in PR #466.

| Document family | Distinct PDFs | Correct behavior |
| --- | ---: | --- |
| Unicred monthly statements | 3 | Preserve credits whose card-arrangement history contains `DEBITO`; prefer the printed balance change over history keywords when the amount has no explicit sign. |
| Inter Windows printouts | 2 | Read integer and one-decimal transaction amounts separately from the balance, identifiers, and annotations; verify the running ledger. |
| Cresol descending export | 1 | Preserve all 10 movements, including page continuations; reverse the complete physical ledger and validate daily controls and cash closing. |
| Cresol Colmeia consolidated statements | 2 | Exclude diagonal watermarks from native text; read the daily C/D ledger, including page continuations; exclude limits and future installments. |
| Alelo corporate prepaid card | 1 | Preserve all 22 signed movements, including amounts formatted as `1,000,00`; exclude a current balance outside the selected period. |
| BTG investment-account cash ledger | 1 | Determine debit/credit direction from the printed opening and successive balances; verify both printed totals and closing. |
| BTG credit-card invoice | 1 | Extract nine purchases and one payment; use the printed BRL conversion for foreign purchases; verify card subtotals and invoice totals. |
| C6 inactive monthly statements | 3 | Return zero transactions for an explicit no-activity statement with zero inflow/outflow summaries; do not turn the export-date current balance into historical balances. |
| XP descending digital statement | 1 | Accept a shortened/spaced time component while requiring both complete monetary fields and every running balance. |
| Banco do Brasil cash ledger | 1 | Recognize compact native column headings; stop at the additional-information section after the verified final cash balance, before future placeholders and investment positions. |
| Notary protest-cancellation receipt | 1 | Reject as `notary_receipt` with a specific Portuguese explanation before OCR or bank-statement parsing. |

## Financial guarantees and limits

Recognized controlled exports do not fall through to generic parsing after a
missing monetary field or inconsistent printed control. Amounts and controls
are compared with `Decimal`. Candidate dates must belong to the statement
period. Unknown layouts keep the existing parser path.

The Inter opening is derived from its first printed balance and movement. The
Alelo document prints no opening/closing for the selected period; its balances
remain unknown and the parser explicitly reports financial validation as
unavailable. No missing balances, transactions, or foreign-exchange values are
synthesized.

Document classification distinguishes the Alelo prepaid card from a credit-card
invoice. A Cresol cash statement mentioning a card payment remains a bank
statement. The two new classification identifiers are additive metadata values;
there is no schema migration or change to previously stored analyses.

## Validation and privacy

Regression fixtures use fictional holders, accounts, descriptions, and amounts.
They cover positive cases, contradictory controls, malformed amounts, year
rollover, two-column foreign purchases, page continuations, diagonal watermarks,
and scaled pages. Real HTTP tests exercise `/convert`, `/report/{analysis_id}`,
CSV/XLSX/OFX downloads, rejected input, and the asynchronous worker. The legacy
`POST /analyze` remains disabled with HTTP 405, matching production.

Original PDFs, retained Textract evidence, independent arithmetic, and detailed
replay output stay in private local temporary storage. No new OCR calls or
production writes are required to replay the audit. Expected final disposition:
18 successful captures and two specific rejections of the same notary receipt.

## Rollout and rollback

Deploy the API and worker from the same revision. Monitor financial-validation
rejections, no-activity counts, document types, and ledger-warning counts for
these layouts. Existing stored results are not recomputed automatically. Rollback
is a code revert and redeploy of both components; no data rollback is needed.
