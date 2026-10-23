"""Offline tests for backend/app/services/sql_validator.py. No SQL is ever executed:
mysql.connector.connect is replaced for the whole module so any attempt to connect fails.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import re
import unittest
from unittest import mock

from backend.app.config import EXAMPLES_FILE
from backend.app.services import sql_validator as v
from backend.app.services.rag.chunker import chunk_documents
from backend.app.services.rag.document_loader import load_document

_no_db = mock.patch("mysql.connector.connect", side_effect=AssertionError("tests must never connect to MySQL"))


def setUpModule() -> None:
    _no_db.start()


def tearDownModule() -> None:
    _no_db.stop()


def example_queries() -> dict[str, str]:
    """The SQL of each knowledge_base/examples.sql example, without its '--' description lines."""
    chunks = chunk_documents([load_document(EXAMPLES_FILE, "example")])
    return {c.id: "\n".join(l for l in c.content.splitlines() if not l.lstrip().startswith("--")).strip() for c in chunks}


# The two queries qwen3.5:4b generated in the previous step, verbatim.
GENERATED_PAID_BILLS = """SELECT SUM(amount) AS total_paid_amount
FROM billing
WHERE payment_status = 'Paid';"""
GENERATED_DOCTOR_APPOINTMENTS = """SELECT
    d.doctor_id,
    d.first_name,
    d.last_name,
    COUNT(a.appointment_id) AS appointment_count
FROM doctors AS d
LEFT JOIN appointments AS a
    ON d.doctor_id = a.doctor_id
GROUP BY
    d.doctor_id,
    d.first_name,
    d.last_name;"""


class ValidatorTestCase(unittest.TestCase):
    def assertAccepted(self, sql: str) -> v.ValidationResult:
        result = v.validate(sql)
        self.assertTrue(result.ok, f"expected accept, got {result.code}: {result.reason}\n{sql}")
        self.assertEqual(result.sql, sql)
        return result

    def assertRejected(self, sql: str, *codes: str) -> v.ValidationResult:
        result = v.validate(sql)
        self.assertFalse(result.ok, f"expected reject ({codes}), but it was accepted:\n{sql}")
        self.assertIn(result.code, codes, f"{sql}\n-> {result.code}: {result.reason}")
        self.assertTrue(result.reason)
        self.assertEqual(result.sql, sql)
        return result


class AcceptsKnownGoodQueries(ValidatorTestCase):
    def test_every_knowledge_base_example(self) -> None:
        queries = example_queries()
        self.assertEqual(len(queries), 250)
        for chunk_id, sql in queries.items():
            with self.subTest(chunk_id):
                self.assertAccepted(sql)

    def test_recently_generated_queries(self) -> None:
        self.assertEqual(self.assertAccepted(GENERATED_PAID_BILLS).tables, ("billing",))
        self.assertEqual(self.assertAccepted(GENERATED_DOCTOR_APPOINTMENTS).tables, ("appointments", "doctors"))

    def test_harmless_words_inside_string_literals(self) -> None:
        for literal in ["'delete'", "'Please DELETE this; DROP TABLE patients'", "'sleep(5) and load_file'",
                        "'-- not a comment'", "'/*! not executable */'", "'it''s'", "'INTO OUTFILE'"]:
            with self.subTest(literal):
                self.assertAccepted(f"SELECT bill_id FROM billing WHERE payment_method = {literal}")

    def test_supported_features(self) -> None:
        for sql in [
            "SELECT * FROM patients",
            "SELECT p.* FROM patients AS p",
            "SELECT COUNT(*) FROM appointments",
            "SELECT COUNT(DISTINCT a.patient_id) AS patients FROM appointments AS a",
            "SELECT status, COUNT(*) AS n FROM appointments GROUP BY status HAVING n > 10 ORDER BY n DESC",
            "SELECT status, COUNT(*) AS n FROM appointments GROUP BY status HAVING COUNT(*) > 10",
            "SELECT YEAR(bill_date) AS y, SUM(amount) AS total FROM billing GROUP BY y ORDER BY y",
            "SELECT DATE_FORMAT(bill_date, '%Y-%m') AS month, ROUND(AVG(amount), 2) FROM billing GROUP BY month",
            "SELECT bill_id FROM billing WHERE bill_date BETWEEN '2023-01-01' AND DATE_SUB('2023-12-31', INTERVAL 1 MONTH)",
            "SELECT TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()) AS age FROM patients",
            "SELECT DATEDIFF(t.treatment_date, a.appointment_date) FROM treatments AS t JOIN appointments AS a ON t.appointment_id = a.appointment_id",
            "SELECT CASE WHEN amount > 4000 THEN 'high' ELSE 'normal' END AS band, IF(amount > 1, 1, 0) FROM billing",
            "SELECT COALESCE(insurance_provider, 'none'), IFNULL(email, ''), CONCAT(first_name, ' ', last_name) FROM patients",
            "SELECT bill_id FROM billing WHERE payment_status IN ('Paid', 'Pending') AND amount IS NOT NULL",
            "SELECT DISTINCT specialization FROM doctors WHERE specialization LIKE 'Ped%' ORDER BY specialization LIMIT 5",
            "SELECT bill_id FROM billing LIMIT 10 OFFSET 20",
            "SELECT bill_id FROM billing LIMIT 20, 10",
            "SELECT b.amount FROM medlens.billing AS b",
            "SELECT `billing`.`amount` FROM `medlens`.`billing`",
            "SELECT CAST(amount AS SIGNED) FROM billing",
            "SELECT 1",
            "SELECT p.first_name, d.last_name FROM appointments AS a INNER JOIN patients AS p ON a.patient_id = p.patient_id "
            "RIGHT JOIN doctors AS d ON a.doctor_id = d.doctor_id LEFT OUTER JOIN treatments AS t ON t.appointment_id = a.appointment_id",
            "SELECT bill_id FROM billing;",
        ]:
            with self.subTest(sql):
                self.assertAccepted(sql)

    def test_original_sql_is_preserved_unchanged(self) -> None:
        sql = "  select   amount\nFROM billing  ;  "
        result = self.assertAccepted(sql)
        self.assertEqual(v.validate_sql(sql), v.ValidatedSQL(sql))
        self.assertNotIn("LIMIT", result.sql.upper())


class AliasResolution(ValidatorTestCase):
    def test_qualified_columns_resolve_through_aliases(self) -> None:
        self.assertAccepted("SELECT d.first_name, a.status FROM doctors AS d JOIN appointments AS a ON a.doctor_id = d.doctor_id")

    def test_unique_unqualified_column_in_a_join(self) -> None:
        self.assertAccepted("SELECT specialization, status FROM doctors AS d JOIN appointments AS a ON a.doctor_id = d.doctor_id")

    def test_column_from_wrong_table_via_alias(self) -> None:
        self.assertRejected("SELECT d.amount FROM doctors AS d", v.UNKNOWN_COLUMN)

    def test_table_name_hidden_by_its_alias(self) -> None:
        self.assertRejected("SELECT doctors.first_name FROM doctors AS d", v.UNKNOWN_ALIAS)

    def test_undefined_alias(self) -> None:
        self.assertRejected("SELECT x.first_name FROM doctors AS d", v.UNKNOWN_ALIAS)

    def test_ambiguous_unqualified_column(self) -> None:
        r = self.assertRejected(
            "SELECT doctor_id FROM doctors AS d JOIN appointments AS a ON a.doctor_id = d.doctor_id", v.AMBIGUOUS_COLUMN)
        self.assertIn("doctor_id", r.reason)
        self.assertRejected("SELECT first_name FROM doctors JOIN patients ON 1 = 1", v.AMBIGUOUS_COLUMN)

    def test_ambiguous_in_self_join_and_duplicate_alias(self) -> None:
        self.assertRejected("SELECT last_name FROM doctors AS d1 JOIN doctors AS d2 ON d1.doctor_id <> d2.doctor_id", v.AMBIGUOUS_COLUMN)
        self.assertRejected("SELECT 1 FROM doctors JOIN doctors ON 1 = 1", v.DUPLICATE_TABLE_ALIAS)
        self.assertRejected("SELECT 1 FROM doctors AS x JOIN billing AS x ON 1 = 1", v.DUPLICATE_TABLE_ALIAS)

    def test_order_by_select_alias_wins_over_ambiguous_columns(self) -> None:
        # first_name exists in doctors and patients, but MySQL resolves ORDER BY to the SELECT alias first.
        self.assertAccepted("SELECT d.first_name AS first_name FROM doctors AS d JOIN appointments AS a "
                            "ON a.doctor_id = d.doctor_id JOIN patients AS p ON p.patient_id = a.patient_id ORDER BY first_name")

    def test_group_by_prefers_columns_and_rejects_ambiguity(self) -> None:
        self.assertRejected("SELECT d.first_name AS first_name FROM doctors AS d JOIN appointments AS a "
                            "ON a.doctor_id = d.doctor_id JOIN patients AS p ON p.patient_id = a.patient_id GROUP BY first_name",
                            v.AMBIGUOUS_COLUMN)

    def test_select_alias_not_allowed_in_where_or_expressions(self) -> None:
        self.assertRejected("SELECT amount AS a2 FROM billing WHERE a2 > 10", v.UNKNOWN_COLUMN)
        self.assertRejected("SELECT amount AS a2 FROM billing ORDER BY a2 + 1", v.UNKNOWN_COLUMN)

    def test_count_star_and_order_by_aggregate_alias(self) -> None:
        self.assertAccepted("SELECT doctor_id, COUNT(*) AS total FROM appointments GROUP BY doctor_id ORDER BY total DESC")

    def test_on_clause_cannot_use_a_table_joined_later(self) -> None:
        self.assertRejected("SELECT 1 FROM doctors AS d JOIN appointments AS a ON a.patient_id = p.patient_id "
                            "JOIN patients AS p ON p.patient_id = a.patient_id", v.UNKNOWN_COLUMN)


class RejectsUnsafeOrUnknown(ValidatorTestCase):
    def test_multiple_statements(self) -> None:
        self.assertRejected("SELECT 1; DROP TABLE patients", v.MULTIPLE_STATEMENTS)
        self.assertRejected("SELECT * FROM doctors; SELECT * FROM patients;", v.MULTIPLE_STATEMENTS)

    def test_writes_ddl_and_admin_statements(self) -> None:
        for sql in ["DELETE FROM billing", "UPDATE billing SET amount = 0", "INSERT INTO doctors (doctor_id) VALUES ('X')",
                    "DROP TABLE patients", "TRUNCATE TABLE billing", "CREATE TABLE x (a INT)", "ALTER TABLE doctors ADD c INT",
                    "CALL refresh()", "SET @a = 1", "SHOW TABLES", "GRANT ALL ON medlens.* TO 'u'", "USE mysql",
                    "REPLACE INTO doctors (doctor_id) VALUES ('X')", "TABLE patients", "VALUES ROW(1)"]:
            with self.subTest(sql):
                self.assertRejected(sql, v.NOT_SELECT, v.PARSE_ERROR)

    def test_unknown_tables_and_columns(self) -> None:
        self.assertRejected("SELECT * FROM nurses", v.UNKNOWN_TABLE)
        self.assertRejected("SELECT * FROM Patients", v.UNKNOWN_TABLE)  # table names are case-sensitive
        self.assertRejected("SELECT salary FROM doctors", v.UNKNOWN_COLUMN)
        self.assertRejected("SELECT bill_id FROM billing ORDER BY total_amount", v.UNKNOWN_COLUMN)

    def test_cross_database_access(self) -> None:
        self.assertRejected("SELECT user FROM mysql.user", v.CROSS_DATABASE)
        self.assertRejected("SELECT table_name FROM information_schema.tables", v.CROSS_DATABASE)
        self.assertRejected("SELECT * FROM otherdb.patients", v.CROSS_DATABASE)
        self.assertRejected("SELECT medlens.billing.amount FROM billing", v.UNSUPPORTED)

    def test_dangerous_functions(self) -> None:
        for sql in ["SELECT SLEEP(5)", "SELECT bill_id FROM billing WHERE SLEEP(1) = 0", "SELECT BENCHMARK(1000000, MD5('x'))",
                    "SELECT LOAD_FILE('C:/secrets.txt')", "SELECT GET_LOCK('medlens', 10)",
                    "SELECT bill_id FROM billing ORDER BY SLEEP(1)"]:
            with self.subTest(sql):
                self.assertRejected(sql, v.DANGEROUS_FUNCTION)

    def test_functions_outside_the_allowlist(self) -> None:
        for sql in ["SELECT USER()", "SELECT VERSION()", "SELECT DATABASE()", "SELECT MD5(email) FROM patients",
                    "SELECT UUID()", "SELECT NOW()"]:
            with self.subTest(sql):
                self.assertRejected(sql, v.FUNCTION_NOT_ALLOWED)

    def test_into_clauses(self) -> None:
        self.assertRejected("SELECT * FROM patients INTO OUTFILE 'C:/tmp/p.csv'", v.INTO, v.PARSE_ERROR)
        self.assertRejected("SELECT * FROM patients INTO DUMPFILE 'C:/tmp/p.bin'", v.INTO, v.PARSE_ERROR)
        self.assertRejected("SELECT amount INTO @total FROM billing LIMIT 1", v.INTO, v.VARIABLE)

    def test_locking_clauses(self) -> None:
        for sql in ["SELECT * FROM billing FOR UPDATE", "SELECT * FROM billing LOCK IN SHARE MODE", "SELECT * FROM billing FOR SHARE"]:
            with self.subTest(sql):
                self.assertRejected(sql, v.LOCKING)

    def test_comments_including_executable_ones(self) -> None:
        self.assertRejected("SELECT 1 /*! , SLEEP(5) */", v.EXECUTABLE_COMMENT)
        self.assertRejected("SELECT bill_id FROM billing /*!50000 WHERE SLEEP(1) = 0 */", v.EXECUTABLE_COMMENT)
        self.assertRejected("SELECT /*+ MAX_EXECUTION_TIME(1) */ bill_id FROM billing", v.EXECUTABLE_COMMENT)
        for sql in ["SELECT 1 /* note */", "SELECT 1 -- note", "SELECT 1 # note", "-- note\nSELECT 1"]:
            with self.subTest(sql):
                self.assertRejected(sql, v.COMMENT)

    def test_variables_and_placeholders(self) -> None:
        for sql in ["SELECT @a", "SELECT @@version", "SELECT @a := 1", "SELECT bill_id FROM billing WHERE amount > ?"]:
            with self.subTest(sql):
                self.assertRejected(sql, v.VARIABLE)

    def test_quoting_rules(self) -> None:
        self.assertRejected('SELECT bill_id FROM billing WHERE payment_status = "Paid"', v.QUOTING)
        self.assertRejected("SELECT bill_id FROM billing WHERE payment_status = 'a" + "\\" + "' OR 1=1 -- '", v.QUOTING)

    def test_invalid_input(self) -> None:
        self.assertRejected("", v.EMPTY)
        self.assertRejected("   ;  ", v.EMPTY, v.PARSE_ERROR)
        self.assertRejected("SELEC amount FROM billing", v.PARSE_ERROR, v.NOT_SELECT)
        with self.assertRaises(TypeError):
            v.validate(None)  # type: ignore[arg-type]


class RejectsUnsupportedConstructsClearly(ValidatorTestCase):
    def test_unsupported_constructs(self) -> None:
        cases = {
            "SELECT bill_id FROM billing WHERE amount > (SELECT AVG(amount) FROM billing)": "subquer",
            "SELECT * FROM (SELECT bill_id FROM billing) AS s": "subquer",
            "SELECT doctor_id FROM doctors WHERE EXISTS (SELECT 1 FROM appointments)": "subquer",
            "WITH paid AS (SELECT * FROM billing) SELECT * FROM paid": "CTE",
            "SELECT doctor_id FROM doctors UNION SELECT doctor_id FROM appointments": "UNION",
            "SELECT bill_id, ROW_NUMBER() OVER (ORDER BY amount) FROM billing": "window",
            "SELECT status, COUNT(*) FROM appointments GROUP BY status WITH ROLLUP": "ROLLUP",
            "SELECT SQL_CALC_FOUND_ROWS bill_id FROM billing": "SQL_CALC_FOUND_ROWS",
            "SELECT HIGH_PRIORITY bill_id FROM billing": "HIGH_PRIORITY",
            "SELECT bill_id FROM billing USE INDEX (idx_billing_date)": "USE INDEX",
            "SELECT bill_id FROM billing PARTITION (p0)": "PARTITION",
            "SELECT x'41'": "",
            "SELECT bill_id FROM billing LIMIT ALL": "",
        }
        for sql, hint in cases.items():
            with self.subTest(sql):
                r = self.assertRejected(sql, v.UNSUPPORTED, v.PARSE_ERROR, v.INVALID_LIMIT)
                self.assertIn(hint.lower(), r.reason.lower())

    def test_unsupported_joins(self) -> None:
        for sql in ["SELECT 1 FROM doctors, appointments", "SELECT 1 FROM doctors CROSS JOIN appointments",
                    "SELECT 1 FROM doctors NATURAL JOIN appointments", "SELECT 1 FROM doctors JOIN appointments USING (doctor_id)",
                    "SELECT 1 FROM doctors STRAIGHT_JOIN appointments ON doctors.doctor_id = appointments.doctor_id",
                    "SELECT 1 FROM doctors JOIN appointments"]:
            with self.subTest(sql):
                self.assertRejected(sql, v.UNSUPPORTED_JOIN)


class ResultObject(unittest.TestCase):
    def test_require_raises_with_code(self) -> None:
        with self.assertRaises(v.SQLValidationError) as ctx:
            v.validate_sql("SELECT SLEEP(1)")
        self.assertEqual(ctx.exception.code, v.DANGEROUS_FUNCTION)

    def test_schema_matches_schema_sql(self) -> None:
        schema = v.schema_columns()
        self.assertEqual(set(schema), {"patients", "doctors", "appointments", "treatments", "billing"})
        self.assertIn("payment_status", schema["billing"])
        self.assertTrue(all(re.fullmatch(r"[a-z_]+", c) for cols in schema.values() for c in cols))


if __name__ == "__main__":
    unittest.main()
