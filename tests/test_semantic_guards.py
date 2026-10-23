"""Offline regression tests for the observed routing, name, join, context and date failures.
No model, index or MySQL: the pipeline runs with fakes, and a fake database answers name lookups.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import datetime as dt
import unittest
from unittest import mock

from backend.app.controllers.chat_controller import _turn_to_keep, to_response
from backend.app.repositories.mysql_repository import QueryResult
from backend.app.services import clock, entity_grounding as eg, question_checks as qc
from backend.app.services import sql_validator as v
from backend.app.services.chat_service import ChatService
from backend.app.services.llm_service import GenerationStats, MessageRouting, SQLGeneration
from backend.app.services.rag.context_builder import expected_schema_chunks

OBSERVED_BAD_SQL = (
    "SELECT a.appointment_id, CONCAT(p.first_name, ' ', p.last_name) AS patient_name, b.amount AS invoice_amount, "
    "d.hospital_branch FROM appointments AS a JOIN doctors AS d ON a.doctor_id = d.doctor_id JOIN patients AS p "
    "ON a.patient_id = p.patient_id JOIN billing AS b ON a.appointment_id = b.treatment_id WHERE d.first_name = "
    "'David' AND d.last_name = 'Dr.' AND a.appointment_date >= '2026-10-01' AND a.appointment_date < '2026-11-01'")


class JoinKeys(unittest.TestCase):
    def test_the_observed_appointment_to_bill_join_is_rejected_before_execution(self) -> None:
        result = v.validate(OBSERVED_BAD_SQL)
        self.assertEqual(result.code, v.INVALID_JOIN_KEY)
        self.assertIn("billing.treatment_id = treatments.treatment_id", result.reason, "the diagnostic lists valid keys")

    def test_schema_paths_and_shared_keys_are_accepted(self) -> None:
        for sql in [
            "SELECT b.amount FROM appointments AS a LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id "
            "LEFT JOIN billing AS b ON b.treatment_id = t.treatment_id",
            "SELECT p.first_name FROM billing AS b JOIN patients AS p ON p.patient_id = b.patient_id",
            "SELECT a.appointment_id FROM appointments AS a JOIN billing AS b ON b.patient_id = a.patient_id",  # same key
            "SELECT d.doctor_id FROM doctors AS d LEFT JOIN appointments AS a ON a.doctor_id = d.doctor_id AND a.status = 'Completed'",
        ]:
            with self.subTest(sql):
                self.assertTrue(v.validate(sql).ok, v.validate(sql).reason)

    def test_unrelated_or_unestablished_joins_fail_safely(self) -> None:
        cases = {
            "SELECT 1 FROM treatments AS t JOIN appointments AS a ON t.treatment_id = a.appointment_id": v.INVALID_JOIN_KEY,
            "SELECT 1 FROM doctors AS d JOIN patients AS p ON d.doctor_id = p.patient_id": v.INVALID_JOIN_KEY,
            "SELECT 1 FROM appointments AS a JOIN treatments AS t ON t.appointment_id = a.appointment_id WHERE a.doctor_id = t.cost": v.INVALID_JOIN_KEY,
            "SELECT 1 FROM billing AS b JOIN treatments AS t ON b.bill_date = t.treatment_date": v.JOIN_WITHOUT_KEY,
            "SELECT 1 FROM appointments AS a JOIN doctors AS d ON 1 = 1": v.JOIN_WITHOUT_KEY,
            "SELECT 1 FROM appointments AS a JOIN doctors AS d ON a.doctor_id = d.doctor_id OR a.status = 'x'": v.JOIN_WITHOUT_KEY,
        }
        for sql, code in cases.items():
            with self.subTest(sql):
                self.assertEqual(v.validate(sql).code, code)

    def test_keys_come_from_the_schema_file(self) -> None:
        keys = v.schema_keys()
        self.assertEqual(keys[("billing", "treatment_id")], ("treatments", "treatment_id"))
        self.assertEqual(keys[("appointments", "appointment_id")], ("appointments", "appointment_id"))
        self.assertNotIn(("doctors", "email"), keys, "unique columns are not join keys")

    def test_join_errors_get_the_single_bounded_correction(self) -> None:
        from backend.app.services import sql_correction
        self.assertTrue(sql_correction.is_retryable_validation(v.INVALID_JOIN_KEY))
        self.assertEqual(sql_correction.MAX_CORRECTIONS, 1)


class KeepAllRows(unittest.TestCase):
    INNER = ("SELECT a.appointment_id, b.amount FROM appointments AS a JOIN treatments AS t ON t.appointment_id = "
             "a.appointment_id JOIN billing AS b ON b.treatment_id = t.treatment_id WHERE a.doctor_id = 'X1'")
    LEFT = INNER.replace(" JOIN treatments", " LEFT JOIN treatments").replace(" JOIN billing", " LEFT JOIN billing")

    Q = "List all appointments of Dr. Mia with the invoice amount"
    OBSERVED_BILLING_FIRST = (  # the shape the model produced for the real all-appointments question
        "SELECT a.appointment_id, CONCAT(p.first_name, ' ', p.last_name) AS patient_name, b.amount, d.hospital_branch "
        "FROM billing AS b JOIN treatments AS t ON b.treatment_id = t.treatment_id JOIN appointments AS a ON "
        "t.appointment_id = a.appointment_id JOIN patients AS p ON a.patient_id = p.patient_id JOIN doctors AS d ON "
        "a.doctor_id = d.doctor_id WHERE a.doctor_id = 'X1' AND a.appointment_date >= '2026-10-01'")

    def test_all_rows_requested_but_inner_join_on_dependent_tables(self) -> None:
        self.assertIn("INNER JOIN with treatments drops appointments", qc.dropped_rows(self.Q, self.INNER))
        self.assertIsNone(qc.dropped_rows(self.Q, self.LEFT))
        self.assertIsNone(qc.dropped_rows("List appointments of Dr. Mia with the invoice amount", self.INNER),
                          "without 'all' the inner join is not assumed wrong")
        parents = "SELECT a.appointment_id FROM appointments AS a JOIN patients AS p ON p.patient_id = a.patient_id"
        self.assertIsNone(qc.dropped_rows("Show all appointments with the patient name", parents), "parents never drop rows")
        doctors = "SELECT d.doctor_id, COUNT(*) FROM doctors AS d JOIN appointments AS a ON a.doctor_id = d.doctor_id GROUP BY d.doctor_id"
        self.assertIn("all doctors", qc.dropped_rows("Count appointments for doctors, including doctors without any", doctors))

    def test_whichever_table_comes_first(self) -> None:
        self.assertIn("starts FROM billing", qc.dropped_rows(self.Q, self.OBSERVED_BILLING_FIRST))
        self.assertIn("starts FROM billing", qc.dropped_rows(
            "all appointments with Dr. David Taylor for this month, including the patient's name, the invoice amount, "
            "and the hospital branch.", self.OBSERVED_BILLING_FIRST))
        via_parent = ("SELECT a.appointment_id FROM doctors AS d JOIN appointments AS a ON a.doctor_id = d.doctor_id "
                      "LEFT JOIN treatments AS t ON t.appointment_id = a.appointment_id WHERE d.last_name = 'Stone'")
        self.assertIsNone(qc.dropped_rows(self.Q, via_parent), "every appointment has its doctor")
        right = ("SELECT a.appointment_id, b.amount FROM billing AS b JOIN treatments AS t ON b.treatment_id = "
                 "t.treatment_id RIGHT JOIN appointments AS a ON t.appointment_id = a.appointment_id")
        self.assertIsNone(qc.dropped_rows(self.Q, right))
        shared_key = "SELECT a.appointment_id FROM appointments AS a JOIN billing AS b ON b.patient_id = a.patient_id"
        self.assertIn("INNER JOIN with billing", qc.dropped_rows(self.Q, shared_key))

    def test_where_conditions_on_optional_tables_null_reject(self) -> None:
        self.assertIn("WHERE condition b.payment_status = 'Paid'", qc.dropped_rows(self.Q, self.LEFT + " AND b.payment_status = 'Paid'"))
        for keeps in [" AND b.bill_id IS NULL", " AND COALESCE(b.amount, 0) = 0", " AND a.appointment_date >= '2023-01-01'"]:
            with self.subTest(keeps):
                self.assertIsNone(qc.dropped_rows(self.Q, self.LEFT + keeps))
        in_on = self.LEFT.replace("b.treatment_id = t.treatment_id", "b.treatment_id = t.treatment_id AND b.payment_status = 'Paid'")
        self.assertIsNone(qc.dropped_rows(self.Q, in_on))

    def test_only_matching_rows_and_other_wording(self) -> None:
        for q in ["List only appointments that have invoices", "Show only the appointments with a Paid bill",
                  "all appointments that have an invoice", "Show appointments having a bill"]:
            with self.subTest(q):
                self.assertIsNone(qc.requested_table(q))
                self.assertIsNone(qc.dropped_rows(q, self.INNER))
        for q, table in [("all of Dr. David Taylor's appointments this month", "appointments"),
                         ("every appointment with the invoice amount", "appointments"),
                         ("appointments in June, including those without invoices", "appointments"),
                         ("How many appointments does every doctor have, including doctors with no appointments?", "doctors"),
                         ("Which doctors had no visits, including doctors with no appointments at all?", "doctors"),
                         ("all bills of patients", None), ("count every appointment status", None)]:
            with self.subTest(q):
                self.assertEqual(qc.requested_table(q), table)
        self.assertIn("start FROM appointments", qc.keep_all_hint(self.Q).replace("Start", "start"))
        self.assertEqual(qc.keep_all_hint("Total paid per branch"), "")

    def test_no_knowledge_base_example_is_flagged(self) -> None:
        from backend.scripts.check_examples import parse
        from backend.app.config import EXAMPLES_FILE
        from backend.app.services.rag.chunker import chunk_documents
        from backend.app.services.rag.document_loader import load_document
        for chunk in chunk_documents([load_document(EXAMPLES_FILE, "example")]):
            question, sql = parse(chunk)
            with self.subTest(chunk.id):
                self.assertIsNone(qc.dropped_rows(question, sql))


class RowLevelGrouping(unittest.TestCase):
    def test_single_table_aggregate_grouped_by_its_primary_key_is_rejected(self) -> None:
        sql = "SELECT SUM(b.amount) AS total FROM billing AS b WHERE b.bill_date >= '2023-12-01' GROUP BY b.bill_id"
        self.assertEqual(v.validate(sql).code, v.ROW_LEVEL_GROUP_BY)
        for ok in ["SELECT payment_method, SUM(amount) FROM billing GROUP BY payment_method",
                   "SELECT patient_id, COUNT(*) FROM appointments GROUP BY patient_id",
                   "SELECT d.doctor_id, COUNT(a.appointment_id) FROM doctors AS d LEFT JOIN appointments AS a "
                   "ON a.doctor_id = d.doctor_id GROUP BY d.doctor_id"]:
            self.assertTrue(v.validate(ok).ok, ok)


class Names(unittest.TestCase):
    def test_honorifics_are_not_names(self) -> None:
        for raw, clean in [("Dr. David", "David"), ("Dr David", "David"), ("Doctor Lee Smith", "Lee Smith"),
                           ("Prof. Ana", "Ana"), ("Mrs. O-Neil", "O-Neil"), ("d004", "D004"), ("Dr.", None),
                           ("Robert'); DROP TABLE x", None), ("a b c d", None)]:
            with self.subTest(raw):
                self.assertEqual(eg.clean_name(raw), clean)

    def test_titled_names_are_found_in_the_question_without_inventing_a_surname(self) -> None:
        mentions = eg.mentions_from("List all appointments with Dr. David for October 2026")
        self.assertEqual([(m.role, m.text) for m in mentions], [("doctor", "David")])
        self.assertEqual(eg.mentions_from("How many appointments does each doctor have?"), [])

    def test_lookups_are_bounded_and_pass_the_validator(self) -> None:
        for mention in (eg.Mention("doctor", "David"), eg.Mention("doctor", "David Jones"), eg.Mention("patient", "P005")):
            sql = eg.lookup_sql(mention)
            with self.subTest(sql):
                self.assertTrue(v.validate(sql).ok, v.validate(sql).reason)
                self.assertIn("LIMIT 20", sql)

    def test_several_matches_are_listed_and_nothing_is_picked(self) -> None:
        a = eg.PersonMatch("X1", "Mia", "Stone", ("Cardiology", "North"))
        b = eg.PersonMatch("X2", "Mia", "Reyes", ("Oncology", "South"))
        text = eg.clarification([eg.Mention("doctor", "Mia", [a, b])])
        self.assertIn("X1 Mia Stone (Cardiology, North)", text)
        self.assertIn("X2 Mia Reyes", text)
        self.assertIn("couldn't find a doctor named \"Kim\"", eg.clarification([eg.Mention("doctor", "Kim", [])]))
        self.assertIsNone(eg.clarification([eg.Mention("doctor", "Mia Stone", [a])]))

    def test_only_the_users_own_words_pick_a_candidate(self) -> None:
        offered = [("doctor", eg.PersonMatch("X1", "Mia", "Stone")), ("doctor", eg.PersonMatch("X2", "Mia", "Reyes"))]
        self.assertEqual(len(eg.pick("Mia", offered)), 2)
        self.assertEqual([p.id for _, p in eg.pick("Reyes", offered)], ["X2"])
        self.assertEqual([p.id for _, p in eg.pick("x1", offered)], ["X1"])
        self.assertEqual(eg.pick("the first one", offered), [])


class QuestionChecks(unittest.TestCase):
    def test_payment_timing_needs_the_user_to_accept_bill_date(self) -> None:
        for q in ["How many bills were paid during March 2023?", "When were the paid bills paid?",
                  "When did patient P005 pay their bills?", "What is the payment date of each bill?"]:
            with self.subTest(q):
                self.assertIn("bill_date", qc.check(q))
        for q in ["How many paid bills were issued in March 2023?", "What is the total paid amount?",
                  "When were the paid bills issued?", "Use bill_date: how many bills were paid in March 2023?"]:
            with self.subTest(q):
                self.assertIsNone(qc.check(q))

    def test_undefined_measures_are_clarified_and_defined_ones_pass(self) -> None:
        self.assertIn("operating ratio", qc.check("Create a donut chart showing the operating ratio for each hospital, based on revenue."))
        self.assertIn("revenue", qc.check("Show revenue per branch"))
        self.assertIn("cannot be calculated", qc.check("What is the profit of each doctor?"))
        self.assertIsNone(qc.check("Create a donut chart for the operational volume of each hospital, based on the "
                                   "number of patients with a bill exceeding 30."))
        self.assertIsNone(qc.check("Workload of each doctor measured by the number of appointments"))


class Dates(unittest.TestCase):
    def test_exact_ranges_are_computed_by_the_application(self) -> None:
        self.assertIn("March 2023: col >= '2023-03-01' AND col < '2023-04-01'", clock.period_ranges("in March 2023"))
        self.assertIn("Q2 2023: col >= '2023-04-01' AND col < '2023-07-01'", clock.period_ranges("second quarter of 2023"))
        both = clock.period_ranges("from 15 March 2023 to 15 April 2023, including both days")
        self.assertIn("col >= '2023-03-15' AND col < '2023-04-16'", both)
        self.assertNotIn("< '2023-04-15'", both)
        separate = clock.period_ranges("Bills on 1 June 2023 and on 3 June 2023")
        self.assertIn("2023-06-03: col >= '2023-06-03' AND col < '2023-06-04'", separate)
        self.assertNotIn("both days included", separate, "two separate days are not a range")
        self.assertIn("December 2024: col >= '2024-12-01' AND col < '2025-01-01'", clock.period_ranges("December 2024"))
        self.assertEqual(clock.period_ranges("How many patients are there?"), "")

    def test_the_month_before_january_is_december_of_the_previous_year(self) -> None:
        previous = "How many bills were issued in January 2023?"
        self.assertEqual(clock.shift_from_history("What about the previous month?", previous,
                                                  "How many bills were issued in December 2023?"),
                         "How many bills were issued in December 2022?")
        self.assertEqual(clock.shift_from_history("and the next month?", "Visits in December 2023", "Visits in December 2023"),
                         "Visits in January 2024")
        self.assertEqual(clock.shift_from_history("only completed ones", previous, "x"), "x", "no shift phrase, no change")
        self.assertIn("December 2022", clock.period_neighbours([previous]))

    def test_this_month_names_the_year(self) -> None:
        text = clock.calendar_context(dt.datetime(2024, 1, 15, tzinfo=dt.timezone.utc))
        self.assertIn("This month: January 2024 = dates from 2024-01-01 up to but not including 2024-02-01", text)


# --- pipeline with fakes -------------------------------------------------------------------

def stats():
    return GenerationStats(None, None, None, None, None, None, "stop")


class FakeDB:
    """Answers name lookups from a small doctor list and records every query."""

    DOCTORS = [("X1", "Mia", "Stone", "Cardiology", "North"), ("X2", "Mia", "Reyes", "Oncology", "South"),
               ("X3", "Omar", "Lane", "Cardiology", "North")]

    def __init__(self):
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        if "FROM doctors WHERE" in sql:
            lowered = sql.lower()
            rows = [list(d) for d in self.DOCTORS if f"'{d[1].lower()}'" in lowered or f"'{d[2].lower()}'" in lowered
                    or f"'{d[0]}'" in sql]
            if " and " in lowered and len(rows) > 1:
                rows = [r for r in rows if f"'{r[1].lower()}'" in lowered and f"'{r[2].lower()}'" in lowered]
            return QueryResult(sql, ["doctor_id", "first_name", "last_name", "specialization", "hospital_branch"],
                               rows, len(rows), False, 200, 0.001, None)
        return QueryResult(sql, ["n"], [[1]], 1, False, 200, 0.001, ["integer"])


class Pipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FakeDB()
        self.llm = mock.Mock()
        self.llm.generate_sql.return_value = SQLGeneration("sql", "SELECT 1 AS n;", "SELECT 1 AS n;", False, "m", stats())
        self.llm.check_budget.return_value = 100
        vectors = mock.Mock()
        vectors.get_chunks_by_type.return_value = expected_schema_chunks()
        vectors.search.return_value = []
        embeddings = mock.Mock()
        embeddings.embed_query.return_value = [0.1]
        embeddings.model_name = "e"
        self.service = ChatService(vectors=vectors, embeddings=embeddings, llm=self.llm, database=self.db)
        self.history = []

    def route(self, intent, resolved=None, **extra):
        self.llm.resolve_message.side_effect = lambda *a, **k: MessageRouting(intent, resolved, 0, **extra)

    def ask(self, message):
        trace = self.service.answer(message, list(self.history))
        turn = _turn_to_keep(trace)
        if turn is not None:
            self.history.append(turn)
        return trace, to_response(trace)

    def test_ambiguous_doctor_is_clarified_then_a_bare_first_name_asks_again_then_a_surname_answers(self) -> None:
        self.route("data_question", "List appointments with Dr. Mia in January 2024", doctor_names=("Mia",))
        trace, body = self.ask("List appointments with Dr. Mia in January 2024")
        self.assertEqual(body.status, "needs_clarification")
        self.assertIn("X1 Mia Stone", body.message)
        self.llm.generate_sql.assert_not_called()
        self.assertTrue(self.history[-1].candidates, "candidates kept for the reply")

        self.route("data_question", "List appointments with Dr. Mia Stone in January 2024", doctor_names=("Mia Stone",))
        trace, body = self.ask("Mia")  # the model's rewrite picked Stone; the user did not
        self.assertEqual(body.status, "needs_clarification")
        self.assertIn("still matches more than one doctor", body.message)
        self.llm.generate_sql.assert_not_called()

        self.route("data_question", "List appointments with Dr. Mia Reyes in January 2024", doctor_names=("Mia Reyes",))
        trace, body = self.ask("Reyes")
        self.assertEqual(body.status, "answered")
        facts = self.llm.generate_sql.call_args.args[2]
        self.assertIn("doctors.doctor_id = 'X2'", facts)
        self.assertIn("January 2024: appointment_date >= '2024-01-01' AND appointment_date < '2024-02-01'", facts)

    def test_a_reply_that_picks_one_listed_person_continues_even_if_mislabelled(self) -> None:
        self.route("data_question", "Appointments with Dr. Mia in March 2023", doctor_names=("Mia",))
        self.ask("Appointments with Dr. Mia in March 2023")
        self.route("needs_clarification")  # the classifier did not see it as an answer
        trace, body = self.ask("Stone")
        self.assertEqual(body.status, "answered")
        self.assertEqual(trace.resolved_question, "Appointments with Dr. Mia in March 2023")
        self.assertIn("doctors.doctor_id = 'X1'", self.llm.generate_sql.call_args.args[2])

    def test_a_short_reply_while_waiting_is_never_a_greeting(self) -> None:
        self.route("data_question", "Appointments of Dr. Mia", doctor_names=("Mia",))
        self.ask("Appointments of Dr. Mia")
        self.route("greeting")
        trace, body = self.ask("Mia")
        self.assertEqual(body.status, "needs_clarification")
        self.assertIn("still need an answer", body.message)
        self.route("greeting")
        self.assertEqual(self.ask("Hello")[1].status, "conversation", "a real standalone greeting still works")

    def test_unknown_name_is_not_replaced_by_someone_else(self) -> None:
        self.route("data_question", "Appointments of Dr. Kim", doctor_names=("Kim",))
        trace, body = self.ask("Appointments of Dr. Kim")
        self.assertEqual(body.status, "needs_clarification")
        self.assertIn("couldn't find a doctor named \"Kim\"", body.message)
        self.llm.generate_sql.assert_not_called()

    def test_undefined_measure_and_payment_date_stop_before_sql(self) -> None:
        for question in ["Show the operating ratio for each hospital, based on revenue.", "How many bills were paid in May 2023?"]:
            with self.subTest(question):
                self.route("data_question", question, chart_request="pie")
                trace, body = self.ask(question)
                self.assertEqual((body.status, body.code), ("needs_clarification", "ambiguous"))
        self.llm.generate_sql.assert_not_called()
        self.assertEqual(self.db.queries, [])

    def test_chart_words_with_data_reach_analysis_and_unsupported_donut_is_explained(self) -> None:
        sql = "SELECT d.hospital_branch, COUNT(DISTINCT b.patient_id) AS n FROM billing AS b GROUP BY d.hospital_branch"
        self.llm.generate_sql.return_value = SQLGeneration("sql", sql + ";", sql, False, "m", stats())
        self.db.execute = lambda q: QueryResult(q, ["hospital_branch", "n"], [["North", 3], ["South", 4]], 2, False, 200,
                                                0.001, ["string", "integer"])
        self.service.database = self.db
        with mock.patch("backend.app.services.chat_service.validate", return_value=mock.Mock(ok=True)):
            self.route("data_question", "Number of patients with a bill over 30 per hospital", chart_request="pie")
            trace, body = self.ask("Create a donut chart of patients with a bill over 30 per hospital")
        self.assertEqual(body.status, "answered")
        self.assertNotIn("pie", body.chart.views)
        self.assertIn("A donut isn't offered for this result", body.message)
        self.assertIn("distinct", body.message)

    def test_redraw_reuses_the_previous_result_or_explains_why_not(self) -> None:
        from backend.app.services.conversation_store import Turn
        self.history = [Turn("q", "q", "SELECT 1", chart_views=("bar_vertical", "bar_horizontal", "pie"))]
        self.route("capabilities", topic="change_view", chart_request="pie")
        trace, body = self.ask("Could I get that as a donut?")
        self.assertEqual((body.status, body.view_request), ("conversation", "pie"))
        self.assertIn("no new query", body.message)

        self.history = [Turn("q", "q", "SELECT 1", chart_views=("bar_vertical", "bar_horizontal"),
                             chart_notes=(("pie", "the measure is an average"),))]
        trace, body = self.ask("Could I get that as a donut?")
        self.assertIsNone(body.view_request)
        self.assertIn("can't be shown as a donut: the measure is an average", body.message)
        self.llm.generate_sql.assert_not_called()

    def test_a_chart_request_for_the_same_question_redraws_instead_of_querying(self) -> None:
        from backend.app.services.conversation_store import Turn
        self.history = [Turn("Treatments per type?", "How many treatments are there of each type?", "SELECT 1",
                             chart_views=("bar_vertical", "bar_horizontal", "pie"))]
        self.route("data_question", "How many treatments are there of each type?", chart_request="pie")
        trace, body = self.ask("Show that as a pie chart")
        self.assertEqual((body.status, body.view_request), ("conversation", "pie"))
        self.llm.generate_sql.assert_not_called()
        self.route("data_question", "How many treatments are there of each type in 2023?", chart_request="pie")
        self.assertEqual(self.ask("Same as a pie, but only 2023")[1].status, "answered", "a changed question queries")


if __name__ == "__main__":
    unittest.main()
