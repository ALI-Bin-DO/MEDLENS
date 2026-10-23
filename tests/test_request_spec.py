"""Tests for preserving user intent (backend/app/services/request_spec.py and its use in ChatService).

The request description comes from the question, the latest message and the previous turn; the checks
compare generated SQL with it before execution. Results are checked on an isolated SQLite fixture whose
appointment and invoice months differ, so an extra date filter changes the answer. No model, index or MySQL.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import unittest
from decimal import Decimal
from unittest import mock

from backend.app.config import EXAMPLES_FILE
from backend.app.controllers.chat_controller import _turn_to_keep, to_response
from backend.app.repositories.mysql_repository import QueryResult
from backend.app.services import clock, request_spec as rs, sql_correction
from backend.app.services.chat_service import ChatService
from backend.app.services.llm_service import GenerationStats, MessageRouting, SQLGeneration
from backend.app.services.rag.chunker import chunk_documents
from backend.app.services.rag.context_builder import expected_schema_chunks
from backend.app.services.rag.document_loader import load_document
from backend.scripts.check_examples import parse
from evaluation.fixture_db import MONTH_ROWS, FixtureRepository

_no_db = mock.patch("mysql.connector.connect", side_effect=AssertionError("tests must never connect to MySQL"))


def setUpModule() -> None:
    _no_db.start()


def tearDownModule() -> None:
    _no_db.stop()


APPT = ("SELECT a.appointment_id, b.bill_id, b.amount FROM appointments AS a JOIN treatments AS t ON "
        "t.appointment_id = a.appointment_id JOIN billing AS b ON b.treatment_id = t.treatment_id "
        "WHERE a.appointment_date >= '2023-06-01' AND a.appointment_date < '2023-07-01'")
APPT_AND_BILL = APPT + " AND b.bill_date >= '2023-06-01' AND b.bill_date < '2023-07-01'"
ISSUED = APPT.replace("a.appointment_date", "b.bill_date")
LEFT = ("SELECT a.appointment_id, b.amount FROM appointments AS a LEFT JOIN treatments AS t ON t.appointment_id = "
        "a.appointment_id LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id WHERE a.appointment_date >= "
        "'2023-06-01' AND a.appointment_date < '2023-07-01'")
TOTALS = ("SELECT a.appointment_id, COALESCE(SUM(b.amount), 0) AS total_billed FROM appointments AS a LEFT JOIN treatments AS t "
          "ON t.appointment_id = a.appointment_id LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id WHERE "
          "a.appointment_date >= '2023-06-01' AND a.appointment_date < '2023-07-01' GROUP BY a.appointment_id")


def rows(sql: str, data=MONTH_ROWS) -> list[tuple]:
    result = FixtureRepository(rows=data).execute(sql)
    return sorted((tuple(None if v is None else (f"{Decimal(str(v)):.2f}" if isinstance(v, (int, float)) else v)
                        for v in r) for r in result.rows), key=lambda r: tuple("" if v is None else v for v in r))


class DatesApplyToTheirOwnColumn(unittest.TestCase):
    Q_APPT = "List only the appointments in June 2023 that have an invoice, with the invoice amount."
    Q_ISSUED = "List the invoices issued in June 2023 with their appointment and amount."

    def test_the_period_is_assigned_to_the_column_it_describes(self) -> None:
        self.assertEqual([p.column for p in rs.build(self.Q_APPT).periods], ["appointment_date"])
        self.assertEqual([p.column for p in rs.build(self.Q_ISSUED).periods], ["bill_date"])
        self.assertEqual([p.column for p in rs.build("What was the total billed amount in February 2023?").periods], ["bill_date"])
        self.assertEqual([p.column for p in rs.build("Appointments of Dr. Lopez in January 2024 with invoice amounts").periods],
                         ["appointment_date"], "the dot in 'Dr.' is not the end of a sentence")
        both = rs.build("Patients registered in 2022 who had appointments in March 2023").periods
        self.assertEqual([p.column for p in both], ["registration_date", "appointment_date"])

    def test_an_extra_bill_date_filter_changes_the_result_and_is_flagged(self) -> None:
        self.assertNotEqual(rows(APPT), rows(APPT_AND_BILL), "the fixture makes the extra filter matter (B4a2)")
        self.assertIn(("A4", "B4a2", "100.00"), rows(APPT))
        problems = rs.mismatches(rs.build(self.Q_APPT), APPT_AND_BILL)
        self.assertTrue(problems and all("describes appointment_date, not bill_date" in p for p in problems), problems)
        self.assertEqual(rs.mismatches(rs.build(self.Q_APPT), APPT), [])

    def test_bill_dates_for_issued_invoices(self) -> None:
        self.assertIn(("A7", "B7", "60.00"), rows(ISSUED), "a May appointment billed in June")
        self.assertEqual(rs.mismatches(rs.build(self.Q_ISSUED), ISSUED), [])
        self.assertTrue(rs.mismatches(rs.build(self.Q_ISSUED), APPT), "appointment_date is not what was asked")

    def test_dates_not_in_the_question_are_flagged(self) -> None:
        copied = "SELECT COUNT(*) FROM appointments WHERE appointment_date >= '2023-06-01' AND appointment_date < '2023-07-01'"
        self.assertIn("names no such date", rs.mismatches(rs.build("How many appointments are there?"), copied)[0])


class AmountsAndMissingInvoices(unittest.TestCase):
    Q = "Show the June 2023 appointments with their bill amounts, including appointments nobody was billed for."

    def test_individual_amounts_with_missing_ones_null(self) -> None:
        spec = rs.build(self.Q)
        self.assertTrue(spec.individual_amounts)
        self.assertFalse(spec.zero_fill)
        self.assertEqual(spec.keep_all, "appointments")
        self.assertEqual(rs.mismatches(spec, LEFT), [])
        result = rows(LEFT)
        self.assertIn(("A1", None), result, "no treatment: NULL, not 0")
        self.assertIn(("A3", "0.00"), result, "a real zero-amount bill stays 0")
        self.assertEqual(sum(1 for r in result if r[0] == "A4"), 3, "one row per invoice")

    def test_totals_and_zero_fill_are_flagged_unless_asked(self) -> None:
        problems = " ".join(rs.mismatches(rs.build(self.Q), TOTALS))
        self.assertIn("totals the invoice amounts", problems)
        self.assertIn("turns a missing amount into 0", problems)
        self.assertIn(("A1", "0.00"), rows(TOTALS), "what the flagged query would have shown")
        asked = rs.build("What is the total billed per appointment in June 2023, writing 0 where there are no bills?")
        self.assertFalse(asked.individual_amounts)
        self.assertTrue(asked.zero_fill)
        self.assertEqual(rs.mismatches(asked, TOTALS), [])

    def test_paraphrases_of_appointments(self) -> None:
        for q in ["Show the June 2023 visits with their bill amounts, including visits nobody was billed for.",
                  "List every consultation in June 2023 with the invoice amount", "all physicians and their branches"]:
            with self.subTest(q):
                self.assertIsNotNone(rs.build(q).keep_all)
        self.assertEqual(rs.build("How many emergency appointments does each doctor have, including doctors with none?").keep_all,
                         "doctors")
        self.assertTrue(rs.mentions_visits_without_status("June 2023 visits with bill amounts"))
        self.assertFalse(rs.mentions_visits_without_status("completed visits in June 2023"))
        self.assertFalse(rs.mentions_visits_without_status("How many appointments per reason for visit?"))
        self.assertEqual(rs.status_answer("All of them, any status"), "appointments of any status")
        self.assertEqual(rs.status_answer("only completed ones"), "Completed appointments only")


class Grouping(unittest.TestCase):
    PREVIOUS = "How many completed appointments does each doctor have?"

    def change(self, message):
        return rs.grouping_change(message, rs.grouping_dims(self.PREVIOUS))

    def test_replace_add_and_remove_are_distinguished(self) -> None:
        cases = {
            "Do it by hospital branch": ("replace", ("hospital branch",), ("doctor",)),
            "Make that by specialization": ("replace", ("specialization",), ("doctor",)),
            "by month instead of by doctor": ("replace", ("month",), ("doctor",)),
            "Also break it down by status": ("add", ("doctor", "status"), ()),
            "and by hospital branch too": ("add", ("doctor", "hospital branch"), ()),
            "Split that further by appointment status": ("add", ("doctor", "status"), ()),
            "Now per treatment type instead": ("replace", ("treatment type",), ("doctor",)),
            "without the doctor breakdown": ("remove", (), ("doctor",)),
            "overall": ("remove", (), ("doctor",)),
        }
        for message, (operation, dims, removed) in cases.items():
            with self.subTest(message):
                change = self.change(message)
                self.assertEqual((change.operation, change.dims, change.removed), (operation, dims, removed))
        self.assertEqual(rs.grouping_change("Split that by patient gender as well", ("payment method",)).dims,
                         ("payment method", "gender"), "'patient gender' is one dimension")
        self.assertEqual(rs.grouping_change("Drop the payment status split", ("payment method", "payment status")).dims,
                         ("payment method",))
        for message in ["How many bills per payment method in 2023?", "Thanks", "only completed ones", "Dr. Taylor",
                        "And in the month before?", "And the month before that?", "what about the next year"]:
            with self.subTest(message):
                self.assertIsNone(self.change(message), "a new question or another kind of follow-up")

    def test_the_change_is_applied_when_the_resolver_ignores_it(self) -> None:
        replaced = rs.apply_grouping_change(self.PREVIOUS, self.change("Do it by hospital branch"), self.PREVIOUS)
        self.assertEqual(rs.grouping_dims(replaced), ("hospital branch",))
        self.assertIn("completed", replaced, "the measure and its filter are kept")
        added = rs.apply_grouping_change(self.PREVIOUS, self.change("Also break it down by status"), self.PREVIOUS)
        self.assertEqual(set(rs.grouping_dims(added)), {"doctor", "status"})
        good = "How many completed appointments are there in each hospital branch?"
        self.assertEqual(rs.apply_grouping_change(self.PREVIOUS, self.change("Do it by hospital branch"), good), good)

    def test_group_by_must_follow_the_request(self) -> None:
        change = self.change("Do it by hospital branch")
        spec = rs.build("How many completed appointments does each hospital branch have?", "Do it by hospital branch",
                        change=change)
        by_doctor = ("SELECT d.doctor_id, COUNT(a.appointment_id) FROM doctors AS d LEFT JOIN appointments AS a ON "
                     "a.doctor_id = d.doctor_id AND a.status = 'Completed' GROUP BY d.doctor_id")
        by_branch = ("SELECT d.hospital_branch, COUNT(*) FROM appointments AS a JOIN doctors AS d ON a.doctor_id = "
                     "d.doctor_id WHERE a.status = 'Completed' GROUP BY d.hospital_branch")
        both = by_branch.replace("GROUP BY d.hospital_branch", "GROUP BY d.hospital_branch, d.doctor_id")
        self.assertEqual(len(rs.mismatches(spec, by_doctor)), 2)
        self.assertEqual(rs.mismatches(spec, by_branch), [])
        self.assertIn("still groups by doctor", rs.mismatches(spec, both)[0])
        added = rs.build("How many completed appointments does each doctor have, broken down by status?", change=self.change(
            "Also break it down by status"))
        self.assertEqual(rs.mismatches(added, by_doctor.replace("GROUP BY d.doctor_id", "GROUP BY d.doctor_id, a.status")), [])


class CorrectionGuard(unittest.TestCase):
    def test_a_filter_moved_into_conditional_aggregation_is_kept(self) -> None:
        before = ("SELECT p.gender, SUM(b.amount) FROM billing AS b JOIN patients AS p ON b.patient_id = p.patient_id "
                  "WHERE b.payment_status = 'Paid' GROUP BY p.gender")
        moved = ("SELECT p.gender, b.payment_method, SUM(CASE WHEN b.payment_status = 'Paid' THEN b.amount END) FROM billing "
                 "AS b JOIN patients AS p ON b.patient_id = p.patient_id GROUP BY p.gender, b.payment_method")
        self.assertIsNone(sql_correction.meaning_change(before, moved))
        self.assertIn("'Paid'", sql_correction.meaning_change(before, moved.replace("'Paid'", "'Failed'")))
        self.assertIn("'Paid'", sql_correction.meaning_change(before, moved.replace("CASE WHEN b.payment_status = 'Paid' THEN "
                                                                                      "b.amount END", "b.amount")))


class FiltersAndCounts(unittest.TestCase):
    def test_values_must_come_from_the_request(self) -> None:
        spec = rs.build("How many appointments does each doctor have?")
        copied = ("SELECT a.doctor_id, COUNT(*) FROM appointments AS a WHERE a.status = 'Completed' AND a.doctor_id = 'D004' "
                  "GROUP BY a.doctor_id")
        problems = " ".join(rs.mismatches(spec, copied))
        self.assertIn("'Completed'", problems)
        self.assertIn("'D004'", problems)
        grounded = rs.build("Appointments of Dr. Mia", grounded="doctors.doctor_id = 'X1'")
        self.assertEqual(rs.mismatches(grounded, "SELECT appointment_id FROM appointments WHERE doctor_id = 'X1'"), [])
        self.assertIn("threshold 30", rs.mismatches(rs.build("Which bills are large?"), "SELECT bill_id FROM billing WHERE amount > 30")[0])
        self.assertEqual(rs.mismatches(rs.build("Which female patients have bills above 4,500?"),
                                       "SELECT p.patient_id FROM billing AS b JOIN patients AS p ON b.patient_id = p.patient_id "
                                       "WHERE p.gender = 'F' AND b.amount > 4500"), [])

    def test_counting_another_tables_key(self) -> None:
        q = "How many treatments of each treatment type did patients insured by MedCare Plus receive?"
        spec = rs.build(q)
        self.assertEqual(spec.counts, "treatments")
        wrong = ("SELECT t.treatment_type, COUNT(DISTINCT p.patient_id) FROM treatments AS t JOIN appointments AS a ON "
                 "t.appointment_id = a.appointment_id JOIN patients AS p ON a.patient_id = p.patient_id WHERE "
                 "p.insurance_provider = 'MedCare Plus' GROUP BY t.treatment_type")
        self.assertIn("counts patients", rs.mismatches(spec, wrong)[0])
        self.assertEqual(rs.mismatches(spec, wrong.replace("COUNT(DISTINCT p.patient_id)", "COUNT(*)")), [])
        self.assertIn("patients.insurance_provider; from treatments join with treatments.appointment_id", spec.describe())

    def test_count_and_grouping_shapes_that_cannot_answer_the_question(self) -> None:
        spec = rs.build("How many completed appointments does each hospital branch have?")
        self.assertEqual(spec.counts, "appointments", "modifiers between 'how many' and the noun")
        self.assertIn("counts patients", rs.mismatches(spec, "SELECT d.hospital_branch, COUNT(DISTINCT a.patient_id) FROM "
                      "appointments AS a JOIN doctors AS d ON a.doctor_id = d.doctor_id WHERE a.status = 'Completed' "
                      "GROUP BY d.hospital_branch")[0])
        always_one = "SELECT COUNT(DISTINCT b.patient_id) AS n FROM billing AS b GROUP BY b.patient_id"
        self.assertIn("every count is 1", rs.mismatches(rs.build("How many different patients have at least one bill?"),
                                                        always_one)[0])
        one_total = "SELECT SUM(b.amount) FROM billing AS b"
        self.assertIn("one overall value", rs.mismatches(rs.build("What is the total billed per payment method?"), one_total)[0])
        self.assertEqual(rs.mismatches(rs.build("What is the average number of appointments per patient?"),
                                       "SELECT COUNT(*) / COUNT(DISTINCT patient_id) FROM appointments"), [], "a ratio")

    def test_inclusive_range_end(self) -> None:
        q = "How many bills were issued from 15 March 2023 to 15 April 2023, including both days?"
        spec = rs.build(q)
        self.assertEqual([(p.start.isoformat(), p.end.isoformat(), p.column) for p in spec.periods],
                         [("2023-03-15", "2023-04-16", "bill_date")], "the two days are one range")
        cut = "SELECT COUNT(*) FROM billing WHERE bill_date >= '2023-03-15' AND bill_date < '2023-04-15'"
        self.assertIn("excludes the last day", rs.mismatches(spec, cut)[0])
        self.assertEqual(rs.mismatches(spec, cut.replace("< '2023-04-15'", "< '2023-04-16'")), [])
        self.assertEqual(rs.mismatches(rs.build("Appointments before 15 June 2023"),
                                       "SELECT COUNT(*) FROM appointments WHERE appointment_date < '2023-06-15'"), [])
        self.assertIn("bill_date >= '2023-03-15' AND bill_date < '2023-04-16'", spec.label_ranges(clock.period_ranges(q)))

    def test_no_knowledge_base_example_is_flagged(self) -> None:
        for chunk in chunk_documents([load_document(EXAMPLES_FILE, "example")]):
            question, sql = parse(chunk)
            with self.subTest(chunk.id):
                self.assertEqual(rs.mismatches(rs.build(question), sql), [])

    def test_unknown_column_diagnostic_names_where_each_column_is(self) -> None:
        sql = ("SELECT treatment_type, COUNT(DISTINCT t.patient_id) FROM treatments t JOIN billing b ON t.treatment_id = "
               "b.treatment_id WHERE b.insurance_provider = 'MedCare Plus' GROUP BY treatment_type")
        text = sql_correction.validation_diagnostic("unknown_column", "table 'treatments' has no column 'patient_id'", sql)
        self.assertIn("insurance_provider is a column of patients", text)
        self.assertIn("from treatments, join appointments with treatments.appointment_id = appointments.appointment_id", text)
        joined = ("SELECT COUNT(DISTINCT t.patient_id) FROM treatments t JOIN appointments a ON t.appointment_id = "
                  "a.appointment_id JOIN patients p ON a.patient_id = p.patient_id")
        self.assertIn("use a.patient_id or p.patient_id", sql_correction.validation_diagnostic(
            "unknown_column", "table 'treatments' has no column 'patient_id'", joined))
        unjoined = "SELECT a.patient_id, p.first_name FROM appointments a JOIN doctors ON a.doctor_id = doctors.doctor_id"
        self.assertIn("p is not in FROM/JOIN; join patients AS p with appointments.patient_id = patients.patient_id",
                      sql_correction.validation_diagnostic("unknown_alias", "'p' in p.first_name is not a table or alias",
                                                           unjoined))


# --- the pipeline with a scripted model ---------------------------------------------------------

def stats():
    return GenerationStats(None, None, None, None, None, None, "stop")


def gen(sql):
    return SQLGeneration("sql", sql + ";", sql, False, "m", stats())


class Pipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FixtureRepository(rows=MONTH_ROWS)
        self.executed = []
        real = self.db.execute
        self.db.execute = lambda sql: (self.executed.append(sql), real(sql))[1]
        self.llm = mock.Mock()
        self.llm.check_budget.return_value = 100
        vectors = mock.Mock()
        vectors.get_chunks_by_type.return_value = expected_schema_chunks()
        vectors.search.return_value = []
        embeddings = mock.Mock(model_name="e")
        embeddings.embed_query.return_value = [0.1]
        self.service = ChatService(vectors=vectors, embeddings=embeddings, llm=self.llm, database=self.db)
        self.history = []

    def ask(self, message, resolved, sql, fixed=None):
        self.llm.resolve_message.side_effect = lambda *a, **k: MessageRouting("data_question", resolved, len(self.history))
        self.llm.generate_sql.return_value = gen(sql)
        if fixed is not None:
            self.llm.fix_sql.return_value = gen(fixed)
        trace = self.service.answer(message, list(self.history))
        turn = _turn_to_keep(trace)
        if turn is not None:
            self.history.append(turn)
        return trace, to_response(trace)

    def test_grouping_replacement_survives_a_resolver_that_ignores_it(self) -> None:
        q1 = "How many completed appointments does each doctor have?"
        by_doctor = ("SELECT d.doctor_id, COUNT(a.appointment_id) AS n FROM doctors AS d LEFT JOIN appointments AS a ON "
                     "a.doctor_id = d.doctor_id AND a.status = 'Completed' GROUP BY d.doctor_id")
        self.ask(q1, q1, by_doctor)
        self.assertEqual(self.history[-1].grouping, ("doctor",))
        by_branch = ("SELECT d.hospital_branch, COUNT(*) AS n FROM appointments AS a JOIN doctors AS d ON a.doctor_id = "
                     "d.doctor_id WHERE a.status = 'Completed' GROUP BY d.hospital_branch")
        trace, body = self.ask("Do it by hospital branch", q1, by_doctor, fixed=by_branch)  # resolver repeats q1
        self.assertEqual(rs.grouping_dims(trace.resolved_question), ("hospital branch",))
        self.assertIn("Group by hospital branch only, not doctor", self.llm.generate_sql.call_args.args[2])
        self.assertEqual([a.error_code for a in trace.attempts], ["request_mismatch", None])
        self.assertEqual(body.status, "answered")
        self.assertEqual(body.columns[0], "hospital_branch")

    def test_extra_date_filter_is_corrected_once_or_not_executed(self) -> None:
        q = "List only the appointments in June 2023 that have an invoice, with the invoice amount."
        trace, body = self.ask(q, q, APPT_AND_BILL, fixed=APPT)
        self.assertEqual((body.status, [a.error_code for a in trace.attempts]), ("answered", ["request_mismatch", None]))
        self.assertIn(["A4", "B4a2", 100], body.rows)
        self.assertNotIn(APPT_AND_BILL + ";", self.executed)
        self.executed.clear()
        trace, body = self.ask(q, q, APPT_AND_BILL, fixed=APPT_AND_BILL)
        self.assertEqual((body.status, body.code), ("rejected", "request_mismatch"))
        self.assertEqual(self.executed, [], "a query known not to match is never run")
        self.assertIn("does not match what was asked", body.message)

    def test_visits_are_clarified_when_statuses_differ_then_answered(self) -> None:
        q = "Show the June 2023 visits with their bill amounts, including visits nobody was billed for."
        trace, body = self.ask(q, q, LEFT)
        self.assertEqual(body.status, "needs_clarification")
        self.assertIn("Scheduled, Cancelled or No-show", body.message)
        self.llm.generate_sql.assert_not_called()
        trace, body = self.ask("All of them, any status", q, TOTALS, fixed=LEFT)  # resolver forgets the answer
        self.assertIn("appointments of any status", trace.resolved_question)
        self.assertEqual([a.error_code for a in trace.attempts], ["request_mismatch", None])
        self.assertIn(["A1", None], body.rows)
        self.assertEqual(body.status, "answered")

    def test_visits_need_no_question_when_every_appointment_in_scope_is_completed(self) -> None:
        q = "Show the visits on 1 June 2023 with their bill amounts."
        sql = LEFT.replace("< '2023-07-01'", "< '2023-06-02'")
        trace, body = self.ask(q, q, sql)
        self.assertEqual(body.status, "answered")
        self.assertTrue(any("status <> 'Completed'" in s for s in self.executed), "bounded read-only count ran first")


if __name__ == "__main__":
    unittest.main()
