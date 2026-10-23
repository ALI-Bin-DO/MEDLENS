"""CSV export of a client-supplied table (POST /export/csv).

The table is untrusted client data: it is validated and converted, never checked against MySQL,
never stored, and never re-queried. No SQL, model, index or database is involved.

CSV format (Python's csv module, UTF-8 with BOM, CRLF line endings):
    - text cells and column names are quoted; JSON numbers are written unquoted exactly as sent
      (1.10 stays 1.10); true/false become "true"/"false"
    - NULL (JSON null) -> empty unquoted field; empty string -> ""  (in a one-column table a NULL
      row is also written as "", because a bare empty line would be dropped by CSV readers)
    - formula-injection protection: a text cell or column name whose first visible character is
      = + - @ (or the full-width ＝ ＋ － ＠), or that starts with a tab or carriage return, gets a
      leading apostrophe ('), so spreadsheets show it as text. Leading whitespace and invisible
      control/format characters are skipped when finding the first visible character. Text that
      is exactly a plain number ("-12.50", "173424.90") or a MySQL TIME ("-01:30:00") is left
      unchanged, as are real numbers.
"""

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from backend.app.config import Settings

BOM = chr(0xFEFF)  # UTF-8 byte order mark, for Excel
_DANGEROUS_FIRST = frozenset("=+-@") | {chr(0xFF1D), chr(0xFF0B), chr(0xFF0D), chr(0xFF20)}  # + full-width
_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_PLAIN_TIME = re.compile(r"-?\d{2,3}:\d{2}:\d{2}(?:\.\d{1,6})?")  # MySQL TIME as serialized by MedLens


class ExportRequestError(ValueError):
    """The payload is invalid. status_code is 422 or 413; the message is safe to show."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ExportTable:
    columns: list[str]
    rows: list[list]
    truncated: bool


@dataclass(frozen=True)
class CsvExport:
    content: bytes
    row_count: int
    neutralized_cells: int  # text cells / column names that got a protective apostrophe


def parse_export_request(body: bytes, settings: Settings) -> ExportTable:
    """Decode and validate the JSON body. JSON numbers are kept exact (as Decimal)."""
    if len(body) > settings.export_max_bytes:
        raise ExportRequestError(f"Request body is larger than {settings.export_max_bytes} bytes.", 413)
    try:
        payload = json.loads(body.decode("utf-8"), parse_float=Decimal, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ExportRequestError("Request body must be valid UTF-8 JSON with finite numbers.") from None

    if not isinstance(payload, dict) or set(payload) != {"columns", "rows", "truncated"}:
        raise ExportRequestError('Body must be an object with exactly "columns", "rows" and "truncated".')
    columns, rows, truncated = payload["columns"], payload["rows"], payload["truncated"]
    if not isinstance(truncated, bool):
        raise ExportRequestError('"truncated" must be true or false.')
    if not isinstance(columns, list) or not columns:
        raise ExportRequestError('"columns" must be a non-empty list of strings.')
    if len(columns) > settings.export_max_columns:
        raise ExportRequestError(f'"columns" has more than {settings.export_max_columns} entries.', 413)
    for i, name in enumerate(columns):
        if not isinstance(name, str):
            raise ExportRequestError(f"Column {i} must be a string.")
        if len(name) > settings.export_max_cell_chars:
            raise ExportRequestError(f"Column {i} name is longer than {settings.export_max_cell_chars} characters.", 413)
    if not isinstance(rows, list):
        raise ExportRequestError('"rows" must be a list of rows.')
    if len(rows) > settings.export_max_rows:
        raise ExportRequestError(f'"rows" has more than {settings.export_max_rows} rows.', 413)
    for r, row in enumerate(rows):
        if not isinstance(row, list):
            raise ExportRequestError(f"Row {r} must be a list.")
        if len(row) != len(columns):
            raise ExportRequestError(f"Row {r} has {len(row)} cells; expected {len(columns)}.")
        for c, cell in enumerate(row):
            if isinstance(cell, Decimal) and not cell.is_finite():
                raise ExportRequestError(f"Row {r}, column {c} is not a finite number.")
            if not (cell is None or isinstance(cell, (str, int, Decimal))):  # bool is an int subclass
                raise ExportRequestError(f"Row {r}, column {c} must be a string, number, boolean or null.")
            if isinstance(cell, str) and len(cell) > settings.export_max_cell_chars:
                raise ExportRequestError(f"Row {r}, column {c} is longer than {settings.export_max_cell_chars} characters.", 413)
    return ExportTable(list(columns), rows, truncated)


def render_csv(table: ExportTable) -> CsvExport:
    """Write the table as CSV bytes (UTF-8 with BOM), applying the documented transformations."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_STRINGS)
    neutralized = 0

    def text(value: str) -> str:
        nonlocal neutralized
        safe = neutralize(value)
        neutralized += safe != value
        return safe

    writer.writerow([text(name) for name in table.columns])
    for row in table.rows:
        out = [_cell(value, text) for value in row]
        if len(out) == 1 and out[0] is None:
            out = [""]  # csv cannot write a lone empty field; see module docstring
        writer.writerow(out)
    return CsvExport((BOM + buffer.getvalue()).encode("utf-8"), len(table.rows), neutralized)


def neutralize(value: str) -> str:
    """Prefix an apostrophe if a spreadsheet could treat the text as a formula."""
    if _PLAIN_NUMBER.fullmatch(value) or _PLAIN_TIME.fullmatch(value):
        return value
    if value[:1] in ("\t", "\r"):
        return "'" + value
    visible = next((ch for ch in value if not (ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf"))), "")
    return "'" + value if visible in _DANGEROUS_FIRST else value


def _cell(value, text):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        return value  # written unquoted, exactly as received
    return text(value)


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name}")
