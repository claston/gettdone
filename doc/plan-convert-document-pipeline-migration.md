# Convert Document Pipeline Migration Plan

## Goal

Move the document-conversion entrypoint toward a worker-ready application pipeline where:

- `ConvertDocumentUseCase` is the case-use entrypoint
- document preflight and quota validation are explicit services
- the current `ConversionPipeline` remains the document-processing engine
- `ConversionService` and `AnalyzeService` can be reduced and eventually removed

## Current state

- `upload.py` still owns upload staging plus part of PDF preflight concerns.
- `ConversionService` still owns most conversion orchestration:
  - identity resolution
  - upload/page validation
  - quota checks
  - processing attempt telemetry
  - call into `AnalyzeService`
  - owner persistence and success/failure persistence
- `AnalyzeService` still:
  - ingests uploaded bytes
  - calls `ConversionPipeline`
  - saves analysis artifacts
  - assembles `AnalyzeResponse`
- `ConversionPipeline` already contains the right processing stages for:
  - parsing
  - classification
  - normalization
  - reconciliation
  - analysis-data assembly

## Target layering

### Application orchestration

- `ConvertDocumentUseCase`
- `DocumentConversionPipeline` (future)
- `DocumentPreflightService`
- `QuotaValidatorService` (future)
- `ConversionAttemptService` or equivalent tracking facade (future)

### Document-processing engine

- existing `ConversionPipeline`

### Adapters / persistence

- `ReportService`
- storage/repository adapters
- access-control adapter usage until a narrower identity/quota interface is extracted

## Phases

### Phase 1

Extract `DocumentPreflightService` and move document preflight logic out of router/service helpers into a dedicated application service while preserving current behavior.

Scope:

- create `DocumentPreflightService`
- move PDF scan-likely detection there
- move page/upload/ocr-limit resolution there
- make current conversion flow delegate preflight decisions to this service
- keep `ConversionService` as the main orchestrator for now

### Phase 2

Extract quota orchestration from `ConversionService`.

Scope:

- create `QuotaValidatorService`
- centralize:
  - `ensure_quota_available`
  - `consume_quota`
  - consumed-units calculation
- keep persistence/telemetry in the existing orchestrator for now

Status:

- implemented in current branch
- `ConversionService` now delegates conversion quota checks/consumption to `QuotaValidatorService`
- quota telemetry/persistence still remains in the current orchestrator for the next cut

### Phase 3

Introduce an application-level conversion pipeline that calls the current processing pipeline directly.

Scope:

- create `DocumentConversionPipeline`
- call:
  - identity resolution
  - document preflight
  - quota validation
  - current `ConversionPipeline`
  - analysis persistence
  - report-owner persistence
  - conversion tracking
- switch `ConvertDocumentUseCase` to call `DocumentConversionPipeline`
- keep `ConversionService` as a compatibility facade temporarily

### Phase 4

Reduce and retire older façades.

Scope:

- shrink `AnalyzeService` to `/analyze`-specific response assembly only
- move conversion persistence/response assembly into dedicated application objects
- remove `ConversionService`
- optionally introduce:
  - `ConvertDocumentCommand`
  - worker handoff payloads / async execution boundary

## Phase 1 acceptance

- there is a dedicated `DocumentPreflightService`
- router/service helper logic delegates to it
- upload/page-limit behavior remains unchanged
- the code is ready for future reuse from a formal application pipeline
