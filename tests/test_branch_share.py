"""Branch percentages (backend/app/services/branch_share.py) and the donut rule for distinct counts of a
primary key (chart_builder.py), on an isolated SQLite fixture. No model, index or MySQL.

The fixture: P1 and P2 have appointments at both branches (North: D1, South: D2); P3 only at North.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import copy
import unittest
from unittest import mock

from backend.app.controllers.chat_controller import _turn_to_keep, to_response
from backend.app.repositories.mysql_repository import QueryResult
from backend.app.services import branch_share as bs
from backend.app.services.chart_builder import _parse_select, _pie_problem, build_chart
from backend.app.services.chat_service import ChatService
from backend.app.services.llm_service import GenerationStats, MessageRouting, SQLGeneration
from backend.app.services.rag.context_builder import expected_schema_chunks
from evaluation.fixture_db import JOIN_ROWS, FixtureRepository

_no_db = mock.patch("mysql.connector.connect", side_effect=AssertionError("tests must never connect to MySQL"))


def setUpModule() -> None:
    _no_db.start()


def tearDownModule() -> None:
    _no_db.stop()


ROWS = copy.deepcopy(JOIN_ROWS)
ROWS["patients"].append(("P3", "Mara", "Quinn", "F", "1990-01-01", "5550103", "3 Pine St", "2022-03-03", None, None, None))
ROWS["appointments"].append(("A8", "P3", "D1", "2023-07-01", "10:00:00", "Checkup", "Completed"))

NUMERATOR = ("SELECT d.hospital_branch, COUNT(DISTINCT a.patient_id) AS patient_count FROM appointments AS a "
             "JOIN doctors AS d ON a.doctor_id = d.doctor_id GROUP BY d.hospital_branch ORDER BY d.hospital_branch")
APPOINTMENTS = NUMERATOR.replace("COUNT(DISTINCT a.patient_id) AS patient_count", "COUNT(*) AS appointment_count")


class Wording(unittest.TestCase):
    def test_patient_percentages_by_hospital_need_a_choice(self) -> None:
        for q in ["I would like a chart showing the percentage of patients at each hospital.",
                  "What share of patients does each branch have?", "Proportion of patients per hospital branch in 2023"]:
            with self.subTest(q):
                self.assertTrue(bs.asks_patient_share(q))
        for q in ["How many patients does each branch have?", "Percentage of appointments that were completed",
                  f"Percentage of patients at each hospital {bs.MARKERS[bs.PATIENTS]}?"]:
            with self.subTest(q):
                self.assertFalse(bs.asks_patient_share(q))
        self.assertIn("more than 100%", bs.clarification().replace("exceed 100%", "more than 100%"))

    def test_the_sql_model_is_asked_for_counts_with_the_scope_kept(self) -> None:
        q = f"What is the percentage of patients at each hospital in June 2023 for Completed appointments {bs.MARKERS[bs.PATIENTS]}?"
        asked = bs.numerator_question(bs.PATIENTS, q)
        self.assertEqual(asked, "What is the number of distinct patients with appointments at each hospital in June 2023 "
                                "for Completed appointments (one row per hospital branch)?")
        self.assertNotRegex(asked.lower(), "percent|share|proportion")
        other = bs.numerator_question(bs.APPOINTMENTS, f"Share of patients by branch {bs.MARKERS[bs.APPOINTMENTS]}?")
        self.assertEqual(other, "the number of appointments by branch (one row per hospital branch)?")

    def test_replies_pick_one_measure_or_none(self) -> None:
        for reply, kind in [("1", bs.PATIENTS), ("the first one", bs.PATIENTS), ("patients please", bs.PATIENTS),
                            ("2", bs.APPOINTMENTS), ("share of appointments", bs.APPOINTMENTS),
                            ("both", None), ("patients and appointments", None), ("hmm", None)]:
            with self.subTest(reply):
                self.assertEqual(bs.choice(reply), kind)


class Calculation(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FixtureRepository(rows=ROWS)

    def test_numerator_shape_is_checked(self) -> None:
        self.assertIsNone(bs.numerator_problem(bs.PATIENTS, NUMERATOR))
        self.assertIsNone(bs.numerator_problem(bs.APPOINTMENTS, APPOINTMENTS))
        billed = ("SELECT d.hospital_branch, COUNT(DISTINCT b.patient_id) FROM appointments AS a JOIN doctors AS d ON "
                  "a.doctor_id = d.doctor_id JOIN treatments AS t ON t.appointment_id = a.appointment_id JOIN billing AS b "
                  "ON b.treatment_id = t.treatment_id GROUP BY d.hospital_branch")
        self.assertIn("no treatments or billing", bs.numerator_problem(bs.PATIENTS, billed))
        self.assertIn("COUNT(DISTINCT a.patient_id)", bs.numerator_problem(bs.PATIENTS, APPOINTMENTS))
        computed = NUMERATOR.replace("AS patient_count", "AS patient_count, 100.0 * COUNT(DISTINCT a.patient_id) AS pct")
        self.assertIn("exactly two columns", bs.numerator_problem(bs.PATIENTS, computed))

    def test_distinct_patients_and_overlapping_percentages(self) -> None:
        numerator = self.db.execute(NUMERATOR)
        denominator_sql = bs.denominator_sql(NUMERATOR)
        self.assertNotIn("GROUP BY", denominator_sql)
        self.assertIn("COUNT(DISTINCT a.patient_id) AS all_patients", denominator_sql)
        total = self.db.execute(denominator_sql).rows[0][0]
        self.assertEqual(total, 3, "P1, P2, P3: each patient once, whatever the number of branches")
        derived, note = bs.derive(bs.PATIENTS, numerator, total)
        self.assertEqual(derived.columns, ["hospital_branch", "patient_count", "all_patients", "patient_percent"])
        self.assertEqual(derived.rows, [["North Branch", 3, 3, "100.0"], ["South Branch", 2, 3, "66.7"]])
        self.assertIn("add up to 166.7%", note)
        self.assertIn("not parts of one whole", note)

    def test_the_denominator_keeps_the_question_scope(self) -> None:
        june = NUMERATOR.replace("GROUP BY", "WHERE a.appointment_date >= '2023-06-01' AND a.appointment_date < '2023-07-01' GROUP BY")
        denominator_sql = bs.denominator_sql(june)
        self.assertIn("a.appointment_date >= '2023-06-01'", denominator_sql)
        self.assertEqual(self.db.execute(denominator_sql).rows[0][0], 2, "P3's July appointment is outside the scope")

    def test_appointment_shares_add_up_to_a_whole(self) -> None:
        derived, note = bs.derive(bs.APPOINTMENTS, self.db.execute(APPOINTMENTS), None)
        self.assertEqual(derived.rows, [["North Branch", 5, 7, "71.4"], ["South Branch", 2, 7, "28.6"]])
        self.assertIn("add up to 100.0%", note)

    def test_zero_denominator_gives_no_percentage(self) -> None:
        empty = QueryResult(NUMERATOR, ["hospital_branch", "patient_count"], [["North Branch", 0]], 1, False, 200, 0.0)
        derived, note = bs.derive(bs.PATIENTS, empty, 0)
        self.assertIsNone(derived.rows[0][3])
        self.assertIn("no percentage can be computed", note)


class DonutForDistinctPrimaryKeys(unittest.TestCase):
    def test_age_bands_of_distinct_patients_form_a_whole(self) -> None:
        age = ("SELECT CASE WHEN TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE()) < 30 THEN 'Under 30' ELSE '30 and above' END "
               "AS age_group, COUNT(DISTINCT patient_id) AS patient_count FROM patients GROUP BY age_group")
        self.assertIsNone(_pie_problem(_parse_select(age), 1))
        result = QueryResult(age, ["age_group", "patient_count"], [["Under 30", 9], ["30 and above", 41]], 2, False, 200, 0.0,
                             ["string", "integer"])
        self.assertIn("pie", build_chart(result, 50).views)

    def test_overlapping_or_duplicated_distinct_counts_never_get_a_donut(self) -> None:
        for sql in [NUMERATOR,  # a patient can be in both branches
                    "SELECT t.treatment_type, COUNT(DISTINCT a.appointment_id) AS n FROM appointments AS a JOIN treatments "
                    "AS t ON t.appointment_id = a.appointment_id GROUP BY t.treatment_type",  # child join
                    "SELECT d.hospital_branch, AVG(a.appointment_id) AS n FROM appointments AS a JOIN doctors AS d ON "
                    "a.doctor_id = d.doctor_id GROUP BY d.hospital_branch"]:
            with self.subTest(sql):
                self.assertIsNotNone(_pie_problem(_parse_select(sql), 1))


def stats():
    return GenerationStats(None, None, None, None, None, None, "stop")


class Conversation(unittest.TestCase):
    QUESTION = "I would like a chart showing the percentage of patients at each hospital."

    def setUp(self) -> None:
        self.db = FixtureRepository(rows=ROWS)
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

    def ask(self, message, resolved, sql=None, chart_request=None):
        self.llm.resolve_message.side_effect = lambda *a, **k: MessageRouting(
            "data_question", resolved, len(self.history), chart_request=chart_request)
        if sql:
            self.llm.generate_sql.return_value = SQLGeneration("sql", sql + ";", sql, False, "m", stats())
        trace = self.service.answer(message, list(self.history))
        turn = _turn_to_keep(trace)
        if turn is not None:
            self.history.append(turn)
        return trace, to_response(trace)

    def test_clarify_then_compute_distinct_patient_percentages(self) -> None:
        trace, body = self.ask(self.QUESTION, "What is the percentage of patients at each hospital?", chart_request="pie")
        self.assertEqual(body.status, "needs_clarification")
        self.assertIn("Patients are not assigned to a hospital branch", body.message)
        self.llm.generate_sql.assert_not_called()
        self.assertEqual(self.executed, [])

        trace, body = self.ask("1", "What is the percentage of patients?", NUMERATOR, chart_request="pie")
        self.assertIn(bs.MARKERS[bs.PATIENTS], trace.resolved_question, "the choice completes the pending question")
        self.assertIn("COUNT(DISTINCT a.patient_id) AS patient_count", self.llm.generate_sql.call_args.args[2])
        self.assertNotIn("percent", self.llm.generate_sql.call_args.args[0].lower(), "the model is never asked for percentages")
        self.assertEqual(body.status, "answered")
        self.assertEqual(body.columns, ["hospital_branch", "patient_count", "all_patients", "patient_percent"])
        self.assertEqual(body.rows, [["North Branch", 3, 3, "100.0"], ["South Branch", 2, 3, "66.7"]])
        self.assertEqual(len(self.executed), 2, "the per-branch count and the derived denominator")
        self.assertIn("Percentages computed by MedLens, not by the model", body.message)
        self.assertNotIn("pie", body.chart.views, "overlapping patient percentages are not parts of a whole")
        self.assertIn("more than 100%", body.chart.notes["pie"])
        self.assertIn("A donut isn't offered", body.message)

    def test_choosing_appointments_gives_shares_that_can_be_a_donut(self) -> None:
        self.ask(self.QUESTION, "What is the percentage of patients at each hospital?")
        trace, body = self.ask("share of appointments", "x", APPOINTMENTS)
        self.assertEqual(body.rows, [["North Branch", 5, 7, "71.4"], ["South Branch", 2, 7, "28.6"]])
        self.assertIn("pie", body.chart.views)
        self.assertEqual(len(self.executed), 1, "the total is the sum of the branch counts")

    def test_a_wrong_numerator_is_corrected_once_or_not_executed(self) -> None:
        self.ask(self.QUESTION, "What is the percentage of patients at each hospital?")
        wrong = APPOINTMENTS  # counts appointments for a patient percentage
        self.llm.fix_sql.return_value = SQLGeneration("sql", wrong + ";", wrong, False, "m", stats())
        trace, body = self.ask("1", "x", wrong)
        self.assertEqual((body.status, body.code), ("rejected", "request_mismatch"))
        self.assertEqual(self.executed, [])
