from io import BytesIO

from pypdf import PdfReader, PdfWriter

from app.application import textract_header_ocr


def _two_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_textract_header_ocr_sends_only_first_page_and_reads_only_header(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class FakeGateway:
        def __init__(self, *, mode: str, timeout_seconds: float) -> None:
            observed["mode"] = mode
            observed["timeout_seconds"] = timeout_seconds

        def analyze_pdf(self, *, raw_bytes: bytes) -> dict[str, object]:
            observed["page_count"] = len(PdfReader(BytesIO(raw_bytes)).pages)
            return {
                "blocks": [
                    {"BlockType": "WORD", "Text": "BANCO", "Page": 1},
                    {"BlockType": "LINE", "Text": "CAIXA ECONÔMICA FEDERAL", "Page": 1,
                     "Geometry": {"BoundingBox": {"Top": 0.08, "Left": 0.1}}},
                    {"BlockType": "LINE", "Text": "BANCO DO BRASIL", "Page": 1,
                     "Geometry": {"BoundingBox": {"Top": 0.65, "Left": 0.1}}},
                    {"BlockType": "LINE", "Text": "SANTANDER", "Page": 2,
                     "Geometry": {"BoundingBox": {"Top": 0.1, "Left": 0.1}}},
                    {"BlockType": "LINE", "Text": "SEM COORDENADAS", "Page": 1},
                ]
            }

    monkeypatch.setattr(textract_header_ocr, "TextractGateway", FakeGateway)

    text = textract_header_ocr.extract_pdf_first_page_header_text_with_textract(_two_page_pdf())

    assert text == "CAIXA ECONÔMICA FEDERAL"
    assert observed == {"mode": "text", "timeout_seconds": 60.0, "page_count": 1}
