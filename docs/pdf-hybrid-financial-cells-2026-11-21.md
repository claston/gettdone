# Financial values embedded as small images

## Failure and cause

A twelve-page statement contained readable native descriptions alongside dates,
protocols and amounts painted as small images. The old quality check looked for
page-sized scans, sparse text or damaged embedded OCR. Dense native text passed
that check, so conversion returned only 132 of 260 movements without requesting
OCR. A selectable text layer is not sufficient evidence of complete extraction.

The daily closing balances in this table appear **before** the corresponding
movements. The generic OCR reader also associated those balances with the wrong
rows. The corrected reader treats them as daily closing controls.

## Change

- Inspect painted images intersecting the monetary column, including continuation
  pages. A financial row with an image and no native monetary value requires OCR.
  Logos, unused image resources and readable native values retain the native path.
- Apply the same evidence to preflight and representation classification, retaining
  the existing OCR page limits. Classifier version: `pdf_representation_v8`.
- Share one coordinate-based reader between native and OCR protocol tables. Keep
  full dates, signs, protocols, legitimate repeated movements and source pages.
- Associate each daily control with the last movement of that day, even when the
  control and movements are on different pages. Surface mismatches for review.
- Do not accept partial native results when OCR is unavailable or over the limit.
  Unreadable financial cells in a later block cannot disappear silently.

## Evidence

The private source and authorized OCR responses remain local and are not fixtures
in this repository. Cached OCR was replayed without additional service calls.
The result contains 260 movements, including the 128 previously omitted, and 20
daily closing controls. All 19 transitions between independently printed daily
balances reconcile. The opening balance is derived, not printed: the result
retains the review warning and `financial_controls_validated=0`.

Synthetic regression coverage includes small images on later pages without a
repeated header, negative controls for logos/native values/unused images, native
and OCR readers, multiple months, controls across page breaks, repeated movements,
repeated daily headers, unreadable later cells, financial mismatch review,
source provenance, preflight and real HTTP Free/PRO exports.

The complete private source was exercised through both HTTP paths with an explicit
**local diagnostic allowance of twelve OCR pages**. This does not change production
quotas. Default Free identity permits ten OCR pages; the seeded paid plan permits
six, subject also to the environment ceiling. Those limits can prevent conversion
of the original twelve-page file. A separate HTTP regression verifies rejection
before OCR is called, rather than returning incomplete exports.

## Compatibility and rollback

No database migration, API shape, plan limit or existing stored analysis changes.
Already generated partial results are not repaired automatically. Reprocess the
original after deployment when its OCR allowance permits the complete document.
Rollback by reverting this change; that restores the risk of incomplete native
conversion for this class of mixed PDF. Monitor classification, OCR-limit errors,
conversion counts and financial review warnings after deployment.
