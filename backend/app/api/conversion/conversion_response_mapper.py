from app.application import ConvertDocumentResult, ConvertDocumentStatus
from app.schemas import ConvertResponse


def _result_to_convert_response(result: ConvertDocumentResult) -> ConvertResponse:
    if result.status != ConvertDocumentStatus.COMPLETED or result.payload is None:
        raise RuntimeError(
            f"ConvertDocumentUseCase must return a completed result with payload for HTTP conversion. status={result.status}"
        )
    return ConvertResponse.model_validate(result.payload)
