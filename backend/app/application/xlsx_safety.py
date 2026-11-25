"""Shared resource limits before any XLSX reader expands an untrusted archive."""
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries

from app.application.errors import InvalidFileContentError

MAX_XLSX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_XLSX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_XLSX_MEMBERS = 2048
MAX_XLSX_ROWS = 50_000
MAX_XLSX_COLUMNS = 256
MAX_XLSX_CELLS = 500_000
MAX_XLSX_CELL_CHARACTERS = 32_767
MAX_XLSX_TEXT_CHARACTERS = 8 * 1024 * 1024
MAX_XLSX_FORMAT_CHARACTERS = 256
MAX_XLSX_SECONDS = 15
LIMIT_MESSAGE = "A planilha excede o limite seguro de processamento. Exporte os lançamentos em CSV e tente novamente."


def check_xlsx_deadline(deadline):
    if time.monotonic() > deadline:
        raise InvalidFileContentError(LIMIT_MESSAGE)


def _check_range(reference):
    try:
        _, _, column, row = range_boundaries(reference)
    except (TypeError, ValueError):
        raise InvalidFileContentError("A planilha contém uma referência de célula inválida.") from None
    if (not column or not row or column > MAX_XLSX_COLUMNS or row > MAX_XLSX_ROWS
            or column * row > MAX_XLSX_CELLS):
        raise InvalidFileContentError(LIMIT_MESSAGE)
    return column, row


def validate_xlsx_archive(raw_bytes, deadline):
    try:
        with ZipFile(BytesIO(raw_bytes)) as archive:
            entries = archive.infolist()
            if (len(entries) > MAX_XLSX_MEMBERS or len({entry.filename for entry in entries}) != len(entries)
                    or sum(entry.file_size for entry in entries) > MAX_XLSX_EXPANDED_BYTES
                    or any(entry.file_size > MAX_XLSX_MEMBER_BYTES for entry in entries)):
                raise InvalidFileContentError(LIMIT_MESSAGE)
            for entry in entries:
                check_xlsx_deadline(deadline)
                with archive.open(entry) as stream:
                    raw = stream.read(MAX_XLSX_MEMBER_BYTES + 1)
                if len(raw) > MAX_XLSX_MEMBER_BYTES:
                    raise InvalidFileContentError(LIMIT_MESSAGE)
                probe = raw.replace(b"\x00", b"").upper()
                # Relationships may point to XML parts with arbitrary suffixes.
                # Include UTF-8/16/32 BOMs when sniffing; binary media is ignored.
                is_xml = probe.lstrip(b"\xef\xbb\xbf\xff\xfe \t\r\n").startswith(b"<")
                if not is_xml and not entry.filename.lower().endswith((".xml", ".rels")):
                    continue
                if b"<!DOCTYPE" in probe or b"<!ENTITY" in probe:
                    raise InvalidFileContentError("A planilha contém declarações XML não permitidas.")
                count = 0
                max_row = max_column = 0
                row_number = 0
                # Worksheet parts can have nonstandard ZIP paths. Inspect XML
                # content instead of trusting the archive's member names.
                for _, element in ET.iterparse(BytesIO(raw), events=("end",)):
                    count += 1
                    if count > 1_000_000:
                        raise InvalidFileContentError(LIMIT_MESSAGE)
                    check_xlsx_deadline(deadline)
                    tag = element.tag.rsplit("}", 1)[-1]
                    if tag == "numFmt" and len(element.get("formatCode", "")) > MAX_XLSX_FORMAT_CHARACTERS:
                        raise InvalidFileContentError(LIMIT_MESSAGE)
                    if tag in {"dimension", "c"} and (reference := element.get("ref" if tag == "dimension" else "r")):
                        column, row = _check_range(reference)
                        max_column, max_row = max(max_column, column), max(max_row, row)
                        if max_column * max_row > MAX_XLSX_CELLS:
                            raise InvalidFileContentError(LIMIT_MESSAGE)
                    if tag == "row":
                        number = element.get("r", str(row_number + 1))
                        if not number.isdigit() or not 1 <= int(number) <= MAX_XLSX_ROWS:
                            raise InvalidFileContentError(LIMIT_MESSAGE)
                        row_number = int(number)
                        columns = sum(child.tag.rsplit("}", 1)[-1] == "c" for child in element)
                        max_column, max_row = max(max_column, columns), max(max_row, row_number)
                        if max_column > MAX_XLSX_COLUMNS or max_column * max_row > MAX_XLSX_CELLS:
                            raise InvalidFileContentError(LIMIT_MESSAGE)
                    element.clear()
    except (BadZipFile, ET.ParseError, OSError, RuntimeError, ValueError) as exc:
        raise InvalidFileContentError("Não foi possível ler a planilha XLSX.") from exc


@contextmanager
def open_bounded_xlsx(raw_bytes):
    deadline = time.monotonic() + MAX_XLSX_SECONDS
    validate_xlsx_archive(raw_bytes, deadline)
    try:
        workbook = load_workbook(BytesIO(raw_bytes), data_only=True, read_only=True, keep_links=False)
    except Exception as exc:
        raise InvalidFileContentError("Não foi possível ler a planilha XLSX.") from exc
    try:
        if not workbook.worksheets:
            raise InvalidFileContentError("A planilha não contém abas para leitura.")
        sheet = workbook.worksheets[0]
        # Cell coordinates are checked independently of the optional dimension.
        if sheet.max_row and sheet.max_column:
            if (sheet.max_row > MAX_XLSX_ROWS or sheet.max_column > MAX_XLSX_COLUMNS
                    or sheet.max_row * sheet.max_column > MAX_XLSX_CELLS):
                raise InvalidFileContentError(LIMIT_MESSAGE)
        # A forged or stale small dimension must not hide later movements.
        sheet.reset_dimensions()
        check_xlsx_deadline(deadline)
        yield workbook, deadline
    finally:
        workbook.close()


def iter_bounded_xlsx_rows(workbook, deadline):
    """Bound decoded shared strings as well as the physical ZIP representation."""
    characters = 0
    for row in workbook.worksheets[0].iter_rows():
        check_xlsx_deadline(deadline)
        for cell in row:
            value = cell.value
            if isinstance(value, str):
                if len(value) > MAX_XLSX_CELL_CHARACTERS:
                    raise InvalidFileContentError(LIMIT_MESSAGE)
                characters += len(value)
            if len(cell.number_format or "") > MAX_XLSX_FORMAT_CHARACTERS:
                raise InvalidFileContentError(LIMIT_MESSAGE)
        if characters > MAX_XLSX_TEXT_CHARACTERS:
            raise InvalidFileContentError(LIMIT_MESSAGE)
        yield row
