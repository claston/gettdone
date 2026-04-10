# AGENTS.md

## Working Agreement

This document defines the default execution rules for the `gettdone` repository.

## Branching Rules

1. Always update local `main` from `origin/main` before starting a new task.
2. Always create a new branch for any code or documentation change.
3. Branches should stay focused on one objective.

## Development Rules

1. Prefer TDD for implementation work:
   - write or update tests first
   - run tests and confirm failure when appropriate
   - implement the change
   - run tests again and confirm success
2. Keep refactors incremental and verifiable.
3. Use Conventional Commits for every commit.
4. Always run Python tooling through project virtual environment (`venv\Scripts\python.exe`) when available.
5. Keep V1 architecture aligned with product constraints:
   - single-page flow
   - no login/dashboard in MVP
   - upload -> processing -> preview -> report download

## Validation Rules

1. Always run test suite before finalizing work.
2. Always run lint checks when configured.
3. For API changes, run the app and validate endpoints with real HTTP requests.

Minimum API validation for V1:

- one `POST /analyze` happy path with supported file (`CSV`, `XLSX`, or `OFX`)
- one `GET /report/{analysis_id}` happy path
- one negative-path request (`invalid file`, `unsupported format`, or `expired/nonexistent analysis_id`)

When reconciliation logic changes, include at least one validation case for:

- internal transfer match
- reversal/storno match
- possible duplicate grouping

## Delivery Rules

1. Push the branch after tests pass.
2. Open Pull Request using `.github/pull_request_template.md`.
3. PR body sections are mandatory and must be completed with concrete data:
   - `## Summary`
   - `## Why`
   - `## Type of change`
   - `## How to test`
   - `## Data / storage impact`
   - `## Checklist`
   - `## Risks and rollback`
4. If a PR is opened outside template standard, edit the existing PR before finalizing.
5. Prefer opening/editing PRs with:
   - `gh pr create --body-file .github/pull_request_template.md`
   - `gh pr edit <number> --body-file <filled_body_file>`
6. Include command outputs in `How to test` whenever possible.
7. Do not merge directly into `main`.
