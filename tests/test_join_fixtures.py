"""Regression tests for optional invoices, zero amounts and one-to-many joins on an isolated fixture.

The real data has exactly one treatment and one bill per appointment, so it cannot tell an INNER JOIN
from a LEFT JOIN or a duplicated total from a correct one. These tests run knowledge-base examples and
typical wrong queries on an in-memory SQLite copy of the schema (evaluation/fixture_db.py, JOIN_ROWS).
Every query passes the real validator first. No model, no index, no MySQL.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import unittest
from decimal import Decimal
from unittest import mock

from backend.app.config import EXAMPLES_FILE
from backend.app.controllers.chat_controller import to_response
from backend.app.services import question_checks as qc
from backend.app.services.chat_service import ChatService
from backend.app.services.llm_service import GenerationStats, MessageRouting, SQLGeneration
from backend.app.services.rag.chunker import chunk_documents
from backend.app.services.rag.context_builder import expected_schema_chunks
from backend.app.services.rag.document_loader import load_document
from backend.scripts.check_examples import parse
from evaluation.fixture_db import JOIN_ROWS as ROWS, FixtureRepository

_no_db = mock.patch("mysql.connector.connect", side_effect=AssertionError("tests must never connect to MySQL"))


def setUpModule() -> None:
    _no_db.start()


def tearDownModule() -> None:
    _no_db.stop()


ALL_APPOINTMENTS = {"A1", "A2", "A3", "A4", "A5", "A6"}


def money(column: str, value):
    """SQLite returns whole amounts as integers; show money like MySQL DECIMAL(10,2). NULL stays None."""
    if value is None or not any(w in column for w in ("amount", "total", "cost")):
        return value
    return f"{Decimal(str(value)):.2f}"


def examples() -> dict[str, tuple[str, str]]:
    return {c.id: parse(c) for c in chunk_documents([load_document(EXAMPLES_FILE, "example")])}


class FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.examples = examples()

    def setUp(self) -> None:
        self.db = FixtureRepository(rows=ROWS)

    def run_sql(self, sql: str) -> list[dict]:
        result = self.db.execute(sql)  # the real validator runs first
        return [{c: money(c, v) for c, v in zip(result.columns, row)} for row in result.rows]

    def run_example(self, number: int) -> list[dict]:
        question, sql = self.examples[f"example:{number}"]
        self.assertIsNone(qc.dropped_rows(question, sql), f"example {number} must keep the rows it asks for")
        return self.run_sql(sql)


class OptionalInvoices(FixtureCase):
    def test_all_appointments_keep_missing_invoices_as_null_and_zero_bills_as_zero(self) -> None:
        rows = self.run_example(201)  # all appointments, one row per treatment-bill match
        self.assertEqual({r["appointment_id"] for r in rows}, ALL_APPOINTMENTS)
        by = {(r["appointment_id"], r["treatment_id"], r["bill_id"]): r["invoice_amount"] for r in rows}
        self.assertIsNone(by[("A1", None, None)], "appointment without a treatment")
        self.assertIsNone(by[("A2", "T2", None)], "treatment without a bill")
        self.assertEqual(by[("A3", "T3", "B3")], "0.00", "a recorded zero-amount bill is not missing")
        self.assertEqual(sum(1 for k in by if k[0] == "A4"), 3, "one row per bill")
        self.assertIsNone(by[("A6", "T6b", None)], "the unbilled treatment of a partly billed appointment")
        self.assertEqual(by[("A6", "T6a", "B6a")], "70.00")

    def test_only_invoiced_appointments(self) -> None:
        rows = self.run_example(202)
        self.assertEqual({r["appointment_id"] for r in rows}, {"A3", "A4", "A5", "A6"})
        self.assertEqual(len(rows), 7, "one row per invoice, the zero-amount one included")
        self.assertIsNone(qc.dropped_rows(self.examples["example:202"][0], self.examples["example:202"][1]))

    def test_one_row_per_appointment_leaves_missing_totals_null(self) -> None:
        rows = {r["appointment_id"]: r for r in self.run_example(205)}
        self.assertEqual(set(rows), ALL_APPOINTMENTS)
        self.assertEqual((rows["A1"]["bill_count"], rows["A1"]["total_billed"]), (0, None))
        self.assertEqual((rows["A2"]["bill_count"], rows["A2"]["total_billed"]), (0, None))
        self.assertEqual((rows["A3"]["bill_count"], rows["A3"]["total_billed"]), (1, "0.00"))
        self.assertEqual((rows["A4"]["bill_count"], rows["A4"]["total_billed"]), (3, "350.00"))
        self.assertEqual((rows["A6"]["bill_count"], rows["A6"]["total_billed"]), (1, "70.00"))

    def test_zero_only_when_the_question_asks_for_it(self) -> None:
        rows = {r["appointment_id"]: r["total_billed"] for r in self.run_example(206)}
        self.assertEqual(rows["A1"], "0.00", "explicitly requested zero for a missing total")
        self.assertEqual(rows["A3"], "0.00")

    def test_no_invoice_is_distinguished_from_a_zero_invoice(self) -> None:
        states = {(r["appointment_id"], r["treatment_id"]): r["invoice_state"] for r in self.run_example(247)}
        self.assertEqual(states[("A1", None)], "No invoice")
        self.assertEqual(states[("A2", "T2")], "No invoice")
        self.assertEqual(states[("A3", "T3")], "Zero-amount invoice")
        self.assertEqual(states[("A6", "T6b")], "No invoice")

    def test_appointments_without_any_bill(self) -> None:
        self.assertEqual([r["appointment_id"] for r in self.run_example(159)], ["A1", "A2"],
                         "A6 has one billed treatment, so it is not unbilled")
        self.assertEqual([r["appointment_id"] for r in self.run_example(160)], ["A2"])

    def test_paid_filter_in_on_keeps_appointments(self) -> None:
        rows = self.run_example(209)  # every appointment, Paid bills only, NULL sum when none
        totals = {r["appointment_id"]: (r["paid_bill_count"], r["total_paid"]) for r in rows}
        self.assertEqual(set(totals), ALL_APPOINTMENTS)
        self.assertEqual(totals["A1"], (0, None))
        self.assertEqual(totals["A4"], (2, "250.00"))
        self.assertEqual(totals["A5"], (1, "100.00"), "the Failed bill is not counted")


class OneToManyCounts(FixtureCase):
    def test_distinct_treatments_and_bill_counts_per_appointment(self) -> None:
        rows = {r["appointment_id"]: (r["treatment_count"], r["bill_count"]) for r in self.run_example(214)}
        self.assertEqual(rows, {"A1": (0, 0), "A2": (1, 0), "A3": (1, 1), "A4": (2, 3), "A5": (2, 2), "A6": (2, 1)})

    def test_doctor_counts_include_doctors_with_none(self) -> None:
        rows = {r["doctor_id"]: (r["appointment_count"], r["treatment_count"], r["bill_count"]) for r in self.run_example(215)}
        self.assertEqual(rows, {"D1": (4, 5, 3), "D2": (2, 3, 4), "D3": (0, 0, 0)})

    def test_treatment_costs_are_not_duplicated_and_equal_costs_both_count(self) -> None:
        rows = {r["doctor_id"]: r["total_treatment_cost"] for r in self.run_example(216)}
        self.assertEqual(rows["D1"], "400.00", "40 + 100 + 100 + 70 + 90: the two equal 100 costs both count")
        self.assertIsNone(rows["D3"])
        wrong = ("SELECT d.doctor_id, SUM(t.cost) AS total FROM doctors AS d JOIN appointments AS a ON a.doctor_id = "
                 "d.doctor_id JOIN treatments AS t ON t.appointment_id = a.appointment_id JOIN billing AS b ON "
                 "b.treatment_id = t.treatment_id GROUP BY d.doctor_id")
        duplicated = {r["doctor_id"]: r["total"] for r in self.run_sql(wrong)}
        self.assertEqual(duplicated["D2"], "650.00", "joining bills repeats T4a's cost (the catalog warns about this)")
        distinct = ("SELECT a.doctor_id, SUM(DISTINCT t.cost) AS total FROM appointments AS a JOIN treatments AS t ON "
                    "t.appointment_id = a.appointment_id GROUP BY a.doctor_id")
        self.assertEqual({r["doctor_id"]: r["total"] for r in self.run_sql(distinct)}["D1"], "300.00",
                         "SUM(DISTINCT cost) loses one of two equal costs")

    def test_branch_only_versus_doctor_grouping(self) -> None:
        branch = {r["hospital_branch"]: r["total_paid"] for r in self.run_example(218)}
        self.assertEqual(branch, {"North Branch": "170.00", "South Branch": "250.00"})
        doctors = {r["doctor_id"]: (r["hospital_branch"], r["total_paid"]) for r in self.run_example(219)}
        self.assertEqual(doctors, {"D1": ("North Branch", "170.00"), "D2": ("South Branch", "250.00")})


class WrongShapesAreCaught(FixtureCase):
    Q = "List all appointments in June 2023 with the patient's name, the invoice amount and the hospital branch."
    BILLING_FIRST = (
        "SELECT a.appointment_id, CONCAT(p.first_name, ' ', p.last_name) AS patient_name, b.amount, d.hospital_branch "
        "FROM billing AS b JOIN treatments AS t ON b.treatment_id = t.treatment_id JOIN appointments AS a ON "
        "t.appointment_id = a.appointment_id JOIN patients AS p ON a.patient_id = p.patient_id JOIN doctors AS d ON "
        "a.doctor_id = d.doctor_id WHERE a.appointment_date >= '2023-06-01' AND a.appointment_date < '2023-07-01'")
    LEFT = (
        "SELECT a.appointment_id, CONCAT(p.first_name, ' ', p.last_name) AS patient_name, b.amount, d.hospital_branch "
        "FROM appointments AS a JOIN patients AS p ON a.patient_id = p.patient_id JOIN doctors AS d ON a.doctor_id = "
        "d.doctor_id LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id LEFT JOIN billing AS b ON "
        "b.treatment_id = t.treatment_id WHERE a.appointment_date >= '2023-06-01' AND a.appointment_date < '2023-07-01'")

    def test_the_billing_first_query_loses_appointments_and_is_flagged(self) -> None:
        lost = ALL_APPOINTMENTS - {r["appointment_id"] for r in self.run_sql(self.BILLING_FIRST)}
        self.assertEqual(lost, {"A1", "A2"}, "what the observed query shape silently drops")
        self.assertIn("starts FROM billing", qc.dropped_rows(self.Q, self.BILLING_FIRST))
        self.assertIsNone(qc.dropped_rows(self.Q, self.LEFT))
        self.assertEqual({r["appointment_id"] for r in self.run_sql(self.LEFT)}, ALL_APPOINTMENTS)

    def test_a_where_filter_on_billing_loses_appointments_and_is_flagged(self) -> None:
        sql = self.LEFT + " AND b.payment_status = 'Paid'"
        self.assertEqual({r["appointment_id"] for r in self.run_sql(sql)}, {"A3", "A4", "A5", "A6"})
        self.assertIn("WHERE condition", qc.dropped_rows("Show all appointments with their Paid invoice amounts", sql))


def stats():
    return GenerationStats(None, None, None, None, None, None, "stop")


class CorrectionPath(FixtureCase):
    """The pipeline with a scripted model: one correction for a query that drops requested rows, and a
    non-answer (nothing executed) if the correction still drops them."""

    def setUp(self) -> None:
        super().setUp()
        self.executed = []
        real = self.db.execute
        self.db.execute = lambda sql: (self.executed.append(sql), real(sql))[1]
        self.llm = mock.Mock()
        self.llm.check_budget.return_value = 100
        self.llm.resolve_message.side_effect = lambda *a, **k: MessageRouting(
            "data_question", WrongShapesAreCaught.Q, 0)
        vectors = mock.Mock()
        vectors.get_chunks_by_type.return_value = expected_schema_chunks()
        vectors.search.return_value = []
        embeddings = mock.Mock()
        embeddings.embed_query.return_value = [0.1]
        embeddings.model_name = "e"
        self.service = ChatService(vectors=vectors, embeddings=embeddings, llm=self.llm, database=self.db)

    def generation(self, sql):
        return SQLGeneration("sql", sql + ";", sql, False, "m", stats())

    def test_dropping_query_is_corrected_once_and_missing_invoices_stay_null(self) -> None:
        self.llm.generate_sql.return_value = self.generation(WrongShapesAreCaught.BILLING_FIRST)
        self.llm.fix_sql.return_value = self.generation(WrongShapesAreCaught.LEFT)
        trace = self.service.answer(WrongShapesAreCaught.Q)
        body = to_response(trace)
        self.assertEqual(body.status, "answered")
        self.assertEqual([a.error_code for a in trace.attempts], ["drops_unmatched_rows", None])
        self.assertEqual([q.rstrip(";") for q in self.executed], [WrongShapesAreCaught.LEFT], "the dropping query never ran")
        self.assertIn("The question asks for all appointments", self.llm.generate_sql.call_args.args[2],
                      "the requirement is in the facts before generation")
        self.assertIn("starts FROM billing", self.llm.fix_sql.call_args.args[3])
        amounts = {row[0]: row[2] for row in body.rows if row[0] in ("A1", "A2")}  # columns: id, name, amount, branch
        self.assertEqual(amounts, {"A1": None, "A2": None})
        self.assertEqual({row[0] for row in body.rows}, ALL_APPOINTMENTS)

    def test_a_correction_that_still_drops_rows_is_not_executed(self) -> None:
        self.llm.generate_sql.return_value = self.generation(WrongShapesAreCaught.BILLING_FIRST)
        self.llm.fix_sql.return_value = self.generation(WrongShapesAreCaught.LEFT + " AND b.payment_status = 'Paid'")
        trace = self.service.answer(WrongShapesAreCaught.Q)
        body = to_response(trace)
        self.assertEqual((body.status, body.code), ("rejected", "drops_unmatched_rows"))
        self.assertEqual(self.executed, [])
        self.assertFalse(body.sql_executed)
        self.assertEqual(body.rows, [])
        self.assertIn("not executed", body.message)
        self.assertEqual(self.llm.fix_sql.call_count, 1, "still only one correction")

    def test_only_invoiced_wording_keeps_the_inner_join(self) -> None:
        question = "List only the appointments in June 2023 that have invoices, with the invoice amount."
        self.llm.resolve_message.side_effect = lambda *a, **k: MessageRouting("data_question", question, 0)
        self.llm.generate_sql.return_value = self.generation(WrongShapesAreCaught.BILLING_FIRST)
        body = to_response(self.service.answer(question))
        self.assertEqual(body.status, "answered")
        self.llm.fix_sql.assert_not_called()
        self.assertNotIn("all appointments", self.llm.generate_sql.call_args.args[2])

    def test_the_users_all_survives_a_rewrite_that_drops_it(self) -> None:
        self.llm.resolve_message.side_effect = lambda *a, **k: MessageRouting(
            "data_question", "List appointments in June 2023 with the invoice amount.", 0)
        self.llm.generate_sql.return_value = self.generation(WrongShapesAreCaught.BILLING_FIRST)
        self.llm.fix_sql.return_value = self.generation(WrongShapesAreCaught.LEFT)
        body = to_response(self.service.answer("Show all appointments in June 2023 with the invoice amount"))
        self.assertEqual(body.status, "answered")
        self.assertEqual([q.rstrip(";") for q in self.executed], [WrongShapesAreCaught.LEFT])


if __name__ == "__main__":
    unittest.main()
