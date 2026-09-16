from app.application import textract_layout_preview


def test_textract_preview_keeps_line_boxes_and_page_order(monkeypatch) -> None:
    class FakeGateway:
        def __init__(self, *, mode):
            assert mode == "text"

        def analyze_pdf(self, *, raw_bytes):
            assert raw_bytes == b"sampled-pdf"
            return {
                "document_hash": "abc",
                "page_count": 2,
                "blocks": [
                    {
                        "BlockType": "LINE", "Page": 1, "Id": "a", "Text": "EXTRATO POR PERIODO",
                        "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.4, "Height": 0.03}},
                    },
                    {
                        "BlockType": "LINE", "Page": 2, "Id": "b", "Text": "DATA MOV VALOR",
                        "Geometry": {"BoundingBox": {"Left": 0.3, "Top": 0.4, "Width": 0.4, "Height": 0.03}},
                    },
                ],
            }

    monkeypatch.setattr(textract_layout_preview, "TextractGateway", FakeGateway)

    texts, lines = textract_layout_preview.extract_pdf_layout_preview_with_textract(b"sampled-pdf")

    assert texts == ("EXTRATO POR PERIODO", "DATA MOV VALOR")
    assert lines[0][0].bbox == {"left": 0.1, "top": 0.2, "width": 0.4, "height": 0.03}
    assert lines[1][0].page_number == 2
