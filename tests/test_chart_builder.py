"""Offline tests for chart specifications (chart_builder.py and the /chat `chart` field).
Results are hand-built QueryResults with explicit column types; no model or database is used.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.controllers import chat_controller
from backend.app.main import create_app
from backend.app.repositories.mysql_repository import QueryResult
from backend.app.services import chat_service as chat_service_module
from backend.app.services.chart_builder import build_chart
from backend.app.services.chat_service import ChatService
from backend.app.services.llm_service import GenerationStats, SQLGeneration
from backend.app.services.rag.context_builder import expected_schema_chunks

_no_db = mock.patch("mysql.connector.connect", side_effect=AssertionError("tests must never connect to MySQL"))


def temp_monitoring():
    """A throwaway monitoring store, so tests never write to storage/monitoring."""
    import tempfile
    from pathlib import Path as _Path

    from backend.app.repositories.monitoring_repository import MonitoringRepository
    return MonitoringRepository(_Path(tempfile.mkdtemp()) / "monitoring.sqlite3", 30, 1000)


def routes_as(label):
    """Fake resolver: label the message; a data question resolves to itself (no history used)."""
    from backend.app.services.llm_service import MessageRouting
    return lambda message, labels, history=(), **kwargs: MessageRouting(label, message if label == "data_question" else None, 0)


def setUpModule() -> None:
    _no_db.start()


def tearDownModule() -> None:
    _no_db.stop()


DOCTOR_ROWS = [["D001", "David", "Taylor", 25], ["D002", "Jane", "Davis", 21], ["D003", "Jane", "Smith", 22]]


def result(columns, types, rows, truncated=False, sql="SELECT 1") -> QueryResult:
    return QueryResult(sql, columns, rows, len(rows), truncated, 200, 0.001, types)


def doctors() -> QueryResult:
    return result(["doctor_id", "first_name", "last_name", "appointment_count"],
                  ["string", "string", "string", "integer"], [list(r) for r in DOCTOR_ROWS])


class SupportedCharts(unittest.TestCase):
    def test_bar_from_text_columns_and_a_count(self) -> None:
        chart = build_chart(doctors(), 50)
        self.assertEqual((chart.type, chart.point_count), ("bar", 3))
        self.assertEqual((chart.x.kind, chart.x.fields, chart.x.label),
                         ("category", ["doctor_id", "first_name", "last_name"], "doctor_id · first_name · last_name"))
        self.assertEqual((chart.y.kind, chart.y.fields, chart.y.label), ("number", ["appointment_count"], "appointment_count"))
        self.assertEqual([(p.x, p.y, p.y_exact, p.row_index) for p in chart.points],
                         [("D001 · David · Taylor", 25, 25, 0), ("D002 · Jane · Davis", 21, 21, 1), ("D003 · Jane · Smith", 22, 22, 2)])

    def test_bar_keeps_result_order(self) -> None:
        r = result(["status", "n"], ["string", "integer"], [["Scheduled", 5], ["Cancelled", 9], ["Completed", 1]])
        self.assertEqual([p.x for p in build_chart(r, 50).points], ["Scheduled", "Cancelled", "Completed"])

    def test_exact_decimals_are_kept_for_tooltips(self) -> None:
        r = result(["payment_method", "total"], ["string", "decimal"], [["Cash", "49366.09"], ["Insurance", "59206.47"]])
        rows_before = [list(x) for x in r.rows]
        chart = build_chart(r, 50)
        self.assertEqual([(p.y, p.y_exact) for p in chart.points], [(49366.09, "49366.09"), (59206.47, "59206.47")])
        self.assertIsInstance(chart.points[0].y_exact, str)
        self.assertEqual(r.rows, rows_before, "rows are never modified")
        r = result(["t", "avg_cost"], ["string", "decimal"], [["MRI", "3224.948889"], ["ECG", "2532.216842"]])
        self.assertEqual(build_chart(r, 50).points[0].y_exact, "3224.948889")

    def test_line_sorted_by_date_without_touching_rows(self) -> None:
        r = result(["bill_date", "total"], ["date", "decimal"],
                   [["2023-03-01", "10.50"], ["2023-01-15", "7.00"], ["2023-02-01", "3.25"]])
        chart = build_chart(r, 50)
        self.assertEqual((chart.type, chart.x.kind), ("line", "date"))
        self.assertEqual([(p.x, p.y_exact, p.row_index) for p in chart.points],
                         [("2023-01-15", "7.00", 1), ("2023-02-01", "3.25", 2), ("2023-03-01", "10.50", 0)])
        self.assertEqual(r.rows[0], ["2023-03-01", "10.50"])

    def test_datetime_line(self) -> None:
        r = result(["at", "n"], ["datetime", "integer"], [["2023-01-02T10:00:00", 2], ["2023-01-01T09:30:00", 1]])
        self.assertEqual([p.x for p in build_chart(r, 50).points], ["2023-01-01T09:30:00", "2023-01-02T10:00:00"])


class NoChart(unittest.TestCase):
    def assertNoChart(self, r) -> None:
        self.assertIsNone(build_chart(r, 50))

    def test_identifiers_and_numeric_looking_text_are_not_metrics(self) -> None:
        self.assertNoChart(result(["doctor_id", "phone_number"], ["string", "string"], [["D001", "8322010158"], ["D002", "9004382050"]]))
        self.assertNoChart(result(["name", "patient_id"], ["string", "integer"], [["a", 1], ["b", 2]]))
        self.assertNoChart(result(["month", "label"], ["string", "string"], [["2023-01", "4"], ["2023-02", "5"]]))

    def test_nulls_are_not_replaced_or_dropped(self) -> None:
        self.assertNoChart(result(["m", "total"], ["string", "decimal"], [["Cash", None], ["Insurance", "1.00"]]))
        self.assertNoChart(result(["m", "total"], ["string", "decimal"], [[None, "2.00"], ["Insurance", "1.00"]]))

    def test_empty_and_single_value_results(self) -> None:
        self.assertNoChart(result(["total_paid_amount"], ["decimal"], [["173424.90"]]))
        self.assertNoChart(result(["m", "n"], ["string", "integer"], [["Cash", 3]]))
        self.assertNoChart(result(["m", "n"], ["string", "integer"], []))

    def test_ambiguous_shapes(self) -> None:
        self.assertNoChart(result(["t", "cost", "count"], ["string", "decimal", "integer"], [["a", "1.0", 1], ["b", "2.0", 2]]))
        self.assertNoChart(result(["y", "n"], ["integer", "integer"], [[2023, 1], [2024, 2]]))  # YEAR() + COUNT
        self.assertNoChart(result(["d", "t", "n"], ["date", "string", "integer"], [["2023-01-01", "a", 1], ["2023-01-02", "b", 2]]))
        self.assertNoChart(result(["at", "n"], ["time", "integer"], [["08:00:00", 1], ["09:00:00", 2]]))
        self.assertNoChart(result(["m", "n"], ["string", "integer"], [["Cash", 1], ["Cash", 2]]))  # would need aggregation
        self.assertNoChart(result(["d", "n"], ["date", "integer"], [["2023-01-01", 1], ["2023-01-01", 2]]))
        self.assertNoChart(result(["m", "n"], ["other", "integer"], [["a", 1], ["b", 2]]))

    def test_missing_metadata(self) -> None:
        self.assertNoChart(QueryResult("q", ["m", "n"], [["a", 1], ["b", 2]], 2, False, 200, 0.0))

    def test_truncated_and_point_limit(self) -> None:
        rows = [[f"c{i}", i] for i in range(10)]
        self.assertNoChart(result(["c", "n"], ["string", "integer"], rows, truncated=True))
        r = result(["c", "n"], ["string", "integer"], rows)
        self.assertIsNone(build_chart(r, 9))
        self.assertEqual(build_chart(r, 10).point_count, 10)


class ChatIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.llm = mock.Mock()
        self.llm.resolve_message.side_effect = routes_as("data_question")
        self.llm.generate_sql.return_value = SQLGeneration("sql", "SELECT d.doctor_id FROM doctors AS d", "", False, "f",
                                                          GenerationStats(0, 0, 0, 0, 1, 1, "stop"))
        self.database = mock.Mock()
        self.database.execute.return_value = doctors()
        vectors = mock.Mock()
        vectors.get_chunks_by_type.return_value = expected_schema_chunks()
        vectors.search.return_value = []
        embeddings = mock.Mock(model_name="e")
        embeddings.embed_query.return_value = [0.1]
        self.app = create_app(open_vector_store=False, monitoring=temp_monitoring())
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.service = ChatService(vectors=vectors, embeddings=embeddings, llm=self.llm, database=self.database)
        self.app.dependency_overrides[chat_controller.get_chat_service] = lambda: self.service

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def ask(self, q="How many appointments does each doctor have?"):
        return self.client.post("/chat", json={"question": q})

    def test_answered_response_has_chart_and_unchanged_rows(self) -> None:
        body = self.ask().json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["rows"], DOCTOR_ROWS)
        self.assertEqual((body["chart"]["type"], body["chart"]["point_count"]), ("bar", 3))
        self.assertEqual([p["y_exact"] for p in body["chart"]["points"]], [r[3] for r in DOCTOR_ROWS])

    def test_single_value_answer_has_null_chart_and_exact_value(self) -> None:
        self.database.execute.return_value = result(["total_paid_amount"], ["decimal"], [["173424.90"]])
        body = self.ask("What is the total amount of paid bills?").json()
        self.assertEqual((body["status"], body["rows"], body["chart"]), ("answered", [["173424.90"]], None))

    def test_chart_failure_never_breaks_the_answer(self) -> None:
        with mock.patch.object(chat_service_module, "build_chart", side_effect=RuntimeError("boom")):
            r = self.ask()
        self.assertEqual((r.status_code, r.json()["status"], r.json()["chart"]), (200, "answered", None))
        self.assertEqual(r.json()["rows"], DOCTOR_ROWS)

    def test_non_answers_and_errors_have_null_chart(self) -> None:
        self.assertIsNone(self.ask("Hello").json()["chart"])
        self.llm.resolve_message.side_effect = routes_as("out_of_scope")
        self.assertIsNone(self.ask("weather?").json()["chart"])
        self.llm.resolve_message.side_effect = routes_as("data_question")
        self.llm.generate_sql.return_value = SQLGeneration("sql", "SELECT SLEEP(1)", "", False, "f", GenerationStats(0, 0, 0, 0, 1, 1, "stop"))
        body = self.ask().json()
        self.assertEqual((body["status"], body["chart"]), ("rejected", None))
        self.database.execute.assert_not_called()

    def test_chart_limit_comes_from_settings(self) -> None:
        self.service.settings = type(self.service.settings)(**{**self.service.settings.__dict__, "chart_max_points": 2})
        self.assertIsNone(self.ask().json()["chart"])


if __name__ == "__main__":
    unittest.main()
