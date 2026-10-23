"""Offline tests for backend/app/repositories/mysql_repository.py. They use fake connections only:
the real mysql.connector.connect is replaced for the whole module, so nothing reaches MySQL.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import dataclasses
import datetime as dt
import unittest
from decimal import Decimal
from unittest import mock

import mysql.connector
from mysql.connector import errorcode

from backend.app.config import MissingSettingError, get_settings
from backend.app.repositories.mysql_repository import (
    DatabaseConnectionError, MySQLRepository, QueryExecutionError, QueryTimeoutError, SessionSetupError, to_json_value,
)
from backend.app.services.sql_validator import SQLValidationError, ValidatedSQL

_no_db = mock.patch("mysql.connector.connect", side_effect=AssertionError("tests must never connect to MySQL"))


def setUpModule() -> None:
    _no_db.start()


def tearDownModule() -> None:
    _no_db.stop()


SETTINGS = dataclasses.replace(
    get_settings(), mysql_host="db.test", mysql_port=3306, mysql_database="medlens",
    mysql_readonly_user="ro_user", mysql_readonly_password="ro-secret",
    mysql_connect_timeout_seconds=5, mysql_io_timeout_seconds=10, mysql_max_execution_ms=5000, mysql_row_cap=3,
)
GOOD_SQL = "SELECT appointment_id FROM appointments"


class FakeCursor:
    def __init__(self, conn: "FakeConnection", buffered: bool) -> None:
        self.conn, self.buffered = conn, buffered
        self.description = None
        self._rows: list[tuple] = []
        self.closed = False
        self.fetchmany_sizes: list[int] = []

    def execute(self, sql: str) -> None:
        self.conn.statements.append(sql)
        if sql.startswith("SET SESSION") or sql.startswith("SELECT @@SESSION"):
            if self.conn.setup_error is not None:
                raise self.conn.setup_error
            self._rows = [self.conn.session_values]
            return
        if self.conn.query_error is not None:
            raise self.conn.query_error
        self.description = [(name,) for name in self.conn.columns]
        self._rows = list(self.conn.rows)

    def fetchone(self) -> tuple | None:
        return self._rows.pop(0) if self._rows else None

    def fetchmany(self, size: int) -> list[tuple]:
        self.fetchmany_sizes.append(size)
        if self.conn.fetch_error is not None:
            raise self.conn.fetch_error
        out, self._rows = self._rows[:size], self._rows[size:]
        self.conn.rows_read += len(out)
        return out

    def close(self) -> None:
        if not self.buffered and self._rows:
            raise mysql.connector.errors.InternalError("Unread result found")
        self.closed = True


class FakeConnection:
    def __init__(self, rows=(), columns=("appointment_id",), session_values=(5000, 1),
                 setup_error=None, query_error=None, fetch_error=None) -> None:
        self.rows, self.columns, self.session_values = list(rows), list(columns), session_values
        self.setup_error, self.query_error, self.fetch_error = setup_error, query_error, fetch_error
        self.statements: list[str] = []
        self.cursors: list[FakeCursor] = []
        self.rows_read = 0
        self.closed = self.shut_down = False

    def cursor(self, buffered: bool = False) -> FakeCursor:
        c = FakeCursor(self, buffered)
        self.cursors.append(c)
        return c

    def close(self) -> None:
        self.closed = True

    def shutdown(self) -> None:
        self.shut_down = True


def repo_with(conn: FakeConnection | None = None, settings=SETTINGS, connect_error=None):
    connect = mock.Mock(return_value=conn, side_effect=connect_error)
    return MySQLRepository(settings, connect=connect), connect


class RejectionBeforeDatabaseAccess(unittest.TestCase):
    def test_dangerous_sql_never_connects(self) -> None:
        for sql in ["SELECT SLEEP(10)", "DROP TABLE patients", "SELECT 1; DELETE FROM billing",
                    "SELECT 1 /*! , SLEEP(5) */", "SELECT * FROM mysql.user", "SELECT * FROM billing FOR UPDATE"]:
            with self.subTest(sql):
                repo, connect = repo_with(FakeConnection())
                with self.assertRaises(SQLValidationError):
                    repo.execute(sql)
                connect.assert_not_called()

    def test_forged_validated_object_is_validated_again(self) -> None:
        repo, connect = repo_with(FakeConnection())
        with self.assertRaises(SQLValidationError):
            repo.execute(ValidatedSQL("DELETE FROM billing"))
        connect.assert_not_called()

    def test_missing_readonly_credentials_never_connects(self) -> None:
        repo, connect = repo_with(FakeConnection(), settings=dataclasses.replace(SETTINGS, mysql_readonly_password=None))
        with self.assertRaises(MissingSettingError):
            repo.execute(GOOD_SQL)
        connect.assert_not_called()

    def test_only_readonly_credentials_and_finite_timeouts_are_used(self) -> None:
        conn = FakeConnection(rows=[("A001",)])
        repo, connect = repo_with(conn)
        repo.execute(GOOD_SQL)
        kwargs = connect.call_args.kwargs
        self.assertEqual((kwargs["user"], kwargs["password"]), ("ro_user", "ro-secret"))
        self.assertEqual((kwargs["host"], kwargs["database"]), ("db.test", "medlens"))
        self.assertEqual((kwargs["connection_timeout"], kwargs["read_timeout"], kwargs["write_timeout"]), (5, 10, 10))

    def test_io_timeout_must_exceed_execution_limit(self) -> None:
        with self.assertRaises(ValueError):
            MySQLRepository(dataclasses.replace(SETTINGS, mysql_io_timeout_seconds=5, mysql_max_execution_ms=5000))


class SessionControls(unittest.TestCase):
    def test_fixed_session_statements_run_before_the_unchanged_query(self) -> None:
        conn = FakeConnection(rows=[("A001",)])
        sql = "SELECT appointment_id FROM appointments  "
        repo_with(conn)[0].execute(sql)
        self.assertEqual(conn.statements, [
            "SET SESSION max_execution_time = 5000",
            "SET SESSION transaction_read_only = ON",
            "SELECT @@SESSION.max_execution_time, @@SESSION.transaction_read_only",
            sql,
        ])
        self.assertFalse(conn.cursors[-1].buffered, "the query must use an unbuffered cursor")

    def test_session_limit_not_applied_means_nothing_runs(self) -> None:
        for values in [(0, 1), (5000, 0), None]:
            with self.subTest(values):
                conn = FakeConnection(session_values=values)
                with self.assertRaises(SessionSetupError):
                    repo_with(conn)[0].execute(GOOD_SQL)
                self.assertNotIn(GOOD_SQL, conn.statements)
                self.assertTrue(conn.shut_down)

    def test_session_setup_error(self) -> None:
        conn = FakeConnection(setup_error=mysql.connector.errors.ProgrammingError(msg="denied", errno=1227))
        with self.assertRaises(SessionSetupError):
            repo_with(conn)[0].execute(GOOD_SQL)
        self.assertTrue(conn.shut_down)


class TimeoutsAndErrors(unittest.TestCase):
    def test_server_execution_timeout(self) -> None:
        error = mysql.connector.errors.DatabaseError(msg="maximum statement execution time exceeded", errno=errorcode.ER_QUERY_TIMEOUT)
        for where in ("query_error", "fetch_error"):  # unbuffered: the timeout can surface while fetching
            with self.subTest(where):
                conn = FakeConnection(rows=[("A001",)], **{where: error})
                with self.assertRaises(QueryTimeoutError) as ctx:
                    repo_with(conn)[0].execute(GOOD_SQL)
                self.assertIn("5000 ms", str(ctx.exception))
                self.assertTrue(conn.shut_down)
                self.assertFalse(conn.closed)

    def test_lost_connection_is_reported_as_timeout(self) -> None:
        conn = FakeConnection(fetch_error=mysql.connector.errors.OperationalError(msg="Lost connection", errno=errorcode.CR_SERVER_LOST))
        with self.assertRaises(QueryTimeoutError):
            repo_with(conn)[0].execute(GOOD_SQL)
        self.assertTrue(conn.shut_down)

    def test_other_mysql_errors(self) -> None:
        conn = FakeConnection(query_error=mysql.connector.errors.ProgrammingError(msg="isn't in GROUP BY", errno=1055))
        with self.assertRaises(QueryExecutionError) as ctx:
            repo_with(conn)[0].execute(GOOD_SQL)
        self.assertIn("1055", str(ctx.exception))
        self.assertTrue(conn.shut_down)

    def test_connection_failure_does_not_leak_the_password(self) -> None:
        repo, _ = repo_with(connect_error=mysql.connector.errors.InterfaceError(msg="Can't connect to MySQL server", errno=2003))
        with self.assertRaises(DatabaseConnectionError) as ctx:
            repo.execute(GOOD_SQL)
        self.assertNotIn("ro-secret", str(ctx.exception))
        self.assertIn("ro_user", str(ctx.exception))


class RowCapAndCleanup(unittest.TestCase):
    def test_more_rows_than_cap_is_truncated_without_reading_the_rest(self) -> None:
        conn = FakeConnection(rows=[(f"A{i:03d}",) for i in range(1, 1001)])
        result = repo_with(conn)[0].execute(GOOD_SQL)
        self.assertEqual(result.rows, [["A001"], ["A002"], ["A003"]])
        self.assertEqual((result.row_count, result.truncated, result.row_cap), (3, True, 3))
        self.assertEqual(conn.cursors[-1].fetchmany_sizes, [4])
        self.assertEqual(conn.rows_read, 4, "only row_cap + 1 rows may be read")
        self.assertTrue(conn.shut_down, "unread rows: the socket must be shut down, not drained")
        self.assertFalse(conn.closed)

    def test_exactly_cap_rows_is_not_truncated(self) -> None:
        conn = FakeConnection(rows=[("A001",), ("A002",), ("A003",)])
        result = repo_with(conn)[0].execute(GOOD_SQL)
        self.assertEqual((result.row_count, result.truncated), (3, False))
        self.assertTrue(conn.closed and conn.cursors[-1].closed)
        self.assertFalse(conn.shut_down)

    def test_empty_result(self) -> None:
        conn = FakeConnection(rows=[])
        result = repo_with(conn)[0].execute(GOOD_SQL)
        self.assertEqual((result.rows, result.row_count, result.truncated), ([], 0, False))
        self.assertTrue(conn.closed)

    def test_cleanup_errors_do_not_hide_the_result_or_error(self) -> None:
        conn = FakeConnection(rows=[("A001",)])
        conn.close = mock.Mock(side_effect=OSError("socket already closed"))
        result = repo_with(conn)[0].execute(GOOD_SQL)
        self.assertEqual(result.rows, [["A001"]])
        self.assertTrue(conn.shut_down)


class ColumnTypeMetadata(unittest.TestCase):
    def test_types_come_from_cursor_metadata(self) -> None:
        from mysql.connector import FieldType
        conn = FakeConnection(rows=[("D001", 25, Decimal("2663.415600"), dt.date(2023, 1, 1), "8322010158")],
                              columns=("doctor_id", "n", "avg_cost", "first_day", "phone_number"))
        codes = iter([FieldType.VAR_STRING, FieldType.LONGLONG, FieldType.NEWDECIMAL, FieldType.DATE, FieldType.VAR_STRING])
        original = FakeCursor.execute

        def typed_execute(cursor, sql):
            original(cursor, sql)
            if cursor.description:
                cursor.description = [(d[0], next(codes)) for d in cursor.description]

        with mock.patch.object(FakeCursor, "execute", typed_execute):
            result = repo_with(conn)[0].execute(GOOD_SQL)
        self.assertEqual(result.column_types, ["string", "integer", "decimal", "date", "string"])
        self.assertEqual(result.rows, [["D001", 25, "2663.415600", "2023-01-01", "8322010158"]])

    def test_missing_type_codes_do_not_fail_the_query(self) -> None:
        result = repo_with(FakeConnection(rows=[("A001",)]))[0].execute(GOOD_SQL)
        self.assertEqual((result.rows, result.column_types), ([["A001"]], ["other"]))


class Serialization(unittest.TestCase):
    def test_values(self) -> None:
        cases = [
            (None, None), (True, True), (42, 42), (1.5, 1.5), ("text", "text"),
            (Decimal("173424.90"), "173424.90"), (Decimal("1E+2"), "100"), (Decimal("-0.010"), "-0.010"),
            (dt.date(2023, 8, 9), "2023-08-09"), (dt.datetime(2023, 8, 9, 15, 15), "2023-08-09T15:15:00"),
            (dt.timedelta(hours=8), "08:00:00"), (dt.timedelta(hours=15, minutes=15), "15:15:00"),
            (dt.timedelta(hours=838, minutes=59, seconds=59), "838:59:59"),
            (-dt.timedelta(hours=1, minutes=30), "-01:30:00"), (dt.timedelta(seconds=1, microseconds=500), "00:00:01.000500"),
            (b"abc", "abc"), (b"\xff\x00", "0xff00"), ({"b", "a"}, ["a", "b"]),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(to_json_value(value), expected)

    def test_unknown_types_fail_loudly(self) -> None:
        with self.assertRaises(TypeError):
            to_json_value(object())

    def test_duplicate_column_labels_and_nulls_are_preserved(self) -> None:
        conn = FakeConnection(rows=[("D001", None, Decimal("10.50"), dt.timedelta(hours=9))],
                              columns=("doctor_id", "doctor_id", "amount", "appointment_time"))
        sql = "SELECT d.doctor_id, a.doctor_id, b.amount, a.appointment_time FROM doctors AS d JOIN appointments AS a ON a.doctor_id = d.doctor_id JOIN treatments AS t ON t.appointment_id = a.appointment_id JOIN billing AS b ON b.treatment_id = t.treatment_id"
        result = repo_with(conn)[0].execute(sql)
        self.assertEqual(result.columns, ["doctor_id", "doctor_id", "amount", "appointment_time"])
        self.assertEqual(result.rows, [["D001", None, "10.50", "09:00:00"]])


if __name__ == "__main__":
    unittest.main()
