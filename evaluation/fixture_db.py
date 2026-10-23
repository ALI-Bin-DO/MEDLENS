"""An isolated, in-memory fixture database for evaluation cases that the real data cannot show.

The real medlens data has exactly one treatment and one bill per appointment and no appointments
without bills, so a wrong INNER JOIN or a duplicated total looks correct there. This fixture has the
same five tables (columns from database/schema.sql) with a few synthetic rows:
    - two doctors who share a first name (Maria Lopez, Maria Chen) and one unique doctor;
    - an appointment with no treatment and no bill;
    - a treatment with two bills (so per-appointment totals must not be duplicated or dropped);
    - appointments, treatments and bills on both sides of the 2023/2024 year boundary.
JOIN_ROWS is a second data set for optional invoices, zero amounts and one-to-many joins; MONTH_ROWS
adds invoices issued in another month than their appointment.

FixtureRepository mirrors MySQLRepository.execute for the evaluation: every query is first checked
by the real sql_validator (the same rules, including join keys), then transpiled from MySQL to SQLite
with SQLGlot and run on the in-memory copy. Nothing touches MySQL. Column types are not reported,
so no charts are built for fixture results. Tests may pass their own rows (same tables). Only for evaluation and tests; never used by the API.
"""

import copy
import datetime as dt
import sqlite3
from decimal import Decimal

import sqlglot

from backend.app.repositories.mysql_repository import QueryResult
from backend.app.services.sql_validator import SQLValidationError, schema_columns, validate

ROWS = {
    "patients": [
        ("P201", "Lina", "Park", "F", "1980-04-02", "5550001", "1 Elm Rd", "2021-05-01", "CareOne", "N1", "lina.park@example.org"),
        ("P202", "Sam", "Okafor", "M", "1975-09-12", "5550002", "2 Oak Rd", "2022-02-10", "CareOne", "N2", "sam.okafor@example.org"),
        ("P203", "Lina", "Brooks", "F", "1990-01-20", "5550003", "3 Ash Rd", "2023-07-07", "HealthPlus", "N3", "lina.brooks@example.org"),
    ],
    "doctors": [
        ("D101", "Maria", "Lopez", "Cardiology", "5551001", 12, "North Branch", "maria.lopez@example.org"),
        ("D102", "Maria", "Chen", "Neurology", "5551002", 8, "South Branch", "maria.chen@example.org"),
        ("D103", "Omar", "Haddad", "Cardiology", "5551003", 20, "North Branch", "omar.haddad@example.org"),
    ],
    "appointments": [
        ("A301", "P201", "D101", "2023-12-30", "09:00:00", "Checkup", "Completed"),
        ("A302", "P202", "D101", "2024-01-03", "10:00:00", "Consultation", "Completed"),  # no treatment, no bill
        ("A303", "P203", "D102", "2024-01-10", "11:00:00", "Therapy", "Completed"),       # one treatment, two bills
        ("A304", "P201", "D103", "2023-12-01", "12:00:00", "Follow-up", "Scheduled"),
        ("A305", "P202", "D102", "2022-12-15", "13:00:00", "Emergency", "Completed"),
    ],
    "treatments": [
        ("T401", "A301", "ECG", "Basic screening", "100.00", "2023-12-30"),
        ("T402", "A303", "MRI", "Advanced protocol", "200.00", "2024-01-10"),
        ("T403", "A304", "X-Ray", "Standard procedure", "80.00", "2023-12-01"),
        ("T404", "A305", "ECG", "Basic screening", "60.00", "2022-12-15"),
    ],
    "billing": [
        ("B501", "P201", "T401", "2023-12-30", "100.00", "Cash", "Paid"),
        ("B502", "P203", "T402", "2024-01-10", "150.00", "Insurance", "Paid"),
        ("B503", "P203", "T402", "2024-01-12", "50.00", "Insurance", "Pending"),
        ("B504", "P201", "T403", "2023-12-01", "80.00", "Credit Card", "Failed"),
        ("B505", "P202", "T404", "2022-12-15", "60.00", "Cash", "Paid"),
    ],
}
# Optional invoices and one-to-many joins (tests/test_join_fixtures.py and scenarios with "fixture": "joins"):
# an appointment without treatments, a treatment without bills, a real zero-amount bill, two treatments
# with three bills, two different treatments with equal costs, one billed and one unbilled treatment, and a
# doctor without appointments.
JOIN_ROWS = {
    "patients": [
        ("P1", "Ines", "Varga", "F", "1985-03-03", "5550101", "1 Pine St", "2022-01-01", "CareOne", "M1", "ines@example.org"),
        ("P2", "Tomas", "Reid", "M", "1970-06-06", "5550102", "2 Pine St", "2022-02-02", None, None, None),
    ],
    "doctors": [
        ("D1", "Hana", "Ito", "Cardiology", "5552001", 10, "North Branch", "hana.ito@example.org"),
        ("D2", "Hana", "Berg", "Oncology", "5552002", 5, "South Branch", "hana.berg@example.org"),
        ("D3", "Leo", "Marsh", "Cardiology", "5552003", 15, "North Branch", "leo.marsh@example.org"),  # no appointments
    ],
    "appointments": [
        ("A1", "P1", "D1", "2023-06-01", "09:00:00", "Checkup", "Completed"),      # no treatment
        ("A2", "P2", "D1", "2023-06-02", "09:00:00", "Checkup", "Completed"),      # a treatment without bills
        ("A3", "P1", "D2", "2023-06-03", "09:00:00", "Therapy", "Completed"),      # a real zero-amount bill
        ("A4", "P2", "D2", "2023-06-04", "09:00:00", "Therapy", "Scheduled"),      # two treatments, three bills
        ("A5", "P1", "D1", "2023-06-05", "09:00:00", "Consultation", "Cancelled"),  # two treatments, equal costs
        ("A6", "P2", "D1", "2023-06-06", "09:00:00", "Follow-up", "Completed"),    # one billed, one unbilled treatment
    ],
    "treatments": [
        ("T2", "A2", "ECG", None, "40.00", "2023-06-02"),
        ("T3", "A3", "Physio", None, "0.00", "2023-06-03"),
        ("T4a", "A4", "MRI", None, "300.00", "2023-06-04"),
        ("T4b", "A4", "X-Ray", None, "50.00", "2023-06-04"),
        ("T5a", "A5", "ECG", None, "100.00", "2023-06-05"),
        ("T5b", "A5", "X-Ray", None, "100.00", "2023-06-05"),
        ("T6a", "A6", "ECG", None, "70.00", "2023-06-06"),
        ("T6b", "A6", "MRI", None, "90.00", "2023-06-06"),
    ],
    "billing": [
        ("B3", "P1", "T3", "2023-06-03", "0.00", "Cash", "Paid"),
        ("B4a1", "P2", "T4a", "2023-06-04", "200.00", "Insurance", "Paid"),
        ("B4a2", "P2", "T4a", "2023-06-10", "100.00", "Insurance", "Pending"),
        ("B4b", "P2", "T4b", "2023-06-04", "50.00", "Cash", "Paid"),
        ("B5a", "P1", "T5a", "2023-06-05", "100.00", "Cash", "Paid"),
        ("B5b", "P1", "T5b", "2023-06-05", "100.00", "Cash", "Failed"),
        ("B6a", "P2", "T6a", "2023-06-06", "70.00", "Credit Card", "Paid"),
    ],
}
# Dates of different columns in different months (tests/test_request_spec.py and scenarios with "fixture":
# "months"): JOIN_ROWS, plus invoices issued in another month than their appointment, so applying a period
# to the wrong date column changes the result:
#   - B4a2 (A4, June appointment) is issued in July;
#   - A7 is a May appointment billed in June.
MONTH_ROWS = copy.deepcopy(JOIN_ROWS)
MONTH_ROWS["billing"] = [("B4a2", "P2", "T4a", "2023-07-10", "100.00", "Insurance", "Pending") if b[0] == "B4a2" else b
                         for b in MONTH_ROWS["billing"]]
MONTH_ROWS["appointments"].append(("A7", "P1", "D2", "2023-05-30", "09:00:00", "Checkup", "Completed"))
MONTH_ROWS["treatments"].append(("T7", "A7", "ECG", None, "60.00", "2023-05-30"))
MONTH_ROWS["billing"].append(("B7", "P1", "T7", "2023-06-02", "60.00", "Cash", "Paid"))

# Column order of database/schema.sql (checked in build()).
COLUMNS = {
    "patients": ["patient_id", "first_name", "last_name", "gender", "date_of_birth", "contact_number", "address",
                 "registration_date", "insurance_provider", "insurance_number", "email"],
    "doctors": ["doctor_id", "first_name", "last_name", "specialization", "phone_number", "years_experience",
                "hospital_branch", "email"],
    "appointments": ["appointment_id", "patient_id", "doctor_id", "appointment_date", "appointment_time",
                     "reason_for_visit", "status"],
    "treatments": ["treatment_id", "appointment_id", "treatment_type", "description", "cost", "treatment_date"],
    "billing": ["bill_id", "patient_id", "treatment_id", "bill_date", "amount", "payment_method", "payment_status"],
}
NUMERIC = {"cost", "amount", "years_experience"}


def build(rows: dict | None = None) -> sqlite3.Connection:
    rows = ROWS if rows is None else rows
    conn = sqlite3.connect(":memory:")
    conn.create_function("YEAR", 1, lambda d: int(str(d)[:4]) if d else None)
    conn.create_function("MONTH", 1, lambda d: int(str(d)[5:7]) if d else None)
    schema = schema_columns()
    for table, columns in COLUMNS.items():
        if set(columns) != set(schema[table]):
            raise RuntimeError(f"fixture columns for {table} do not match database/schema.sql")
        defs = ", ".join(f"{c} {'NUMERIC' if c in NUMERIC else 'TEXT'}" for c in columns)
        conn.execute(f"CREATE TABLE {table} ({defs})")
        conn.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(columns))})", rows.get(table, []))
    return conn


def _json(value):
    """Money columns come back from SQLite as floats; show them as 2-decimal strings like MySQL DECIMAL."""
    return f"{Decimal(str(value)):.2f}" if isinstance(value, float) else value


class FixtureRepository:
    """execute(sql) like MySQLRepository: validate with the real validator, then run on the fixture."""

    def __init__(self, reference_time: dt.datetime | None = None, row_cap: int = 200, rows: dict | None = None) -> None:
        self.conn = build(rows)
        self.reference_time = reference_time
        self.row_cap = row_cap

    def execute(self, sql: str) -> QueryResult:
        validation = validate(sql)
        if not validation.ok:
            raise SQLValidationError(validation.code or "rejected", validation.reason or "rejected")
        lite = sqlglot.transpile(sql, read="mysql", write="sqlite")[0]
        if self.reference_time is not None:
            lite = lite.replace("CURRENT_DATE", f"'{self.reference_time.date().isoformat()}'")
        cursor = self.conn.execute(lite)
        columns = [d[0] for d in cursor.description]
        fetched = cursor.fetchmany(self.row_cap + 1)
        rows = [[_json(v) for v in row] for row in fetched[: self.row_cap]]
        return QueryResult(sql, columns, rows, len(rows), len(fetched) > self.row_cap, self.row_cap, 0.0, None)
