"""Offline tests for POST /export/csv (csv_export.py, export_controller.py).
Bodies are raw JSON text so number spelling (1.10) is tested exactly. MySQL, Ollama (urllib),
Qdrant and the chat pipeline are made to fail if touched.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import csv
import dataclasses
import io
import json
import re
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.controllers import export_controller
from backend.app.main import create_app
from backend.app.services.csv_export import neutralize

FORBIDDEN = [
    mock.patch("mysql.connector.connect", side_effect=AssertionError("export must not connect to MySQL")),
    mock.patch("urllib.request.urlopen", side_effect=AssertionError("export must not call Ollama")),
    mock.patch("qdrant_client.QdrantClient.__init__", side_effect=AssertionError("export must not open Qdrant")),
    mock.patch("backend.app.services.chat_service.ChatService.answer", side_effect=AssertionError("export must not re-run questions")),
]
BOM = chr(0xFEFF)


def temp_monitoring():
    """A throwaway monitoring store, so tests never write to storage/monitoring."""
    import tempfile
    from pathlib import Path as _Path

    from backend.app.repositories.monitoring_repository import MonitoringRepository
    return MonitoringRepository(_Path(tempfile.mkdtemp()) / "monitoring.sqlite3", 30, 1000)


def setUpModule() -> None:
    for patcher in FORBIDDEN:
        patcher.start()


def tearDownModule() -> None:
    for patcher in FORBIDDEN:
        patcher.stop()


class ExportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(open_vector_store=False, monitoring=temp_monitoring()))

    def post(self, body: str | bytes, content_type: str = "application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        return self.client.post("/export/csv", content=data, headers={"Content-Type": content_type})

    def csv_text(self, body: str) -> str:
        r = self.post(body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.content.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM")
        return r.content.decode("utf-8")[len(BOM):]

    def assertRejected(self, body, status_code: int, status: str, content_type: str = "application/json") -> dict:
        r = self.post(body, content_type)
        self.assertEqual(r.status_code, status_code, r.text)
        self.assertEqual(r.json()["status"], status)
        self.assertNotIn("Traceback", r.text)
        return r.json()


class ContentPreservation(ExportTestCase):
    def test_exact_decimals_numbers_and_duplicate_headers(self) -> None:
        text = self.csv_text('{"columns": ["amount", "amount", "n", "neg"], '
                             '"rows": [["173424.90", 1.10, 25, -0.010], ["2663.415600", 1e3, -7, -12.50]], "truncated": false}')
        self.assertEqual(text, '"amount","amount","n","neg"\r\n'
                               '"173424.90",1.10,25,-0.010\r\n'
                               '"2663.415600",1E+3,-7,-12.50\r\n')

    def test_arabic_commas_quotes_newlines_null_and_empty(self) -> None:
        body = json.dumps({"columns": ["الاسم", "note", "null", "empty"],
                           "rows": [["مرحبا، عالم", 'a, "quoted"\nline 2', None, ""]], "truncated": False}, ensure_ascii=False)
        text = self.csv_text(body)
        self.assertEqual(text, '"الاسم","note","null","empty"\r\n"مرحبا، عالم","a, ""quoted""\nline 2",,""\r\n')
        parsed = list(csv.reader(io.StringIO(text)))
        self.assertEqual(parsed[1], ["مرحبا، عالم", 'a, "quoted"\nline 2', "", ""])

    def test_dates_times_and_row_order(self) -> None:
        text = self.csv_text('{"columns": ["d", "t", "x"], "rows": [["2023-08-09", "15:15:00", "b"], '
                             '["2023-01-01T09:30:00", "-01:30:00", "a"]], "truncated": false}')
        self.assertEqual(text.splitlines()[1:], ['"2023-08-09","15:15:00","b"', '"2023-01-01T09:30:00","-01:30:00","a"'])

    def test_single_column_null_row_and_booleans(self) -> None:
        self.assertEqual(self.csv_text('{"columns": ["x"], "rows": [[null], ["a"]], "truncated": false}'), '"x"\r\n""\r\n"a"\r\n')
        self.assertEqual(self.csv_text('{"columns": ["b"], "rows": [[true], [false]], "truncated": false}'),
                         '"b"\r\n"true"\r\n"false"\r\n')

    def test_empty_rows_give_header_only(self) -> None:
        self.assertEqual(self.csv_text('{"columns": ["a", "b"], "rows": [], "truncated": false}'), '"a","b"\r\n')


class FormulaInjection(ExportTestCase):
    def test_dangerous_text_is_prefixed(self) -> None:
        zero_width_space, no_break_space = chr(0x200B), chr(0x00A0)
        fullwidth_equals, fullwidth_plus = chr(0xFF1D), chr(0xFF0B)
        for value in ["=1+1", "+1+1", "-2+3", "@SUM(A1)", " =cmd", "\t=x", "\r=x", "\n=x", zero_width_space + "=x",
                      "\x00=x", no_break_space + "-x", fullwidth_equals + "1+1", fullwidth_plus + "1", "\tplain",
                      "\rplain", "-", "=HYPERLINK(\"http://x\")", "-01:30:00+1"]:
            with self.subTest(repr(value)):
                self.assertEqual(neutralize(value), "'" + value)

    def test_safe_text_and_plain_numbers_are_unchanged(self) -> None:
        for value in ["-12.50", "173424.90", "+5", "-0.010", "1e5", "a=b", "Paid", "email@x.com", "", " ", "مرحبا", "2023-01-01",
                      "-01:30:00", "-838:59:59", "15:15:00", "-00:00:01.000500", "2023-01-01T09:30:00"]:
            with self.subTest(repr(value)):
                self.assertEqual(neutralize(value), value)

    def test_headers_and_cells_in_the_file(self) -> None:
        r = self.post('{"columns": ["=cmd|\' /C calc\'!A0", "amount"], '
                      '"rows": [["@x", -5], ["-12.50", -3.5], ["  +1+1", 0]], "truncated": false}')
        self.assertEqual(r.status_code, 200)
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8")[1:])))
        self.assertEqual(rows, [["'=cmd|' /C calc'!A0", "amount"], ["'@x", "-5"], ["-12.50", "-3.5"], ["'  +1+1", "0"]])
        self.assertEqual(r.headers["X-MedLens-Neutralized-Cells"], "3")
        self.assertIn(",-5\r\n", r.content.decode("utf-8"), "real negative numbers stay unquoted")


class Validation(ExportTestCase):
    def test_shape_errors(self) -> None:
        cases = [
            '{"columns": ["a", "b"], "rows": [["x"]], "truncated": false}',
            '{"columns": ["a"], "rows": [["x", "y"]], "truncated": false}',
            '{"columns": ["a"], "rows": [[{"k": 1}]], "truncated": false}',
            '{"columns": ["a"], "rows": [[["nested"]]], "truncated": false}',
            '{"columns": ["a"], "rows": [[NaN]], "truncated": false}',
            '{"columns": ["a"], "rows": [[Infinity]], "truncated": false}',
            '{"columns": ["a"], "rows": [[-Infinity]], "truncated": false}',
            '{"columns": [], "rows": [], "truncated": false}',
            '{"columns": [1], "rows": [], "truncated": false}',
            '{"columns": ["a"], "rows": "x", "truncated": false}',
            '{"columns": ["a"], "rows": [], "truncated": "true"}',
            '{"columns": ["a"], "rows": []}',
            '{"columns": ["a"], "rows": [], "truncated": false, "sql": "DROP TABLE billing"}',
            '{"columns": ["a"], "rows": [], "truncated": false, "filename": "../../.env"}',
            '[["a"]]', "not json", "",
        ]
        for body in cases:
            with self.subTest(body[:60]):
                self.assertRejected(body, 422, "invalid_request")

    def test_empty_columns_message(self) -> None:
        body = self.assertRejected('{"columns": [], "rows": [], "truncated": false}', 422, "invalid_request")
        self.assertEqual(body["message"], '"columns" must be a non-empty list of strings.')

    def test_wrong_content_type_and_bad_utf8(self) -> None:
        self.assertRejected('{"columns": ["a"], "rows": [], "truncated": false}', 422, "invalid_request", "text/plain")
        self.assertRejected(b'{"columns": ["\xff"], "rows": [], "truncated": false}', 422, "invalid_request")

    def test_row_width_message_has_no_cell_content(self) -> None:
        body = self.assertRejected('{"columns": ["a", "b"], "rows": [["SECRET-VALUE"]], "truncated": false}', 422, "invalid_request")
        self.assertEqual(body["message"], "Row 0 has 1 cells; expected 2.")


class Limits(ExportTestCase):
    def limited(self, **limits):
        settings = dataclasses.replace(get_settings(), **limits)
        return mock.patch.object(export_controller, "get_settings", return_value=settings)

    def test_body_size_limit(self) -> None:
        with self.limited(export_max_bytes=100):
            self.assertRejected('{"columns": ["a"], "rows": [["' + "x" * 200 + '"]], "truncated": false}', 413, "too_large")

    def test_size_limit_while_streaming_without_content_length(self) -> None:
        """Drive the ASGI app directly: TestClient buffers the whole body, a real server streams it."""
        import asyncio

        app = create_app(open_vector_store=False, monitoring=temp_monitoring())
        received, sent = [0], []

        async def receive():
            received[0] += 1
            return {"type": "http.request", "body": b"x" * 100, "more_body": received[0] < 1000}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/export/csv", "raw_path": b"/export/csv", "query_string": b"", "root_path": "",
                 "headers": [(b"content-type", b"application/json")], "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000)}
        with self.limited(export_max_bytes=1000):
            asyncio.run(app(scope, receive, send))
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        self.assertEqual((start["status"], json.loads(body)["status"]), (413, "too_large"))
        self.assertLessEqual(received[0], 11, "stopped after ~1000 bytes instead of reading all 100000")

    def test_declared_content_length_over_limit(self) -> None:
        with self.limited(export_max_bytes=10):
            self.assertRejected('{"columns": ["a"], "rows": [], "truncated": false}', 413, "too_large")

    def test_column_row_and_cell_limits(self) -> None:
        with self.limited(export_max_columns=2, export_max_rows=2, export_max_cell_chars=5):
            self.assertRejected('{"columns": ["a", "b", "c"], "rows": [], "truncated": false}', 413, "too_large")
            self.assertRejected('{"columns": ["a"], "rows": [["1"], ["2"], ["3"]], "truncated": false}', 413, "too_large")
            self.assertRejected('{"columns": ["a"], "rows": [["123456"]], "truncated": false}', 413, "too_large")
            self.assertRejected('{"columns": ["abcdef"], "rows": [], "truncated": false}', 413, "too_large")
            self.assertEqual(self.post('{"columns": ["a", "b"], "rows": [["12345", 1], ["x", 2]], "truncated": false}').status_code, 200)


class DownloadResponse(ExportTestCase):
    NAME = re.compile(r'attachment; filename="medlens-export-\d{8}-\d{6}(-partial)?\.csv"')

    def test_complete_result_headers(self) -> None:
        r = self.post('{"columns": ["a"], "rows": [["x"], ["y"]], "truncated": false}')
        self.assertEqual(r.headers["content-type"], "text/csv; charset=utf-8")
        self.assertRegex(r.headers["content-disposition"], self.NAME)
        self.assertNotIn("-partial", r.headers["content-disposition"])
        self.assertEqual((r.headers["X-MedLens-Partial"], r.headers["X-MedLens-Row-Count"]), ("false", "2"))
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_partial_result_is_signalled_without_changing_the_table(self) -> None:
        r = self.post('{"columns": ["a", "b"], "rows": [["x", 1], ["y", 2]], "truncated": true}')
        self.assertRegex(r.headers["content-disposition"], self.NAME)
        self.assertIn("-partial.csv", r.headers["content-disposition"])
        self.assertEqual(r.headers["X-MedLens-Partial"], "true")
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8")[1:])))
        self.assertEqual(rows, [["a", "b"], ["x", "1"], ["y", "2"]], "no warning rows added")

    def test_partial_header_is_exposed_to_browsers_when_cors_is_on(self) -> None:
        settings = dataclasses.replace(get_settings(), cors_allowed_origins=("http://127.0.0.1:5173",))
        client = TestClient(create_app(settings, open_vector_store=False, monitoring=temp_monitoring()))
        r = client.post("/export/csv", content=b'{"columns": ["a"], "rows": [], "truncated": true}',
                        headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:5173"})
        exposed = r.headers.get("access-control-expose-headers", "")
        self.assertIn("X-MedLens-Partial", exposed)
        self.assertIn("Content-Disposition", exposed)


if __name__ == "__main__":
    unittest.main()
