"""Offline tests for the single SQL correction attempt (sql_correction.py, ChatService, POST /chat).
Errors are simulated with fakes; mysql.connector.connect is disabled; no SQL reaches MySQL.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import dataclasses
import unittest
from unittest import mock

import mysql.connector
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.controllers import chat_controller
from backend.app.main import create_app
from backend.app.repositories import mysql_repository as repo_module
from backend.app.repositories.mysql_repository import (
    DatabaseConnectionError, MySQLRepository, QueryExecutionError, QueryResult, QueryTimeoutError,
)
from backend.app.services import sql_correction
from backend.app.services.chat_service import ChatService
from backend.app.services.llm_service import (
    ContextBudgetError, GenerationStats, IncompleteGenerationError, LLMError, LLMService, SQLGeneration,
)
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


STATS = GenerationStats(0, 0.1, 0.1, 0.2, 100, 10, "stop")
BAD_COLUMN_SQL = "SELECT SUM(total) AS paid FROM billing WHERE payment_status = 'Paid';"  # validator: unknown_column
GOOD_SQL = "SELECT SUM(amount) AS paid FROM billing WHERE payment_status = 'Paid';"
GROUP_SQL = ("SELECT d.doctor_id, d.first_name, COUNT(a.appointment_id) FROM doctors AS d LEFT JOIN appointments AS a "
             "ON a.doctor_id = d.doctor_id WHERE a.status = 'Completed' GROUP BY d.doctor_id")
RESULT = QueryResult(GOOD_SQL, ["paid"], [["173424.90"]], 1, False, 200, 0.001)


def sql(text: str) -> SQLGeneration:
    return SQLGeneration("sql", text, text, False, "fake", STATS)


def mysql_error(errno: int, message: str) -> QueryExecutionError:
    return QueryExecutionError(f"MySQL error: {errno} {message}", errno, message)


class Vectors:
    def __init__(self) -> None:
        self.calls = 0

    def get_chunks_by_type(self, chunk_type):
        self.calls += 1
        return expected_schema_chunks()

    def search(self, vector, top_k, embedding_model, types=None):
        self.calls += 1
        return []


class Embeddings:
    model_name = "fake-embed"

    def embed_query(self, question):
        return [0.1]


class CorrectionTestCase(unittest.TestCase):
    QUESTION = "What is the total amount of paid bills?"

    def setUp(self) -> None:
        self.llm = mock.Mock()
        self.llm.resolve_message.side_effect = routes_as("data_question")
        self.database = mock.Mock()
        self.database.execute.return_value = RESULT
        self.vectors = Vectors()
        self.service = ChatService(vectors=self.vectors, embeddings=Embeddings(), llm=self.llm, database=self.database)

    def run_with(self, initial, correction=None):
        self.llm.generate_sql.side_effect = [initial] if not isinstance(initial, Exception) else initial
        if correction is not None:
            self.llm.fix_sql.side_effect = [correction] if not isinstance(correction, Exception) else correction
        return self.service.answer(self.QUESTION)

    def assert_no_correction(self, trace) -> None:
        self.llm.fix_sql.assert_not_called()
        self.assertEqual((len(trace.attempts), trace.correction_attempted), (1, False))
        self.assertIsNone(trace.timings.correction_seconds)


class RecoverableThenCorrected(CorrectionTestCase):
    def test_validation_error_then_valid_correction(self) -> None:
        trace = self.run_with(sql(BAD_COLUMN_SQL), sql(GOOD_SQL))
        self.assertEqual(trace.stage, "answered")
        first, second = trace.attempts
        self.assertEqual((first.validation, first.error_code, first.retryable, first.execution),
                         ("rejected", "unknown_column", True, "not_run"))
        self.assertEqual((second.purpose, second.validation, second.execution), ("correction", "passed", "succeeded"))
        self.database.execute.assert_called_once_with(GOOD_SQL)
        question, context, failed_sql, diagnostic, _calendar = self.llm.fix_sql.call_args.args
        self.assertEqual((question, failed_sql), (self.QUESTION, BAD_COLUMN_SQL))
        self.assertEqual(context, self.llm.generate_sql.call_args.args[1], "the original context is reused")
        self.assertIn("unknown_column", diagnostic)
        self.assertEqual(self.vectors.calls, 2, "no second retrieval for the correction")
        self.assertIsNone(trace.error, "the corrected answer must not carry the initial failure")

    def test_mysql_error_then_valid_correction(self) -> None:
        self.database.execute.side_effect = [mysql_error(1054, "Unknown column 'b.total' in 'field list'"), RESULT]
        trace = self.run_with(sql(GOOD_SQL), sql(GOOD_SQL.replace("paid", "total_paid")))
        self.assertEqual(trace.stage, "answered")
        first = trace.attempts[0]
        self.assertEqual((first.execution, first.error_code, first.retryable), ("failed", "mysql_1054", True))
        diagnostic = self.llm.fix_sql.call_args.args[3]
        self.assertEqual(diagnostic, "MySQL error 1054 (unknown column): Unknown column 'b.total' in 'field list'")

    def test_moving_a_filter_into_the_join_is_allowed(self) -> None:
        moved = GROUP_SQL.replace(" WHERE a.status = 'Completed'", "").replace(
            "ON a.doctor_id = d.doctor_id", "ON a.doctor_id = d.doctor_id AND a.status = 'Completed'") + ", d.first_name"
        self.database.execute.side_effect = [mysql_error(1055, "Expression #2 of SELECT list is not in GROUP BY clause"), RESULT]
        self.QUESTION = "How many Completed appointments does each doctor have?"
        trace = self.run_with(sql(GROUP_SQL), sql(moved))
        self.assertEqual(trace.stage, "answered")


class CorrectionDoesNotSucceed(CorrectionTestCase):
    def test_corrected_sql_still_rejected_is_not_executed(self) -> None:
        for corrected, code in [("SELECT SUM(totals) FROM billing WHERE payment_status = 'Paid'", "unknown_column"),
                                ("SELECT SLEEP(5)", "dangerous_function"), ("DELETE FROM billing", "not_select")]:
            with self.subTest(corrected):
                self.setUp()
                trace = self.run_with(sql(BAD_COLUMN_SQL), sql(corrected))
                self.assertEqual((trace.stage, trace.attempts[-1].error_code), ("rejected", code))
                self.database.execute.assert_not_called()
                self.assertEqual(self.llm.fix_sql.call_count, 1)

    def test_correction_that_drops_a_filter_or_aggregate_is_rejected(self) -> None:
        for corrected in ["SELECT SUM(amount) AS paid FROM billing", "SELECT amount FROM billing WHERE payment_status = 'Paid'"]:
            with self.subTest(corrected):
                self.setUp()
                trace = self.run_with(sql(BAD_COLUMN_SQL), sql(corrected))
                self.assertEqual((trace.stage, trace.attempts[-1].error_code), ("rejected", "correction_changed_meaning"))
                self.assertIn("removed", trace.final_reason)
                self.database.execute.assert_not_called()

    def test_correction_returns_cannot_answer_or_clarification(self) -> None:
        for kind in ("cannot_answer", "needs_clarification"):
            with self.subTest(kind):
                self.setUp()
                trace = self.run_with(sql(BAD_COLUMN_SQL), SQLGeneration(kind, "No such data.", "", False, "fake", STATS))
                self.assertEqual((trace.stage, trace.attempts[-1].outcome, len(trace.attempts)), (kind, kind, 2))
                self.database.execute.assert_not_called()

    def test_correction_generation_failures(self) -> None:
        for error, stage in [(IncompleteGenerationError("cut off"), "generation_failed"),
                             (LLMError("slow", "timeout"), "timeout"), (LLMError("down", "unavailable"), "unavailable"),
                             (ContextBudgetError("too big"), "error")]:
            with self.subTest(stage):
                self.setUp()
                trace = self.run_with(sql(BAD_COLUMN_SQL), error)
                self.assertEqual((trace.stage, len(trace.attempts), trace.attempts[-1].outcome), (stage, 2, "generation_failed"))
                self.database.execute.assert_not_called()

    def test_only_one_correction_even_if_it_fails_recoverably(self) -> None:
        self.database.execute.side_effect = [mysql_error(1054, "Unknown column 'x'"), mysql_error(1054, "Unknown column 'y'")]
        trace = self.run_with(sql(GOOD_SQL), sql(GOOD_SQL.replace("paid", "p2")))
        self.assertEqual((trace.stage, len(trace.attempts)), ("query_failed", 2))
        self.assertTrue(trace.attempts[1].retryable, "retryable, but no second correction is allowed")
        self.assertEqual((self.llm.generate_sql.call_count, self.llm.fix_sql.call_count, self.database.execute.call_count), (1, 1, 2))
        self.assertIsNone(trace.result, "never presents the failed query as a result")


class NotRetried(CorrectionTestCase):
    def test_policy_violations_and_unsupported_constructs(self) -> None:
        for bad, code in [("DELETE FROM billing", "not_select"), ("SELECT SLEEP(1)", "dangerous_function"),
                          ("SELECT 1; SELECT 2", "multiple_statements"), ("SELECT 1 /*! , SLEEP(5) */", "executable_comment"),
                          ("SELECT user FROM mysql.user", "cross_database"), ("SELECT * FROM billing FOR UPDATE", "locking_clause"),
                          ("SELECT * FROM (SELECT 1 AS a) AS s", "unsupported_construct"),
                          ("SELECT 1 FROM doctors, appointments", "unsupported_join"), ("SELECT * FROM nurses", "unknown_table")]:
            with self.subTest(bad):
                self.setUp()
                trace = self.run_with(sql(bad))
                self.assertEqual((trace.stage, trace.attempts[0].error_code, trace.attempts[0].retryable), ("rejected", code, False))
                self.assert_no_correction(trace)
                self.database.execute.assert_not_called()

    def test_operational_and_non_allowlisted_database_errors(self) -> None:
        for error, stage in [(QueryTimeoutError("limit"), "timeout"), (DatabaseConnectionError("down"), "unavailable"),
                             (mysql_error(1142, "SELECT command denied"), "query_failed"),
                             (mysql_error(1365, "Division by 0"), "query_failed")]:
            with self.subTest(stage=stage, error=str(error)):
                self.setUp()
                self.database.execute.side_effect = error
                trace = self.run_with(sql(GOOD_SQL))
                self.assertEqual(trace.stage, stage)
                self.assert_no_correction(trace)

    def test_empty_successful_result_and_non_sql_replies(self) -> None:
        self.database.execute.return_value = QueryResult(GOOD_SQL, ["paid"], [], 0, False, 200, 0.001)
        trace = self.run_with(sql(GOOD_SQL))
        self.assertEqual((trace.stage, trace.result.row_count), ("answered", 0))
        self.assert_no_correction(trace)
        for kind in ("cannot_answer", "needs_clarification"):
            self.setUp()
            trace = self.run_with(SQLGeneration(kind, "x", "x", False, "fake", STATS))
            self.assertEqual(trace.stage, kind)
            self.assert_no_correction(trace)
        self.setUp()
        trace = self.run_with(IncompleteGenerationError("cut off"))
        self.assertEqual(trace.stage, "generation_failed")
        self.assert_no_correction(trace)


class SameExecutionBoundary(unittest.TestCase):
    """The corrected SQL goes through the real MySQLRepository: revalidation + fixed read-only session."""

    def test_corrected_sql_is_revalidated_and_run_read_only(self) -> None:
        statements, queries = [], iter([mysql.connector.errors.ProgrammingError(msg="Unknown column 'total'", errno=1054),
                                        [("173424.90",)]])

        class Cursor:
            def __init__(self, buffered=False):
                self.description, self.rows = None, []

            def execute(self, text):
                statements.append(text)
                if text.startswith(("SET SESSION", "SELECT @@SESSION")):
                    self.rows = [(5000, 1)]
                    return
                outcome = next(queries)
                if isinstance(outcome, Exception):
                    raise outcome
                self.description, self.rows = [("paid",)], outcome

            def fetchone(self):
                return self.rows.pop(0)

            def fetchmany(self, n):
                out, self.rows = self.rows[:n], self.rows[n:]
                return out

            def close(self):
                pass

        class Connection:
            def cursor(self, buffered=False):
                return Cursor(buffered)

            def close(self):
                pass

            def shutdown(self):
                pass

        settings = dataclasses.replace(get_settings(), mysql_readonly_user="ro", mysql_readonly_password="pw")
        connect = mock.Mock(return_value=Connection())
        database = MySQLRepository(settings, connect=connect)
        llm = mock.Mock()
        llm.resolve_message.side_effect = routes_as("data_question")
        llm.generate_sql.return_value = sql(GOOD_SQL)
        corrected = GOOD_SQL.replace("AS paid", "AS total_paid")
        llm.fix_sql.return_value = sql(corrected)
        service = ChatService(settings, vectors=Vectors(), embeddings=Embeddings(), llm=llm, database=database)
        with mock.patch.object(repo_module, "validate", wraps=repo_module.validate) as boundary_validate:
            trace = service.answer("What is the total amount of paid bills?")
        self.assertEqual((trace.stage, trace.result.rows), ("answered", [["173424.90"]]))
        self.assertEqual([c.args[0] for c in boundary_validate.call_args_list], [GOOD_SQL, corrected],
                         "both attempts revalidated inside the execution boundary")
        self.assertEqual(connect.call_args.kwargs["user"], "ro")
        session = ["SET SESSION max_execution_time = 5000", "SET SESSION transaction_read_only = ON",
                   "SELECT @@SESSION.max_execution_time, @@SESSION.transaction_read_only"]
        self.assertEqual(statements, session + [GOOD_SQL] + session + [corrected])


class ApiReporting(unittest.TestCase):
    def setUp(self) -> None:
        self.llm = mock.Mock()
        self.llm.resolve_message.side_effect = routes_as("data_question")
        self.database = mock.Mock()
        self.database.execute.side_effect = [mysql_error(1054, "Unknown column 'total'"), RESULT]
        self.llm.generate_sql.return_value = sql(GOOD_SQL)
        self.llm.fix_sql.return_value = sql(GOOD_SQL.replace("paid", "total_paid"))
        self.app = create_app(open_vector_store=False, monitoring=temp_monitoring())
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.app.dependency_overrides[chat_controller.get_chat_service] = lambda: ChatService(
            vectors=Vectors(), embeddings=Embeddings(), llm=self.llm, database=self.database)

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def test_corrected_answer_reports_attempts_and_timings(self) -> None:
        body = self.client.post("/chat", json={"question": "What is the total amount of paid bills?"}).json()
        self.assertEqual((body["status"], body["attempt_count"], body["correction_attempted"]), ("answered", 2, True))
        self.assertEqual((body["sql_executed"], body["sql_reached_database"]), (True, True))
        self.assertEqual(body["sql"], GOOD_SQL.replace("paid", "total_paid"))
        first, second = body["sql_attempts"]
        self.assertEqual((first["execution"], first["error_code"], first["retryable"]), ("failed", "mysql_1054", True))
        self.assertEqual((second["purpose"], second["execution"]), ("correction", "succeeded"))
        t = body["timings_ms"]
        self.assertIsNotNone(t["correction_ms"])
        self.assertGreaterEqual(t["total_ms"], t["correction_ms"])
        self.assertNotIn("Unknown column 'total'", str(body), "MySQL diagnostics are not returned to clients")

    def test_failed_final_query_is_not_reported_as_executed(self) -> None:
        self.database.execute.side_effect = [mysql_error(1054, "x"), mysql_error(1054, "y")]
        body = self.client.post("/chat", json={"question": "What is the total amount of paid bills?"}).json()
        self.assertEqual((body["status"], body["sql_executed"], body["sql_reached_database"]), ("query_failed", False, True))
        self.assertEqual((body["rows"], body["row_count"]), ([], 0))
        self.assertTrue(body["message"].startswith("One correction was attempted"))

    def test_first_attempt_success_is_unchanged(self) -> None:
        self.database.execute.side_effect = None
        self.database.execute.return_value = RESULT
        body = self.client.post("/chat", json={"question": "What is the total amount of paid bills?"}).json()
        self.assertEqual((body["status"], body["attempt_count"], body["correction_attempted"]), ("answered", 1, False))
        self.assertIsNone(body["timings_ms"]["correction_ms"])
        self.llm.fix_sql.assert_not_called()


class FixSqlRequest(unittest.TestCase):
    def payload(self, content: str, done_reason: str = "stop") -> dict:
        return {"done": True, "done_reason": done_reason, "message": {"content": content}, "prompt_eval_count": 1500}

    def test_prompt_delimits_reference_data_and_uses_generation_controls(self) -> None:
        with mock.patch("backend.app.services.llm_service.post_json", return_value=self.payload(GOOD_SQL)) as post:
            result = LLMService().fix_sql("Q {failed_sql}?", "CONTEXT {question}", BAD_COLUMN_SQL, "diag {error}")
        self.assertEqual((result.kind, result.text), ("sql", GOOD_SQL))
        body = post.call_args.args[2]
        user = body["messages"][1]["content"]
        for block in ("<reference>\nCONTEXT {question}\n</reference>", "<question>\nQ {failed_sql}?\n</question>",
                      f"<failed_sql>\n{BAD_COLUMN_SQL}\n</failed_sql>", "<diagnostic>\ndiag {error}\n</diagnostic>"):
            self.assertIn(block, user)
        s = get_settings()
        self.assertEqual((body["think"], body["options"]["temperature"], body["options"]["num_ctx"], body["options"]["num_predict"]),
                         (False, 0, s.ollama_sql_num_ctx, s.ollama_sql_num_predict))
        self.assertIn("keep every filter", body["messages"][0]["content"])

    def test_budget_and_incomplete_checks_apply(self) -> None:
        with mock.patch("backend.app.services.llm_service.post_json") as post:
            with self.assertRaises(ContextBudgetError):
                LLMService().fix_sql("q", "x" * 20000, "SELECT 1", "d")
            post.assert_not_called()
        with mock.patch("backend.app.services.llm_service.post_json", return_value=self.payload("SELECT", "length")):
            with self.assertRaises(IncompleteGenerationError):
                LLMService().fix_sql("q", "ctx", "SELECT 1", "d")


class Diagnostics(unittest.TestCase):
    def test_sanitized_and_bounded(self) -> None:
        text = sql_correction.mysql_diagnostic(1054, "Unknown column\n'x'\x00 in 'field list' " + "y" * 500)
        self.assertNotIn("\n", text)
        self.assertNotIn("\x00", text)
        self.assertLessEqual(len(text), sql_correction.MAX_DIAGNOSTIC_CHARS)

    def test_allowlists_are_explicit(self) -> None:
        self.assertEqual(sql_correction.RETRYABLE_VALIDATION_CODES,
                         {"unknown_column", "unknown_alias", "ambiguous_column", "duplicate_table_alias", "parse_error", "invalid_limit",
                          "invalid_join_key", "join_without_key", "row_level_group_by"})
        self.assertEqual(set(sql_correction.RETRYABLE_MYSQL_ERRORS), {1054, 1052, 1066, 1247, 1064, 1055, 1056, 1111, 1140, 3065})


if __name__ == "__main__":
    unittest.main()
