# Native split-sheet and Banco da Amazônia recovery

This change addresses the three new PDF structures found in the October 21
evening recovery audit. It is based on production `512b138`; the BB zero-date
statement already covered by PR #468 remains in that separate PR.

## Behavior

| Structure | Before | After |
| --- | --- | --- |
| BB spreadsheet printed horizontally across eight pages | Unsupported table layout | Pair the four date/history pages with their four value/type pages by physical row position. Validate signed amounts against Entrada/Saída and every printed balance. |
| Banco da Amazônia HTML export | Misidentified as BB; duplicate text and icon signs confused amount and balance columns; 273 output rows with 272 balance warnings | Deduplicate overlapping text objects, read the explicit plus/minus icon, preserve real repeated transactions, exclude balance controls, and use the existing Banco da Amazônia catalog record (`003`). |
| Payment ledger spreadsheet printed across four pages | Unsupported table layout | Read the original text objects before narrow cells overlap in plain text. Keep amount and balance separate from invoice numbers, pair credit/debit pages, and validate every running balance. |

The payment spreadsheet does not identify an institution. Its bank stays
unspecified; an OFX identity can be supplied through the existing export options.
Scientific-notation transaction identifiers remain as printed in source evidence;
the parser does not fabricate their missing digits.

The native route recognizes only these explicit structures. Missing column pages,
misaligned rows, unmatched monetary rows, invalid dates, sign/type conflicts and
inconsistent balances raise `PdfFinancialValidationError`, without retrying OCR.
Physical page provenance is retained for both halves of a transaction.

Bank identity uses the existing catalog. The bank confirms its COMPE code on its
[official website](https://www.bancoamazonia.com.br/o-banco/seguranca).

## Private-document replay

Customer documents and extracted rows remain in private local audit storage.
Committed fixtures contain invented records only. No production state was changed
and no OCR service was called.

| Document | Transactions | Printed controls | Balance validation |
| --- | ---: | ---: | --- |
| BB spreadsheet | 146 | 18 | All controls match |
| Banco da Amazônia | 271 | 21 | All controls match |
| Payment spreadsheet | 53 | 55 | All controls match |

All three have complete row coverage, zero canonical warnings and zero balance
failures. Two repeat captures reproduce the same results. The preliminary BB
audit counted the spaced `S A L D O` final control as a zero-value transaction;
the correct movement count is 146, with 18 balance controls.

## Validation

The 37 new regression tests cover original PDF text-object ordering, shuffled
objects, scaling, overlapping duplicate draws, signs, daily balances, horizontal
page pairing, missing/misaligned pages, unmatched amounts and statement identity.
Seven of these start an actual HTTP server and exercise `/convert`, `/report`,
CSV/XLSX/OFX export, invalid printed controls, and a missing report.

`POST /analyze` currently returns 405; supported upload validation uses
`POST /convert`. The HTTP checks also include a CSV upload and confirm that the
historical `/analyze` route remains disabled.

The final full suite passed: **2436 passed, 1 skipped, 2 xfailed**
(286.67 seconds). Ruff and frontend text/navigation lint passed.
The first full run hit a transient existing SSE quota-test capacity rejection;
that file passed in the focused repeat and the final full suite.

## Compatibility and rollback

There are no database, API schema, dependency or storage changes. API and worker
processing share the PDF parser. Existing reports remain readable. Reverting the
commit restores the previous routing; existing stored conversions require no
migration. The main risk is accepting another superficially similar spreadsheet;
explicit structure, geometry, row coverage, signs and printed arithmetic constrain
this route. Unsupported variants continue to fail closed.
