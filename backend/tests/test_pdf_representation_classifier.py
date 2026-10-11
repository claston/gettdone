from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.application.pdf_representation_classifier import (
    CLASSIFIER_VERSION,
    classify_pdf_representation,
)


def _write_pdf(writer: PdfWriter) -> bytes:
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _native_text_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Courier"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 10 Tf 40 800 Td (Extrato bancario com texto selecionavel e movimentacoes) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Producer": "Bank PDF Export"})
    return _write_pdf(writer)


def _vector_outline_pdf(*, producer: str = "Microsoft: Print To PDF") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    commands = []
    for index in range(180):
        offset = index % 100
        commands.append(f"{offset} {offset} m {offset + 2} {offset} l {offset + 1} {offset + 4} l h f")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Producer": producer})
    return _write_pdf(writer)


def _full_page_raster_pdf(*, producer: str = "Microsoft: Print To PDF") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    image = DecodedStreamObject()
    image.set_data(b"\xff" * (595 * 842))
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(595),
            NameObject("/Height"): NumberObject(842),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image_ref = writer._add_object(image)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject({NameObject("/PageImage"): image_ref}),
            NameObject("/ProcSet"): ArrayObject([NameObject("/PDF"), NameObject("/ImageB")]),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"q 595 0 0 842 0 0 cm /PageImage Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Producer": producer})
    return _write_pdf(writer)


def test_classifies_pdf_with_extractable_text_as_native_text() -> None:
    result = classify_pdf_representation(_native_text_pdf())

    assert result.representation == "native_text"
    assert result.creation_method == "direct_export"
    assert result.confidence == 0.99
    assert result.classifier_version == CLASSIFIER_VERSION
    assert "native_text_present" in result.evidence


def test_classifies_microsoft_print_pdf_with_dense_paths_as_vector_outlines() -> None:
    result = classify_pdf_representation(_vector_outline_pdf())

    assert result.representation == "vector_outlines"
    assert result.creation_method == "virtual_print"
    assert result.confidence == 0.99
    assert result.requires_visual_extraction is True
    assert result.evidence == (
        "native_text_absent",
        "font_resources_absent",
        "dense_vector_paths",
        "full_page_raster_absent",
        "producer_microsoft_print_to_pdf",
    )


def test_classifies_full_page_image_as_raster_even_when_pdf_was_virtually_printed() -> None:
    result = classify_pdf_representation(_full_page_raster_pdf())

    assert result.representation == "raster_images"
    assert result.creation_method == "virtual_print"
    assert result.confidence == 0.97
    assert result.requires_visual_extraction is True
    assert "full_page_raster_present" in result.evidence
    assert "dense_vector_paths" not in result.evidence


def test_classifies_blank_pdf_without_structural_evidence_as_unknown() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)

    result = classify_pdf_representation(_write_pdf(writer))

    assert result.representation == "unknown_no_text"
    assert result.creation_method == "unknown"
    assert result.confidence == 0.2
    assert result.requires_visual_extraction is False


def test_returns_unknown_instead_of_failing_for_invalid_pdf() -> None:
    result = classify_pdf_representation(b"not a pdf")

    assert result.representation == "unknown_no_text"
    assert result.creation_method == "unknown"
    assert result.confidence == 0.0
    assert result.evidence == ("pdf_structure_unreadable",)
