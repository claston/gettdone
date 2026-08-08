from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NullObject

_PAGE_WIDTH = 595
_PAGE_HEIGHT = 842
_LEFT_MARGIN = 48
_TOP_POSITION = 790
_FONT_SIZE = 10
_LINE_HEIGHT = 14


def generate_native_text_pdf(pages: tuple[tuple[str, ...], ...]) -> bytes:
    writer = PdfWriter()
    font_reference = writer._add_object(  # noqa: SLF001 - low-level PDF fixture generation
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    for lines in pages:
        page = writer.add_blank_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference}),
            }
        )
        content = _build_page_content(lines)
        stream = DecodedStreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = writer._add_object(stream)  # noqa: SLF001

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def generate_null_font_pdf() -> bytes:
    """Build a privacy-safe PDF reproducing a font resource resolved as null."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): NullObject()}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 10 Tf 48 790 Td (synthetic statement) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)  # noqa: SLF001

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _build_page_content(lines: tuple[str, ...]) -> bytes:
    commands = [
        "BT",
        f"/F1 {_FONT_SIZE} Tf",
        f"{_LEFT_MARGIN} {_TOP_POSITION} Td",
        f"{_LINE_HEIGHT} TL",
    ]
    for line in lines:
        commands.append(f"({_escape_pdf_text(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    return "\n".join(commands).encode("cp1252", errors="replace")


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
