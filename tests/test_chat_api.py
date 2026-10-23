"""Offline tests for POST /chat. The real ChatService, controller, gate and validator are used;
the index, embedding, LLM and database are fakes. mysql.connector.connect is disabled.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import threading
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.controllers import chat_controller
from backend.app.main import create_app
from backend.app.repositories.mysql_repository import (
    DatabaseConnectionError, QueryExecutionError, QueryResult, QueryTimeoutError,
)
from backend.app.repositories.vector_repository import IndexNotFoundError
from backend.app.services.chat_service import ChatService
from backend.app.services.inference_gate import InferenceGate
from backend.app.services.llm_service import GenerationStats, IncompleteGenerationError, LLMError, SQLGeneration
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


PAID_SQL = "SELECT SUM(amount) AS total_paid_amount FROM billing WHERE payment_status = 'Paid';"
STATS = GenerationStats(0.01, 0.9, 0.3, 1.2, 1726, 20, "stop")


class FakeVectors:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def get_chunks_by_type(self, chunk_type: str):
        if self.error:
            raise self.error
        return expected_schema_chunks()

    def search(self, vector, top_k, embedding_model, types=None):
        return []


class FakeEmbeddings:
    model_name = "fake-embed"

    def embed_query(self, question: str) -> list[float]:
        return [0.1, 0.2]


def generation(kind: str, text: str) -> SQLGeneration:
    return SQLGeneration(kind, text, text, False, "fake-llm", STATS)


class ChatApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.llm = mock.Mock()
        self.llm.resolve_message.side_effect = routes_as("data_question")
        self.llm.generate_sql.return_value = generation("sql", PAID_SQL)
        self.database = mock.Mock()
        self.database.execute.return_value = QueryResult(PAID_SQL, ["total_paid_amount"], [["173424.90"]], 1, False, 200, 0.0007)
        self.vectors = FakeVectors()
        self.app = create_app(open_vector_store=False, monitoring=temp_monitoring())
        self.client = TestClient(self.app)
        self.client.__enter__()  # runs the lifespan (gate only; no vector store)
        self.app.dependency_overrides[chat_controller.get_chat_service] = lambda: ChatService(
            vectors=self.vectors, embeddings=FakeEmbeddings(), llm=self.llm, database=self.database)

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def post(self, body):
        return self.client.post("/chat", json=body)


class RequestValidation(ChatApiTestCase):
    def test_bad_requests_are_422_and_do_nothing(self) -> None:
        for body in [{"question": ""}, {"question": "   \n "}, {"question": "x" * 501}, {}, {"question": 42}, {"q": "hi"}]:
            with self.subTest(body=str(body)[:40]):
                r = self.post(body)
                self.assertEqual(r.status_code, 422)
                self.assertEqual(r.json()["status"], "invalid_request")
                self.assertTrue(r.json()["message"].startswith("Invalid request"))
        self.llm.generate_sql.assert_not_called()
        self.database.execute.assert_not_called()

    def test_malformed_json(self) -> None:
        r = self.client.post("/chat", content=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual((r.status_code, r.json()["status"]), (422, "invalid_request"))

    def test_question_is_trimmed_and_limit_is_inclusive(self) -> None:
        count = "SELECT COUNT(*) AS n FROM billing;"  # no filter value the question would have to name
        self.llm.generate_sql.return_value = generation("sql", count)
        r = self.post({"question": "  " + "x" * 500 + "  "})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.llm.generate_sql.call_args.args[0], "x" * 500)


class SuccessfulAnswer(ChatApiTestCase):
    def test_answered_response_contract(self) -> None:
        r = self.post({"question": "What is the total amount of paid bills?"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["sql"], PAID_SQL)
        self.assertTrue(body["sql_executed"])
        self.assertEqual((body["columns"], body["rows"]), (["total_paid_amount"], [["173424.90"]]))
        self.assertIsInstance(body["rows"][0][0], str, "Decimal values must stay exact strings")
        self.assertEqual((body["row_count"], body["truncated"]), (1, False))
        self.assertEqual(set(body["timings_ms"]), {"routing_ms", "context_ms", "llm_ms", "validation_ms", "database_ms", "correction_ms", "total_ms"})
        self.assertTrue(all(isinstance(v, (int, float)) for k, v in body["timings_ms"].items() if k != "correction_ms"))
        self.assertIsNone(body["timings_ms"]["correction_ms"], "no correction ran")
        self.assertEqual((body["attempt_count"], body["correction_attempted"], body["sql_reached_database"]), (1, False, True))
        self.assertGreaterEqual(body["timings_ms"]["total_ms"], body["timings_ms"]["routing_ms"])
        self.assertEqual(body["source_chunk_ids"][:5], [c.id for c in expected_schema_chunks()])
        self.database.execute.assert_called_once_with(PAID_SQL)

    def test_truncated_result_is_reported(self) -> None:
        self.database.execute.return_value = QueryResult("q", ["appointment_id"], [["A001"]] * 200, 200, True, 200, 0.01)
        body = self.post({"question": "List the paid bills"}).json()
        self.assertEqual((body["status"], body["row_count"], body["truncated"]), ("answered", 200, True))
        self.assertIn("first 200 rows", body["message"])


class NonAnswersNeverExecute(ChatApiTestCase):
    def test_cannot_answer_and_needs_clarification(self) -> None:
        for kind, text in [("cannot_answer", "No weather data."), ("needs_clarification", "Which year?")]:
            with self.subTest(kind):
                self.llm.generate_sql.return_value = generation(kind, text)
                r = self.post({"question": "Total of paid bills?"})
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertEqual((body["status"], body["message"], body["sql"], body["sql_executed"]), (kind, text, None, False))
        self.database.execute.assert_not_called()

    def test_rejected_sql_is_returned_but_not_executed(self) -> None:
        for sql, code in [("SELECT SLEEP(5)", "dangerous_function"), ("DELETE FROM billing", "not_select"),
                          ("SELECT 1 /*! , SLEEP(5) */", "executable_comment")]:
            with self.subTest(sql):
                self.llm.generate_sql.return_value = generation("sql", sql)
                r = self.post({"question": "Total of paid bills?"})
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertEqual((body["status"], body["code"], body["sql"], body["sql_executed"]), ("rejected", code, sql, False))
                self.assertIn("not executed", body["message"])
        self.database.execute.assert_not_called()


class OperationalErrors(ChatApiTestCase):
    SECRET = "password=hunter2 host=10.0.0.5"

    def check(self, expected_http: int, expected_status: str, expected_code: str | None) -> dict:
        r = self.post({"question": "What is the total amount of paid bills?"})
        self.assertEqual(r.status_code, expected_http, r.text)
        body = r.json()
        self.assertEqual((body["status"], body["code"]), (expected_status, expected_code))
        for leaked in ("hunter2", "10.0.0.5", "Traceback", "Error:"):
            self.assertNotIn(leaked, r.text)
        return body

    def test_llm_failures(self) -> None:
        cases = [(LLMError(self.SECRET, "unavailable"), 503, "unavailable"), (LLMError(self.SECRET, "http"), 503, "unavailable"),
                 (LLMError(self.SECRET, "timeout"), 504, "timeout"), (IncompleteGenerationError(self.SECRET), 200, "generation_failed")]
        for error, http, status in cases:
            with self.subTest(status=status, kind=getattr(error, "kind", None)):
                self.llm.generate_sql.side_effect = error
                self.check(http, status, "llm")
        self.database.execute.assert_not_called()

    def test_database_failures(self) -> None:
        for error, http, status in [(DatabaseConnectionError(self.SECRET), 503, "unavailable"),
                                    (QueryTimeoutError(self.SECRET), 504, "timeout"),
                                    (QueryExecutionError(self.SECRET), 200, "query_failed")]:
            with self.subTest(status=status):
                self.database.execute.side_effect = error
                body = self.check(http, status, "database")
                self.assertFalse(body["sql_executed"])

    def test_index_failure(self) -> None:
        self.vectors.error = IndexNotFoundError(self.SECRET)
        self.check(503, "unavailable", "index")
        self.llm.generate_sql.assert_not_called()

    def test_unexpected_exception_is_a_generic_500(self) -> None:
        self.llm.generate_sql.side_effect = RuntimeError(self.SECRET)
        body = self.check(500, "error", None)
        self.assertEqual(body["message"], "An internal error occurred.")

    def test_no_index_opened_at_startup(self) -> None:
        self.app.dependency_overrides[chat_controller.get_chat_service] = lambda: ChatService(
            vectors=None, embeddings=FakeEmbeddings(), llm=self.llm, database=self.database)
        self.check(503, "unavailable", "index")
        self.llm.generate_sql.assert_not_called()


class BusyHandling(ChatApiTestCase):
    def test_full_gate_returns_503_busy_with_retry_after(self) -> None:
        gate: InferenceGate = self.app.state.gate
        held = [gate.try_reserve() for _ in range(gate.capacity)]
        try:
            r = self.post({"question": "Total of paid bills?"})
            self.assertEqual((r.status_code, r.json()["status"]), (503, "busy"))
            self.assertEqual(r.headers["Retry-After"], "5")
            self.llm.generate_sql.assert_not_called()
        finally:
            for reservation in held:
                reservation.abandon()
        self.assertEqual(self.post({"question": "Total of paid bills?"}).status_code, 200)
        self.assertEqual(gate.pending, 0, "slots must be released after each request")

    def test_concurrent_request_gets_busy_while_one_runs(self) -> None:
        self.app.state.gate = InferenceGate(max_waiting=0)
        started, release = threading.Event(), threading.Event()

        def slow(question, context, calendar=""):
            started.set()
            release.wait(5)
            return generation("sql", PAID_SQL)

        self.llm.generate_sql.side_effect = slow
        first: dict = {}
        worker = threading.Thread(target=lambda: first.update(r=self.post({"question": "First: total of paid bills?"})))
        worker.start()
        self.assertTrue(started.wait(5))
        second = self.post({"question": "Second: total of paid bills?"})
        release.set()
        worker.join(5)
        self.assertEqual((second.status_code, second.json()["status"]), (503, "busy"))
        self.assertEqual(first["r"].status_code, 200)
        self.assertEqual(self.llm.generate_sql.call_count, 1)


class GateUnit(unittest.TestCase):
    def test_runs_one_at_a_time(self) -> None:
        gate, active, peak = InferenceGate(max_waiting=3), [0], [0]
        lock = threading.Lock()

        def work():
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            with lock:
                active[0] -= 1

        reservations = [gate.try_reserve() for _ in range(4)]
        self.assertIsNone(gate.try_reserve(), "capacity is 1 running + 3 waiting")
        threads = [threading.Thread(target=r.run, args=(work,)) for r in reservations]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual((peak[0], gate.pending), (1, 0))

    def test_abandon_before_start_and_errors_release_slots(self) -> None:
        gate = InferenceGate(max_waiting=0)
        r = gate.try_reserve()
        r.abandon()
        r.abandon()
        self.assertEqual(gate.pending, 0)
        r = gate.try_reserve()
        with self.assertRaises(ZeroDivisionError):
            r.run(lambda: 1 / 0)
        self.assertEqual(gate.pending, 0)


class HealthAndCors(ChatApiTestCase):
    def test_health_is_lightweight(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.llm.generate_sql.assert_not_called()

    def test_no_cors_headers_by_default(self) -> None:
        r = self.client.options("/chat", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in r.headers})

    def test_wildcard_origin_is_refused(self) -> None:
        from backend.app import config
        with mock.patch.dict("os.environ", {"CORS_ALLOWED_ORIGINS": "*"}):
            config.get_settings.cache_clear()
            try:
                with self.assertRaises(ValueError):
                    config.get_settings()
            finally:
                config.get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
