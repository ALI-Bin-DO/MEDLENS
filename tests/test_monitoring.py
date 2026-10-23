"""Offline tests for request monitoring (middleware, monitoring_repository, monitoring_service, /admin).
Model, index and database are fakes; SQLite files are temporary; mysql.connector.connect is disabled.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.controllers import chat_controller
from backend.app.main import create_app
from backend.app.repositories import monitoring_repository as repo_module
from backend.app.repositories.monitoring_repository import MonitoringEvent, MonitoringRepository
from backend.app.repositories.mysql_repository import QueryResult, QueryTimeoutError
from backend.app.services import monitoring_service as ms
from backend.app.services.chat_service import ChatService
from backend.app.services.llm_service import GenerationStats, SQLGeneration
from backend.app.services.rag.context_builder import expected_schema_chunks

_no_db = mock.patch("mysql.connector.connect", side_effect=AssertionError("tests must never connect to MySQL"))
SECRET = "SECRET-7731"
COUNT_SQL = f"SELECT COUNT(*) AS n FROM patients WHERE first_name <> '{SECRET}-Q';"
STATS = GenerationStats(0, 0, 0, 0, 1, 1, "stop")


def routes_as(label):
    """Fake resolver: label the message; a data question resolves to itself (no history used)."""
    from backend.app.services.llm_service import MessageRouting
    return lambda message, labels, history=(), **kwargs: MessageRouting(label, message if label == "data_question" else None, 0)


def setUpModule() -> None:
    _no_db.start()


def tearDownModule() -> None:
    _no_db.stop()


def store(**kwargs) -> MonitoringRepository:
    return MonitoringRepository(Path(tempfile.mkdtemp()) / "monitoring.sqlite3",
                                kwargs.get("retention_days", 30), kwargs.get("max_events", 1000))


def event(n: int, *, ago: float = 0.0, epoch: float | None = None, data_pipeline=True, status="answered",
          total_ms=100.0, correction=False, now: float | None = None) -> MonitoringEvent:
    now = time.time() if now is None else now
    created = epoch if epoch is not None else now - ago
    return MonitoringEvent(
        request_id=f"req-{n}", timestamp=f"t{n}", created_epoch=created, route="data_question" if data_pipeline else "greeting",
        route_source="classifier", data_pipeline=data_pipeline, http_status=200, status=status, code=None,
        total_ms=total_ms, routing_ms=None, context_ms=None, llm_ms=None, validation_ms=None, database_ms=None,
        correction_ms=50.0 if correction else None, attempt_count=2 if correction else 1, correction_attempted=correction,
        sql_reached_database=True, sql_executed=status == "answered", row_count=1, truncated=False)


class ApiHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.llm = mock.Mock()
        self.llm.resolve_message.side_effect = routes_as("data_question")
        self.llm.generate_sql.return_value = SQLGeneration("sql", COUNT_SQL, COUNT_SQL, False, "f", STATS)
        self.database = mock.Mock()
        self.database.execute.return_value = QueryResult(COUNT_SQL, ["n"], [[f"{SECRET}-ROW"]], 1, False, 200, 0.001, ["string"])
        vectors = mock.Mock()
        vectors.get_chunks_by_type.return_value = expected_schema_chunks()
        vectors.search.return_value = []
        embeddings = mock.Mock(model_name="e")
        embeddings.embed_query.return_value = [0.1]
        self.monitoring = store()
        self.app = create_app(open_vector_store=False, monitoring=self.monitoring)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.service = ChatService(vectors=vectors, embeddings=embeddings, llm=self.llm, database=self.database)
        self.app.dependency_overrides[chat_controller.get_chat_service] = lambda: self.service

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def ask(self, question):
        return self.client.post("/chat", json={"question": question} if question is not None else {})

    def events(self) -> list[MonitoringEvent]:
        return self.monitoring.events_since(0)


class OneEventPerRequest(ApiHarness):
    def test_representative_outcomes(self) -> None:
        sleep_sql = SQLGeneration("sql", "SELECT SLEEP(1)", "", False, "f", STATS)
        steps = [
            ("greeting", lambda: self.ask("Hello"), 200, "conversation", False),
            ("answered", lambda: self.ask(f"How many patients? {SECRET}-Q"), 200, "answered", True),
            ("clarification", lambda: (setattr(self.llm.resolve_message, "side_effect", routes_as("needs_clarification")),
                                       self.ask("How many of them?"))[1], 200, "needs_clarification", False),
            ("rejected", lambda: (setattr(self.llm.resolve_message, "side_effect", routes_as("data_question")),
                                  setattr(self.llm.generate_sql, "return_value", sleep_sql),
                                  self.ask(f"q {SECRET}-Q"))[2], 200, "rejected", True),
            ("timeout", lambda: (setattr(self.llm.generate_sql, "return_value",
                                         SQLGeneration("sql", COUNT_SQL, COUNT_SQL, False, "f", STATS)),
                                 setattr(self.database.execute, "side_effect", QueryTimeoutError("limit")),
                                 self.ask(f"q {SECRET}-Q"))[2], 504, "timeout", True),
            ("invalid", lambda: self.ask("   "), 422, "invalid_request", False),
            ("malformed", lambda: self.client.post("/chat", content=b"{nope", headers={"Content-Type": "application/json"}),
             422, "invalid_request", False),
            ("unexpected", lambda: (setattr(self.llm.resolve_message, "side_effect", RuntimeError(SECRET)),
                                    self.ask(f"q {SECRET}-Q"))[1], 500, "error", False),
        ]
        for n, (label, call, http, status, pipeline) in enumerate(steps, start=1):
            with self.subTest(label):
                r = call()
                self.assertEqual(r.status_code, http)
                events = self.events()
                self.assertEqual(len(events), n, "exactly one new event per request")
                newest = events[0]
                self.assertEqual((newest.http_status, newest.status, newest.data_pipeline), (http, status, pipeline))
                self.assertEqual(newest.request_id, r.headers["X-Request-ID"])
                self.assertGreater(newest.total_ms, 0)
        self.llm.resolve_message.side_effect = routes_as("data_question")
        held = [self.app.state.gate.try_reserve() for _ in range(self.app.state.gate.capacity)]
        try:
            self.assertEqual(self.ask(f"q {SECRET}-Q").status_code, 503)
        finally:
            for reservation in held:
                reservation.abandon()
        busy = self.events()[0]
        self.assertEqual((busy.status, busy.data_pipeline, busy.route), ("busy", False, None))
        self.assertEqual(len(self.events()), len(steps) + 1)

    def test_other_endpoints_are_not_recorded(self) -> None:
        self.client.get("/health")
        self.client.get("/catalog/tables")
        self.client.get("/admin/metrics")
        self.client.post("/export/csv", json={"columns": ["a"], "rows": [], "truncated": False})
        self.assertEqual(self.events(), [])

    def test_stage_timings_and_flags_come_from_the_response(self) -> None:
        body = self.ask(f"How many patients? {SECRET}-Q").json()
        e = self.events()[0]
        t = body["timings_ms"]
        self.assertEqual((e.routing_ms, e.llm_ms, e.database_ms, e.correction_ms),
                         (t["routing_ms"], t["llm_ms"], t["database_ms"], t["correction_ms"]))
        self.assertGreaterEqual(e.total_ms, t["total_ms"], "middleware total covers the whole request")
        self.assertEqual((e.attempt_count, e.sql_reached_database, e.sql_executed, e.row_count, e.truncated),
                         (1, True, True, 1, False))


class Metrics(unittest.TestCase):
    def test_empty_window(self) -> None:
        m = ms.metrics(store(), "24h")
        self.assertEqual((m.total_requests, m.data_pipeline_requests), (0, 0))
        self.assertEqual((m.operational_answer_success.numerator, m.operational_answer_success.denominator,
                          m.operational_answer_success.value), (0, 0, None))
        self.assertEqual((m.data_pipeline_latency.sample_count, m.data_pipeline_latency.median_ms,
                          m.data_pipeline_latency.p95_ms), (0, None, None))
        self.assertEqual(set(m.by_status), set(ms.KNOWN_STATUSES))

    def test_denominators_rates_and_counts(self) -> None:
        s, now = store(), time.time()
        for n, kw in enumerate([dict(status="answered"), dict(status="answered", correction=True),
                                dict(status="query_failed", correction=True), dict(status="rejected"),
                                dict(status="timeout"), dict(status="conversation", data_pipeline=False),
                                dict(status="busy", data_pipeline=False), dict(status="invalid_request", data_pipeline=False),
                                dict(status="timeout", data_pipeline=False)]):
            s.record(event(n, now=now, **kw))
        m = ms.metrics(s, "24h", now=now + 1)
        self.assertEqual(m.total_requests, 9)
        self.assertEqual(m.data_pipeline_requests, 5, "greeting, busy, invalid and a pre-pipeline timeout excluded")
        self.assertEqual((m.operational_answer_success.numerator, m.operational_answer_success.denominator), (2, 5))
        self.assertAlmostEqual(m.operational_answer_success.value, 0.4)
        self.assertEqual((m.validation_rejections, m.correction_attempts, m.answered_after_correction), (1, 2, 1))
        self.assertEqual((m.busy, m.timeouts), (1, 2))
        self.assertEqual(m.by_status["answered"], 2)

    def test_nearest_rank_percentiles_use_only_pipeline_requests(self) -> None:
        s, now = store(), time.time()
        for n, ms_value in enumerate([30, 10, 100, 50, 20, 90, 40, 80, 70, 60]):
            s.record(event(n, now=now, total_ms=float(ms_value), status="answered" if n % 2 else "query_failed"))
        s.record(event(99, now=now, total_ms=99999.0, data_pipeline=False, status="conversation"))
        lat = ms.metrics(s, "1h", now=now + 1).data_pipeline_latency
        self.assertEqual((lat.sample_count, lat.median_ms, lat.p95_ms), (10, 50.0, 100.0))
        self.assertEqual(ms.nearest_rank([7.0], 0.5), 7.0)
        self.assertEqual(ms.nearest_rank([1.0, 2.0, 3.0, 4.0], 0.5), 2.0)
        self.assertEqual(ms.nearest_rank([float(i) for i in range(1, 21)], 0.95), 19.0)

    def test_windows(self) -> None:
        s, now = store(), time.time()
        s.record(event(1, now=now, ago=30 * 60))
        s.record(event(2, now=now, ago=5 * 3600))
        s.record(event(3, now=now, ago=3 * 86400))
        s.record(event(4, now=now, ago=8 * 86400))
        self.assertEqual([ms.metrics(s, w, now=now).total_requests for w in ("1h", "24h", "7d")], [1, 2, 3])


class Pagination(ApiHarness):
    def test_newest_first_with_stable_tie_breaker(self) -> None:
        now = time.time()
        for n in range(7):
            self.monitoring.record(event(n, epoch=now - 10))  # identical timestamps
        seen = []
        for offset in (0, 3, 6):
            body = self.client.get(f"/admin/activity?window=1h&limit=3&offset={offset}").json()
            self.assertEqual(body["total"], 7)
            seen += [e["request_id"] for e in body["events"]]
        self.assertEqual(seen, [f"req-{n}" for n in range(6, -1, -1)], "no gaps, no duplicates, insertion order as tie-breaker")

    def test_bounds_and_windows_are_validated(self) -> None:
        for query in ("limit=0", "limit=101", "offset=-1", "offset=10001", "window=2h", "window=30d"):
            with self.subTest(query):
                self.assertEqual(self.client.get(f"/admin/activity?{query}").status_code, 422)
        self.assertEqual(self.client.get("/admin/metrics?window=12h").status_code, 422)
        body = self.client.get("/admin/activity").json()
        self.assertEqual((body["window"], body["limit"], body["offset"], body["total"], body["events"]), ("24h", 50, 0, 0, []))


class RetentionAndStorage(unittest.TestCase):
    def test_age_and_count_retention(self) -> None:
        now = time.time()
        s = store(retention_days=1, max_events=3)
        for n, ago in enumerate([2 * 86400, 10, 9, 8, 7]):
            s.record(event(n, now=now, ago=ago))
        self.assertEqual(s.prune(now), 2)
        self.assertEqual([e.request_id for e in s.events_since(0)], ["req-4", "req-3", "req-2"])

    def test_automatic_cleanup_every_n_inserts(self) -> None:
        s = store(max_events=2)
        with mock.patch.object(repo_module, "PRUNE_EVERY", 5):
            for n in range(5):
                s.record(event(n, ago=10 - n))
        self.assertEqual(len(s.events_since(0)), 2)

    def test_persistence_across_reopening(self) -> None:
        s = store()
        s.record(event(1))
        reopened = MonitoringRepository(s.path, 30, 1000)
        self.assertEqual([e.request_id for e in reopened.events_since(0)], ["req-1"])
        self.assertTrue(reopened.events_since(0)[0].data_pipeline)

    def test_concurrent_writes(self) -> None:
        s = store(max_events=10000)
        errors = []

        def writer(t):
            try:
                for i in range(25):
                    s.record(event(t * 1000 + i))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        ids = [e.request_id for e in s.events_since(0)]
        self.assertEqual((len(ids), len(set(ids))), (200, 200))


class FailuresAndPrivacy(ApiHarness):
    def test_monitoring_failure_leaves_chat_unchanged(self) -> None:
        expected = self.ask(f"How many patients? {SECRET}-Q").json()
        with mock.patch.object(MonitoringRepository, "record", side_effect=OSError("disk full")):
            r = self.ask(f"How many patients? {SECRET}-Q")
        self.assertEqual((r.status_code, r.json()["status"], r.json()["rows"]), (200, "answered", expected["rows"]))

    def test_unwritable_store_does_not_break_chat(self) -> None:
        blocker = Path(tempfile.mkdtemp()) / "not-a-dir"
        blocker.write_text("x")
        app = create_app(open_vector_store=False, monitoring=MonitoringRepository(blocker / "m.sqlite3", 30, 10))
        with TestClient(app) as client:
            self.assertEqual(client.post("/chat", json={"question": "Hello"}).json()["status"], "conversation")
            self.assertEqual(client.get("/admin/metrics").status_code, 503)
            self.assertEqual(client.get("/admin/metrics").json(), {"status": "unavailable",
                                                                    "message": "Monitoring data is not available right now."})

    def test_no_question_sql_rows_or_errors_are_stored_or_returned(self) -> None:
        self.ask(f"How many patients? {SECRET}-Q")
        self.llm.resolve_message.side_effect = RuntimeError(f"{SECRET}-ERR password=hunter2")
        self.ask(f"Another {SECRET}-Q")
        stored = b"".join(p.read_bytes() for p in self.monitoring.path.parent.glob("monitoring.sqlite3*"))
        api = self.client.get("/admin/activity").text + self.client.get("/admin/metrics").text
        for marker in (SECRET, "hunter2", "SELECT", "patients"):
            with self.subTest(marker):
                self.assertNotIn(marker.encode(), stored)
                self.assertNotIn(marker, api)


if __name__ == "__main__":
    unittest.main()
